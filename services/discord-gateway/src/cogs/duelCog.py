import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger

# Set up logger
flogger = bblogger.get_logger("discord-gateway-DuelCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"duelCog loading with API_BASE_URL: {api_base}")


class DuelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient()
        flogger.debug("DuelCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def pending_duel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Live autocomplete for pending duels where the user is the target."""
        try:
            resp = await self.http_client.get(
                f"{api_base}/duels/pending",
                params={"user_id": interaction.user.id, "guild_id": interaction.guild_id},
                timeout=5,
            )
            resp.raise_for_status()
            duels = resp.json()
            choices = []
            for d in duels:
                duel_id = d["id"]
                stakes = d.get("stakes", 0)
                label = f"Duel #{duel_id} — {stakes:,}cr stakes" if stakes else f"Duel #{duel_id} — friendly duel"
                if current.lower() in label.lower():
                    choices.append(
                        app_commands.Choice(name=label[:100], value=str(duel_id))
                    )
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /duel-challenge <target> [stakes]
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-challenge", description="Challenge another player to a duel")
    @app_commands.describe(
        target="The player to challenge",
        stakes="Credits to wager (default: 0 for a friendly duel)",
    )
    async def duel_challenge(
        self,
        interaction: discord.Interaction,
        target: discord.User,
        stakes: int = 0,
    ):
        """Challenge a player to a duel."""
        await interaction.response.defer(thinking=True)

        try:
            resp = await self.http_client.post(
                f"{api_base}/duels/challenge",
                json={
                    "challenger_id": interaction.user.id,
                    "target_id": target.id,
                    "stakes": stakes,
                    "guild_id": interaction.guild_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = self._build_challenge_embed(interaction.user, target, data, stakes)
            await interaction.followup.send(embed=embed)
            flogger.debug(
                f"/duel-challenge by {interaction.user} → {target} stakes={stakes}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /duel-challenge: {e}")
            await interaction.followup.send(
                "⚠️ An error occurred while creating the duel challenge.", ephemeral=True
            )

    def _build_challenge_embed(
        self,
        challenger: discord.User,
        target: discord.User,
        data: dict,
        stakes: int,
    ) -> discord.Embed:
        """Build an embed for a successful duel challenge."""
        duel_id = data.get("id", "?")
        stakes_str = f"**{stakes:,}** credits" if stakes else "**Friendly duel** (no stakes)"

        embed = discord.Embed(
            title="⚔️ Duel Challenge Issued!",
            description=(
                f"{challenger.mention} has challenged {target.mention} to a duel!\n\n"
                f"**Stakes:** {stakes_str}\n"
                f"**Duel ID:** #{duel_id}"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="📋 Instructions",
            value=(
                f"{target.mention}: Use `/duel-accept` to accept or `/duel-reject` to decline.\n"
                "Challenge expires in **24 hours**."
            ),
            inline=False,
        )
        return embed

    @duel_challenge.error
    async def duel_challenge_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        flogger.exception("Error in /duel-challenge", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /duel-accept <duel>
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-accept", description="Accept a pending duel challenge")
    @app_commands.describe(duel="Select a pending duel challenge to accept")
    @app_commands.autocomplete(duel=pending_duel_autocomplete)
    async def duel_accept(self, interaction: discord.Interaction, duel: str):
        """Accept a pending duel challenge and resolve combat."""
        await interaction.response.defer(thinking=True)

        try:
            duel_id = int(duel)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid duel selection. Please select from the dropdown.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.post(
                f"{api_base}/duels/{duel_id}/accept",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = self._build_accept_embed(duel_id, data)
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/duel-accept duel_id={duel_id} by {interaction.user}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Duel not found.", ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /duel-accept: {e}")
            await interaction.followup.send(
                "⚠️ An error occurred while accepting the duel.", ephemeral=True
            )

    def _build_accept_embed(self, duel_id: int, data: dict) -> discord.Embed:
        """Build an embed for a completed duel (accept result)."""
        is_stalemate = data.get("is_stalemate", False)
        credits_transferred = data.get("credits_transferred", 0)
        stakes = data.get("stakes", 0)

        if is_stalemate:
            embed = discord.Embed(
                title="⚔️ Duel Complete — Stalemate!",
                description=(
                    f"**Duel #{duel_id}** ended in a stalemate!\n\n"
                    "Neither combatant could overcome the other.\n"
                    "No credits were transferred."
                ),
                color=discord.Color.yellow(),
            )
        else:
            winner_name = data.get("winner_name", "Unknown")
            loser_name = data.get("loser_name", "Unknown")
            challenger_id = data.get("challenger_id")
            challenger_credits = data.get("challenger_credits", 0)
            target_id = data.get("target_id")
            target_credits = data.get("target_credits", 0)

            embed = discord.Embed(
                title="⚔️ Duel Complete — Victory!",
                description=(
                    f"**Duel #{duel_id}** has been resolved!\n\n"
                    f"🏆 **Winner:** {winner_name}\n"
                    f"💀 **Loser:** {loser_name}\n"
                    f"💰 **Credits transferred:** {credits_transferred:,}"
                ),
                color=discord.Color.green(),
            )
            if stakes:
                embed.add_field(
                    name="💳 Final Balances",
                    value=(
                        f"Player <@{challenger_id}>: **{challenger_credits:,}** cr\n"
                        f"Player <@{target_id}>: **{target_credits:,}** cr"
                    ),
                    inline=False,
                )

        return embed

    @duel_accept.error
    async def duel_accept_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        flogger.exception("Error in /duel-accept", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /duel-reject <duel>
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-reject", description="Reject a pending duel challenge")
    @app_commands.describe(duel="Select a pending duel challenge to reject")
    @app_commands.autocomplete(duel=pending_duel_autocomplete)
    async def duel_reject(self, interaction: discord.Interaction, duel: str):
        """Reject a pending duel challenge."""
        await interaction.response.defer(thinking=True)

        try:
            duel_id = int(duel)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid duel selection. Please select from the dropdown.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.post(
                f"{api_base}/duels/{duel_id}/reject",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = discord.Embed(
                title="🚫 Duel Rejected",
                description=(
                    f"**Duel #{duel_id}** has been rejected.\n"
                    "The challenge has been declined."
                ),
                color=discord.Color.red(),
            )
            if data.get("challenger_id"):
                embed.add_field(
                    name="Details",
                    value=f"Challenger: <@{data['challenger_id']}>",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/duel-reject duel_id={duel_id} by {interaction.user}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Duel not found.", ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /duel-reject: {e}")
            await interaction.followup.send(
                "⚠️ An error occurred while rejecting the duel.", ephemeral=True
            )

    @duel_reject.error
    async def duel_reject_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        flogger.exception("Error in /duel-reject", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up DuelCog...")
    await bot.add_cog(DuelCog(bot))
    flogger.info("DuelCog loaded")
