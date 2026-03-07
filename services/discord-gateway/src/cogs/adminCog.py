import os
import discord
from discord import app_commands
from discord.ext import commands
import shared.bblogger as bblogger
import httpx
from typing import Optional, List, Dict, Any

# Set up logger
flogger = bblogger.get_logger("discord-gateway-AdminCog")

# Base URL of your Gateway API
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"adminCog loading with API_BASE_URL: {api_base}")

def is_admin():
    """
    Allow users with the built-in Administrator permission,
    the configured Bot Admin role, or listed developer IDs.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        # 0) developer override via ENV var
        devs = os.getenv("DEVELOPERS", "")
        if str(interaction.user.id) in [d.strip() for d in devs.split(",") if d.strip()]:
            return True

        # 1) built-in Discord Administrator
        if interaction.user.guild_permissions.administrator:
            return True

        # 2) configured Bot Admin role from API
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{api_base}/config/guild/{interaction.guild_id}",
                    timeout=5
                )
            resp.raise_for_status()
            admin_role_id = resp.json().get("admin_role_id")
            if admin_role_id and any(r.id == admin_role_id for r in interaction.user.roles):
                return True
        except Exception:
            pass

        return False

    return app_commands.check(predicate)

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        self.http_client = httpx.AsyncClient()
        flogger.debug("AdminCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def tier_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for shop tiers."""
        return [
            app_commands.Choice(name=t, value=t)
            for t in self._valid_tiers
            if current.lower() in t.lower()
        ]

    @app_commands.command(
        name="admin_check",
        description="[ADMIN] Check if a user has bot‐admin rights and why"
    )
    @app_commands.describe(
        user="The user to check"
    )
    @is_admin()
    async def admin_check(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
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
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{interaction.guild_id}",
                    timeout=5
                )
                resp.raise_for_status()
                admin_role_id = resp.json().get("admin_role_id")
                if admin_role_id and any(r.id == admin_role_id for r in member.roles):
                    has_admin = True
                    reason = "Assigned Bot Admin role"
            except Exception:
                pass

        # Build response
        if has_admin:
            msg = f"{user.mention} **has** bot-admin rights.\n> **Reason:** {reason}"
        else:
            msg = f"{user.mention} **does not have** bot-admin rights."

        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="admin_setup",
        description="[ADMIN] Initialize the bot for this guild"
    )
    @app_commands.describe(
        admin_role="Role that should have admin permissions for the bot",
        starting_credits="Starting credits for new players (default: 0)"
    )
    @is_admin()
    async def admin_setup(
        self,
        interaction: discord.Interaction,
        admin_role: Optional[discord.Role] = None,
        starting_credits: int = 0
    ):
        """Initialize guild for bot usage."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild = self.bot.get_guild(interaction.guild_id)

        try:
            # If no role provided, create one via your Gateway API
            if admin_role is None:
                payload = {
                    "name": "BountyBot Admins",
                    "permissions": discord.Permissions(manage_guild=True).value,
                    "hoist": False,
                    "mentionable": False
                }
                resp = await self.http_client.post(
                    f"{api_base}/guilds/{interaction.guild_id}/roles",
                    json=payload,
                    timeout=30
                )
                resp.raise_for_status()
                role_data = resp.json()["data"]
                # Fetch the Role object from cache or reload
                admin_role = guild.get_role(role_data["id"])
                if admin_role is None:
                    await guild.fetch_roles()
                    admin_role = guild.get_role(role_data["id"])

            # Send initialization to core API
            init_payload = {
                "guild_id": interaction.guild_id,
                "admin_role_id": admin_role.id,
                "starting_credits": max(0, starting_credits)
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/guilds/initialize",
                json=init_payload,
                timeout=30
            )
            resp.raise_for_status()
            result = resp.json()

            # Build and send confirmation embed
            embed = discord.Embed(
                title="✅ Guild Initialization Complete!",
                description=result["message"],
                color=discord.Color.green()
            )
            embed.add_field(name="Guild ID", value=str(result["guild_id"]), inline=True)
            embed.add_field(name="Shops Created", value=str(result["shops_created"]), inline=True)
            embed.add_field(name="Admin Role", value=admin_role.mention, inline=True)
            embed.add_field(name="Starting Credits", value=f"{starting_credits:,}", inline=True)
            embed.set_footer(text="The bot is now ready for use in this guild!")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Guild {interaction.guild_id} initialized by {interaction.user}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /admin_setup: {e}")
            await interaction.followup.send(
                "⚠️ An error occurred during guild initialization.",
                ephemeral=True
            )

    @app_commands.command(
        name="admin_player",
        description="[ADMIN] Manage player data"
    )
    @app_commands.describe(
        user="Player to manage",
        action="Action to perform",
        credits="Credits to set/add (for credit actions)",
        xp="XP to set (for XP actions)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Set Credits", value="set_credits"),
        app_commands.Choice(name="Add Credits", value="add_credits"),
        app_commands.Choice(name="Set XP", value="set_xp"),
        app_commands.Choice(name="View Stats", value="view_stats"),
        app_commands.Choice(name="Reset Player", value="reset")
    ])
    @is_admin()
    async def admin_player(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        action: str,
        credits: Optional[int] = None,
        xp: Optional[int] = None
    ):
        """Manage player data."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            # Ensure player exists or create
            user_data = {
                "discord_id": user.id,
                "guild_id": interaction.guild_id,
                "discord_username": str(user)
            }
            player_resp = await self.http_client.post(
                f"{api_base}/players/",
                json=user_data,
                timeout=10
            )
            player_resp.raise_for_status()
            player = player_resp.json()

            # View stats
            if action == "view_stats":
                stats_resp = await self.http_client.get(
                    f"{api_base}/players/{player['id']}/statistics",
                    timeout=10
                )
                stats_resp.raise_for_status()
                stats = stats_resp.json()

                embed = discord.Embed(
                    title=f"📊 Admin View - {user.display_name}",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Player ID", value=str(player['id']), inline=True)
                embed.add_field(name="Tier", value=player['tier'], inline=True)
                embed.add_field(name="XP", value=f"{player['xp']:,}", inline=True)
                embed.add_field(name="Credits", value=f"{player['credits']:,}", inline=True)
                embed.add_field(name="Lifetime Credits", value=f"{player['lifetime_credits']:,}", inline=True)
                embed.add_field(name="Prestige Count", value=str(player['prestige_count']), inline=True)
                embed.set_thumbnail(url=user.display_avatar.url)
                embed.set_footer(text=f"Created: {player['created_at'][:10]}")

                await interaction.followup.send(embed=embed, ephemeral=True)

            # Set credits
            elif action == "set_credits":
                if credits is None:
                    await interaction.followup.send("❌ Credits amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/credits",
                    json={
                        "player_id": player['id'],
                        "credits": max(0, credits),
                        "update_lifetime": False
                    },
                    timeout=10
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ Credits Updated",
                    description=f"Set {user.display_name}'s credits to {credits:,}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Old Credits", value=f"{result['old_credits']:,}", inline=True)
                embed.add_field(name="New Credits", value=f"{result['new_credits']:,}", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

            # Add credits
            elif action == "add_credits":
                if credits is None:
                    await interaction.followup.send("❌ Credits amount required.", ephemeral=True)
                    return
                new_total = max(0, player['credits'] + credits)
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/credits",
                    json={
                        "player_id": player['id'],
                        "credits": new_total,
                        "update_lifetime": True
                    },
                    timeout=10
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ Credits Added",
                    description=f"Added {credits:,} credits to {user.display_name}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Amount Added", value=f"{credits:,}", inline=True)
                embed.add_field(name="New Total", value=f"{result['new_credits']:,}", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

            # Set XP
            elif action == "set_xp":
                if xp is None:
                    await interaction.followup.send("❌ XP amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/admin/players/xp",
                    json={
                        "player_id": player['id'],
                        "xp": max(0, min(1_000_000, xp))
                    },
                    timeout=10
                )
                resp.raise_for_status()
                result = resp.json()
                embed = discord.Embed(
                    title="✅ XP Updated",
                    description=f"Set {user.display_name}'s XP to {xp:,}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Old XP", value=f"{result['old_xp']:,}", inline=True)
                embed.add_field(name="New XP", value=f"{result['new_xp']:,}", inline=True)
                embed.add_field(name="Old Tier", value=result['old_tier'], inline=True)
                embed.add_field(name="New Tier", value=result['new_tier'], inline=True)
                if result.get('tier_changed'):
                    embed.add_field(name="Tier Change", value="✅ Tier Updated!", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

            flogger.info(f"Admin {interaction.user} performed {action} on player {user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /admin_player: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing player.", ephemeral=True)

    @app_commands.command(
        name="admin_refresh_shop",
        description="[ADMIN] Force refresh a shop"
    )
    @app_commands.describe(
        tier="Shop tier to refresh",
        force_tech_level="Force all items to specific tech level (1-9)"
    )
    @app_commands.autocomplete(tier=tier_autocomplete)
    @is_admin()
    async def admin_refresh_shop(
        self,
        interaction: discord.Interaction,
        tier: str,
        force_tech_level: Optional[int] = None
    ):
        """Force refresh a guild shop."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if tier not in self._valid_tiers:
                await interaction.followup.send(
                    f"❌ Invalid tier. Valid tiers: {', '.join(self._valid_tiers)}",
                    ephemeral=True
                )
                return
            if force_tech_level and (force_tech_level < 1 or force_tech_level > 9):
                await interaction.followup.send("❌ Tech level must be between 1 and 9.", ephemeral=True)
                return

            refresh_data = {
                "guild_id": interaction.guild_id,
                "tier": tier,
                "force_tech_level": force_tech_level
            }
            resp = await self.http_client.post(
                f"{api_base}/admin/shops/refresh",
                json=refresh_data,
                timeout=30
            )
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="✅ Shop Refreshed Successfully!",
                description=result['message'],
                color=discord.Color.green()
            )
            embed.add_field(name="Shop Tier", value=tier, inline=True)
            embed.add_field(name="Guild ID", value=str(interaction.guild_id), inline=True)
            if force_tech_level:
                embed.add_field(name="Forced Tech Level", value=str(force_tech_level), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(f"Admin {interaction.user} refreshed {tier} shop in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /admin_refresh_shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while refreshing shop.", ephemeral=True)

    @app_commands.command(
        name="admin_guild_stats",
        description="[ADMIN] View guild statistics"
    )
    @is_admin()
    async def admin_guild_stats(
        self,
        interaction: discord.Interaction
    ):
        """View comprehensive guild statistics."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            resp = await self.http_client.get(
                f"{api_base}/admin/guilds/{interaction.guild_id}/stats",
                timeout=10
            )
            resp.raise_for_status()
            stats = resp.json()

            embed = discord.Embed(
                title=f"📊 Guild Statistics - {interaction.guild.name}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Guild ID", value=str(stats['guild_id']), inline=True)
            embed.add_field(name="Total Players", value=str(stats['total_players']), inline=True)
            if stats.get('tier_distribution'):
                tier_text = "\n".join([f"{tier}: {count}" for tier, count in stats['tier_distribution'].items()])
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
        except Exception as e:
            flogger.error(f"Error in /admin_guild_stats: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching guild statistics.", ephemeral=True)

    @app_commands.command(
        name="admin_config",
        description="[ADMIN] View or update guild configuration"
    )
    @app_commands.describe(
        action="Configuration action to perform",
        starting_credits="Starting credits for new players",
        admin_role="Admin role for the bot"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="View Config", value="view"),
        app_commands.Choice(name="Set Starting Credits", value="set_credits"),
        app_commands.Choice(name="Set Admin Role", value="set_role"),
        app_commands.Choice(name="Reset to Defaults", value="reset")
    ])
    @is_admin()
    async def admin_config(
        self,
        interaction: discord.Interaction,
        action: str,
        starting_credits: Optional[int] = None,
        admin_role: Optional[discord.Role] = None
    ):
        """Manage guild configuration."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if action == "view":
                resp = await self.http_client.get(
                    f"{api_base}/config/guild/{interaction.guild_id}",
                    timeout=10
                )
                resp.raise_for_status()
                cfg = resp.json()

                embed = discord.Embed(
                    title="⚙️ Guild Configuration",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Guild ID", value=str(cfg['guild_id']), inline=True)
                embed.add_field(name="Configured", value="✅" if cfg['configured'] else "❌", inline=True)
                embed.add_field(name="Admin Role Set", value="✅" if cfg['admin_role_configured'] else "❌", inline=True)
                embed.add_field(name="Starting Credits", value=f"{cfg['starting_credits']:,}", inline=True)
                embed.add_field(name="Sale Price Factor", value=f"{cfg['sale_price_factor']:.1%}", inline=True)

                thresholds = cfg['xp_thresholds']
                threshold_text = (
                    f"Silver: {thresholds['Silver']:,}\n"
                    f"Gold: {thresholds['Gold']:,}\n"
                    f"Platinum: {thresholds['Platinum']:,}"
                )
                embed.add_field(name="XP Thresholds", value=threshold_text, inline=True)
                embed.set_footer(text=f"Created: {cfg['created_at'][:10]} | Updated: {cfg['updated_at'][:10]}")

                await interaction.followup.send(embed=embed, ephemeral=True)

            elif action == "set_credits":
                if starting_credits is None:
                    await interaction.followup.send("❌ Starting credits amount required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/starting-credits/{max(0, starting_credits)}",
                    timeout=10
                )
                resp.raise_for_status()
                await interaction.followup.send(
                    f"✅ Starting credits set to {starting_credits:,}",
                    ephemeral=True
                )

            elif action == "set_role":
                if admin_role is None:
                    await interaction.followup.send("❌ Admin role required.", ephemeral=True)
                    return
                resp = await self.http_client.put(
                    f"{api_base}/config/guild/{interaction.guild_id}/admin-role/{admin_role.id}",
                    timeout=10
                )
                resp.raise_for_status()
                await interaction.followup.send(
                    f"✅ Admin role set to {admin_role.mention}",
                    ephemeral=True
                )

            elif action == "reset":
                resp = await self.http_client.post(
                    f"{api_base}/config/guild/{interaction.guild_id}/reset",
                    timeout=10
                )
                resp.raise_for_status()
                await interaction.followup.send(
                    "✅ Guild configuration has been reset to default values",
                    ephemeral=True
                )

            flogger.info(f"Admin {interaction.user} performed config {action} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /admin_config: {e}")
            await interaction.followup.send("⚠️ An error occurred while managing configuration.", ephemeral=True)

    # Error handlers
    @admin_setup.error
    async def admin_setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need Administrator permissions to use this command.",
                ephemeral=True
            )
        else:
            flogger.exception("Error in /admin_setup", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @admin_player.error
    async def admin_player_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need Administrator permissions to use this command.",
                ephemeral=True
            )
        else:
            flogger.exception("Error in /admin_player", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    flogger.debug("Setting up AdminCog...")
    await bot.add_cog(AdminCog(bot))
    flogger.info("AdminCog loaded")
