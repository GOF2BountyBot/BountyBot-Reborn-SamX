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

# The 7 deprecated JSONB dict fields — still accept json_value for legacy JSON input.
# Prefer the scalar *_bronze / *_silver / *_gold / *_platinum counterparts for all new usage.
_DEPRECATED_DICT_FIELDS: frozenset[str] = frozenset(
    {
        "division_max_tl",
        "bounty_division_reward_mult",
        "primary_tl_band_weights",
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
    }
)

# Fields that cannot be reset via POST /game-constants/reset — they are top-level guild
# config scalars with no game-constant default (use /admin_config action:Set to change them).
_NON_RESETTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "starting_credits",
        "sale_price_factor",
    }
)


class ConfigPageView(discord.ui.View):
    """Paginated category browser for /admin_config action:View only_overridden:False."""

    def __init__(
        self,
        categories: list[str],
        pages: dict[str, list[tuple[str, str, str, bool]]],
        *,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.categories = categories
        self.pages = pages
        self.current_idx = 0
        self._sync_buttons()
        if len(categories) > 1:
            self._rebuild_select()

    def _sync_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if getattr(item, "custom_id", None) == "cfg_prev":
                    item.disabled = self.current_idx == 0
                elif getattr(item, "custom_id", None) == "cfg_next":
                    item.disabled = self.current_idx >= len(self.categories) - 1

    def _rebuild_select(self) -> None:
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        options = [
            discord.SelectOption(label=cat[:100], value=str(i), default=(i == self.current_idx))
            for i, cat in enumerate(self.categories[:25])
        ]
        sel = discord.ui.Select(placeholder="Jump to category…", options=options, custom_id="cfg_select")
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.current_idx = int(interaction.data["values"][0])
        self._sync_buttons()
        if len(self.categories) > 1:
            self._rebuild_select()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def build_embed(self) -> discord.Embed:
        cat = self.categories[self.current_idx]
        rows = self.pages.get(cat, [])
        n = len(self.categories)
        idx = self.current_idx + 1
        lines: list[str] = []
        for field, current, default, is_overridden in rows:
            if is_overridden:
                lines.append(f"**{field}**: `{current}` *(default: {default})*")
            else:
                lines.append(f"{field}: {default}")
        desc = "\n".join(lines) or "No settings in this category."
        if len(desc) > 4000:
            desc = desc[:3997] + "..."
        return discord.Embed(
            title=f"⚙️ Guild Settings — {cat} ({idx}/{n})",
            description=desc,
            color=discord.Color.blue(),
        )

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="cfg_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_idx = max(0, self.current_idx - 1)
        self._sync_buttons()
        if len(self.categories) > 1:
            self._rebuild_select()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="cfg_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_idx = min(len(self.categories) - 1, self.current_idx + 1)
        self._sync_buttons()
        if len(self.categories) > 1:
            self._rebuild_select()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


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
        # Config metadata cache — preloaded from GET /config/metadata.
        # Powers setting autocomplete (97 fields: 95 game-constant + starting_credits +
        # sale_price_factor), help text, and local bounds pre-check before any API call.
        # NOTE: starting_credits and sale_price_factor are metadata-only additions and
        # do NOT appear in _GAME_CONSTANT_FIELDS (the static fallback list, 117 fields).
        # Falls back to _GAME_CONSTANT_FIELDS when the metadata endpoint is unavailable.
        self._config_metadata: list[dict] = []
        self._config_metadata_by_field: dict[str, dict] = {}
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

    async def _fetch_config_metadata(self) -> list[dict]:
        """Fetch per-field metadata from bot-core GET /config/metadata.

        Returns a list of field descriptors:
        {field, type, ge, le, default, description, category, deprecated, replaced_by}.
        Raises on HTTP error; caller handles retry / graceful fallback.
        """
        resp = await self.http_client.get(f"{api_base}/config/metadata", timeout=10)
        resp.raise_for_status()
        return resp.json()

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

        # Preload config metadata (97 fields: 95 game-constant + starting_credits + sale_price_factor).
        for attempt in range(5):
            try:
                metadata = await self._fetch_config_metadata()
                self._config_metadata = metadata
                self._config_metadata_by_field = {m["field"]: m for m in metadata}
                flogger.info(f"_preload_static_catalogs: loaded {len(metadata)} config metadata entries")
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                wait = min(5 * (2**attempt), 60)
                flogger.warning(
                    f"_preload_static_catalogs: failed for config metadata "
                    f"(attempt {attempt + 1}/5): {type(exc).__name__}: {exc}, retrying in {wait}s"
                )
                await asyncio.sleep(wait)
        else:
            flogger.error(
                "_preload_static_catalogs: terminal failure for config metadata after 5 attempts; "
                "setting autocomplete will fall back to _GAME_CONSTANT_FIELDS (117 fields)"
            )

    async def _fetch_admin_pending_duels(self, guild_id: int) -> list[dict]:
        """Refresh the guild-wide pending-duel list for /admin_duel. Cache refresh_fn.

        Pre-computes ``_norm`` at fill time so the hot autocomplete path performs
        only a substring check per keystroke.
        """
        resp = await self.http_client.get(f"{api_base}/duels/pending-all", params={"guild_id": guild_id}, timeout=3.0)
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
        force_tech_level="Force all items to specific tech level (1-10)",
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
            if force_tech_level is not None and not (1 <= force_tech_level <= 10):
                await interaction.followup.send("❌ Tech level must be between 1 and 10.", ephemeral=True)
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

    # ------------------------------------------------------------------
    # /admin_config — unified guild-settings command (issue #70 Option A)
    # ------------------------------------------------------------------

    async def setting_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for the ``setting`` param of /admin_config.

        When metadata is loaded (97 fields: 95 game-constant + starting_credits +
        sale_price_factor), uses the live list with deprecated fields sorted last and
        a "(deprecated)" suffix in the choice *name* (value stays the bare field name).

        Falls back to _GAME_CONSTANT_FIELDS (117 fields — excludes starting_credits /
        sale_price_factor which are metadata-only) when the metadata endpoint is offline.
        """
        current_lower = current.lower()
        if self._config_metadata:
            active = [m for m in self._config_metadata if not m.get("deprecated")]
            deprecated = [m for m in self._config_metadata if m.get("deprecated")]
            ordered = active + deprecated
            choices: list[app_commands.Choice[str]] = []
            for m in ordered:
                field = m["field"]
                if current_lower not in field.lower():
                    continue
                name = f"{field} (deprecated)" if m.get("deprecated") else field
                choices.append(app_commands.Choice(name=name[:100], value=field))
                if len(choices) == 25:
                    break
            return choices
        # Fallback to static list (no metadata — 117 fields, no starting_credits/sale_price_factor)
        return [app_commands.Choice(name=f, value=f) for f in self._GAME_CONSTANT_FIELDS if current_lower in f.lower()][
            :25
        ]

    @app_commands.command(name="admin_config", description="[ADMIN] View, set, or reset any per-guild setting")
    @app_commands.describe(
        action="What to do",
        setting="The setting to act on (autocomplete; leave blank for view/validate/help-overview)",
        int_value="New value (integer settings)",
        float_value="New value (decimal settings)",
        bool_value="New value (on/off settings, e.g. criminal_exclude_emp_weapons)",
        text_value="New value (text/enum settings)",
        json_value="Advanced: raw JSON for the 7 legacy dict settings (prefer the *_bronze/... scalars)",
        only_overridden="View: show only settings overridden from default (default: True)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View settings", value="view"),
            app_commands.Choice(name="Set a value", value="set"),
            app_commands.Choice(name="Help for a setting", value="help"),
            app_commands.Choice(name="Reset to default", value="reset"),
            app_commands.Choice(name="Validate config", value="validate"),
        ]
    )
    @app_commands.autocomplete(setting=setting_autocomplete)
    async def admin_config(
        self,
        interaction: discord.Interaction,
        action: str,
        setting: str | None = None,
        int_value: int | None = None,
        float_value: float | None = None,
        bool_value: bool | None = None,
        text_value: str | None = None,
        json_value: str | None = None,
        only_overridden: bool = True,
    ):
        """View, set, help, reset, or validate per-guild config settings."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        guild_id = interaction.guild_id

        try:
            if action == "validate":
                await self._admin_config_do_validate(interaction, guild_id)

            elif action == "view":
                await self._admin_config_do_view(interaction, guild_id, only_overridden=only_overridden)

            elif action == "help":
                await self._admin_config_do_help(interaction, guild_id, setting=setting)

            elif action == "set":
                if setting is None:
                    await interaction.followup.send(
                        "❌ `setting` is required for `action:Set`. Use autocomplete to pick a field.",
                        ephemeral=True,
                    )
                    return
                await self._admin_config_do_set(
                    interaction,
                    guild_id,
                    setting=setting,
                    int_value=int_value,
                    float_value=float_value,
                    bool_value=bool_value,
                    text_value=text_value,
                    json_value=json_value,
                )

            elif action == "reset":
                await self._admin_config_do_reset(interaction, guild_id, setting=setting)

            flogger.info(f"Admin {interaction.user} /admin_config action={action} setting={setting!r} guild={guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config action={action}: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing configuration.", ephemeral=True)

    # ---------- action helpers ----------

    async def _admin_config_do_validate(self, interaction: discord.Interaction, guild_id: int) -> None:
        """Render the validate endpoint result (replaces /admin_config_validate)."""
        resp = await self.http_client.get(
            f"{api_base}/config/guild/{guild_id}/validate",
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()

        valid = result.get("valid", False)
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])

        embed = discord.Embed(
            title="✅ Configuration Valid" if valid else "❌ Configuration Invalid",
            description=f"Validation results for guild **{interaction.guild.name}**",
            color=discord.Color.green() if valid else discord.Color.red(),
        )
        embed.add_field(
            name="❌ Errors",
            value="\n".join(f"• {e}" for e in errors) or "None",
            inline=False,
        )
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(f"• {w}" for w in warnings) or "None",
            inline=False,
        )
        embed.set_footer(text=f"Guild ID: {result.get('guild_id', guild_id)}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        flogger.debug(f"Admin {interaction.user} validated config for guild={guild_id}")

    async def _admin_config_do_view(
        self, interaction: discord.Interaction, guild_id: int, *, only_overridden: bool
    ) -> None:
        """Show guild settings — compact overrides view or category-paginated full browse."""
        resp = await self.http_client.get(
            f"{api_base}/config/guild/{guild_id}/game-constants",
            timeout=10,
        )
        resp.raise_for_status()
        gc_data = resp.json()

        if only_overridden:
            # Compact view: fields that differ from global default
            meta_by = self._config_metadata_by_field
            all_fields = (
                [m["field"] for m in self._config_metadata]
                if self._config_metadata
                else list(self._GAME_CONSTANT_FIELDS)
            )
            lines: list[str] = []
            for field in all_fields:
                val = gc_data.get(field)
                if val is None:
                    continue
                meta = meta_by.get(field, {})
                default = meta.get("default", "?")
                lines.append(f"**{field}**: `{val}` *(default: {default})*")

            if not lines:
                embed = discord.Embed(
                    title="⚙️ Guild Settings — Overrides",
                    description=(
                        "No game-constant overrides set — all settings use global defaults.\n\n"
                        "Use `/admin_config action:View only_overridden:False` to browse all settings by category."
                    ),
                    color=discord.Color.blue(),
                )
            else:
                n_total = len(all_fields)
                desc = "\n".join(lines)
                if len(desc) > 4096:
                    desc = desc[:4093] + "..."
                embed = discord.Embed(
                    title=f"⚙️ Guild Settings — Overrides ({len(lines)} of {n_total} overridden)",
                    description=desc,
                    color=discord.Color.blue(),
                )
                embed.set_footer(text="Use /admin_config action:View only_overridden:False to browse all by category.")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # Category-paginated full browse
            if self._config_metadata:
                cats_order: list[str] = []
                cats_fields: dict[str, list[dict]] = {}
                for meta in self._config_metadata:
                    cat = meta.get("category", "Other")
                    if cat not in cats_fields:
                        cats_fields[cat] = []
                        cats_order.append(cat)
                    cats_fields[cat].append(meta)
            else:
                cats_order = ["All Settings"]
                cats_fields = {"All Settings": [{"field": f} for f in self._GAME_CONSTANT_FIELDS]}

            pages: dict[str, list[tuple[str, str, str, bool]]] = {}
            for cat in cats_order:
                rows: list[tuple[str, str, str, bool]] = []
                for meta in cats_fields[cat]:
                    field = meta["field"]
                    val = gc_data.get(field)
                    default = str(meta.get("default", "—"))
                    is_overridden = val is not None
                    current = str(val) if is_overridden else "—"
                    rows.append((field, current, default, is_overridden))
                pages[cat] = rows

            if not cats_order:
                await interaction.followup.send("⚠️ No settings available.", ephemeral=True)
                return

            view = ConfigPageView(cats_order, pages)
            await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

    async def _admin_config_do_help(
        self, interaction: discord.Interaction, guild_id: int, *, setting: str | None
    ) -> None:
        """Show help for a specific setting or a usage overview with category list."""
        if setting is None:
            # Overview: usage summary + category list
            if self._config_metadata:
                seen_cats: list[str] = []
                for m in self._config_metadata:
                    c = m.get("category", "Other")
                    if c not in seen_cats:
                        seen_cats.append(c)
                cat_list = "\n".join(f"• {c}" for c in seen_cats)
            else:
                cat_list = "• (metadata not loaded — restart bot to retry)"

            embed = discord.Embed(
                title="❔ Admin Config Help",
                description=(
                    "**Usage:**\n"
                    "`/admin_config action:View` — show overridden settings\n"
                    "`/admin_config action:View only_overridden:False` — browse all categories\n"
                    "`/admin_config action:Set setting:<name> <typed_value>` — update a setting\n"
                    "`/admin_config action:Help setting:<name>` — detailed help for one setting\n"
                    "`/admin_config action:Reset setting:<name>` — reset one to default\n"
                    "`/admin_config action:Reset` — reset all overrides (confirmation required)\n"
                    "`/admin_config action:Validate` — check config for issues\n\n"
                    "**Categories:**\n" + cat_list
                ),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Per-setting help
        meta = self._config_metadata_by_field.get(setting)

        # Fetch current value (best-effort — don't fail help on API error)
        current_val = None
        try:
            if setting in _NON_RESETTABLE_FIELDS:
                cfg_resp = await self.http_client.get(f"{api_base}/config/guild/{guild_id}", timeout=10)
                cfg_resp.raise_for_status()
                current_val = cfg_resp.json().get(setting)
            else:
                gc_resp = await self.http_client.get(f"{api_base}/config/guild/{guild_id}/game-constants", timeout=10)
                gc_resp.raise_for_status()
                current_val = gc_resp.json().get(setting)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        if meta:
            field_type = meta.get("type", "unknown")
            ge = meta.get("ge")
            le = meta.get("le")
            default = meta.get("default")
            description = meta.get("description", "No description available.")
            category = meta.get("category", "Other")
            deprecated = meta.get("deprecated", False)
            replaced_by = meta.get("replaced_by")

            if ge is not None and le is not None:
                range_str = f"{ge} – {le}"
            elif ge is not None:
                range_str = f"≥ {ge}"
            elif le is not None:
                range_str = f"≤ {le}"
            else:
                range_str = "—"

            is_overridden = current_val is not None
            current_display = f"`{current_val}` *(overridden)*" if is_overridden else f"`{default}` *(global default)*"

            title = f"❔ {setting}"
            if deprecated:
                title += " ⚠️ deprecated"

            embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
            embed.add_field(name="Type", value=field_type, inline=True)
            embed.add_field(name="Range", value=range_str, inline=True)
            embed.add_field(name="Default", value=str(default) if default is not None else "—", inline=True)
            embed.add_field(name="Current", value=current_display, inline=True)
            embed.add_field(name="Category", value=category, inline=True)

            if deprecated:
                dep_msg = "⚠️ Deprecated. Prefer the scalar fields instead."
                if replaced_by:
                    dep_msg += f"\nReplaced by: `{replaced_by}`"
                embed.add_field(name="Deprecation", value=dep_msg, inline=False)

            type_param = {
                "int": "int_value",
                "float": "float_value",
                "bool": "bool_value",
                "str": "text_value",
                "dict": "json_value",
            }.get(field_type, "int_value")
            embed.add_field(
                name="Set with",
                value=f"`/admin_config action:Set setting:{setting} {type_param}:<value>`",
                inline=False,
            )
        else:
            current_display = f"`{current_val}`" if current_val is not None else "*(global default)*"
            embed = discord.Embed(
                title=f"❔ {setting}",
                description=(
                    f"**Current value:** {current_display}\n\n"
                    "*Detailed metadata unavailable — metadata endpoint may be offline.*"
                ),
                color=discord.Color.blurple(),
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _admin_config_do_set(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        *,
        setting: str,
        int_value: int | None,
        float_value: float | None,
        bool_value: bool | None,
        text_value: str | None,
        json_value: str | None,
    ) -> None:
        """Set a per-guild setting to a new value with local validation + forwarding to bot-core."""
        import json as _json

        # Validate setting name
        valid_fields = (
            {m["field"] for m in self._config_metadata} if self._config_metadata else set(self._GAME_CONSTANT_FIELDS)
        )
        if setting not in valid_fields:
            await interaction.followup.send(
                f"❌ Unknown setting `{setting}`. Use autocomplete to pick a valid field.",
                ephemeral=True,
            )
            return

        # Exactly one typed param must be provided
        provided = {
            k: v
            for k, v in {
                "int_value": int_value,
                "float_value": float_value,
                "bool_value": bool_value,
                "text_value": text_value,
                "json_value": json_value,
            }.items()
            if v is not None
        }
        if not provided:
            await interaction.followup.send(
                "❌ Provide exactly one value parameter: `int_value`, `float_value`, `bool_value`, "
                "`text_value`, or `json_value` (dict fields only).",
                ephemeral=True,
            )
            return
        if len(provided) > 1:
            keys = ", ".join(f"`{k}`" for k in provided)
            await interaction.followup.send(
                f"❌ Provide only one value parameter at a time. You provided: {keys}.",
                ephemeral=True,
            )
            return

        param_name = next(iter(provided))
        meta = self._config_metadata_by_field.get(setting)
        field_type = meta.get("type") if meta else None

        # json_value only accepted for dict-type (deprecated JSONB) fields
        if param_name == "json_value":
            is_dict_field = (field_type == "dict") if meta else (setting in _DEPRECATED_DICT_FIELDS)
            if not is_dict_field:
                type_hint = field_type or "unknown"
                param_hint = {
                    "int": "int_value",
                    "float": "float_value",
                    "bool": "bool_value",
                    "str": "text_value",
                }.get(type_hint, "the appropriate typed param")
                await interaction.followup.send(
                    f"❌ `json_value` is only accepted for the 7 legacy dict settings. "
                    f"`{setting}` is a `{type_hint}` field — use `{param_hint}` instead.",
                    ephemeral=True,
                )
                return
            try:
                new_value = _json.loads(json_value)  # type: ignore[arg-type]
            except _json.JSONDecodeError as exc:
                await interaction.followup.send(f"❌ Invalid JSON: {exc}", ephemeral=True)
                return
        elif param_name == "bool_value":
            new_value = bool_value
        elif param_name == "float_value":
            new_value = float_value
        elif param_name == "int_value":
            new_value = int_value
        else:  # text_value
            new_value = text_value

        # Type-param mismatch check (metadata-driven; int→float widening is allowed)
        if meta and field_type and param_name != "json_value":
            type_param_map = {
                "int": "int_value",
                "float": "float_value",
                "bool": "bool_value",
                "str": "text_value",
            }
            expected = type_param_map.get(field_type)
            # int→float widening is acceptable per bot-core strict model
            _widening_ok = field_type == "float" and param_name == "int_value"
            if expected and param_name != expected and not _widening_ok:
                type_label = {
                    "int": "an integer",
                    "float": "a decimal",
                    "bool": "an on/off",
                    "str": "a text",
                }.get(field_type, field_type)
                await interaction.followup.send(
                    f"❌ `{setting}` is {type_label} setting — use `{expected}`, not `{param_name}`.",
                    ephemeral=True,
                )
                return

        # Local bounds pre-check (numeric only; saves a round-trip on obvious range violations)
        if meta and isinstance(new_value, (int, float)):
            ge = meta.get("ge")
            le = meta.get("le")
            if ge is not None and new_value < ge:
                await interaction.followup.send(
                    f"❌ `{setting}` must be between {ge} and {le}. You gave {new_value}.",
                    ephemeral=True,
                )
                return
            if le is not None and new_value > le:
                ge_str = str(ge) if ge is not None else "—"
                await interaction.followup.send(
                    f"❌ `{setting}` must be between {ge_str} and {le}. You gave {new_value}.",
                    ephemeral=True,
                )
                return

        # Fetch old value for the success embed (best-effort, non-blocking)
        old_value = None
        try:
            if setting in _NON_RESETTABLE_FIELDS:
                old_resp = await self.http_client.get(f"{api_base}/config/guild/{guild_id}", timeout=10)
                old_resp.raise_for_status()
                old_value = old_resp.json().get(setting)
            else:
                old_resp = await self.http_client.get(f"{api_base}/config/guild/{guild_id}/game-constants", timeout=10)
                old_resp.raise_for_status()
                old_value = old_resp.json().get(setting)
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Embed shows None → new_value which is acceptable

        # PUT the new value via the general config endpoint (accepts all 97 fields)
        put_resp = await self.http_client.put(
            f"{api_base}/config/guild/{guild_id}",
            json={"guild_id": guild_id, setting: new_value},
            timeout=10,
        )
        put_resp.raise_for_status()

        # Success embed
        description_text = meta.get("description", "") if meta else ""
        range_text = ""
        if meta:
            ge = meta.get("ge")
            le = meta.get("le")
            if ge is not None and le is not None:
                range_text = f" (range {ge}–{le})"

        embed = discord.Embed(title="✅ Setting updated", color=discord.Color.green())
        embed.add_field(name=setting, value=f"`{old_value}` → `{new_value}`", inline=False)
        if description_text:
            embed.add_field(name="Description", value=description_text + range_text, inline=False)
        elif range_text:
            embed.add_field(name="Range", value=range_text.strip(" ()"), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
        flogger.info(f"Admin {interaction.user} set {setting}={new_value!r} (was {old_value!r}) in guild={guild_id}")

    async def _admin_config_do_reset(
        self, interaction: discord.Interaction, guild_id: int, *, setting: str | None
    ) -> None:
        """Reset one field or all game-constant overrides, with confirmation flow."""
        # starting_credits / sale_price_factor cannot be reset via game-constants endpoint
        if setting is not None and setting in _NON_RESETTABLE_FIELDS:
            await interaction.followup.send(
                f"❌ `{setting}` has no game-constant default and cannot be reset automatically. "
                f"Use `/admin_config action:Set setting:{setting}` to set it to the value you want.",
                ephemeral=True,
            )
            return

        # Validate setting name if one was provided
        if setting is not None:
            valid_fields = (
                {m["field"] for m in self._config_metadata}
                if self._config_metadata
                else set(self._GAME_CONSTANT_FIELDS)
            )
            if setting not in valid_fields:
                await interaction.followup.send(
                    f"❌ Unknown setting `{setting}`. Use autocomplete to pick a valid field.",
                    ephemeral=True,
                )
                return

        action_desc = (
            f"reset override for **{setting}**" if setting else "reset **all per-guild game-constant overrides**"
        )
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
        flogger.info(f"Admin {interaction.user} reset game constants setting={setting!r} in guild={guild_id}")

    @admin_config.error
    async def admin_config_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_config", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

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
        ship_qty_min="Min units per ship in shop (quantity_ranges.ships.min)",
        ship_qty_max="Max units per ship in shop (quantity_ranges.ships.max)",
        weapon_qty_min="Min units per weapon in shop (quantity_ranges.weapons.min)",
        weapon_qty_max="Max units per weapon in shop (quantity_ranges.weapons.max)",
        secondary_weapon_qty_min="Min units per secondary weapon (quantity_ranges.secondary_weapons.min)",
        secondary_weapon_qty_max="Max units per secondary weapon (quantity_ranges.secondary_weapons.max)",
        module_qty_min="Min units per module in shop (quantity_ranges.modules.min)",
        module_qty_max="Max units per module in shop (quantity_ranges.modules.max)",
        turret_qty_min="Min units per turret in shop (quantity_ranges.turrets.min)",
        turret_qty_max="Max units per turret in shop (quantity_ranges.turrets.max)",
        tl_prob_same_level="TL probability: item at same level as shop band (0.0–1.0)",
        tl_prob_one_lower="TL probability: item one level below band (0.0–1.0)",
        tl_prob_two_lower="TL probability: item two levels below band (0.0–1.0)",
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
        ship_qty_min: int | None = None,
        ship_qty_max: int | None = None,
        weapon_qty_min: int | None = None,
        weapon_qty_max: int | None = None,
        secondary_weapon_qty_min: int | None = None,
        secondary_weapon_qty_max: int | None = None,
        module_qty_min: int | None = None,
        module_qty_max: int | None = None,
        turret_qty_min: int | None = None,
        turret_qty_max: int | None = None,
        tl_prob_same_level: float | None = None,
        tl_prob_one_lower: float | None = None,
        tl_prob_two_lower: float | None = None,
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

        # Build quantity_ranges — same bot-core "both required" rule as item_count_ranges
        quantity_ranges: dict[str, dict[str, int]] = {}
        if ship_qty_min is not None and ship_qty_max is not None:
            quantity_ranges["ships"] = {"min": ship_qty_min, "max": ship_qty_max}
        if weapon_qty_min is not None and weapon_qty_max is not None:
            quantity_ranges["weapons"] = {"min": weapon_qty_min, "max": weapon_qty_max}
        if secondary_weapon_qty_min is not None and secondary_weapon_qty_max is not None:
            quantity_ranges["secondary_weapons"] = {"min": secondary_weapon_qty_min, "max": secondary_weapon_qty_max}
        if module_qty_min is not None and module_qty_max is not None:
            quantity_ranges["modules"] = {"min": module_qty_min, "max": module_qty_max}
        if turret_qty_min is not None and turret_qty_max is not None:
            quantity_ranges["turrets"] = {"min": turret_qty_min, "max": turret_qty_max}

        # Build tech_level_probabilities — include any provided values
        tl_probs: dict[str, float] = {}
        if tl_prob_same_level is not None:
            tl_probs["same_level"] = tl_prob_same_level
        if tl_prob_one_lower is not None:
            tl_probs["one_lower"] = tl_prob_one_lower
        if tl_prob_two_lower is not None:
            tl_probs["two_lower"] = tl_prob_two_lower

        # Build the shop-config payload matching UpdateShopConfigRequest
        shop_payload: dict = {"guild_id": interaction.guild_id}
        if item_count_ranges:
            shop_payload["item_count_ranges"] = item_count_ranges
        if quantity_ranges:
            shop_payload["quantity_ranges"] = quantity_ranges
        if tl_probs:
            shop_payload["tech_level_probabilities"] = tl_probs

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
            #     "tech_level_probabilities": {"same_level": F, "one_lower": F, "two_lower": F},
            #   }
            shop_cfg = cfg.get("shop_config", {})
            item_ranges = shop_cfg.get("item_count_ranges", {})
            qty_ranges = shop_cfg.get("quantity_ranges", {})
            tl_probs_resp = shop_cfg.get("tech_level_probabilities", {})

            def _range_str(d: dict, key: str) -> str:
                r = d.get(key, {})
                if not r:
                    return "—"
                return f"Min: {r.get('min', '?')} / Max: {r.get('max', '?')}"

            embed = discord.Embed(
                title="✅ Shop Configuration Updated",
                description="Current shop configuration for this guild:",
                color=discord.Color.green(),
            )
            embed.add_field(name="Item Count — Ships", value=_range_str(item_ranges, "ships"), inline=True)
            embed.add_field(name="Item Count — Weapons", value=_range_str(item_ranges, "weapons"), inline=True)
            embed.add_field(
                name="Item Count — Secondary", value=_range_str(item_ranges, "secondary_weapons"), inline=True
            )
            embed.add_field(name="Item Count — Modules", value=_range_str(item_ranges, "modules"), inline=True)
            embed.add_field(name="Item Count — Turrets", value=_range_str(item_ranges, "turrets"), inline=True)
            sale_pf = cfg.get("sale_price_factor")
            embed.add_field(
                name="Sale Price Factor",
                value=f"{sale_pf:.0%}" if isinstance(sale_pf, float) else str(sale_pf or "?"),
                inline=True,
            )
            # Show quantity_ranges if any were updated or are set in response
            if qty_ranges:
                qty_lines = [f"{k}: {_range_str(qty_ranges, k)}" for k in qty_ranges]
                embed.add_field(name="Quantity Ranges", value="\n".join(qty_lines), inline=False)
            # Show TL probabilities if set in response
            if tl_probs_resp:
                tl_lines = [f"{k}: {v}" for k, v in tl_probs_resp.items()]
                embed.add_field(name="TL Probabilities", value="\n".join(tl_lines), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} updated shop config for guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while updating shop configuration.", ephemeral=True)

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
        description="[ADMIN] Reset a player's bounty check cooldown immediately",
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
                from utils.autocomplete_helpers import resolve_player_id

                from utils import autocomplete_state

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
    # _GAME_CONSTANT_FIELDS — static fallback list (117 fields, rev 0032)
    # Used by setting_autocomplete when the /config/metadata endpoint is offline.
    # NOTE: starting_credits and sale_price_factor are NOT in this list — they are
    # Tier-1 scalars served only via the metadata endpoint (metadata-only additions).
    # ------------------------------------------------------------------

    # All slash-settable per-guild game-constant override field names (identical to _OVERRIDE_FIELDS in
    # bot-core config router). 117 fields as of rev 0032 (+22 combat engine constants; 95+22=117).
    # Rev 0031 retired 14 fields: duel_cloak_chance, criminal_equip_damageless_weapon_chance,
    # ship_value_reward_percentage, shop_default_{ships,weapons,modules,turrets}_num,
    # turret_spawn_probability, guild_activity_decay_rate, min_guild_activity,
    # activity_temp_per_player, bounty_delay_random_min, bounty_delay_random_max, bounty_spawn_jitter.
    _GAME_CONSTANT_FIELDS: tuple[str, ...] = (
        "division_max_tl",
        # ship_value_reward_percentage — RETIRED rev 0031
        # criminal_equip_damageless_weapon_chance — RETIRED rev 0031
        "criminal_max_gear_upgrade",
        "bounty_reward_to_xp_gain_mult",
        "bounty_winner_reserve_factor",
        "bounty_division_reward_mult",
        # bounty_pvc_armour_buff_factor — retired T10 (dropped from guild_config)
        # duel_variance_percent — retired T10 (SimpleTTKResolver removed)
        # duel_cloak_chance — RETIRED rev 0031
        "close_bounty_threshold",
        "max_route_length",
        # bounty_delay_random_min — RETIRED rev 0031
        # bounty_delay_random_max — RETIRED rev 0031
        # bounty_spawn_jitter — RETIRED rev 0031
        "check_cooldown",
        "duel_request_expiry",
        "tier_change_cooldown",
        # guild_activity_decay_rate — RETIRED rev 0031
        # min_guild_activity — RETIRED rev 0031
        # activity_temp_per_player — RETIRED rev 0031
        # shop_default_ships_num — RETIRED rev 0031
        # shop_default_weapons_num — RETIRED rev 0031
        # shop_default_modules_num — RETIRED rev 0031
        # shop_default_turrets_num — RETIRED rev 0031
        # turret_spawn_probability — RETIRED rev 0031
        "classic_credits_per_check",
        # Criminal loadout balance (Threads 3/4/6)
        "long_range_threshold_m",
        "criminal_long_range_pct",
        "primary_tl_band_weights",
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
        "criminal_exclude_emp_weapons",
        # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2)
        "loot_chance_tractor_t1",
        "loot_chance_tractor_t2",
        "loot_chance_tractor_t3",
        "loot_chance_tractor_t4",
        "loot_chance_no_tractor",
        "loot_band1_select_pct",
        "loot_band2_select_pct",
        "loot_band3_select_pct",
        "loot_band1_tl_window",
        "loot_band1_qty_min",
        "loot_band1_qty_max",
        "loot_band1_qty_mode",
        "loot_band2_qty_min",
        "loot_band2_qty_max",
        "loot_band2_qty_mode",
        "loot_band3_qty_min",
        "loot_band3_qty_max",
        "loot_band3_qty_mode",
        "loot_commodity_sell_fraction",
        # Previously API-only fields — now slash-settable (issue #70 batch)
        "min_route_systems",
        "recently_spotted_max_window",
        "demotion_credit_penalty_pct",
        "shop_combat_module_prob",
        # D-trivial + DIVISION_TL_CENTERS scalar overrides (revision 0028)
        # Criminal loadout — secondary selection
        "criminal_secondary_min_damage",
        # Shop — secondary weapon quantity scalers
        "shop_secondary_qty_scaler_heavy",
        "shop_secondary_qty_scaler_standard",
        # Shop — per-tier in-band TL range bounds
        "shop_tl_band_lo_bronze",
        "shop_tl_band_hi_bronze",
        "shop_tl_band_lo_silver",
        "shop_tl_band_hi_silver",
        "shop_tl_band_lo_gold",
        "shop_tl_band_hi_gold",
        "shop_tl_band_lo_platinum",
        "shop_tl_band_hi_platinum",
        # Shop — batch TL draw parameters
        "shop_banded_tl_weight",
        "shop_uptier_tl_decay",
        "shop_downtier_tl_decay",
        # Division TL draw centres
        "division_tl_center_bronze",
        "division_tl_center_silver",
        "division_tl_center_gold",
        "division_tl_center_platinum",
        # Previously column-only orphans (columns from 0026; slash exposure added here)
        "bounty_single_waypoint_prob",
        "bounty_dual_waypoint_prob",
        "bounty_waypoint_attempts",
        "bounty_waypoint_min_degree",
        "pvc_damage_reduction",
        # Bronze combat bonus per-guild overrides (issue #70 Unit C, revision 0029)
        "bronze_combat_bonus_base_mult",
        "bronze_combat_bonus_per_prestige",
        "bronze_combat_bonus_cap",
        # JSONB flatten scalars (issue #70, revision 0030) — 27 new fields
        # division_max_tl flat scalars
        "division_max_tl_bronze",
        "division_max_tl_silver",
        "division_max_tl_gold",
        "division_max_tl_platinum",
        # bounty_division_reward_mult flat scalars
        "bounty_division_reward_mult_bronze",
        "bounty_division_reward_mult_silver",
        "bounty_division_reward_mult_gold",
        "bounty_division_reward_mult_platinum",
        # primary_tl_band_weights flat scalars
        "primary_tl_band_weight_center",
        "primary_tl_band_weight_minus1",
        "primary_tl_band_weight_plus1",
        # criminal chance flat scalars
        "criminal_cloak_chance_bronze",
        "criminal_cloak_chance_silver",
        "criminal_cloak_chance_gold",
        "criminal_cloak_chance_platinum",
        "criminal_booster_chance_bronze",
        "criminal_booster_chance_silver",
        "criminal_booster_chance_gold",
        "criminal_booster_chance_platinum",
        "criminal_emergency_chance_bronze",
        "criminal_emergency_chance_silver",
        "criminal_emergency_chance_gold",
        "criminal_emergency_chance_platinum",
        "criminal_weaponmod_chance_bronze",
        "criminal_weaponmod_chance_silver",
        "criminal_weaponmod_chance_gold",
        "criminal_weaponmod_chance_platinum",
        # Combat engine per-guild overrides, wired (issue #70 unit A1, revision 0032) — 22 new fields
        # Accuracy system (§5)
        "cloak_set_value",
        "booster_accuracy_debuff_factor",
        "thruster_accuracy_bonus_factor",
        "auto_turret_accuracy_multiplier",
        "player_base_accuracy",
        "npc_base_accuracy",
        "scanner_tier_b_bonus_pp",
        "scanner_tier_c_bonus_pp",
        # Distance model (§2)
        "starting_distance_m",
        "base_ship_speed_mps",
        "min_distance_m",
        "thruster_window_m",
        # Emergency system (§7.7)
        "emergency_system_invuln_s",
        # Nuke (§6.2)
        "nuke_magnitude_scale",
        "nuke_friendly_factor",
        "nuke_range_regime_threshold_m",
        "nuke_lr_near_frac",
        "nuke_cr_short_m",
        "nuke_cr_overshoot_m",
        "nuke_stack_falloff",
        # Shock-blast (§6.2 / D6)
        "shock_blast_trigger_range_m",
        # Shield / armour regen reemission (CI-21)
        "combat_layer_reemit_fraction",
        # _GAME_CONSTANT_FIELDS == _OVERRIDE_FIELDS (config.py): 117 fields as of rev 0032 (+22 combat engine).
    )

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
