import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger

# Set up logger
flogger = bblogger.get_logger("discord-gateway-PlayerCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"playerCog loading with API_BASE_URL: {api_base}")


class PlayerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("PlayerCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    @app_commands.command(name="profile", description="View your player profile and statistics")
    async def profile(self, interaction: discord.Interaction):
        """Display player profile with statistics."""
        flogger.info(f"/profile: guild={interaction.guild_id}, user={interaction.user.id}")
        await interaction.response.defer(thinking=True)

        try:
            # First ensure user/player exists
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": str(interaction.user),
            }

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=10)
            resp.raise_for_status()
            player_data = resp.json()

            # Get detailed statistics
            stats_resp = await self.http_client.get(f"{api_base}/players/{player_data['id']}/statistics", timeout=10)
            stats_resp.raise_for_status()
            stats = stats_resp.json()

            # Create profile embed
            embed = discord.Embed(
                title=f"🎮 {interaction.user.display_name}'s Profile", color=self._get_tier_color(player_data["tier"])
            )

            # Basic info
            embed.add_field(name="Tier", value=f"**{player_data['tier']}**", inline=True)
            embed.add_field(name="XP", value=f"{player_data['xp']:,}", inline=True)
            embed.add_field(name="Credits", value=f"{player_data['credits']:,}", inline=True)

            # Progression
            if player_data["prestige_count"] > 0:
                embed.add_field(name="Prestige", value=f"⭐ {player_data['prestige_count']}", inline=True)

            embed.add_field(name="Lifetime Credits", value=f"{player_data['lifetime_credits']:,}", inline=True)
            embed.add_field(name="Systems Checked", value=f"{player_data['systems_checked']:,}", inline=True)

            # Bounty stats
            bounty_stats = stats["bounty_stats"]
            embed.add_field(name="Bounty Wins", value=f"{bounty_stats['bounty_wins']}", inline=True)

            # Duel stats
            duel_stats = stats["duel_stats"]
            if duel_stats["wins"] > 0 or duel_stats["losses"] > 0:
                embed.add_field(
                    name="Duel Record", value=f"W: {duel_stats['wins']} | L: {duel_stats['losses']}", inline=True
                )
                embed.add_field(name="Duel Win Rate", value=f"{duel_stats['win_rate']}%", inline=True)

            # Set thumbnail based on tier
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Player ID: {player_data['id']} | Joined: {player_data['created_at'][:10]}")

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/profile success: guild={interaction.guild_id}, user={interaction.user.id}")

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/profile HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Player profile not found.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/profile error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching your profile.", ephemeral=True)

    @app_commands.command(name="leaderboard", description="View the guild leaderboard")
    @app_commands.describe(tier="Filter by specific tier")
    async def leaderboard(self, interaction: discord.Interaction, tier: str | None = None):
        """Display guild leaderboard."""
        flogger.info(f"/leaderboard: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/leaderboard params: guild={interaction.guild_id}, user={interaction.user.id}, tier={tier}")
        await interaction.response.defer(thinking=True)

        try:
            # Build URL with tier filter if provided
            url = f"{api_base}/players/guild/{interaction.guild_id}"
            params = {}
            if tier:
                params["tier"] = tier

            resp = await self.http_client.get(url, params=params, timeout=10)
            resp.raise_for_status()
            players = resp.json()

            if not players:
                await interaction.followup.send("📭 No players found in this guild.", ephemeral=True)
                return

            # Sort by XP descending
            players.sort(key=lambda p: p["xp"], reverse=True)

            # Create leaderboard embed
            title = "🏆 Guild Leaderboard"
            if tier:
                title += f" - {tier} Tier"

            embed = discord.Embed(title=title, color=discord.Color.gold())

            # Top 10 players
            leaderboard_text = ""
            for i, player in enumerate(players[:10]):
                rank_emoji = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                emoji = rank_emoji[i] if i < len(rank_emoji) else "🏅"

                # Get Discord user if possible
                try:
                    user = await self.bot.fetch_user(player["user_id"])
                    username = user.display_name
                except Exception:  # pylint: disable=broad-exception-caught
                    username = f"User {player['user_id']}"

                leaderboard_text += (
                    f"{emoji} **{username}**\n"
                    f"    {player['tier']} | {player['xp']:,} XP | {player['credits']:,} Credits\n"
                )

            embed.description = leaderboard_text
            embed.set_footer(text=f"Showing top {min(10, len(players))} of {len(players)} players")

            await interaction.followup.send(embed=embed)
            flogger.debug(
                f"/leaderboard success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"players={len(players)}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/leaderboard HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/leaderboard error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching the leaderboard.", ephemeral=True)

    @app_commands.command(name="prestige", description="Prestige your character (Platinum tier only)")
    @app_commands.describe(confirm="Type CONFIRM to execute the prestige")
    async def prestige(self, interaction: discord.Interaction, confirm: str | None = None):
        """Prestige player character."""
        flogger.info(f"/prestige: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/prestige params: guild={interaction.guild_id}, user={interaction.user.id}, confirm={confirm}")
        await interaction.response.defer(thinking=True)

        try:
            # Get player data first
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": str(interaction.user),
            }

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=10)
            resp.raise_for_status()
            player_data = resp.json()

            if player_data["tier"] != "Platinum":
                flogger.debug(
                    f"/prestige rejected: guild={interaction.guild_id}, user={interaction.user.id}, "
                    f"tier={player_data['tier']}"
                )
                await interaction.followup.send("❌ You must be Platinum tier to prestige!", ephemeral=True)
                return

            # If confirmation not provided or incorrect, show warning embed
            if confirm != "CONFIRM":
                flogger.debug(
                    f"/prestige awaiting confirmation: guild={interaction.guild_id}, user={interaction.user.id}"
                )
                embed = discord.Embed(
                    title="⚠️ Prestige Confirmation",
                    description=(
                        "Prestiging will reset you to **Bronze tier** with **0 XP**, "
                        "but you'll keep your ships, credits, and gain a prestige star!\n\n"
                        "To confirm, run: `/prestige confirm:CONFIRM`"
                    ),
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Execute the prestige via API
            prestige_resp = await self.http_client.post(f"{api_base}/players/{player_data['id']}/prestige", timeout=10)
            prestige_resp.raise_for_status()
            prestige_data = prestige_resp.json()

            embed = discord.Embed(
                title="⭐ Prestige Complete!",
                description=(
                    f"Congratulations! You have prestiged successfully.\n\n"
                    f"You are now back at **Bronze tier** with **0 XP**.\n"
                    f"Your prestige count is now **{prestige_data['prestige_count']}** ⭐"
                ),
                color=discord.Color.gold(),
            )
            embed.add_field(name="Previous Level", value=str(prestige_data["level_before"]), inline=True)
            embed.add_field(name="Prestige Stars", value=str(prestige_data["prestige_count"]), inline=True)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/prestige success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"prestige_count={prestige_data['prestige_count']}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/prestige HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Level too low to prestige.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Level too low to prestige."
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/prestige error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    def _get_tier_color(self, tier: str) -> discord.Color:
        """Get Discord color based on player tier."""
        tier_colors = {
            "Bronze": discord.Color.from_rgb(205, 127, 50),
            "Silver": discord.Color.from_rgb(192, 192, 192),
            "Gold": discord.Color.from_rgb(255, 215, 0),
            "Platinum": discord.Color.from_rgb(229, 228, 226),
        }
        return tier_colors.get(tier, discord.Color.default())

    @profile.error
    async def profile_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /profile", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @leaderboard.error
    async def leaderboard_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /leaderboard", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @prestige.error
    async def prestige_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /prestige", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up PlayerCog...")
    await bot.add_cog(PlayerCog(bot))
    flogger.info("PlayerCog loaded")
