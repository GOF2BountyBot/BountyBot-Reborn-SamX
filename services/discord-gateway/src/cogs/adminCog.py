import asyncio
import os
from typing import Literal

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search
from utils.guild_setup import ensure_bountybot_infrastructure
from utils.timestamp_utils import iso_to_discord_ts

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
        admin_role_id = resp.json().get("admin_role_id")
        if admin_role_id and any(r.id == admin_role_id for r in interaction.user.roles):
            return True
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return False


def is_admin():
    """
    Allow users with the built-in Administrator permission,
    the configured Bot Admin role, or listed developer IDs.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        return await _check_is_admin(interaction)

    return app_commands.check(predicate)


class AdminCog(commands.Cog):  # pylint: disable=too-many-public-methods
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        self._render_settings: list[str] = []
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        bot.loop.create_task(self._preload_render_settings())
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
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user="The user to check")
    @is_admin()
    async def admin_check(self, interaction: discord.Interaction, user: discord.User):
        """Report whether the given user has admin rights—and by which rule."""
        await interaction.response.defer(thinking=True, ephemeral=True)

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
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        admin_role="Role that should have admin permissions for the bot (required)",
        starting_credits="Starting credits for new players (default: 0)",
    )
    @is_admin()
    async def admin_setup(self, interaction: discord.Interaction, admin_role: discord.Role, starting_credits: int = 0):
        """Initialize guild for bot usage."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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

            embed.set_footer(text="The bot is now ready for use in this guild!")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Guild {interaction.guild_id} initialized by {interaction.user}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_setup: {e}")
            await interaction.followup.send("⚠️ An error occurred during guild initialization.", ephemeral=True)

    @app_commands.command(name="admin_player", description="[ADMIN] Manage player data")
    @app_commands.default_permissions(administrator=True)
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
        ]
    )
    @is_admin()
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
        try:
            # Ensure player exists or create
            user_data = {"discord_id": user.id, "guild_id": interaction.guild_id, "discord_username": str(user)}
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

            flogger.info(
                f"Admin {interaction.user} performed {action} on player {user} in guild {interaction.guild_id}"
            )

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_player: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing player.", ephemeral=True)

    @app_commands.command(name="admin_refresh_shop", description="[ADMIN] Force refresh a shop")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(tier="Shop tier to refresh", force_tech_level="Force all items to specific tech level (1-9)")
    @app_commands.autocomplete(tier=tier_autocomplete)
    @is_admin()
    async def admin_refresh_shop(
        self, interaction: discord.Interaction, tier: str, force_tech_level: int | None = None
    ):
        """Force refresh a guild shop."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if tier not in self._valid_tiers:
                await interaction.followup.send(
                    f"❌ Invalid tier. Valid tiers: {', '.join(self._valid_tiers)}", ephemeral=True
                )
                return
            if force_tech_level and (force_tech_level < 1 or force_tech_level > 9):
                await interaction.followup.send("❌ Tech level must be between 1 and 9.", ephemeral=True)
                return

            refresh_data = {"guild_id": interaction.guild_id, "tier": tier, "force_tech_level": force_tech_level}
            resp = await self.http_client.post(
                f"{api_base}/admin/shops/refresh",
                json=refresh_data,
                params={"user_id": interaction.user.id},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="✅ Shop Refreshed Successfully!", description=result["message"], color=discord.Color.green()
            )
            embed.add_field(name="Shop Tier", value=tier, inline=True)
            embed.add_field(name="Guild ID", value=str(interaction.guild_id), inline=True)
            if force_tech_level:
                embed.add_field(name="Forced Tech Level", value=str(force_tech_level), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} refreshed {tier} shop in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_refresh_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while refreshing shop.", ephemeral=True)

    @app_commands.command(name="admin_guild_stats", description="[ADMIN] View guild statistics")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def admin_guild_stats(self, interaction: discord.Interaction):
        """View comprehensive guild statistics."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_guild_stats: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching guild statistics.", ephemeral=True)

    @app_commands.command(name="admin_config", description="[ADMIN] View or update guild configuration")
    @app_commands.default_permissions(administrator=True)
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
    @is_admin()
    async def admin_config(
        self,
        interaction: discord.Interaction,
        action: str,
        starting_credits: int | None = None,
        admin_role: discord.Role | None = None,
    ):
        """Manage guild configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
                threshold_text = (
                    f"Silver: {thresholds['Silver']:,}\n"
                    f"Gold: {thresholds['Gold']:,}\n"
                    f"Platinum: {thresholds['Platinum']:,}"
                )
                embed.add_field(name="XP Thresholds", value=threshold_text, inline=True)
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing configuration.", ephemeral=True)

    @app_commands.command(name="admin_uninstall", description="[ADMIN] Completely remove all bot data from this guild")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(confirm="Type CONFIRM-DELETE to confirm (this is IRREVERSIBLE)")
    @is_admin()
    async def admin_uninstall(
        self,
        interaction: discord.Interaction,
        confirm: str | None = None,
    ):
        """Destructively remove all bot data for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        # 2-step confirmation: show warning if no/wrong confirmation string
        if confirm != "CONFIRM-DELETE":
            embed = discord.Embed(
                title="⚠️ WARNING: Destructive Operation",
                description=(
                    "This will **permanently delete** all bot data for this guild including:\n"
                    "• All player records and statistics\n"
                    "• All shop configurations\n"
                    "• All guild settings\n\n"
                    "**This action cannot be undone.**\n\n"
                    "To confirm, run:\n"
                    "`/admin_uninstall confirm:CONFIRM-DELETE`"
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text="Bot data will NOT be deleted until you confirm.")
            await interaction.followup.send(embed=embed, ephemeral=True)
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_uninstall: {e}")
            await interaction.followup.send("⚠️ An error occurred during uninstall.", ephemeral=True)

    @app_commands.command(name="admin_config_shop", description="[ADMIN] Update shop configuration")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        ship_count_min="Minimum number of ship types in shop",
        ship_count_max="Maximum number of ship types in shop",
        weapon_count_min="Minimum number of weapon types in shop",
        weapon_count_max="Maximum number of weapon types in shop",
        module_count_min="Minimum number of module types in shop",
        module_count_max="Maximum number of module types in shop",
        turret_count_min="Minimum number of turret types in shop",
        turret_count_max="Maximum number of turret types in shop",
        sale_factor="Sale price factor (0.0 - 1.0, e.g. 0.8 = 80% of base price)",
    )
    @is_admin()
    async def admin_config_shop(
        self,
        interaction: discord.Interaction,
        ship_count_min: int | None = None,
        ship_count_max: int | None = None,
        weapon_count_min: int | None = None,
        weapon_count_max: int | None = None,
        module_count_min: int | None = None,
        module_count_max: int | None = None,
        turret_count_min: int | None = None,
        turret_count_max: int | None = None,
        sale_factor: float | None = None,
    ):
        """Update shop-specific configuration for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        # Build item_count_ranges — only include a type's range when BOTH min and max
        # are provided so that bot-core never receives a partial {"min": N} dict that
        # would fail its "min and max required" validation.
        item_count_ranges: dict[str, dict[str, int]] = {}
        if ship_count_min is not None and ship_count_max is not None:
            item_count_ranges["ships"] = {"min": ship_count_min, "max": ship_count_max}
        if weapon_count_min is not None and weapon_count_max is not None:
            item_count_ranges["weapons"] = {"min": weapon_count_min, "max": weapon_count_max}
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while updating shop configuration.", ephemeral=True)

    @app_commands.command(name="admin_config_validate", description="[ADMIN] Validate guild configuration")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def admin_config_validate(self, interaction: discord.Interaction):
        """Validate the current guild configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)

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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_validate: {e}")
            await interaction.followup.send("⚠️ An error occurred while validating configuration.", ephemeral=True)

    # ------------------------------------------------------------------
    # Render configuration commands
    # ------------------------------------------------------------------

    @app_commands.command(name="render_config", description="[ADMIN] View/update blender render settings")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        action="Action to perform: view current config, set a value, or reset to defaults",
        setting="Setting name to update (required for 'set' action)",
        value="New integer value (required for 'set' action)",
    )
    @is_admin()
    @app_commands.autocomplete(setting=render_setting_autocomplete)
    async def render_config(
        self,
        interaction: discord.Interaction,
        action: Literal["view", "set", "reset"] = "view",
        setting: str | None = None,
        value: int | None = None,
    ) -> None:
        """Admin command to view/update blender-service render configuration."""
        blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")

        try:
            if action == "view":
                resp = await self.http_client.get(f"{blender_base}/config/render")
                resp.raise_for_status()
                config = resp.json()
                embed = discord.Embed(title="🎨 Render Configuration", color=discord.Color.blue())
                for key, val in config.items():
                    embed.add_field(name=key, value=str(val), inline=True)
                await interaction.response.send_message(embed=embed, ephemeral=True)

            elif action == "set":
                if not setting or value is None:
                    await interaction.response.send_message(
                        "⚠️ Usage: `/render_config set <setting> <value>`", ephemeral=True
                    )
                    return
                resp = await self.http_client.put(
                    f"{blender_base}/config/render",
                    json={setting: value},
                )
                resp.raise_for_status()
                await interaction.response.send_message(f"✅ Updated `{setting}` = `{value}`", ephemeral=True)
                flogger.info(f"Admin {interaction.user} updated render config: {setting}={value}")

            elif action == "reset":
                resp = await self.http_client.post(f"{blender_base}/config/render/reset")
                resp.raise_for_status()
                await interaction.response.send_message("✅ Render config reset to defaults.", ephemeral=True)
                flogger.info(f"Admin {interaction.user} reset render config")

        except httpx.HTTPStatusError as e:
            await interaction.response.send_message(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /render_config: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="render_cache_clear", description="[ADMIN] Clear blender render cache (/tmp)")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def render_cache_clear(self, interaction: discord.Interaction) -> None:
        """Admin command to clear blender-service temp render files."""
        blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")

        try:
            resp = await self.http_client.post(f"{blender_base}/cache/clear")
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(title="🗑️ Render Cache Cleared", color=discord.Color.green())
            embed.add_field(name="Directories Cleared", value=str(result["cleared_directories"]), inline=True)
            embed.add_field(name="Space Freed", value=f"{result['freed_mb']} MB", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} cleared render cache: "
                f"{result['cleared_directories']} dirs, {result['freed_mb']} MB"
            )

        except httpx.HTTPStatusError as e:
            await interaction.response.send_message(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /render_cache_clear: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # Bounty admin commands
    # ------------------------------------------------------------------

    @app_commands.command(name="admin_clear_bounties", description="[ADMIN] Clear active bounties for this guild")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    @app_commands.describe(
        tier="Tier to clear (omit for all tiers)",
        confirm="Type CONFIRM to execute this destructive action",
    )
    @app_commands.choices(
        tier=[
            app_commands.Choice(name="Bronze", value="bronze"),
            app_commands.Choice(name="Silver", value="silver"),
            app_commands.Choice(name="Gold", value="gold"),
            app_commands.Choice(name="Platinum", value="platinum"),
        ]
    )
    async def admin_clear_bounties(self, interaction: discord.Interaction, confirm: str, tier: str | None = None):
        """Clear active bounties for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if confirm != "CONFIRM":
            await interaction.followup.send(
                "❌ You must type CONFIRM to execute this destructive action.", ephemeral=True
            )
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_clear_bounties: {e}")
            await interaction.followup.send("⚠️ An error occurred while clearing bounties.", ephemeral=True)

    @app_commands.command(name="admin_config_bounty", description="[ADMIN] View or update bounty configuration")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_bounty: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing bounty configuration.", ephemeral=True)

    @app_commands.command(name="admin_config_xp", description="[ADMIN] View or update XP tier thresholds")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    @app_commands.describe(
        action="View current thresholds or update them",
        silver="XP threshold for Silver tier",
        gold="XP threshold for Gold tier",
        platinum="XP threshold for Platinum tier",
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
    ):
        """View or update XP tier thresholds for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)

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

                embed = discord.Embed(title="⚙️ XP Tier Thresholds", color=discord.Color.blue())
                embed.add_field(
                    name="Thresholds",
                    value=(
                        f"Silver: {silver_val:,} XP\nGold: {gold_val:,} XP\nPlatinum: {platinum_val:,} XP"
                        if isinstance(silver_val, int)
                        else f"Silver: {silver_val}\nGold: {gold_val}\nPlatinum: {platinum_val}"
                    ),
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "update":
                # Require all three thresholds
                if silver is None or gold is None or platinum is None:
                    await interaction.followup.send(
                        "❌ All three thresholds are required: silver, gold, and platinum.", ephemeral=True
                    )
                    return

                # Client-side pre-validation: ascending order
                if not silver < gold < platinum:
                    await interaction.followup.send(
                        "❌ Thresholds must be in strictly ascending order: silver < gold < platinum.", ephemeral=True
                    )
                    return

                payload = {
                    "guild_id": interaction.guild_id,
                    "thresholds": {"Silver": silver, "Gold": gold, "Platinum": platinum},
                }
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/xp-thresholds",
                    json=payload,
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                updated = result.get("xp_thresholds", payload["thresholds"])

                embed = discord.Embed(title="✅ XP Thresholds Updated", color=discord.Color.green())
                embed.add_field(
                    name="New Thresholds",
                    value=(
                        f"Silver: {updated.get('Silver', silver):,} XP\n"
                        f"Gold: {updated.get('Gold', gold):,} XP\n"
                        f"Platinum: {updated.get('Platinum', platinum):,} XP"
                    ),
                    inline=False,
                )
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_config_xp: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing XP thresholds.", ephemeral=True)

    @app_commands.command(name="admin_spawn_bounty", description="[ADMIN] Manually trigger a bounty spawn")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    @app_commands.describe(tier="Tier to spawn for (omit for all tiers)")
    @app_commands.choices(
        tier=[
            app_commands.Choice(name="Bronze", value="bronze"),
            app_commands.Choice(name="Silver", value="silver"),
            app_commands.Choice(name="Gold", value="gold"),
            app_commands.Choice(name="Platinum", value="platinum"),
        ]
    )
    async def admin_spawn_bounty(self, interaction: discord.Interaction, tier: str | None = None):
        """Manually trigger a bounty spawn for this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            params: dict = {"user_id": interaction.user.id}
            if tier:
                params["tier"] = tier

            resp = await self.http_client.post(
                f"{api_base}/bounties/guild/{interaction.guild_id}/admin-spawn",
                params=params,
                timeout=30,
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user="The Discord member whose cooldown should be reset")
    @is_admin()
    async def admin_cooldown_reset(self, interaction: discord.Interaction, user: discord.Member):
        """Reset a player's bounty cooldown."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for item names from game data (weapons, modules, turrets)."""
        try:
            norm_current = normalize_for_search(current)
            choices: list[app_commands.Choice[str]] = []
            seen: set[str] = set()

            # Query game data for weapons, modules, turrets
            for category in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
                resp = await self.http_client.get(f"{api_base}/data/{category}", timeout=5)
                if resp.status_code != 200:
                    continue
                for item in resp.json():
                    name = item.get("name", "")
                    if name and name not in seen and norm_current in normalize_for_search(name):
                        seen.add(name)
                        choices.append(app_commands.Choice(name=name, value=name))
                if len(choices) >= 25:
                    break
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def game_ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for ship names from game data (for give-ship)."""
        try:
            norm_current = normalize_for_search(current)
            resp = await self.http_client.get(f"{api_base}/about/ships", timeout=5)
            if resp.status_code != 200:
                return []
            ships = resp.json()
            choices = [
                app_commands.Choice(name=s["name"], value=s["name"])
                for s in ships
                if norm_current in normalize_for_search(s.get("name", ""))
            ]
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="admin_give_item", description="[ADMIN] Give an item directly to a player's inventory")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="Target player",
        item_name="Item to give (autocomplete from game data)",
        item_type="Type of item",
        quantity="Number of items to give (default: 1)",
    )
    @app_commands.choices(
        item_type=[
            app_commands.Choice(name="Weapon", value="weapon"),
            app_commands.Choice(name="Module", value="module"),
            app_commands.Choice(name="Turret", value="turret"),
        ]
    )
    @app_commands.autocomplete(item_name=item_name_autocomplete)
    @is_admin()
    async def admin_give_item(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        item_name: str,
        item_type: str,
        quantity: int = 1,
    ):
        """Give an item directly to a player's inventory (no credit cost)."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "item_name": item_name,
                "item_type": item_type,
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

            embed = discord.Embed(
                title="✅ Item Given",
                description=result.get("message", "Item given successfully."),
                color=discord.Color.green(),
            )
            embed.add_field(name="Item", value=item_name, inline=True)
            embed.add_field(name="Type", value=item_type.title(), inline=True)
            embed.add_field(name="Quantity", value=str(quantity), inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="New Total", value=str(result.get("new_total_quantity", "?")), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} gave {quantity}x {item_name} to {user} in guild {interaction.guild_id}"
            )

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_give_item: {e}")
            await interaction.followup.send("⚠️ An error occurred while giving item.", ephemeral=True)

    @admin_give_item.error
    async def admin_give_item_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_give_item", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="admin_remove_item", description="[ADMIN] Remove an item from a player's inventory")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="Target player",
        item_name="Item to remove (autocomplete from game data)",
        item_type="Type of item",
        quantity="Number of items to remove (default: 1)",
    )
    @app_commands.choices(
        item_type=[
            app_commands.Choice(name="Weapon", value="weapon"),
            app_commands.Choice(name="Module", value="module"),
            app_commands.Choice(name="Turret", value="turret"),
        ]
    )
    @app_commands.autocomplete(item_name=item_name_autocomplete)
    @is_admin()
    async def admin_remove_item(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        item_name: str,
        item_type: str,
        quantity: int = 1,
    ):
        """Remove an item from a player's inventory."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            payload = {
                "guild_id": interaction.guild_id,
                "user_id": user.id,
                "item_name": item_name,
                "item_type": item_type,
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

            embed = discord.Embed(
                title="✅ Item Removed",
                description=result.get("message", "Item removed successfully."),
                color=discord.Color.orange(),
            )
            embed.add_field(name="Item", value=item_name, inline=True)
            embed.add_field(name="Type", value=item_type.title(), inline=True)
            embed.add_field(name="Quantity Removed", value=str(quantity), inline=True)
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="Remaining", value=str(result.get("new_quantity", 0)), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"Admin {interaction.user} removed {quantity}x {item_name} from {user} in guild {interaction.guild_id}"
            )

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_remove_item: {e}")
            await interaction.followup.send("⚠️ An error occurred while removing item.", ephemeral=True)

    @admin_remove_item.error
    async def admin_remove_item_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_remove_item", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="admin_give_ship", description="[ADMIN] Give a ship to a player")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="Target player",
        ship_name="Name of the ship to give (autocomplete from game data)",
    )
    @app_commands.autocomplete(ship_name=game_ship_autocomplete)
    @is_admin()
    async def admin_give_ship(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        ship_name: str,
    ):
        """Give a ship to a player. Ship starts inactive with empty loadout."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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
        """Autocomplete for remove-ship — shows ships owned by the target player."""
        try:
            # We can only show ships for a generic autocomplete since we don't know the target user
            # until the command is submitted. Show all ships from game data as fallback.
            norm_current = normalize_for_search(current)
            resp = await self.http_client.get(f"{api_base}/about/ships", timeout=5)
            if resp.status_code != 200:
                return []
            ships = resp.json()
            choices = [
                app_commands.Choice(name=s["name"], value=s["name"])
                for s in ships
                if norm_current in normalize_for_search(s.get("name", ""))
            ]
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="admin_remove_ship", description="[ADMIN] Remove a ship from a player")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="Target player",
        ship_name="Name of the ship to remove",
    )
    @app_commands.autocomplete(ship_name=player_ship_autocomplete)
    @is_admin()
    async def admin_remove_ship(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        ship_name: str,
    ):
        """Remove a ship from a player. Unequips all items first. Cannot remove only active ship."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /admin_remove_ship: {e}")
            await interaction.followup.send("⚠️ An error occurred while removing ship.", ephemeral=True)

    @admin_remove_ship.error
    async def admin_remove_ship_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_remove_ship", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up AdminCog...")
    await bot.add_cog(AdminCog(bot))
    flogger.info("AdminCog loaded")
