import os

import discord
import httpx
from cogs._shared.http_error_handler import report_api_error
from cogs._shared.loadout_embed import build_loadout_embed, build_loadout_error_embed
from cogs.adminCog import _check_is_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.timestamp_utils import iso_to_discord_ts

# Set up logger
flogger = bblogger.get_logger("discord-gateway-PlayerCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"playerCog loading with API_BASE_URL: {api_base}")

# Message shown when the guild hasn't been set up via /admin_setup
_GUILD_NOT_CONFIGURED_MSG = (
    "⚠️ This server hasn't been set up yet. An admin must run `/admin_setup` "
    "to initialize BountyBot before you can use this command."
)


def _is_guild_not_configured(exc: httpx.HTTPStatusError) -> bool:
    """Return True if the HTTPStatusError is a 'guild not configured' 400 response."""
    if exc.response.status_code != 400:
        return False
    try:
        detail = exc.response.json().get("detail", "")
        return "not configured" in detail.lower() or "admin_setup" in detail.lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return False


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
        await self._display_profile(interaction)

    @app_commands.command(name="register", description="Register as a player (alias of /profile)")
    async def register(self, interaction: discord.Interaction):
        """Alias for /profile — registers the player and displays their profile embed."""
        flogger.info(f"/register: guild={interaction.guild_id}, user={interaction.user.id}")
        await self._display_profile(interaction)

    async def _display_profile(self, interaction: discord.Interaction) -> None:
        """Shared handler for /profile and /register.

        Both public commands are full behavioural aliases — identical side
        effects (player upsert, role assignment) and identical embed output.
        Only the slash-command name differs, and each wrapper logs its own
        name for usage-frequency analysis.
        """
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
            embed.add_field(name="Joined", value=iso_to_discord_ts(player_data["created_at"], "D"), inline=True)
            embed.set_footer(text=f"Player ID: {player_data['id']}")

            # Fetch promotion status (non-fatal enhancement)
            try:
                promo_resp = await self.http_client.get(
                    f"{api_base}/players/{player_data['id']}/promotion-status", timeout=5
                )
                promo_resp.raise_for_status()
                promo_status = promo_resp.json()

                if promo_status.get("can_promote") and promo_status.get("next_tier"):
                    embed.add_field(
                        name="Promotion",
                        value=f"⬆️ **Eligible for {promo_status['next_tier']}!** Use `/promote`",
                        inline=False,
                    )
                elif promo_status.get("next_tier"):
                    threshold = promo_status.get("xp_threshold_for_next", 0)
                    embed.add_field(
                        name="Next Tier",
                        value=f"{promo_status['next_tier']} ({threshold:,} XP needed)",
                        inline=False,
                    )
                else:
                    embed.add_field(name="Tier", value="🏆 Maximum Tier", inline=False)
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # Promotion status is non-fatal

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/profile success: guild={interaction.guild_id}, user={interaction.user.id}")

            # Attempt to assign the Bounty Hunter role + tier role (non-fatal)
            try:
                config_resp = await self.http_client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=5)
                config_resp.raise_for_status()
                config = config_resp.json()
                guild = interaction.guild
                roles_to_add: list[discord.Role] = []

                # General Bounty Hunter role
                bh_role_id = config.get("bounty_hunter_role_id")
                if bh_role_id:
                    role = guild.get_role(bh_role_id)
                    if role and role not in interaction.user.roles:
                        roles_to_add.append(role)

                # Tier-specific role based on the player's current tier
                player_tier = (player_data.get("tier") or "Bronze").lower()
                tier_role_key = f"{player_tier}_role_id"
                tier_role_id = config.get(tier_role_key)
                if tier_role_id:
                    tier_role = guild.get_role(tier_role_id)
                    if tier_role and tier_role not in interaction.user.roles:
                        roles_to_add.append(tier_role)

                if roles_to_add:
                    await interaction.user.add_roles(*roles_to_add, reason="BountyBot player registration")
                    role_names = ", ".join(r.name for r in roles_to_add)
                    flogger.info(
                        f"Assigned roles [{role_names}] to user {interaction.user.id} in guild {interaction.guild_id}"
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"Failed to assign roles: guild={interaction.guild_id}, user={interaction.user.id}, error={e}"
                )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/profile HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send("❌ Player profile not found.", ephemeral=True)
            else:
                await report_api_error(interaction, e)
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
                msg = (
                    f"📭 No {tier}-tier players found in this guild." if tier else "📭 No players found in this guild."
                )
                await interaction.followup.send(msg, ephemeral=True)
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
            await report_api_error(interaction, e)
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
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Level too low to prestige.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Level too low to prestige."
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/prestige error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="promote", description="Promote to the next tier")
    async def promote(self, interaction: discord.Interaction):
        """Promote player to the next tier if eligible."""
        flogger.info(f"/promote: guild={interaction.guild_id}, user={interaction.user.id}")
        await interaction.response.defer(thinking=True)

        try:
            # Resolve player (create if not exists)
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": str(interaction.user),
            }

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=10)
            resp.raise_for_status()
            player_data = resp.json()

            # Attempt promotion
            promote_resp = await self.http_client.put(f"{api_base}/players/{player_data['id']}/promote", timeout=10)
            promote_resp.raise_for_status()
            promote_data = promote_resp.json()

            new_tier = promote_data["new_tier"]
            old_tier = promote_data["old_tier"]

            embed = discord.Embed(
                title="⬆️ Tier Promoted!",
                description=f"You have advanced from **{old_tier}** to **{new_tier}**!",
                color=self._get_tier_color(new_tier),
            )
            embed.add_field(name="New Tier", value=f"**{new_tier}**", inline=True)
            embed.add_field(name="XP", value=f"{promote_data['xp']:,}", inline=True)

            if promote_data.get("eligible_for_next") and promote_data.get("next_tier"):
                embed.add_field(
                    name="Next Promotion",
                    value=f"⬆️ Eligible for **{promote_data['next_tier']}**! Use `/promote` again.",
                    inline=False,
                )
            elif promote_data.get("next_tier"):
                embed.add_field(
                    name="Next Promotion",
                    value=f"Keep earning XP to reach **{promote_data['next_tier']}**!",
                    inline=False,
                )
            else:
                embed.add_field(name="Next Promotion", value="🏆 Maximum tier reached! Use `/prestige`.", inline=False)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/promote success: guild={interaction.guild_id}, user={interaction.user.id}, {old_tier} -> {new_tier}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/promote HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Cannot promote at this time.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Cannot promote at this time."
                # Show error embed with current tier color if we know it
                embed = discord.Embed(
                    title="❌ Cannot Promote",
                    description=detail,
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/promote error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="loadout", description="View an active ship loadout")
    @app_commands.describe(
        player="Player to view loadout for (default: yourself)",
        public="Make the response visible to everyone (default: False — only you see it)",
    )
    async def loadout(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
        public: bool = False,
    ):
        """Display the active ship loadout for a player using the shared embed builder."""
        target = player or interaction.user
        flogger.info(
            f"/loadout: guild={interaction.guild_id}, user={interaction.user.id}, target={target.id}, public={public}"
        )
        # Errors always stay ephemeral regardless of `public`; success follows `public`.
        await interaction.response.defer(thinking=True, ephemeral=not public)

        try:
            # 1) Resolve target Discord user → bot-core player_id.
            user_data = {
                "discord_id": target.id,
                "guild_id": interaction.guild_id,
                "discord_username": None,  # preserve existing username; only /profile updates it
            }
            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=10)
            resp.raise_for_status()
            player_id = resp.json()["id"]

            # 2) Determine cargo visibility (self-view OR admin viewing another).
            is_self_view = player is None or player.id == interaction.user.id
            show_cargo = is_self_view or (not is_self_view and await _check_is_admin(interaction))

            # 3) Fetch unified LoadoutResponse.
            params = {
                "include_cargo": "true" if show_cargo else "false",
                "viewer_discord_id": str(target.id),
            }
            loadout_resp = await self.http_client.get(
                f"{api_base}/players/{player_id}/loadout", params=params, timeout=10
            )
            loadout_resp.raise_for_status()
            data = loadout_resp.json()

            # 4) Override subject_name with the live Discord display_name so renames
            #    show up immediately (bot-core only knows the username at last /profile).
            data["subject_name"] = target.display_name
            data["subject_mention"] = f"<@{target.id}>"

            # 5) Build embed (handles message / no-active-ship internally → red error embed).
            embed = build_loadout_embed(data, viewer_is_owner_or_admin=show_cargo)

            # Errors (response.message set) must always be ephemeral, regardless of `public`.
            if data.get("message"):
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            await interaction.followup.send(embed=embed, ephemeral=not public)
            flogger.info(
                f"/loadout success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"target={target.id}, public={public}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/loadout HTTP error: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"status={e.response.status_code}"
            )
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send(
                    embed=build_loadout_error_embed(
                        title=f"Loadout — {target.display_name}",
                        description="Player not found.",
                    ),
                    ephemeral=True,
                )
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/loadout error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching the loadout.", ephemeral=True)

    @loadout.error
    async def loadout_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /loadout", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @app_commands.command(name="unregister", description="Remove your Bounty Hunter role (keeps your player data)")
    async def unregister(self, interaction: discord.Interaction):
        """Remove the Bounty Hunter role(s) from the user. Does NOT delete player data."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            config_resp = await self.http_client.get(f"{api_base}/config/guild/{interaction.guild_id}", timeout=5)
            config_resp.raise_for_status()
            config = config_resp.json()

            bh_role_id = config.get("bounty_hunter_role_id")
            if not bh_role_id:
                await interaction.followup.send("⚠️ No Bounty Hunter role is configured for this guild.", ephemeral=True)
                return

            guild = interaction.guild
            role = guild.get_role(bh_role_id)

            if not role:
                await interaction.followup.send("⚠️ Bounty Hunter role not found in this guild.", ephemeral=True)
                return

            # Collect all BH-related role IDs from config (generic + 4 tier roles)
            tier_role_ids: list[int] = [
                rid
                for rid in [
                    config.get("bronze_role_id"),
                    config.get("silver_role_id"),
                    config.get("gold_role_id"),
                    config.get("platinum_role_id"),
                ]
                if rid is not None
            ]

            # Build list of roles the user actually has
            roles_to_remove: list[discord.Role] = []
            if role in interaction.user.roles:
                roles_to_remove.append(role)
            for tier_id in tier_role_ids:
                tier_role = guild.get_role(tier_id)
                if tier_role is not None and tier_role in interaction.user.roles:
                    roles_to_remove.append(tier_role)

            if not roles_to_remove:
                await interaction.followup.send(
                    "ℹ️ You don't have the Bounty Hunter role.",
                    ephemeral=True,
                )
                return

            await interaction.user.remove_roles(*roles_to_remove, reason="Player unregistered from BountyBot")
            removed_names = ", ".join(f"@{r.name}" for r in roles_to_remove)
            await interaction.followup.send(
                f"✅ Bounty Hunter role(s) removed: {removed_names}. "
                "Your player data is preserved — use `/profile` to re-register anytime.",
                ephemeral=True,
            )
            removed_ids = [r.id for r in roles_to_remove]
            flogger.info(
                f"/unregister: removed roles {removed_ids} from user {interaction.user.id} "
                f"in guild {interaction.guild_id}"
            )

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            flogger.error(
                f"/unregister HTTP error: guild={interaction.guild_id}, "
                f"user={interaction.user.id}, status={e.response.status_code}"
            )
            await interaction.followup.send("⚠️ An error occurred while removing the role.", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/unregister error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred while removing the role.", ephemeral=True)

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

    @register.error
    async def register_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /register", exc_info=error)
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

    @promote.error
    async def promote_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /promote", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @unregister.error
    async def unregister_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /unregister", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up PlayerCog...")
    await bot.add_cog(PlayerCog(bot))
    flogger.info("PlayerCog loaded")
