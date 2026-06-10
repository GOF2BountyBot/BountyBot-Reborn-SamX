import asyncio
import os
from typing import Literal

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.confirm_view import ConfirmView
from cogs._shared.http_error_handler import report_api_error
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import fuzzy_filter, normalize_for_search
from utils.guild_setup import ensure_bountybot_infrastructure
from utils.timestamp_utils import iso_to_discord_ts

from utils import autocomplete_state

# Set up logger
flogger = bblogger.get_logger("discord-gateway-AdminCog")

# Base URL of your Gateway API
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"adminCog loading with API_BASE_URL: {api_base}")


async def _check_is_admin(interaction: discord.Interaction) -> bool:
    """
    Core admin permission check logic, callable directly.

    Returns True if the interacting user has admin rights:
    - Developer override via DEVELOPERS env var
    - Built-in Discord Administrator permission
    - Configured Bot Admin role from API
    """
    # 0) developer override via ENV var
    devs = os.getenv("DEVELOPERS", "")
    if str(interaction.user.id) in [d.strip() for d in devs.split(",") if d.strip()]:
        return True

    # 1) built-in Discord Administrator
    if interaction.user.guild_permissions.administrator:
        return True

    # 2) configured Bot Admin role from API
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=5)
        resp.raise_for_status()
        config_data = resp.json()
        admin_role_id = config_data.get("admin_role_id")
        # Use interaction.user.roles (interaction.user IS a discord.Member for guild
        # slash commands and carries .roles). Mirror the pattern used in playerCog.py
        # /promote: guild.get_role(id) then check if role in interaction.user.roles.
        guild = interaction.guild
        if guild and admin_role_id:
            admin_role = guild.get_role(admin_role_id)
            if admin_role and admin_role in interaction.user.roles:
                return True
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"_check_is_admin: unexpected error for user={interaction.user.id} guild={interaction.guild_id}: {e}"
        )

    return False


def is_admin():
    """
    Allow users with the built-in Administrator permission,
    the configured Bot Admin role, or listed developer IDs.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        return await _check_is_admin(interaction)

    return app_commands.check(predicate)


async def _check_is_super_admin(interaction: discord.Interaction) -> bool:
    """
    Super-admin check: only users listed in the DEVELOPERS env var are permitted.
    No role fallback, no Discord Administrator fallback.
    """
    devs = os.getenv("DEVELOPERS", "")
    return str(interaction.user.id) in [d.strip() for d in devs.split(",") if d.strip()]


def is_super_admin():
    """
    Decorator: restricts command to users listed in the DEVELOPERS env var.
    Use for commands that affect the global data layer (scheduler, data loading).
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if not await _check_is_super_admin(interaction):
            await interaction.response.send_message("❌ This command requires super-admin privileges.", ephemeral=True)
            return False
        return True

    return app_commands.check(predicate)


# B.91: semantic grouping of blender-service render config settings, mirrored
# from RenderConfig.PARAM_GROUPS so /render_config view can present the flat
# settings dict grouped by category instead of as an unordered field dump.
_RENDER_PARAM_GROUPS: dict[str, tuple[str, ...]] = {
    "resolution_limits": ("min_res_x", "max_res_x", "min_res_y", "max_res_y"),
    "sample_limits": ("min_samples", "max_samples"),
    "defaults": ("default_res_x", "default_res_y", "default_samples"),
    "concurrency": ("max_concurrent_renders", "job_ttl_hours"),
}


class AdminCog(commands.Cog):  # pylint: disable=too-many-public-methods
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        self._render_settings: list[str] = []
        # Static catalogs — TTL=None. SELF-HEAL via refresh_fn: a cleared key
        # (from /reload_autocomplete) lazily re-fills on the next get() instead of
        # staying empty forever (kills the D-010 class bug). The handlers already use
        # `await cache.get(category)`, so no handler change is needed for self-heal.
        self._item_catalog: AutocompleteCache[str, list[str]] = AutocompleteCache(
            ttl_seconds=None, refresh_fn=self._fetch_item_catalog, name="adminCog-item-catalog"
        )
        self._ship_catalog: AutocompleteCache[str, list[str]] = AutocompleteCache(
            ttl_seconds=None, refresh_fn=self._fetch_ship_catalog, name="adminCog-ship-catalog"
        )
        # Guild-scoped pending-duel cache for /admin_duel (distinct from DuelCog's
        # per-player caches — this is the guild-wide admin view). Consistency via
        # invalidate-and-cold-fill + a 300s TTL dead-man switch: admin_duel is rare
        # and low-traffic, so a 1.0s cold-fill is acceptable and avoids a second
        # push-payload shape (documented divergence from the per-player push model).
        self._admin_pending_duel_cache: AutocompleteCache[int, list[dict]] = AutocompleteCache(
            ttl_seconds=float(os.getenv("AUTOCOMPLETE_ADMIN_DUEL_TTL_SECONDS", "300")),
            refresh_fn=self._fetch_admin_pending_duels,
            name="adminCog-pending-duels",
        )
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        bot.loop.create_task(self._preload_render_settings())
        bot.loop.create_task(self._preload_static_catalogs())
        flogger.debug("AdminCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_render_settings(self):
        """Preload valid render config setting names from blender-service."""
        blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")
        await self.bot.wait_until_ready()
        for attempt in range(3):
            try:
                resp = await self.http_client.get(f"{blender_base}/config/render", timeout=10)
                resp.raise_for_status()
                self._render_settings = list(resp.json().keys())
                flogger.info(f"Preloaded {len(self._render_settings)} render config settings")
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                wait = 5 * (2**attempt)
                flogger.warning(
                    f"Failed to preload render settings (attempt {attempt + 1}/3): {exc}, retrying in {wait}s"
                )
                await asyncio.sleep(wait)
        flogger.error("Failed to preload render settings after 3 attempts")

    async def _fetch_item_catalog(self, category: str) -> list[str]:
        """Refresh one item-type catalog. Reused as _item_catalog.refresh_fn AND by
        the preload, so a cleared key self-heals on the next get(). Raises on HTTP error.
        """
        resp = await self.http_client.get(f"{api_base}/about/categories/{category}/objects", timeout=10)
        resp.raise_for_status()
        return [obj["name"] for obj in resp.json() if obj.get("name")]

    async def _fetch_ship_catalog(self, _key: str) -> list[str]:
        """Refresh the game-ship catalog. Reused as _ship_catalog.refresh_fn AND by the
        preload, so a cleared key self-heals on the next get(). Raises on HTTP error.
        """
        resp = await self.http_client.get(f"{api_base}/about/categories/ship/objects", timeout=10)
        resp.raise_for_status()
        return [s["name"] for s in resp.json() if s.get("name")]

    async def _preload_static_catalogs(self) -> None:
        """Preload item catalogs (4 categories) and ship catalog from bot-core.

        Uses 5-attempt exponential-backoff retry (5s, 10s, 20s, 40s, 60s) mirroring
        the pattern in bountyCog._preload_data.  On terminal failure leaves the cache
        empty for that category so autocomplete degrades gracefully to an empty list.
        Each loader is shared with the cache refresh_fn so self-heal and preload agree.
        """
        await self.bot.wait_until_ready()

        # Preload each item category independently so one failure doesn't block others.
        for category in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
            for attempt in range(5):
                try:
                    names = await self._fetch_item_catalog(category)
                    self._item_catalog.set(category, names)
                    flogger.info(f"_preload_static_catalogs: loaded {len(names)} items for category={category}")
                    break
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    wait = min(5 * (2**attempt), 60)
                    flogger.warning(
                        f"_preload_static_catalogs: failed for category={category} "
                        f"(attempt {attempt + 1}/5): {type(exc).__name__}: {exc}, retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
            else:
                flogger.error(
                    f"_preload_static_catalogs: terminal failure for category={category} "
                    "after 5 attempts; autocomplete will be empty"
                )
                self._item_catalog.set(category, [])

        # Preload ship catalog.
        for attempt in range(5):
            try:
                names = await self._fetch_ship_catalog("all")
                self._ship_catalog.set("all", names)
                flogger.info(f"_preload_static_catalogs: loaded {len(names)} ships")
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                wait = min(5 * (2**attempt), 60)
                flogger.warning(
                    f"_preload_static_catalogs: failed for ship catalog "
                    f"(attempt {attempt + 1}/5): {type(exc).__name__}: {exc}, retrying in {wait}s"
                )
                await asyncio.sleep(wait)
        else:
            flogger.error(
                "_preload_static_catalogs: terminal failure for ship catalog after 5 attempts; "
                "autocomplete will be empty"
            )
            self._ship_catalog.set("all", [])

    async def _fetch_admin_pending_duels(self, guild_id: int) -> list[dict]:
        """Refresh the guild-wide pending-duel list for /admin_duel. Cache refresh_fn.

        Pre-computes ``_norm`` at fill time so the hot autocomplete path performs
        only a substring check per keystroke.
        """
        resp = await self.http_client.get(
            f"{api_base}/duels/pending-all", params={"guild_id": guild_id}, timeout=3.0
        )
        resp.raise_for_status()
        duels = resp.json()
        for d in duels:
            challenger = d.get("challenger_name") or f"Player {d.get('challenger_id', '?')}"
            target = d.get("target_name") or f"Player {d.get('target_id', '?')}"
            stakes = d.get("stakes", 0)
            label = (
                f"{challenger} vs {target} — {stakes:,} credits"
                if stakes
                else f"{challenger} vs {target} — friendly duel"
            )
            d["_norm"] = normalize_for_search(label)
        return duels

    async def render_setting_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for render config setting names."""
        norm_current = normalize_for_search(current)
        return [
            app_commands.Choice(name=s, value=s)
            for s in self._render_settings
            if norm_current in normalize_for_search(s)
        ][:25]

    async def tier_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for shop tiers."""
        norm_current = normalize_for_search(current)
        return [
            app_commands.Choice(name=t, value=t) for t in self._valid_tiers if norm_current in normalize_for_search(t)
        ]

    @app_commands.command(name="admin_check", description="[ADMIN] Check if a user has bot-admin rights and why")
    @app_commands.describe(user="The user to check")
    async def admin_check(self, interaction: discord.Interaction, user: discord.User):
        """Report whether the given user has admin rights—and by which rule."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        reason = None
        has_admin = False

        # 1) developer override
        devs = os.getenv("DEVELOPERS", "")
        dev_list = [d.strip() for d in devs.split(",") if d.strip()]
        if str(user.id) in dev_list:
            has_admin = True
            reason = "Developer override"

        # 2) built-in Discord Administrator
        if not has_admin:
            guild = self.bot.get_guild(interaction.guild_id)
            member = guild.get_member(user.id) or await guild.fetch_member(user.id)
            if member.guild_permissions.administrator:
                has_admin = True
                reason = "Discord Administrator permission"

        # 3) custom Bot Admin role
        if not has_admin:
            try:
                resp = await self.http_client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=5)
                resp.raise_for_status()
                admin_role_id = resp.json().get("admin_role_id")
                if admin_role_id and any(r.id == admin_role_id for r in member.roles):
                    has_admin = True
                    reason = "Assigned Bot Admin role"
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        # Build response
        if has_admin:
            msg = f"{user.mention} **has** bot-admin rights.\n> **Reason:** {reason}"
        else:
            msg = f"{user.mention} **does not have** bot-admin rights."

        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="admin_setup", description="[ADMIN] Initialize the bot for this guild")
    @app_commands.describe(
        admin_role="Role that should have admin permissions for the bot (required)",
        starting_credits="Starting credits for new players (default: 0)",
    )
    async def admin_setup(self, interaction: discord.Interaction, admin_role: discord.Role, starting_credits: int = 0):
        """Initialize guild for bot usage."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        guild = self.bot.get_guild(interaction.guild_id)

        try:
            # Create (or find) BountyBot Discord infrastructure (role + category + 7 channels)
            channel_ids = await ensure_bountybot_infrastructure(guild)

            # Send initialization to core API
            init_payload = {
                "guild_id": interaction.guild_id,
                "admin_role_id": admin_role.id if admin_role else None,
                "starting_credits": max(0, starting_credits),
                "category_id": channel_ids.get("category_id"),
                "bronze_bounty_channel_id": channel_ids.get("bronze_bounty_channel_id"),
                "silver_bounty_channel_id": channel_ids.get("silver_bounty_channel_id"),
                "gold_bounty_channel_id": channel_ids.get("gold_bounty_channel_id"),
                "platinum_bounty_channel_id": channel_ids.get("platinum_bounty_channel_id"),
                "shop_channel_id": channel_ids.get("shop_channel_id"),
                "hunting_channel_id": channel_ids.get("hunting_channel_id"),
                "discussion_channel_id": channel_ids.get("discussion_channel_id"),
                "image_channel_id": channel_ids.get("image_channel_id"),
                "bounty_hunter_role_id": channel_ids.get("bounty_hunter_role_id"),
                "bronze_role_id": channel_ids.get("bronze_role_id"),
                "silver_role_id": channel_ids.get("silver_role_id"),
                "gold_role_id": channel_ids.get("gold_role_id"),
                "platinum_role_id": channel_ids.get("platinum_role_id"),
                "shop_announcements_role_id": channel_ids.get("shop_announcements_role_id"),
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/guilds/initialize",
                json=init_payload,
                params={"user_id": interaction.user.id},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            # Build and send confirmation embed
            embed = discord.Embed(
                title="✅ Guild Initialization Complete!", description=result["message"], color=discord.Color.green()
            )
            embed.add_field(name="Guild ID", value=str(result["guild_id"]), inline=True)
            embed.add_field(name="Shops Created", value=str(result["shops_created"]), inline=True)
            embed.add_field(name="Admin Role", value=admin_role.mention, inline=True)
            embed.add_field(name="Starting Credits", value=f"{starting_credits:,}", inline=True)

            # Add channel info to embed (all 8 channels)
            channel_display = [
                ("bronze_bounty_channel_id", "Bronze Bounty Board"),
                ("silver_bounty_channel_id", "Silver Bounty Board"),
                ("gold_bounty_channel_id", "Gold Bounty Board"),
                ("platinum_bounty_channel_id", "Platinum Bounty Board"),
                ("shop_channel_id", "Shop"),
                ("hunting_channel_id", "Bounty Hunting"),
                ("discussion_channel_id", "Bounty Discussions"),
                ("image_channel_id", "Bot Images (hidden)"),
            ]
            channels_info = []
            for key, label in channel_display:
                cid = channel_ids.get(key)
                if cid:
                    channels_info.append(f"{label}: <#{cid}>")
            if channels_info:
                embed.add_field(name="Channels", value="\n".join(channels_info), inline=False)

            # Add Bounty Hunter role if created
            bh_role_id = channel_ids.get("bounty_hunter_role_id")
            if bh_role_id:
                embed.add_field(name="Bounty Hunter Role", value=f"<@&{bh_role_id}>", inline=True)

            # Show tier roles
            for tier_key, tier_label in [
                ("bronze_role_id", "Bronze"),
                ("silver_role_id", "Silver"),
                ("gold_role_id", "Gold"),
                ("platinum_role_id", "Platinum"),
            ]:
                tier_rid = channel_ids.get(tier_key)
                if tier_rid:
                    embed.add_field(name=f"Bounty Hunter {tier_label}", value=f"<@&{tier_rid}>", inline=True)

            # Show Shop Announcements role if created
            shop_ann_role_id = channel_ids.get("shop_announcements_role_id")
            if shop_ann_role_id:
                embed.add_field(name="Shop Announcements Role", value=f"<@&{shop_ann_role_id}>", inline=True)

            embed.set_footer(text="The bot is now ready for use in this guild!")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Guild {interaction.guild_id} initialized by {interaction.user}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_setup: {e}")
            await interaction.followup.send("⚠️ An error occurred during guild initialization.", ephemeral=True)

    @app_commands.command(name="admin_player", description="[ADMIN] Manage player data")
    @app_commands.describe(
        user="Player to manage",
        action="Action to perform",
        credit_amount="Credits to set/add (for credit actions)",
        xp="XP to set (for XP actions)",
    )
    @app_commands.rename(credit_amount="credits")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Set Credits", value="set_credits"),
            app_commands.Choice(name="Add Credits", value="add_credits"),
            app_commands.Choice(name="Set XP", value="set_xp"),
            app_commands.Choice(name="View Stats", value="view_stats"),
            app_commands.Choice(name="Reset Player", value="reset"),
            app_commands.Choice(name="Reset Tier-Change Cooldown", value="reset_tier_cooldown"),
            app_commands.Choice(name="Reset Bounty Cooldown", value="reset_bounty_cooldown"),
        ]
    )
    async def admin_player(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        action: str,
        credit_amount: int | None = None,
        xp: int | None = None,
    ):
        """Manage player data."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            # Ensure player exists or create
            user_data = {
                "discord_id": user.id,
                "guild_id": interaction.guild_id,
                "discord_username": str(user),
                "display_name": getattr(user, "display_name", None),
            }
            player_resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=10)
            player_resp.raise_for_status()
            player = player_resp.json()

            # View stats
            if action == "view_stats":
                stats_resp = await self.http_client.get(f"{api_base}/players/{player['id']}/statistics", timeout=10)
                stats_resp.raise_for_status()
                stats_resp.json()

                embed = discord.Embed(title=f"📊 Admin View - {user.display_name}", color=discord.Color.blue())
                embed.add_field(name="Player ID", value=str(player["id"]), inline=True)
                embed.add_field(name="Tier", value=player["tier"], inline=True)
                embed.add_field(name="XP", value=f"{player['xp']:,}", inline=True)
                embed.add_field(name="Credits", value=f"{player['credits']:,}", inline=True)
                embed.add_field(name="Lifetime Credits", value=f"{player['lifetime_credits']:,}", inline=True)
                embed.add_field(name="Prestige Count", value=str(player["prestige_count"]), inline=True)
                embed.set_thumbnail(url=user.display_avatar.url)
                embed.add_field(
                    name="Registered",
                    value=iso_to_discord_ts(player["created_at"], "D"),
                    inline=False,
                )
                embed.set_footer(text="Player data managed by BountyBot")

                await interaction.followup.send(embed=embed, ephemeral=True)

            # Set credits
            elif action == "set_credits":
                if credit_amount is None:
                    await interaction.followup.send("❌ Credits amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/credits",
                    json={"player_id": player["id"], "credits": max(0, credit_amount), "update_lifetime": False},
                    params={"user_id": interaction.user.id, "guild_id": interaction.guild_id},
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ Credits Updated",
                    description=f"Set {user.display_name}'s credits to {credit_amount:,}",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Old Credits", value=f"{result['old_credits']:,}", inline=True)
                embed.add_field(name="New Credits", value=f"{result['new_credits']:,}", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

                # Invalidate player cache — credits changed
                try:
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_player set_credits: cache invalidation failed for user={user.id}; "
                        "transaction still succeeded"
                    )

            # Add credits
            elif action == "add_credits":
                if credit_amount is None:
                    await interaction.followup.send("❌ Credits amount required.", ephemeral=True)
                    return
                new_total = max(0, player["credits"] + credit_amount)
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/credits",
                    json={"player_id": player["id"], "credits": new_total, "update_lifetime": True},
                    params={"user_id": interaction.user.id, "guild_id": interaction.guild_id},
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ Credits Added",
                    description=f"Added {credit_amount:,} credits to {user.display_name}",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Amount Added", value=f"{credit_amount:,}", inline=True)
                embed.add_field(name="New Total", value=f"{result['new_credits']:,}", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

                # Invalidate player cache — credits changed
                try:
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_player add_credits: cache invalidation failed for user={user.id}; "
                        "transaction still succeeded"
                    )

            # Set XP
            elif action == "set_xp":
                if xp is None:
                    await interaction.followup.send("❌ XP amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/xp",
                    json={"player_id": player["id"], "xp": max(0, min(1_000_000, xp))},
                    params={"user_id": interaction.user.id, "guild_id": interaction.guild_id},
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ XP Updated",
                    description=f"Set {user.display_name}'s XP to {xp:,}",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Old XP", value=f"{result['old_xp']:,}", inline=True)
                embed.add_field(name="New XP", value=f"{result['new_xp']:,}", inline=True)
                embed.add_field(name="Old Tier", value=result["old_tier"], inline=True)
                embed.add_field(name="New Tier", value=result["new_tier"], inline=True)
                if result.get("tier_changed"):
                    embed.add_field(name="Tier Change", value="✅ Tier Updated!", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

                # Invalidate player cache — xp (and possibly tier) changed
                try:
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_player set_xp: cache invalidation failed for user={user.id}; "
                        "transaction still succeeded"
                    )

            # Reset cooldown (tier_change or bounty) via cooldown reset endpoint
            elif action in ("reset_tier_cooldown", "reset_bounty_cooldown"):
                cooldown_type = "tier_change" if action == "reset_tier_cooldown" else "bounty"
                resp = await self.http_client.put(
                    f"{api_base}/players/{interaction.guild_id}/{user.id}/cooldown/reset",
                    params={"cooldown_type": cooldown_type},
                    timeout=10,
                )
                if resp.status_code == 404:
                    await interaction.followup.send(f"❌ Player not found for {user.display_name}.", ephemeral=True)
                    return
                resp.raise_for_status()
                label = "Tier-Change" if cooldown_type == "tier_change" else "Bounty"
                embed = discord.Embed(
                    title=f"✅ {label} Cooldown Reset",
                    description=f"{user.display_name}'s {label.lower()} cooldown has been cleared.",
                    color=discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            # Reset player stats
            elif action == "reset":
                resp = await self.http_client.post(
                    f"{api_base}/admin/players/{player['id']}/reset",
                    params={"user_id": interaction.user.id, "guild_id": interaction.guild_id},
                    timeout=10,
                )
                if resp.status_code == 404:
                    await interaction.followup.send(f"❌ Player not found for {user.display_name}.", ephemeral=True)
                    return
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ Player Reset",
                    description=f"Reset {user.display_name}'s stats to defaults",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Credits", value=f"{result['credits']:,}", inline=True)
                embed.add_field(name="XP", value=f"{result['xp']:,}", inline=True)
                embed.add_field(name="Tier", value=result["tier"], inline=True)
                embed.add_field(name="Bounty Wins", value=str(result["bounty_wins"]), inline=True)
                embed.add_field(name="Duel Wins", value=str(result["duel_wins"]), inline=True)
                embed.add_field(name="Duel Losses", value=str(result["duel_losses"]), inline=True)
                embed.add_field(name="Prestige Count", value=str(result["prestige_count"]), inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

                # Invalidate player cache — full player reset
                try:
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_player reset: cache invalidation failed for user={user.id}; "
                        "transaction still succeeded"
                    )

            flogger.info(
                f"Admin {interaction.user} performed {action} on player {user} in guild {interaction.guild_id}"
            )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_player: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing player.", ephemeral=True)

    @app_commands.command(name="admin_refresh_shop", description="[ADMIN] Force refresh a shop")
    @app_commands.describe(
        tier="Shop tier to refresh (omit to refresh ALL tiers)",
        force_tech_level="Force all items to specific tech level (1-9)",
    )
    @app_commands.autocomplete(tier=tier_autocomplete)
    async def admin_refresh_shop(
        self,
        interaction: discord.Interaction,
        tier: str | None = None,
        force_tech_level: int | None = None,
    ):
        """Force refresh a guild shop.

        When ``tier`` is omitted, refreshes every tier (Bronze/Silver/Gold/Platinum)
        in sequence. Useful for admins who want to reset the whole shop economy
        in one command rather than running the command four times.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            if tier is not None and tier not in self._valid_tiers:
                await interaction.followup.send(
                    f"❌ Invalid tier. Valid tiers: {', '.join(self._valid_tiers)}", ephemeral=True
                )
                return
            if force_tech_level and (force_tech_level < 1 or force_tech_level > 9):
                await interaction.followup.send("❌ Tech level must be between 1 and 9.", ephemeral=True)
                return

            tiers_to_refresh = [tier] if tier is not None else list(self._valid_tiers)
            refreshed = []
            for t in tiers_to_refresh:
                refresh_data = {"guild_id": interaction.guild_id, "tier": t, "force_tech_level": force_tech_level}
                resp = await self.http_client.post(
                    f"{api_base}/admin/shops/refresh",
                    json=refresh_data,
                    params={"user_id": interaction.user.id},
                    timeout=30,
                )
                resp.raise_for_status()
                refreshed.append(t)

            tier_summary = ", ".join(refreshed)
            embed = discord.Embed(
                title="✅ Shop Refreshed Successfully!",
                description=f"Refreshed tier(s): **{tier_summary}**",
                color=discord.Color.green(),
            )
            embed.add_field(name="Guild ID", value=str(interaction.guild_id), inline=True)
            if force_tech_level:
                embed.add_field(name="Forced Tech Level", value=str(force_tech_level), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} refreshed shop(s) {tier_summary} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_refresh_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while refreshing shop.", ephemeral=True)

    @app_commands.command(name="admin_guild_stats", description="[ADMIN] View guild statistics")
    async def admin_guild_stats(self, interaction: discord.Interaction):
        """View comprehensive guild statistics."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            resp = await self.http_client.get(
                f"{api_base}/admin/guilds/{interaction.guild_id}/stats",
                params={"user_id": interaction.user.id},
                timeout=10,
            )
            resp.raise_for_status()
            stats = resp.json()

            embed = discord.Embed(title=f"📊 Guild Statistics - {interaction.guild.name}", color=discord.Color.blue())
            embed.add_field(name="Guild ID", value=str(stats["guild_id"]), inline=True)
            embed.add_field(name="Total Players", value=str(stats["total_players"]), inline=True)
            if stats.get("tier_distribution"):
                tier_text = "\n".join([f"{tier}: {count}" for tier, count in stats["tier_distribution"].items()])
                embed.add_field(name="Tier Distribution", value=tier_text, inline=True)
            embed.add_field(name="Total Credits", value=f"{stats['total_credits']:,}", inline=True)
            embed.add_field(name="Total XP", value=f"{stats['total_xp']:,}", inline=True)
            embed.add_field(name="Average Credits", value=f"{stats['average_credits']:,.1f}", inline=True)
            embed.add_field(name="Average XP", value=f"{stats['average_xp']:,.1f}", inline=True)
            embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.debug(f"Admin {interaction.user} viewed guild stats for {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_guild_stats: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching guild statistics.", ephemeral=True)

    @app_commands.command(name="admin_config", description="[ADMIN] View or update guild configuration")
    @app_commands.describe(
        action="Configuration action to perform",
        starting_credits="Starting credits for new players",
        admin_role="Admin role for the bot",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View Config", value="view"),
            app_commands.Choice(name="Set Starting Credits", value="set_credits"),
            app_commands.Choice(name="Set Admin Role", value="set_role"),
            app_commands.Choice(name="Reset to Defaults", value="reset"),
        ]
    )
    async def admin_config(
        self,
        interaction: discord.Interaction,
        action: str,
        starting_credits: int | None = None,
        admin_role: discord.Role | None = None,
    ):
        """Manage guild configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            if action == "view":
                resp = await self.http_client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=10)
                resp.raise_for_status()
                cfg = resp.json()

                embed = discord.Embed(title="⚙️ Guild Configuration", color=discord.Color.blue())
                embed.add_field(name="Guild ID", value=str(cfg["guild_id"]), inline=True)
                embed.add_field(name="Configured", value="✅" if cfg["configured"] else "❌", inline=True)
                embed.add_field(
                    name="Admin Role Set", value="✅" if cfg["admin_role_configured"] else "❌", inline=True
                )
                embed.add_field(name="Starting Credits", value=f"{cfg['starting_credits']:,}", inline=True)
                embed.add_field(name="Sale Price Factor", value=f"{cfg['sale_price_factor']:.1%}", inline=True)

                thresholds = cfg["xp_thresholds"]
                threshold_lines = [
                    f"Silver: {thresholds['Silver']:,}",
                    f"Gold: {thresholds['Gold']:,}",
                    f"Platinum: {thresholds['Platinum']:,}",
                ]
                if "Prestige" in thresholds:
                    threshold_lines.append(f"Prestige: {thresholds['Prestige']:,}")
                else:
                    threshold_lines.append("Prestige: (default)")
                embed.add_field(name="XP Thresholds", value="\n".join(threshold_lines), inline=True)
                embed.add_field(
                    name="Timestamps",
                    value=(
                        f"Created: {iso_to_discord_ts(cfg['created_at'], 'D')}\n"
                        f"Updated: {iso_to_discord_ts(cfg['updated_at'], 'D')}"
                    ),
                    inline=False,
                )
                embed.set_footer(text="Use /admin_config action:Set ... to update")

                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "set_credits":
                if starting_credits is None:
                    await interaction.followup.send("❌ Starting credits amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/starting-credits/{max(0, starting_credits)}",
                    timeout=10,
                )
                resp.raise_for_status()
                await interaction.followup.send(f"✅ Starting credits set to {starting_credits:,}", ephemeral=True)

            elif action == "set_role":
                if admin_role is None:
                    await interaction.followup.send("❌ Admin role required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/admin-role/{admin_role.id}", timeout=10
                )
                resp.raise_for_status()
                await interaction.followup.send(f"✅ Admin role set to {admin_role.mention}", ephemeral=True)

            elif action == "reset":
                resp = await self.http_client.post(f"{api_base}/config/guild/{interaction.guild_id}/reset", timeout=10)
                resp.raise_for_status()
                await interaction.followup.send(
                    "✅ Guild configuration has been reset to default values", ephemeral=True
                )

            flogger.info(f"Admin {interaction.user} performed config {action} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing configuration.", ephemeral=True)

    @app_commands.command(name="admin_uninstall", description="[ADMIN] Completely remove all bot data from this guild")
    async def admin_uninstall(
        self,
        interaction: discord.Interaction,
    ):
        """Destructively remove all bot data for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        # 2-step confirmation: show warning embed with button dialog
        warning_embed = discord.Embed(
            title="⚠️ WARNING: Destructive Operation",
            description=(
                "This will **permanently delete** all bot data for this guild including:\n"
                "• All player records and statistics\n"
                "• All shop configurations\n"
                "• All guild settings\n\n"
                "**This action cannot be undone.**\n\n"
                "Press **Confirm** to proceed or **Cancel** to abort."
            ),
            color=discord.Color.red(),
        )
        warning_embed.set_footer(text="Bot data will NOT be deleted until you confirm.")
        view = ConfirmView(action="uninstall the bot", timeout=60)
        await interaction.followup.send(embed=warning_embed, view=view, ephemeral=True)
        await view.wait()

        if view.result is None:
            await interaction.followup.send("⏱️ Confirmation timed out. Uninstall cancelled.", ephemeral=True)
            return
        if not view.result:
            await interaction.followup.send("❌ Uninstall cancelled.", ephemeral=True)
            return

        try:
            # Fetch current config to get channel/role IDs for cleanup
            cfg: dict = {}
            try:
                cfg_resp = await self.http_client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=10)
                cfg_resp.raise_for_status()
                cfg = cfg_resp.json()
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Guild {interaction.guild_id}: could not fetch config for cleanup: {e}")

            # Delete Discord channels/category/role (best-effort, non-fatal)
            guild = self.bot.get_guild(interaction.guild_id)
            if guild:
                # Delete the 8 text channels
                channel_keys = [
                    "bronze_bounty_channel_id",
                    "silver_bounty_channel_id",
                    "gold_bounty_channel_id",
                    "platinum_bounty_channel_id",
                    "shop_channel_id",
                    "hunting_channel_id",
                    "discussion_channel_id",
                    "image_channel_id",
                ]
                for key in channel_keys:
                    ch_id = cfg.get(key)
                    if ch_id:
                        ch = guild.get_channel(ch_id)
                        if ch:
                            try:
                                await ch.delete(reason="BountyBot uninstall")
                            except Exception as e:  # pylint: disable=broad-exception-caught
                                flogger.warning(f"Guild {interaction.guild_id}: failed to delete channel {ch_id}: {e}")

                # Delete the category
                cat_id = cfg.get("category_id")
                if cat_id:
                    cat = guild.get_channel(cat_id)
                    if cat:
                        try:
                            await cat.delete(reason="BountyBot uninstall")
                        except Exception as e:  # pylint: disable=broad-exception-caught
                            flogger.warning(f"Guild {interaction.guild_id}: failed to delete category {cat_id}: {e}")

                # Delete BountyBot roles (by stored ID + by known name for robustness)
                _BOUNTYBOT_ROLE_NAMES = {
                    "bounty hunter",
                    "bounty hunter bronze",
                    "bounty hunter silver",
                    "bounty hunter gold",
                    "bounty hunter platinum",
                }
                stored_role_ids: set = set()
                for rk in (
                    "bounty_hunter_role_id",
                    "bronze_role_id",
                    "silver_role_id",
                    "gold_role_id",
                    "platinum_role_id",
                ):
                    rid = cfg.get(rk)
                    if rid:
                        stored_role_ids.add(rid)

                for role in guild.roles:
                    if role.id in stored_role_ids or role.name.lower() in _BOUNTYBOT_ROLE_NAMES:
                        try:
                            await role.delete(reason="BountyBot uninstall")
                        except Exception as e:  # pylint: disable=broad-exception-caught
                            flogger.warning(
                                f"Guild {interaction.guild_id}: failed to delete role {role.name} ({role.id}): {e}"
                            )

            # Call bot-core uninstall API to remove game data
            resp = await self.http_client.delete(
                f"{api_base}/admin/guilds/{interaction.guild_id}/uninstall",
                params={"user_id": interaction.user.id},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="✅ Bot Uninstalled",
                description=result.get("message", "Bot data has been removed from this guild."),
                color=discord.Color.orange(),
            )
            removed = result.get("removed_counts", {})
            if removed:
                removed_text = "\n".join(f"{k}: {v}" for k, v in removed.items())
                embed.add_field(name="Records Removed", value=removed_text, inline=False)
            embed.add_field(name="Warning", value=result.get("warning", "All data has been deleted."), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.warning(f"Guild {interaction.guild_id} uninstalled by {interaction.user}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_uninstall: {e}")
            await interaction.followup.send("⚠️ An error occurred during uninstall.", ephemeral=True)

    @app_commands.command(name="admin_config_shop", description="[ADMIN] Update shop configuration")
    @app_commands.describe(
        ship_count_min="Minimum number of ship types in shop",
        ship_count_max="Maximum number of ship types in shop",
        weapon_count_min="Minimum number of primary weapon types in shop",
        weapon_count_max="Maximum number of primary weapon types in shop",
        secondary_weapon_count_min="Minimum number of secondary weapon types in shop",
        secondary_weapon_count_max="Maximum number of secondary weapon types in shop",
        module_count_min="Minimum number of module types in shop",
        module_count_max="Maximum number of module types in shop",
        turret_count_min="Minimum number of turret types in shop",
        turret_count_max="Maximum number of turret types in shop",
        sale_factor="Sale price factor (0.0 - 1.0, e.g. 0.8 = 80% of base price)",
    )
    async def admin_config_shop(
        self,
        interaction: discord.Interaction,
        ship_count_min: int | None = None,
        ship_count_max: int | None = None,
        weapon_count_min: int | None = None,
        weapon_count_max: int | None = None,
        secondary_weapon_count_min: int | None = None,
        secondary_weapon_count_max: int | None = None,
        module_count_min: int | None = None,
        module_count_max: int | None = None,
        turret_count_min: int | None = None,
        turret_count_max: int | None = None,
        sale_factor: float | None = None,
    ):
        """Update shop-specific configuration for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        # Build item_count_ranges — only include a type's range when BOTH min and max
        # are provided so that bot-core never receives a partial {"min": N} dict that
        # would fail its "min and max required" validation.
        item_count_ranges: dict[str, dict[str, int]] = {}
        if ship_count_min is not None and ship_count_max is not None:
            item_count_ranges["ships"] = {"min": ship_count_min, "max": ship_count_max}
        if weapon_count_min is not None and weapon_count_max is not None:
            item_count_ranges["weapons"] = {"min": weapon_count_min, "max": weapon_count_max}
        if secondary_weapon_count_min is not None and secondary_weapon_count_max is not None:
            item_count_ranges["secondary_weapons"] = {
                "min": secondary_weapon_count_min,
                "max": secondary_weapon_count_max,
            }
        if module_count_min is not None and module_count_max is not None:
            item_count_ranges["modules"] = {"min": module_count_min, "max": module_count_max}
        if turret_count_min is not None and turret_count_max is not None:
            item_count_ranges["turrets"] = {"min": turret_count_min, "max": turret_count_max}

        # Build the shop-config payload matching UpdateShopConfigRequest
        shop_payload: dict = {"guild_id": interaction.guild_id}
        if item_count_ranges:
            shop_payload["item_count_ranges"] = item_count_ranges

        try:
            resp = await self.http_client.put(
                f"{api_base}/config/guild/{interaction.guild_id}/shop",
                json=shop_payload,
                timeout=10,
            )
            resp.raise_for_status()
            cfg = resp.json()

            # If the user also supplied sale_factor, update it via the general
            # config endpoint (sale_price_factor is not part of UpdateShopConfigRequest).
            if sale_factor is not None:
                sale_resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}",
                    json={"guild_id": interaction.guild_id, "sale_price_factor": sale_factor},
                    timeout=10,
                )
                sale_resp.raise_for_status()
                cfg = sale_resp.json()

            # Display updated config.
            # The GuildConfigResponse.shop_config dict is structured as:
            #   {
            #     "item_count_ranges": {"ships": {"min": N, "max": N}, ...},
            #     "quantity_ranges":   {"ships": {"min": N, "max": N}, ...},
            #     "tech_level_probabilities": {...},
            #   }
            shop_cfg = cfg.get("shop_config", {})
            item_ranges = shop_cfg.get("item_count_ranges", {})

            def _range_str(key: str) -> str:
                r = item_ranges.get(key, {})
                return f"Min: {r.get('min', '?')} / Max: {r.get('max', '?')}"

            embed = discord.Embed(
                title="✅ Shop Configuration Updated",
                description="Current shop configuration for this guild:",
                color=discord.Color.green(),
            )
            embed.add_field(name="Ships", value=_range_str("ships"), inline=True)
            embed.add_field(name="Weapons", value=_range_str("weapons"), inline=True)
            embed.add_field(name="Secondary Weapons", value=_range_str("secondary_weapons"), inline=True)
            embed.add_field(name="Modules", value=_range_str("modules"), inline=True)
            embed.add_field(name="Turrets", value=_range_str("turrets"), inline=True)
            sale_pf = cfg.get("sale_price_factor")
            embed.add_field(
                name="Sale Price Factor",
                value=f"{sale_pf:.0%}" if isinstance(sale_pf, float) else str(sale_pf or "?"),
                inline=True,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} updated shop config for guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while updating shop configuration.", ephemeral=True)

    @app_commands.command(name="admin_config_validate", description="[ADMIN] Validate guild configuration")
    async def admin_config_validate(self, interaction: discord.Interaction):
        """Validate the current guild configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/config/guild/{interaction.guild_id}/validate",
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()

            valid = result.get("valid", False)
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])

            embed = discord.Embed(
                title=f"{'✅ Configuration Valid' if valid else '❌ Configuration Invalid'}",
                description=f"Validation results for guild **{interaction.guild.name}**",
                color=discord.Color.green() if valid else discord.Color.red(),
            )

            if errors:
                embed.add_field(
                    name="❌ Errors",
                    value="\n".join(f"• {e}" for e in errors) or "None",
                    inline=False,
                )
            else:
                embed.add_field(name="❌ Errors", value="None", inline=False)

            if warnings:
                embed.add_field(
                    name="⚠️ Warnings",
                    value="\n".join(f"• {w}" for w in warnings) or "None",
                    inline=False,
                )
            else:
                embed.add_field(name="⚠️ Warnings", value="None", inline=False)

            embed.set_footer(text=f"Guild ID: {result.get('guild_id', interaction.guild_id)}")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.debug(f"Admin {interaction.user} validated config for guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_validate: {e}")
            await interaction.followup.send("⚠️ An error occurred while validating configuration.", ephemeral=True)

    # ------------------------------------------------------------------
    # Render configuration commands
    # ------------------------------------------------------------------

    @app_commands.command(name="render_config", description="[ADMIN] View/update blender render settings")
    @app_commands.describe(
        action="Action to perform: view current config, set a value, or reset to defaults",
        setting="Setting name to update (required for 'set' action)",
        value="New integer value (required for 'set' action)",
    )
    @app_commands.autocomplete(setting=render_setting_autocomplete)
    async def render_config(
        self,
        interaction: discord.Interaction,
        action: Literal["view", "set", "reset"] = "view",
        setting: str | None = None,
        value: int | None = None,
    ) -> None:
        """Admin command to view/update blender-service render configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")

        try:
            if action == "view":
                resp = await self.http_client.get(f"{blender_base}/config/render")
                resp.raise_for_status()
                config = resp.json()
                # B.91: present the flat settings dict grouped by semantic category.
                embed = discord.Embed(
                    title="🎨 Render Configuration",
                    description="Settings grouped by category — change one with `/render_config set`.",
                    color=discord.Color.blue(),
                )
                shown: set[str] = set()
                for group_name, fields in _RENDER_PARAM_GROUPS.items():
                    lines = [f"`{f}` = `{config[f]}`" for f in fields if f in config]
                    if not lines:
                        continue
                    shown.update(f for f in fields if f in config)
                    embed.add_field(name=group_name.replace("_", " ").title(), value="\n".join(lines), inline=False)
                # Forward-compat: surface any settings the gateway has no group for.
                ungrouped = [f"`{k}` = `{v}`" for k, v in config.items() if k not in shown]
                if ungrouped:
                    embed.add_field(name="Other", value="\n".join(ungrouped), inline=False)
                embed.add_field(
                    name="⚙️ Invariants",
                    value=(
                        "• `min_* ≤ default_* ≤ max_*` for resolution and samples\n"
                        "• all resolution / sample bounds must be positive\n"
                        "Updates that would break these are rejected."
                    ),
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "set":
                # DEF-U1-001: mutating render config requires super-admin (DEVELOPERS only).
                if not await _check_is_super_admin(interaction):
                    await interaction.followup.send(
                        "❌ Changing render config requires super-admin privileges.", ephemeral=True
                    )
                    return
                if not setting or value is None:
                    await interaction.followup.send("⚠️ Usage: `/render_config set <setting> <value>`", ephemeral=True)
                    return
                # C.1 / B.32: validate setting against preloaded allowlist before API call.
                # Fail CLOSED when preload failed (empty list): do not silently pass the
                # call through to blender-service, which would re-introduce the B.32
                # silent-no-op behavior.  Fail OPEN would only be correct if blender-service
                # guaranteed a friendly 422; we cannot rely on that for UX clarity.
                if not self._render_settings:
                    flogger.warning(
                        f"render_config set: _render_settings preload not ready "
                        f"(empty); blocking call for setting={setting!r} "
                        f"user={interaction.user} guild={interaction.guild_id}"
                    )
                    await interaction.followup.send(
                        "⚠️ Render config preload is not yet ready. "
                        "Please retry in a moment or contact an admin if this persists.",
                        ephemeral=True,
                    )
                    return
                if setting not in self._render_settings:
                    valid = ", ".join(f"`{s}`" for s in sorted(self._render_settings))
                    await interaction.followup.send(
                        f"⚠️ Unknown setting `{setting}`. Valid settings: {valid}",
                        ephemeral=True,
                    )
                    return
                resp = await self.http_client.put(
                    f"{blender_base}/config/render",
                    json={setting: value},
                )
                resp.raise_for_status()
                await interaction.followup.send(f"✅ Updated `{setting}` = `{value}`", ephemeral=True)
                flogger.info(f"Admin {interaction.user} updated render config: {setting}={value}")

            elif action == "reset":
                # DEF-U1-001: resetting render config requires super-admin (DEVELOPERS only).
                if not await _check_is_super_admin(interaction):
                    await interaction.followup.send(
                        "❌ Resetting render config requires super-admin privileges.", ephemeral=True
                    )
                    return
                resp = await self.http_client.post(f"{blender_base}/config/render/reset")
                resp.raise_for_status()
                await interaction.followup.send("✅ Render config reset to defaults.", ephemeral=True)
                flogger.info(f"Admin {interaction.user} reset render config")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /render_config: {e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="render_cache_clear", description="[ADMIN] Clear blender render cache (/tmp)")
    async def render_cache_clear(self, interaction: discord.Interaction) -> None:
        """Admin command to clear blender-service temp render files."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")

        try:
            resp = await self.http_client.post(f"{blender_base}/cache/clear")
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(title="🗑️ Render Cache Cleared", color=discord.Color.green())
            embed.add_field(name="Directories Cleared", value=str(result["cleared_directories"]), inline=True)
            embed.add_field(name="Space Freed", value=f"{result['freed_mb']} MB", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} cleared render cache: "
                f"{result['cleared_directories']} dirs, {result['freed_mb']} MB"
            )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /render_cache_clear: {e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # Bounty admin commands
    # ------------------------------------------------------------------

    @app_commands.command(name="admin_clear_bounties", description="[ADMIN] Clear active bounties for this guild")
    @app_commands.describe(
        tier="Tier to clear (omit for all tiers)",
    )
    @app_commands.choices(
        tier=[
            app_commands.Choice(name="Bronze", value="bronze"),
            app_commands.Choice(name="Silver", value="silver"),
            app_commands.Choice(name="Gold", value="gold"),
            app_commands.Choice(name="Platinum", value="platinum"),
        ]
    )
    async def admin_clear_bounties(self, interaction: discord.Interaction, tier: str | None = None):
        """Clear active bounties for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        tier_display = tier.title() if tier else "All"
        warning_embed = discord.Embed(
            title="⚠️ Clear Bounties",
            description=(
                f"This will **permanently delete** all active **{tier_display}** tier bounties in this guild.\n\n"
                "**This action cannot be undone.**\n\n"
                "Press **Confirm** to proceed or **Cancel** to abort."
            ),
            color=discord.Color.orange(),
        )
        view = ConfirmView(action="clear bounties", timeout=60)
        await interaction.followup.send(embed=warning_embed, view=view, ephemeral=True)
        await view.wait()

        if view.result is None:
            await interaction.followup.send("⏱️ Confirmation timed out. Clear cancelled.", ephemeral=True)
            return
        if not view.result:
            await interaction.followup.send("❌ Clear bounties cancelled.", ephemeral=True)
            return

        try:
            params: dict = {"user_id": interaction.user.id}
            if tier:
                params["tier"] = tier

            resp = await self.http_client.delete(
                f"{api_base}/bounties/guild/{interaction.guild_id}/clear",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            tier_display = tier.title() if tier else "All"
            embed = discord.Embed(title="🗑️ Bounties Cleared", color=discord.Color(0xFFA500))
            embed.add_field(name="Tier", value=tier_display, inline=True)
            embed.add_field(name="Bounties Cleared", value=str(result.get("cleared_count", 0)), inline=True)
            embed.add_field(
                name="Announcements Deleted", value=str(result.get("announcements_deleted", 0)), inline=True
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} cleared {tier_display} bounties in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_clear_bounties: {e}")
            await interaction.followup.send("⚠️ An error occurred while clearing bounties.", ephemeral=True)

    @app_commands.command(name="admin_config_bounty", description="[ADMIN] View or update bounty configuration")
    @app_commands.describe(
        action="View current config or update settings",
        max_bronze="Max active bounties for Bronze tier (0-20)",
        max_silver="Max active bounties for Silver tier (0-20)",
        max_gold="Max active bounties for Gold tier (0-20)",
        max_platinum="Max active bounties for Platinum tier (0-20)",
        expiry_minutes="Bounty expiry time in minutes (10-10080)",
        spawn_interval="Spawn check interval in minutes (5-1440)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View", value="view"),
            app_commands.Choice(name="Update", value="update"),
        ]
    )
    async def admin_config_bounty(
        self,
        interaction: discord.Interaction,
        action: str,
        max_bronze: int | None = None,
        max_silver: int | None = None,
        max_gold: int | None = None,
        max_platinum: int | None = None,
        expiry_minutes: int | None = None,
        spawn_interval: int | None = None,
    ):
        """View or update bounty configuration for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        try:
            if action == "view":
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{interaction.guild_id}/bounty",
                    timeout=10,
                )
                resp.raise_for_status()
                cfg = resp.json()

                max_per_tier = cfg.get("max_bounties_per_tier", {})
                active_per_tier = cfg.get("active_bounties_per_tier", {})
                expiry = cfg.get("bounty_expiry_minutes", "?")
                spawn_int = cfg.get("bounty_spawn_interval_minutes", "?")
                next_spawn = cfg.get("next_spawn_check_at")

                bronze_max = max_per_tier.get("bronze", "?")
                silver_max = max_per_tier.get("silver", "?")
                gold_max = max_per_tier.get("gold", "?")
                bronze_active = active_per_tier.get("bronze", 0)
                silver_active = active_per_tier.get("silver", 0)
                gold_active = active_per_tier.get("gold", 0)

                embed = discord.Embed(title="⚙️ Bounty Configuration", color=discord.Color.blue())
                embed.add_field(
                    name="Max Per Tier",
                    value=(
                        f"Bronze: {bronze_max} ({bronze_active} active) | "
                        f"Silver: {silver_max} ({silver_active} active) | "
                        f"Gold: {gold_max} ({gold_active} active)"
                    ),
                    inline=False,
                )
                embed.add_field(name="Expiry Time", value=f"{expiry} minutes", inline=True)
                embed.add_field(name="Spawn Interval", value=f"{spawn_int} minutes (±25% randomization)", inline=True)
                embed.add_field(name="Next Spawn Check", value=iso_to_discord_ts(next_spawn, "R"), inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "update":
                payload: dict = {"guild_id": interaction.guild_id}

                # Build max_bounties_per_tier nested dict only with provided tiers
                tier_updates: dict[str, int] = {}
                if max_bronze is not None:
                    tier_updates["bronze"] = max_bronze
                if max_silver is not None:
                    tier_updates["silver"] = max_silver
                if max_gold is not None:
                    tier_updates["gold"] = max_gold
                if max_platinum is not None:
                    tier_updates["platinum"] = max_platinum
                if tier_updates:
                    payload["max_bounties_per_tier"] = tier_updates

                if expiry_minutes is not None:
                    payload["bounty_expiry_minutes"] = expiry_minutes
                if spawn_interval is not None:
                    payload["bounty_spawn_interval_minutes"] = spawn_interval

                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/bounty",
                    json=payload,
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()

                embed = discord.Embed(title="✅ Bounty Config Updated", color=discord.Color.green())
                max_per_tier = result.get("max_bounties_per_tier", {})
                if max_per_tier:
                    embed.add_field(
                        name="Max Per Tier",
                        value=(
                            f"Bronze: {max_per_tier.get('bronze', '?')} | "
                            f"Silver: {max_per_tier.get('silver', '?')} | "
                            f"Gold: {max_per_tier.get('gold', '?')} | "
                            f"Platinum: {max_per_tier.get('platinum', '?')}"
                        ),
                        inline=False,
                    )
                if result.get("bounty_expiry_minutes"):
                    embed.add_field(name="Expiry Time", value=f"{result['bounty_expiry_minutes']} minutes", inline=True)
                if result.get("bounty_spawn_interval_minutes"):
                    embed.add_field(
                        name="Spawn Interval",
                        value=f"{result['bounty_spawn_interval_minutes']} minutes",
                        inline=True,
                    )

                await interaction.followup.send(embed=embed, ephemeral=True)

            flogger.info(f"Admin {interaction.user} performed bounty config {action} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_bounty: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing bounty configuration.", ephemeral=True)

    @app_commands.command(name="admin_config_xp", description="[ADMIN] View or update XP tier thresholds")
    @app_commands.describe(
        action="View current thresholds or update them",
        silver="XP threshold for Silver tier",
        gold="XP threshold for Gold tier",
        platinum="XP threshold for Platinum tier",
        prestige="XP threshold required to /prestige (must exceed Platinum)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View", value="view"),
            app_commands.Choice(name="Update", value="update"),
        ]
    )
    async def admin_config_xp(
        self,
        interaction: discord.Interaction,
        action: str,
        silver: int | None = None,
        gold: int | None = None,
        platinum: int | None = None,
        prestige: int | None = None,
    ):
        """View or update XP tier thresholds for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        try:
            if action == "view":
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{interaction.guild_id}",
                    timeout=10,
                )
                resp.raise_for_status()
                config = resp.json()
                thresholds = config.get("xp_thresholds", {})

                silver_val = thresholds.get("Silver", "?")
                gold_val = thresholds.get("Gold", "?")
                platinum_val = thresholds.get("Platinum", "?")
                prestige_val = thresholds.get("Prestige", "(default)")

                def _fmt(label: str, val) -> str:
                    if isinstance(val, int):
                        return f"{label}: {val:,} XP"
                    return f"{label}: {val}"

                embed = discord.Embed(title="⚙️ XP Tier Thresholds", color=discord.Color.blue())
                embed.add_field(
                    name="Thresholds",
                    value="\n".join(
                        [
                            _fmt("Silver", silver_val),
                            _fmt("Gold", gold_val),
                            _fmt("Platinum", platinum_val),
                            _fmt("Prestige", prestige_val),
                        ]
                    ),
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "update":
                # Require all three core thresholds (Silver/Gold/Platinum). Prestige is optional.
                if silver is None or gold is None or platinum is None:
                    await interaction.followup.send(
                        "❌ Silver, gold, and platinum are required (prestige is optional).",
                        ephemeral=True,
                    )
                    return

                # Client-side pre-validation: ascending order
                if not silver < gold < platinum:
                    await interaction.followup.send(
                        "❌ Thresholds must be in strictly ascending order: silver < gold < platinum.", ephemeral=True
                    )
                    return

                if prestige is not None and prestige <= platinum:
                    await interaction.followup.send(
                        "❌ Prestige threshold must be greater than the platinum threshold.",
                        ephemeral=True,
                    )
                    return

                threshold_payload: dict[str, int] = {
                    "Silver": silver,
                    "Gold": gold,
                    "Platinum": platinum,
                }
                if prestige is not None:
                    threshold_payload["Prestige"] = prestige

                payload = {
                    "guild_id": interaction.guild_id,
                    "thresholds": threshold_payload,
                }
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/xp-thresholds",
                    json=payload,
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                updated = result.get("xp_thresholds", payload["thresholds"])

                lines = [
                    f"Silver: {updated.get('Silver', silver):,} XP",
                    f"Gold: {updated.get('Gold', gold):,} XP",
                    f"Platinum: {updated.get('Platinum', platinum):,} XP",
                ]
                prestige_after = updated.get("Prestige")
                if prestige_after is not None:
                    lines.append(f"Prestige: {prestige_after:,} XP")
                else:
                    lines.append("Prestige: (default — not set per-guild)")

                embed = discord.Embed(title="✅ XP Thresholds Updated", color=discord.Color.green())
                embed.add_field(name="New Thresholds", value="\n".join(lines), inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)

            flogger.info(f"Admin {interaction.user} performed XP config {action} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Validation error.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Validation error."
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_xp: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing XP thresholds.", ephemeral=True)

    @app_commands.command(name="admin_spawn_bounty", description="[ADMIN] Manually trigger a bounty spawn")
    @app_commands.describe(
        tier="Tier to spawn for (omit for all tiers)",
        quantity="Number of bounties to spawn per tier (1-10, default 1)",
    )
    @app_commands.choices(
        tier=[
            app_commands.Choice(name="Bronze", value="bronze"),
            app_commands.Choice(name="Silver", value="silver"),
            app_commands.Choice(name="Gold", value="gold"),
            app_commands.Choice(name="Platinum", value="platinum"),
        ]
    )
    async def admin_spawn_bounty(self, interaction: discord.Interaction, tier: str | None = None, quantity: int = 1):
        """Manually trigger a bounty spawn for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        if not 1 <= quantity <= 10:
            await interaction.followup.send("❌ Quantity must be between 1 and 10.", ephemeral=True)
            return

        try:
            params: dict = {"user_id": interaction.user.id, "quantity": quantity}
            if tier:
                params["tier"] = tier

            resp = await self.http_client.post(
                f"{api_base}/bounties/guild/{interaction.guild_id}/admin-spawn",
                params=params,
                timeout=60,
            )
            resp.raise_for_status()
            result = resp.json()

            spawned = result.get("spawned", [])
            skipped_tiers = result.get("skipped_tiers", [])
            errors = result.get("errors", [])

            embed = discord.Embed(title="🎯 Bounties Spawned", color=discord.Color.green())

            if spawned:
                lines = [
                    f"- {b['division'].title()}: {b['criminal_name']} "
                    f"(T{b.get('tech_level', '?')}, {b.get('reward', 0):,}cr)"
                    for b in spawned
                ]
                embed.add_field(name=f"Spawned ({len(spawned)})", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Spawned", value="No bounties spawned.", inline=False)

            if skipped_tiers:
                embed.add_field(name="Skipped Tiers", value=", ".join(skipped_tiers), inline=True)

            if errors:
                embed.add_field(name="Errors", value="\n".join(str(e) for e in errors), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} spawned bounties in guild {interaction.guild_id}: "
                f"{len(spawned)} spawned, {skipped_tiers} skipped"
            )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_spawn_bounty: {e}")
            await interaction.followup.send("⚠️ An error occurred while spawning bounties.", ephemeral=True)

    # ------------------------------------------------------------------
    # /admin_cooldown_reset <user>
    # ------------------------------------------------------------------

    @app_commands.command(
        name="admin_cooldown_reset",
        description="Reset a player's bounty check cooldown immediately",
    )
    @app_commands.describe(user="The Discord member whose cooldown should be reset")
    async def admin_cooldown_reset(self, interaction: discord.Interaction, user: discord.Member):
        """Reset a player's bounty cooldown."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        flogger.info(
            f"/admin_cooldown_reset invoked: guild={interaction.guild_id} admin={interaction.user.id} target={user.id}"
        )

        try:
            resp = await self.http_client.put(
                f"{api_base}/players/{interaction.guild_id}/{user.id}/cooldown/reset",
                timeout=10,
            )
            if resp.status_code == 404:
                await interaction.followup.send(
                    f"❌ Player not found for {user.mention}. They may not have played yet.",
                    ephemeral=True,
                )
                return
            resp.raise_for_status()
            data = resp.json()
            await interaction.followup.send(
                f"✅ {data.get('message', 'Cooldown reset successfully')} for {user.mention}.",
                ephemeral=True,
            )
            flogger.info(f"/admin_cooldown_reset success: guild={interaction.guild_id} target={user.id}")

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/admin_cooldown_reset API error: guild={interaction.guild_id} "
                f"target={user.id} status={e.response.status_code}"
            )
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/admin_cooldown_reset error: {e}")
            await interaction.followup.send("⚠️ An error occurred while resetting the cooldown.", ephemeral=True)

    @admin_cooldown_reset.error
    async def admin_cooldown_reset_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_cooldown_reset", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # Error handlers
    @admin_setup.error
    async def admin_setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need Administrator permissions to use this command.", ephemeral=True
            )
        else:
            flogger.exception("Error in /admin_setup", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @admin_player.error
    async def admin_player_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need Administrator permissions to use this command.", ephemeral=True
            )
        else:
            flogger.exception("Error in /admin_player", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # Admin inventory management commands
    # ------------------------------------------------------------------

    async def item_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for item names — served from preloaded in-memory cache (zero HTTP per keystroke).

        Used by /admin_give_item: shows the full game catalog since we're giving items the
        player may not own yet.  The item_type filter honours any item_type already filled
        in the command, but item_type is not a parameter on /admin_give_item so it is
        always None there (all categories shown).
        """
        # TODO: Phase 7/8 — preload the game catalog at bot startup instead of fetching per keystroke.
        # Admin commands are infrequent so this is low priority.
        try:
            # Determine the item type category from the already-filled item_type parameter.
            item_type = getattr(interaction.namespace, "item_type", None)

            categories = (
                (item_type,)
                if item_type in ("primary_weapon", "secondary_weapon", "turret_weapon", "module")
                else ("primary_weapon", "secondary_weapon", "turret_weapon", "module")
            )

            # Collect all candidate names across categories (deduplicated).
            # Budget: at most two inline 1.0s cold-fills (gold-standard ≤2s rule);
            # remaining categories peek-only. Catalog is normally pre-warmed.
            all_names: list[str] = []
            seen: set[str] = set()
            cold_fills = 0
            for category in categories:
                cat_names = self._item_catalog.peek(category)
                if cat_names is None and cold_fills < 2:
                    cat_names = await self._item_catalog.get_with_timeout(category, timeout=1.0)
                    cold_fills += 1
                cat_names = cat_names or []
                for name in cat_names:
                    if name and name not in seen:
                        seen.add(name)
                        all_names.append(name)

            return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, all_names)]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def remove_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /admin_remove_item item_name — shows the target user's inventory.

        Phase 6: Zero-HTTP on the hot path. Uses peek() on autocomplete_state caches.

        When ``interaction.namespace.user`` is already selected:
        1. Peek player_cache for target user → player_id (no HTTP).
        2. Peek inventory_cache for (guild_id, player_id) → items (no HTTP).
        3. Return choices filtered by ``current`` text, labelled "ItemName (Type) xN".

        Falls back to showing all equippable game-catalog items when:
        - No user is selected yet (namespace.user is None)
        - Player resolution cache misses (guild not configured, not yet warmed, etc.)
        - Inventory cache misses

        Degrades silently on any error (returns [] or catalog fallback) — autocomplete
        must never raise.
        """
        try:
            from utils import autocomplete_state

            norm_current = normalize_for_search(current)
            target_user = getattr(interaction.namespace, "user", None)

            if target_user is None:
                # No user selected yet — prompt the user to pick a user first
                return [app_commands.Choice(name="— Select a user first —", value="__select_user_first__")]

            guild_id = interaction.guild_id
            target_user_id = target_user.id

            # GATE 1 (cold-fill): resolve target player from player_cache so the 0th
            # keystroke is never empty for a cold-but-resolvable target.
            if autocomplete_state.player_cache is not None:
                player_entry = autocomplete_state.player_cache.peek((guild_id, target_user_id))
                if player_entry is None:
                    player_entry = await autocomplete_state.player_cache.get_with_timeout(
                        (guild_id, target_user_id), timeout=1.0
                    )
                if player_entry is not None:
                    player_id = player_entry.get("id")
                    if player_id is not None and autocomplete_state.inventory_cache is not None:
                        # GATE 2 (cold-fill): target player's inventory. Two 1.0s gates ≈ 2s.
                        items = autocomplete_state.inventory_cache.peek((guild_id, player_id))
                        if items is None:
                            items = await autocomplete_state.inventory_cache.get_with_timeout(
                                (guild_id, player_id), timeout=1.0
                            )
                        if items is not None:
                            choices: list[app_commands.Choice[str]] = []
                            seen: set[str] = set()
                            for nc in items:
                                raw = nc.raw if hasattr(nc, "raw") else nc
                                item_name = raw.get("item_name") or ""
                                item_type = raw.get("item_type") or ""
                                quantity = raw.get("quantity") or 0
                                if not item_name or item_name in seen:
                                    continue
                                qty_suffix = f" x{quantity}" if quantity and quantity > 1 else ""
                                type_label = item_type.replace("_", " ").title() or "Item"
                                label = f"{item_name} ({type_label}){qty_suffix}"
                                norm_label = normalize_for_search(label)
                                norm_name = normalize_for_search(item_name)
                                if norm_current in norm_label or norm_current in norm_name:
                                    seen.add(item_name)
                                    choices.append(app_commands.Choice(name=label[:100], value=item_name))
                            return choices[:25]

            flogger.warning(
                f"remove_item_autocomplete: could not resolve inventory for "
                f"user={getattr(target_user, 'id', None)} guild={interaction.guild_id}; "
                "falling back to all items"
            )

            # Fallback: show all game-catalog items across all equippable categories.
            # Budget: at most two inline 1.0s cold-fills (gold-standard ≤2s rule);
            # remaining categories peek-only (the cold-fills' shielded refresh warms
            # them for the next keystroke). Catalog is normally pre-warmed anyway.
            choices_fb: list[app_commands.Choice[str]] = []
            seen_fb: set[str] = set()
            cold_fills_fb = 0
            for category in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
                names = self._item_catalog.peek(category)
                if names is None and cold_fills_fb < 2:
                    names = await self._item_catalog.get_with_timeout(category, timeout=1.0)
                    cold_fills_fb += 1
                names = names or []
                for name in names:
                    if name and name not in seen_fb and norm_current in normalize_for_search(name):
                        seen_fb.add(name)
                        choices_fb.append(app_commands.Choice(name=name, value=name))
                if len(choices_fb) >= 25:
                    break
            return choices_fb[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def game_ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for ship names — served from preloaded in-memory cache (zero HTTP per keystroke).

        Cold cache (e.g. just after /reload_autocomplete) self-heals via a single
        bounded 1.0s cold-fill rather than an unbounded get() that could blow the
        Discord autocomplete budget.
        """
        try:
            names = self._ship_catalog.peek("all")
            if names is None:
                names = await self._ship_catalog.get_with_timeout("all", timeout=1.0)
            names = names or []
            return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, names)]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="admin_give_item", description="[ADMIN] Give an item directly to a player's inventory")
    @app_commands.describe(
        user="Target player",
        item_name="Item to give (autocomplete from game data)",
        quantity="Number of items to give (default: 1)",
    )
    @app_commands.autocomplete(item_name=item_name_autocomplete)
    async def admin_give_item(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        item_name: str,
        quantity: int = 1,
    ):
        """Give an item directly to a player's inventory (no credit cost).

        B.80: item_type parameter removed — the server resolves it from the item name.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            # B.80: send only player_id, item_name, quantity — let the server resolve item_type
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "item_name": item_name,
                "quantity": quantity,
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/give-item",
                json=payload,
                params={"admin_user_id": interaction.user.id},
                timeout=10,
            )
            if resp.status_code == 404:
                detail = resp.json().get("detail", "Player or item not found.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Invalid request.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            resp.raise_for_status()
            result = resp.json()

            # item_type is resolved server-side and returned in the response
            resolved_type = result.get("item_type", "")
            type_display = resolved_type.replace("_", " ").title() if resolved_type else "Unknown"

            embed = discord.Embed(
                title="✅ Item Given",
                description=result.get("message", "Item given successfully."),
                color=discord.Color.green(),
            )
            embed.add_field(name="Item", value=item_name, inline=True)
            embed.add_field(name="Type", value=type_display, inline=True)
            embed.add_field(name="Quantity", value=str(quantity), inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="New Total", value=str(result.get("new_total_quantity", "?")), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} gave {quantity}x {item_name} to {user} in guild {interaction.guild_id}"
            )

            # Invalidate target player's inventory cache — item was added
            target_player_id = result.get("player_id")
            if target_player_id is not None:
                try:
                    autocomplete_state.invalidate_inventory(interaction.guild_id, target_player_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_give_item: cache invalidation failed for player_id={target_player_id}; "
                        "transaction still succeeded"
                    )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_give_item: {e}")
            await interaction.followup.send("⚠️ An error occurred while giving item.", ephemeral=True)

    @admin_give_item.error
    async def admin_give_item_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_give_item", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="admin_remove_item", description="[ADMIN] Remove an item from a player's inventory")
    @app_commands.describe(
        user="Target player",
        item_name="Item to remove (autocomplete from player's inventory)",
        quantity="Number of items to remove (default: 1)",
    )
    @app_commands.autocomplete(item_name=remove_item_autocomplete)
    async def admin_remove_item(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        item_name: str,
        quantity: int = 1,
    ):
        """Remove an item from a player's inventory.

        item_type parameter removed — the server resolves it from the player's inventory
        by item_name (same pattern as B.80 /admin_give_item and A.42b /sell).
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if item_name == "__select_user_first__":
            await interaction.followup.send("❌ Please select a user first.", ephemeral=True)
            return
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            # item_type omitted — the server resolves it from the player's inventory
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "item_name": item_name,
                "quantity": quantity,
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/remove-item",
                json=payload,
                params={"admin_user_id": interaction.user.id},
                timeout=10,
            )
            if resp.status_code == 404:
                detail = resp.json().get("detail", "Player or item not found.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Invalid request.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            resp.raise_for_status()
            result = resp.json()

            # item_type resolved server-side and returned in the response
            resolved_type = result.get("item_type", "")
            type_display = resolved_type.replace("_", " ").title() if resolved_type else "Unknown"

            embed = discord.Embed(
                title="✅ Item Removed",
                description=result.get("message", "Item removed successfully."),
                color=discord.Color.orange(),
            )
            embed.add_field(name="Item", value=item_name, inline=True)
            embed.add_field(name="Type", value=type_display, inline=True)
            embed.add_field(name="Quantity Removed", value=str(quantity), inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="Remaining", value=str(result.get("new_quantity", 0)), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} removed {quantity}x {item_name} from {user} in guild {interaction.guild_id}"
            )

            # Invalidate target player's inventory cache — item was removed
            target_player_id = result.get("player_id")
            if target_player_id is not None:
                try:
                    autocomplete_state.invalidate_inventory(interaction.guild_id, target_player_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_remove_item: cache invalidation failed for player_id={target_player_id}; "
                        "transaction still succeeded"
                    )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_remove_item: {e}")
            await interaction.followup.send("⚠️ An error occurred while removing item.", ephemeral=True)

    @admin_remove_item.error
    async def admin_remove_item_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_remove_item", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="admin_give_ship", description="[ADMIN] Give a ship to a player")
    @app_commands.describe(
        user="Target player",
        ship_name="Name of the ship to give (autocomplete from game data)",
    )
    @app_commands.autocomplete(ship_name=game_ship_autocomplete)
    async def admin_give_ship(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        ship_name: str,
    ):
        """Give a ship to a player. Ship starts inactive with empty loadout."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "ship_name": ship_name,
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/give-ship",
                json=payload,
                params={"admin_user_id": interaction.user.id},
                timeout=10,
            )
            if resp.status_code == 404:
                detail = resp.json().get("detail", "Player or ship not found.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Invalid request.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="✅ Ship Given",
                description=result.get("message", "Ship given successfully."),
                color=discord.Color.green(),
            )
            embed.add_field(name="Ship", value=ship_name, inline=True)
            embed.add_field(name="Ship ID", value=str(result.get("ship_id", "?")), inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="Status", value="Inactive (empty loadout)", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} gave ship {ship_name} to {user} in guild {interaction.guild_id}")

            # Invalidate target player's ships and player caches — new ship acquired
            target_player_id = result.get("player_id")
            if target_player_id is not None:
                try:
                    autocomplete_state.invalidate_ships(interaction.guild_id, target_player_id)
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_give_ship: cache invalidation failed for player_id={target_player_id}; "
                        "transaction still succeeded"
                    )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_give_ship: {e}")
            await interaction.followup.send("⚠️ An error occurred while giving ship.", ephemeral=True)

    @admin_give_ship.error
    async def admin_give_ship_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_give_ship", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    async def player_ship_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for remove-ship — filters to the target player's ships when possible.

        If ``interaction.namespace.user`` is already populated (the user param was filled
        in before the ship_name field), resolves that user to a bot-core player and fetches
        only that player's owned ships via ``GET /api/v1/ships/player/{player_id}``.

        Falls back to showing all ships from game data when:
        - The user param has not been selected yet (namespace.user is None/absent)
        - Player resolution fails (e.g. guild not configured)
        - The player-ships API call fails

        Degrades silently on any error (returns []) — autocomplete must never raise.
        """
        try:
            norm_current = normalize_for_search(current)

            # Attempt to resolve the target user from the partially-filled command
            target_user = getattr(interaction.namespace, "user", None)
            if target_user is not None:
                from utils import autocomplete_state
                from utils.autocomplete_helpers import resolve_player_id

                # GATE 1 (cold-fill): resolve target user → bot-core player_id.
                player_id = await resolve_player_id(
                    self.http_client, api_base, target_user.id, interaction.guild_id, timeout=3.0
                )
                if player_id is not None:
                    # GATE 2 (cold-fill): REUSE the shared ships_cache (key = (guild, player_id))
                    # instead of a live GET per keystroke. ships_cache is already warmed for
                    # active players and invalidated by setactive/sell-ship/give-ship/admin-remove-ship.
                    sc = autocomplete_state.ships_cache
                    ships_nc = sc.peek((interaction.guild_id, player_id)) if sc else None
                    if ships_nc is None and sc is not None:
                        ships_nc = await sc.get_with_timeout((interaction.guild_id, player_id), timeout=1.0)
                    if ships_nc is not None:
                        choices: list[app_commands.Choice[str]] = []
                        for nc in ships_nc:
                            raw = nc.raw if hasattr(nc, "raw") else nc
                            ship_name = raw.get("ship_name") or raw.get("name") or ""
                            if ship_name and norm_current in normalize_for_search(ship_name):
                                choices.append(app_commands.Choice(name=ship_name, value=ship_name))
                        return choices[:25]
                # Player resolution failed, ships_cache miss for an un-warmed target, or guild
                # not configured — fall through to the game-data catalog fallback (intended
                # degrade path; not every guild member is warmed).
                flogger.warning(
                    f"player_ship_autocomplete: could not resolve player ships for "
                    f"user={getattr(target_user, 'id', None)} guild={interaction.guild_id}; "
                    "falling back to all ships"
                )

            # Fallback: show all ships from preloaded catalog (user param not yet selected, or
            # resolution failed). Bounded 1.0s cold-fill instead of unbounded get() (budget).
            names = self._ship_catalog.peek("all")
            if names is None:
                names = await self._ship_catalog.get_with_timeout("all", timeout=1.0)
            names = names or []
            return [
                app_commands.Choice(name=name, value=name)
                for name in names
                if norm_current in normalize_for_search(name)
            ][:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="admin_remove_ship", description="[ADMIN] Remove a ship from a player")
    @app_commands.describe(
        user="Target player",
        ship_name="Name of the ship to remove",
    )
    @app_commands.autocomplete(ship_name=player_ship_autocomplete)
    async def admin_remove_ship(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        ship_name: str,
    ):
        """Remove a ship from a player. Unequips all items first. Cannot remove only active ship."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return
        try:
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "ship_name": ship_name,
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/remove-ship",
                json=payload,
                params={"admin_user_id": interaction.user.id},
                timeout=10,
            )
            if resp.status_code == 404:
                detail = resp.json().get("detail", "Player or ship not found.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Invalid request.")
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                return
            resp.raise_for_status()
            result = resp.json()

            items_returned = result.get("items_returned_to_inventory", [])
            embed = discord.Embed(
                title="✅ Ship Removed",
                description=result.get("message", "Ship removed successfully."),
                color=discord.Color.orange(),
            )
            embed.add_field(name="Ship", value=ship_name, inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            if items_returned:
                embed.add_field(
                    name="Items Returned to Inventory",
                    value=", ".join(items_returned[:10]) + ("..." if len(items_returned) > 10 else ""),
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} removed ship {ship_name} from {user} in guild {interaction.guild_id}"
            )

            # Invalidate ships + inventory caches — ship removed, items returned to cargo
            target_player_id = result.get("player_id")
            if target_player_id is not None:
                try:
                    autocomplete_state.invalidate_ships(interaction.guild_id, target_player_id)
                    autocomplete_state.invalidate_inventory(interaction.guild_id, target_player_id)
                    autocomplete_state.invalidate_player(interaction.guild_id, user.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/admin_remove_ship: cache invalidation failed for player_id={target_player_id}; "
                        "transaction still succeeded"
                    )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_remove_ship: {e}")
            await interaction.followup.send("⚠️ An error occurred while removing ship.", ephemeral=True)

    @admin_remove_ship.error
    async def admin_remove_ship_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_remove_ship", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # B.49/B.50: /admin_config_constants — per-guild game-constant overrides
    # ------------------------------------------------------------------

    # All 25 per-guild game-constant override field names (must match _OVERRIDE_FIELDS in bot-core config router)
    _GAME_CONSTANT_FIELDS: tuple[str, ...] = (
        "division_max_tl",
        "ship_value_reward_percentage",
        "criminal_equip_damageless_weapon_chance",
        "criminal_max_gear_upgrade",
        "bounty_reward_to_xp_gain_mult",
        "bounty_winner_reserve_factor",
        # bounty_pvc_armour_buff_factor — retired T10 (dropped from guild_config)
        # duel_variance_percent — retired T10 (SimpleTTKResolver removed)
        "duel_cloak_chance",
        "close_bounty_threshold",
        "max_route_length",
        "bounty_delay_random_min",
        "bounty_delay_random_max",
        "bounty_spawn_jitter",
        "check_cooldown",
        "duel_request_expiry",
        "tier_change_cooldown",
        "guild_activity_decay_rate",
        "min_guild_activity",
        "activity_temp_per_player",
        "shop_default_ships_num",
        "shop_default_weapons_num",
        "shop_default_modules_num",
        "shop_default_turrets_num",
        "turret_spawn_probability",
        "kaamo_max_capacity",
        "classic_credits_per_check",
    )

    async def constants_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for game-constant field names."""
        current_lower = current.lower()
        return [app_commands.Choice(name=f, value=f) for f in self._GAME_CONSTANT_FIELDS if current_lower in f.lower()][
            :25
        ]

    @app_commands.command(
        name="admin_config_constants",
        description="[ADMIN] View or set a per-guild game-constant override (B.49)",
    )
    @app_commands.describe(
        setting="The game-constant field name (leave blank to list all)",
        int_value="Integer value to set",
        float_value="Float value to set",
        json_value="JSON value to set (for dict fields like division_max_tl)",
    )
    @app_commands.autocomplete(setting=constants_autocomplete)
    async def admin_config_constants(
        self,
        interaction: discord.Interaction,
        setting: str | None = None,
        int_value: int | None = None,
        float_value: float | None = None,
        json_value: str | None = None,
    ):
        """View or set a per-guild game-constant override."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        guild_id = interaction.guild_id

        # No setting specified → show all current overrides (compact view)
        if setting is None:
            try:
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{guild_id}/game-constants",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                lines = []
                for field in self._GAME_CONSTANT_FIELDS:
                    val = data.get(field)
                    display = f"`{val}`" if val is not None else "*default*"
                    lines.append(f"**{field}**: {display}")
                desc = "\n".join(lines) or "No overrides set."
                embed = discord.Embed(
                    title=f"Game Constant Overrides — Guild {guild_id}",
                    description=desc[:4096],
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            except httpx.HTTPStatusError as e:
                await report_api_error(interaction, e)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"admin_config_constants list error: {e}")
                await interaction.followup.send("⚠️ Failed to fetch game constants.", ephemeral=True)
            return

        # Validate setting name
        if setting not in self._GAME_CONSTANT_FIELDS:
            await interaction.followup.send(
                f"❌ Unknown setting `{setting}`. Use autocomplete to pick a valid field.",
                ephemeral=True,
            )
            return

        # No value provided → show current value of the specific setting
        if int_value is None and float_value is None and json_value is None:
            try:
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{guild_id}/game-constants",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                val = data.get(setting)
                display = f"`{val}`" if val is not None else "*using global default*"
                await interaction.followup.send(
                    f"**{setting}**: {display}",
                    ephemeral=True,
                )
            except httpx.HTTPStatusError as e:
                await report_api_error(interaction, e)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"admin_config_constants get error: {e}")
                await interaction.followup.send("⚠️ Failed to fetch game constant.", ephemeral=True)
            return

        # Determine new value
        import json as _json

        new_value = None
        if json_value is not None:
            try:
                new_value = _json.loads(json_value)
            except _json.JSONDecodeError as e:
                await interaction.followup.send(f"❌ Invalid JSON: {e}", ephemeral=True)
                return
        elif float_value is not None:
            new_value = float_value
        elif int_value is not None:
            new_value = int_value

        # PATCH the config
        try:
            resp = await self.http_client.put(
                f"{api_base}/config/guild/{guild_id}",
                json={"guild_id": guild_id, setting: new_value},
                timeout=10,
            )
            resp.raise_for_status()
            embed = discord.Embed(
                title="✅ Game Constant Updated",
                description=f"**{setting}** set to `{new_value}` for guild {guild_id}.",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} set game constant {setting}={new_value!r} in guild {guild_id}")
        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"admin_config_constants set error: {e}")
            await interaction.followup.send("⚠️ Failed to update game constant.", ephemeral=True)

    @admin_config_constants.error
    async def admin_config_constants_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_config_constants", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(
        name="admin_config_constants_view",
        description="[ADMIN] Compact read-only view of all per-guild game-constant overrides (B.49)",
    )
    async def admin_config_constants_view(self, interaction: discord.Interaction):
        """Read-only compact view of all per-guild game-constant overrides."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        try:
            resp = await self.http_client.get(
                f"{api_base}/config/guild/{guild_id}/game-constants",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            set_fields = [(f, data[f]) for f in self._GAME_CONSTANT_FIELDS if data.get(f) is not None]
            if not set_fields:
                await interaction.followup.send(
                    "ℹ️ No per-guild overrides set — all constants use global defaults.",
                    ephemeral=True,
                )
                return

            lines = [f"**{f}**: `{v}`" for f, v in set_fields]
            embed = discord.Embed(
                title=f"Active Game Constant Overrides — Guild {guild_id}",
                description="\n".join(lines)[:4096],
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"admin_config_constants_view error: {e}")
            await interaction.followup.send("⚠️ Failed to fetch game constants.", ephemeral=True)

    @admin_config_constants_view.error
    async def admin_config_constants_view_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        flogger.exception("Error in /admin_config_constants_view", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(
        name="admin_config_constants_reset",
        description="[ADMIN] Reset per-guild game-constant overrides to global defaults (B.49/B.50)",
    )
    @app_commands.describe(
        setting="Specific field to reset (leave blank to reset ALL 25 overrides)",
    )
    @app_commands.autocomplete(setting=constants_autocomplete)
    async def admin_config_constants_reset(
        self,
        interaction: discord.Interaction,
        setting: str | None = None,
    ):
        """Reset per-guild game-constant overrides with button confirmation (B.50)."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        guild_id = interaction.guild_id

        if setting is not None and setting not in self._GAME_CONSTANT_FIELDS:
            await interaction.followup.send(
                f"❌ Unknown setting `{setting}`. Use autocomplete to pick a valid field.",
                ephemeral=True,
            )
            return

        action_desc = f"reset override for **{setting}**" if setting else "reset **all 25** game-constant overrides"

        view = ConfirmView(action=f"{action_desc} for guild {guild_id}", timeout=60)
        warning_embed = discord.Embed(
            title="⚠️ Confirm Reset",
            description=(
                f"You are about to {action_desc} to global defaults.\n"
                "This cannot be undone. Click **Confirm** to proceed."
            ),
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=warning_embed, view=view, ephemeral=True)
        await view.wait()

        if view.result is None:
            await interaction.followup.send("⏱️ Reset timed out — no changes made.", ephemeral=True)
            return

        if not view.result:
            await interaction.followup.send("❌ Reset cancelled — no changes made.", ephemeral=True)
            return

        # Confirmed — call the reset endpoint
        try:
            body = {"fields": [setting] if setting else None}
            resp = await self.http_client.post(
                f"{api_base}/config/guild/{guild_id}/game-constants/reset",
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            embed = discord.Embed(
                title="✅ Game Constants Reset",
                description=f"Successfully {action_desc} for guild {guild_id}.",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} reset game constants (setting={setting!r}) in guild {guild_id}")
        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"admin_config_constants_reset error: {e}")
            await interaction.followup.send("⚠️ Failed to reset game constants.", ephemeral=True)

    @admin_config_constants_reset.error
    async def admin_config_constants_reset_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        flogger.exception("Error in /admin_config_constants_reset", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /admin_duel — admin duel management (B.65 + touch-up)
    # ------------------------------------------------------------------

    async def admin_duel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /admin_duel duel parameter.

        First choice is always "⚠️ Cancel ALL pending duels" (value="all") —
        including the error path, so the admin can always cancel-all even if
        the per-duel list can't be built.
        """
        # Sentinel is ALWAYS first, even on a cold/empty cache OR an exception.
        # Built BEFORE the try so the except handler can return it unconditionally.
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name="⚠️ Cancel ALL pending duels", value="all"),
        ]
        try:
            guild_id = interaction.guild_id

            # Guild-scoped cache: peek → single 1.0s cold-fill (within budget).
            duels = self._admin_pending_duel_cache.peek(guild_id)
            if duels is None:
                duels = await self._admin_pending_duel_cache.get_with_timeout(guild_id, timeout=1.0)
            if duels is None:
                return choices

            norm_current = normalize_for_search(current)
            for d in duels[:24]:  # max 24 duels + 1 "all" = 25 total (Discord limit)
                duel_id = d.get("id")
                challenger = d.get("challenger_name") or f"Player {d.get('challenger_id', '?')}"
                target = d.get("target_name") or f"Player {d.get('target_id', '?')}"
                stakes = d.get("stakes", 0)
                if stakes:
                    label = f"{challenger} vs {target} — {stakes:,} credits"
                else:
                    label = f"{challenger} vs {target} — friendly duel"
                norm_label = d.get("_norm") or normalize_for_search(label)
                if norm_current and norm_current not in norm_label:
                    continue
                # Discord choice names are max 100 chars
                if len(label) > 100:
                    label = label[:97] + "..."
                choices.append(app_commands.Choice(name=label, value=str(duel_id)))

            return choices
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.debug(f"admin_duel_autocomplete error: {e}")
            # Preserve the Cancel-ALL sentinel even on error.
            return choices

    @app_commands.command(name="admin_duel", description="[ADMIN] Cancel a pending duel or all pending duels")
    @app_commands.describe(duel="Select a pending duel to cancel, or 'All' to cancel everything")
    @app_commands.autocomplete(duel=admin_duel_autocomplete)
    async def admin_duel(
        self,
        interaction: discord.Interaction,
        duel: str,
    ):
        """Admin duel management: cancel any pending duel (or all of them)."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        if duel == "all":
            # Cancel ALL pending duels for this guild
            try:
                resp = await self.http_client.post(
                    f"{api_base}/duels/admin-cancel-all",
                    params={
                        "guild_id": interaction.guild_id,
                        "admin_user_id": interaction.user.id,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                count = data.get("cancelled_count", 0)
                if count == 0:
                    await interaction.followup.send("✅ No pending duels to cancel.", ephemeral=True)
                else:
                    embed = discord.Embed(
                        title="✅ All Duels Cancelled",
                        description=f"Cancelled **{count}** pending duel(s).",
                        color=discord.Color.orange(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                flogger.info(
                    f"Admin {interaction.user} cancelled all {count} pending duels in guild {interaction.guild_id}"
                )
                # Invalidate the guild-scoped admin-duel cache (cold-fill on next keystroke).
                self._admin_pending_duel_cache.invalidate(interaction.guild_id)
            except httpx.HTTPStatusError as e:
                await report_api_error(interaction, e)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"Error in /admin_duel cancel-all: {e}")
                await interaction.followup.send("⚠️ An error occurred while cancelling all duels.", ephemeral=True)
        else:
            # Cancel a specific duel by ID
            try:
                duel_id = int(duel)
            except ValueError:
                await interaction.followup.send("❌ Invalid duel selection.", ephemeral=True)
                return

            try:
                resp = await self.http_client.post(
                    f"{api_base}/duels/{duel_id}/admin-cancel",
                    params={"admin_user_id": interaction.user.id},
                    timeout=10,
                )
                if resp.status_code == 404:
                    detail = resp.json().get("detail", "Duel not found.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                if resp.status_code == 400:
                    detail = resp.json().get("detail", "Invalid request.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                resp.raise_for_status()

                embed = discord.Embed(
                    title="✅ Duel Cancelled",
                    description=f"Duel **#{duel_id}** has been cancelled by admin.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                flogger.info(f"Admin {interaction.user} cancelled duel_id={duel_id} in guild {interaction.guild_id}")
                # Invalidate the guild-scoped admin-duel cache (cold-fill on next keystroke).
                self._admin_pending_duel_cache.invalidate(interaction.guild_id)

            except httpx.HTTPStatusError as e:
                await report_api_error(interaction, e)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"Error in /admin_duel cancel: {e}")
                await interaction.followup.send("⚠️ An error occurred while cancelling the duel.", ephemeral=True)

    @admin_duel.error
    async def admin_duel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_duel", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up AdminCog...")
    await bot.add_cog(AdminCog(bot))
    flogger.info("AdminCog loaded")
