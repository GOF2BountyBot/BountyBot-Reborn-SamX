import os
from datetime import UTC, datetime

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger

# Set up logger
flogger = bblogger.get_logger("discord-gateway-BountyCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"bountyCog loading with API_BASE_URL: {api_base}")

_VALID_DIVISIONS = ["bronze", "silver", "gold"]


class BountyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._systems: list[str] = []

        # Preload system data at startup
        bot.loop.create_task(self._preload_data())
        flogger.debug("BountyCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_data(self):
        """Preload star system names at startup for autocomplete."""
        await self.bot.wait_until_ready()
        try:
            flogger.info("BountyCog: Starting preload of system data...")
            resp = await self.http_client.get(
                f"{api_base}/about/categories/system/objects", timeout=10
            )
            resp.raise_for_status()
            systems = resp.json()
            self._systems = [s.get("name", "") for s in systems if s.get("name")]
            flogger.info(f"BountyCog: Preloaded {len(self._systems)} system names")
        except httpx.TimeoutException as e:
            flogger.warning(f"BountyCog: Timeout preloading systems: {e}")
            self._systems = []
        except httpx.HTTPStatusError as e:
            flogger.warning(f"BountyCog: HTTP error preloading systems: {e.response.status_code}")
            self._systems = []
        except httpx.RequestError as e:
            flogger.warning(f"BountyCog: Request error preloading systems: {e}")
            self._systems = []
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"BountyCog: Unexpected error preloading systems: {e}")
            self._systems = []

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def division_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for division selection."""
        return [
            app_commands.Choice(name=div.title(), value=div)
            for div in _VALID_DIVISIONS
            if current.lower() in div.lower()
        ]

    async def system_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for star system names — includes ALL systems (game balance)."""
        return [
            app_commands.Choice(name=name, value=name)
            for name in self._systems
            if current.lower() in name.lower()
        ][:25]

    async def bounty_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Live autocomplete for active bounties — fetches current bounties."""
        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/",
                params={"guild_id": interaction.guild_id},
                timeout=5,
            )
            resp.raise_for_status()
            bounties = resp.json()
            choices = []
            for b in bounties:
                label = (
                    f"{b['criminal_name']} ({b['division'].title()}, T{b.get('tech_level', '?')})"
                    f" — {b['reward']:,}cr"
                )
                if current.lower() in label.lower():
                    choices.append(
                        app_commands.Choice(name=label[:100], value=str(b["id"]))
                    )
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /check <system>
    # ------------------------------------------------------------------

    @app_commands.command(name="check", description="Check a star system for a bounty target")
    @app_commands.describe(system="The star system name to check")
    @app_commands.autocomplete(system=system_autocomplete)
    async def check(self, interaction: discord.Interaction, system: str):
        """Check a system for bounties."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/check invoked: guild={interaction.guild_id} user={interaction.user.id} system={system}")

        try:
            resp = await self.http_client.post(
                f"{api_base}/bounties/check",
                json={"player_id": interaction.user.id, "system_name": system},
                params={"guild_id": interaction.guild_id},
                timeout=10,
            )

            if resp.status_code == 429:
                await interaction.followup.send(
                    "⏱️ You are on cooldown. Please wait before checking again.",
                    ephemeral=True,
                )
                return

            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", "")
            message = data.get("message", "")

            embed = self._build_check_embed(result, system, message)
            await interaction.followup.send(embed=embed)
            flogger.info(
            f"/check success: guild={interaction.guild_id} user={interaction.user.id}"
            f" system={system} result={result}"
        )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                await interaction.followup.send(
                    "⏱️ You are on cooldown. Please wait before checking again.",
                    ephemeral=True,
                )
            else:
                flogger.error(
                    f"/check API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/check error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send(
                "⚠️ An error occurred while checking the system.", ephemeral=True
            )

    def _build_check_embed(self, result: str, system: str, message: str) -> discord.Embed:
        """Build an embed for the /check command based on result."""
        if result == "CORRECT":
            embed = discord.Embed(
                title="🎯 Bounty Found!",
                description=f"**{system}** — Bounty found! Combat initiated!",
                color=discord.Color.green(),
            )
            if message:
                embed.add_field(name="Result", value=message, inline=False)
        elif result == "INCORRECT":
            embed = discord.Embed(
                title="❌ System Checked",
                description=f"**{system}** — System checked, bounty not here.",
                color=discord.Color.red(),
            )
            if message:
                embed.add_field(name="Intel", value=message, inline=False)
        elif result == "ALREADY_CHECKED":
            embed = discord.Embed(
                title="🔁 Already Checked",
                description=f"**{system}** — This system has already been checked.",
                color=discord.Color.yellow(),
            )
            if message:
                embed.add_field(name="Note", value=message, inline=False)
        else:  # NOT_FOUND or unknown
            embed = discord.Embed(
                title="🔍 No Bounty",
                description=f"**{system}** — No bounty found at this system.",
                color=discord.Color.orange(),
            )
            if message:
                embed.add_field(name="Note", value=message, inline=False)
        return embed

    @check.error
    async def check_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /check", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /bounties [division]
    # ------------------------------------------------------------------

    @app_commands.command(name="bounties", description="List active bounties in this guild")
    @app_commands.describe(division="Filter by division (bronze, silver, gold)")
    @app_commands.autocomplete(division=division_autocomplete)
    async def bounties(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
    ):
        """List active bounties."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/bounties invoked: guild={interaction.guild_id} user={interaction.user.id} division={division}")

        try:
            params: dict = {"guild_id": interaction.guild_id}
            if division:
                params["division"] = division

            resp = await self.http_client.get(
                f"{api_base}/bounties/",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            bounty_list = resp.json()

            if not bounty_list:
                title = "📋 Active Bounties"
                if division:
                    title += f" — {division.title()}"
                embed = discord.Embed(
                    title=title,
                    description="No active bounties at this time.",
                    color=discord.Color.light_grey(),
                )
                await interaction.followup.send(embed=embed)
                return

            title = "📋 Active Bounties"
            if division:
                title += f" — {division.title()}"

            embed = discord.Embed(
                title=title,
                description=f"**{len(bounty_list)}** active bounty(s)",
                color=discord.Color.blue(),
            )

            for bounty in bounty_list:
                systems_checked = len(bounty.get("checked", {}))
                total_systems = len(bounty.get("route", []))
                end_time = bounty.get("end_time")
                time_str = ""
                if end_time:
                    try:
                        dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                        now = datetime.now(tz=UTC)
                        remaining = dt - now
                        secs = int(remaining.total_seconds())
                        if secs > 0:
                            hours, rem = divmod(secs, 3600)
                            mins, _ = divmod(rem, 60)
                            time_str = f" | ⏰ {hours}h {mins}m"
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass

                faction = bounty.get("criminal_faction") or ""
                faction_str = f" ({faction})" if faction else ""

                embed.add_field(
                    name=f"🎯 {bounty['criminal_name']}{faction_str} — {bounty['division'].title()}",
                    value=(
                        f"💰 Reward: **{bounty['reward']:,}** cr"
                        f" (+{bounty['reward_per_sys']:,}/sys)\n"
                        f"🗺️ Systems: {systems_checked}/{total_systems} checked"
                        f"{time_str}\n"
                        f"🆔 Bounty ID: {bounty['id']}"
                    ),
                    inline=False,
                )

            embed.set_footer(text="Use /route <bounty_id> to see the route")
            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/bounties success: guild={interaction.guild_id} user={interaction.user.id}"
                f" count={len(bounty_list)}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/bounties API error: guild={interaction.guild_id} user={interaction.user.id}"
                f" status={e.response.status_code}"
            )
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/bounties error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send(
                "⚠️ An error occurred while fetching bounties.", ephemeral=True
            )

    @bounties.error
    async def bounties_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /bounties", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /route <bounty>
    # ------------------------------------------------------------------

    @app_commands.command(name="route", description="Show the route for a bounty")
    @app_commands.describe(bounty="Select a bounty or enter bounty ID")
    @app_commands.autocomplete(bounty=bounty_autocomplete)
    async def route(self, interaction: discord.Interaction, bounty: str):
        """Show bounty route."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/route invoked: guild={interaction.guild_id} user={interaction.user.id} bounty={bounty}")

        try:
            bounty_id = int(bounty)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid bounty selection. Please select from the dropdown or enter a numeric ID.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/{bounty_id}/route",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            criminal_name = data.get("criminal_name", "Unknown")
            route_systems = data.get("route", [])
            checked = data.get("checked", {})
            status = data.get("status", "active")

            embed = discord.Embed(
                title=f"🗺️ Route — {criminal_name}",
                description=f"Bounty #{bounty_id} | Status: **{status.title()}**",
                color=discord.Color.blurple(),
            )

            if not route_systems:
                embed.add_field(name="Route", value="No systems in route.", inline=False)
            else:
                route_lines = []
                for i, system_name in enumerate(route_systems, start=1):
                    if system_name in checked:
                        route_lines.append(f"{i}. ~~{system_name}~~ ✅")
                    else:
                        route_lines.append(f"{i}. {system_name}")
                embed.add_field(
                    name=f"Systems ({len(route_systems)} total)",
                    value="\n".join(route_lines),
                    inline=False,
                )

            systems_checked = len(checked)
            embed.set_footer(
                text=f"{systems_checked}/{len(route_systems)} systems checked"
            )

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/route success: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} systems={len(route_systems)}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Bounty not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/route API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" bounty_id={bounty_id} status={e.response.status_code}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/route error: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} error={e}"
            )
            await interaction.followup.send(
                "⚠️ An error occurred while fetching the route.", ephemeral=True
            )

    @route.error
    async def route_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /route", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /criminal-loadout <bounty>
    # ------------------------------------------------------------------

    @app_commands.command(name="criminal-loadout", description="Show the criminal's ship loadout for a bounty")
    @app_commands.describe(bounty="Select a bounty or enter bounty ID")
    @app_commands.autocomplete(bounty=bounty_autocomplete)
    async def criminal_loadout(self, interaction: discord.Interaction, bounty: str):
        """Show criminal loadout."""
        await interaction.response.defer(thinking=True)
        flogger.info(
            f"/criminal-loadout invoked: guild={interaction.guild_id} user={interaction.user.id}"
            f" bounty={bounty}"
        )

        try:
            bounty_id = int(bounty)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid bounty selection. Please select from the dropdown or enter a numeric ID.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/{bounty_id}/loadout",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            criminal_name = data.get("criminal_name", "Unknown")
            criminal_ship = data.get("criminal_ship") or {}
            tech_level = data.get("tech_level", 1)

            ship_name = criminal_ship.get("name", "Unknown Ship")
            weapons = criminal_ship.get("weapons", [])
            modules = criminal_ship.get("modules", [])

            embed = discord.Embed(
                title=f"🚀 Loadout — {criminal_name}",
                description=f"Bounty #{bounty_id} | Tech Level: **T{tech_level}**",
                color=discord.Color.dark_red(),
            )

            embed.add_field(name="🛸 Ship", value=ship_name, inline=False)

            if weapons:
                weapons_str = "\n".join(
                    f"• {w}" if isinstance(w, str) else f"• {w.get('name', str(w))}"
                    for w in weapons
                )
                embed.add_field(name="🔫 Weapons", value=weapons_str, inline=False)
            else:
                embed.add_field(name="🔫 Weapons", value="None", inline=False)

            if modules:
                modules_str = "\n".join(
                    f"• {m}" if isinstance(m, str) else f"• {m.get('name', str(m))}"
                    for m in modules
                )
                embed.add_field(name="⚙️ Modules", value=modules_str, inline=False)
            else:
                embed.add_field(name="⚙️ Modules", value="None", inline=False)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/criminal-loadout success: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} criminal={criminal_name}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Bounty not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/criminal-loadout API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" bounty_id={bounty_id} status={e.response.status_code}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/criminal-loadout error: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} error={e}"
            )
            await interaction.followup.send(
                "⚠️ An error occurred while fetching the loadout.", ephemeral=True
            )

    @criminal_loadout.error
    async def criminal_loadout_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        flogger.exception("Error in /criminal-loadout", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up BountyCog...")
    await bot.add_cog(BountyCog(bot))
    flogger.info("BountyCog loaded")
