import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.timestamp_utils import iso_to_discord_ts

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
            resp = await self.http_client.get(f"{api_base}/about/categories/system/objects", timeout=10)
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

    async def _get_player_id(self, user_id: int, guild_id: int) -> int | None:
        """Resolve a Discord user ID to a game player ID via the upsert endpoint."""
        try:
            user_data = {"discord_id": user_id, "guild_id": guild_id, "discord_username": "temp"}
            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception:  # pylint: disable=broad-exception-caught
            return None

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
            app_commands.Choice(name=name, value=name) for name in self._systems if current.lower() in name.lower()
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
                    f"{b['criminal_name']} ({b['division'].title()}, T{b.get('tech_level', '?')}) — {b['reward']:,}cr"
                )
                if current.lower() in label.lower():
                    choices.append(app_commands.Choice(name=label[:100], value=str(b["id"])))
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
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if player_id is None:
                await interaction.followup.send("❌ Player not found. Use `/profile` first.", ephemeral=True)
                return

            resp = await self.http_client.post(
                f"{api_base}/bounties/check",
                json={"player_id": player_id, "system_name": system},
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

            # Handle on_cooldown as ephemeral message (not an embed)
            if result == "on_cooldown":
                await interaction.followup.send(f"⏱️ {message}", ephemeral=True)
                return

            embed = self._build_check_embed(result, system, message)
            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/check success: guild={interaction.guild_id} user={interaction.user.id}"
                f" system={system} result={result}"
            )

            # If the player's tier changed, update their Discord role (non-fatal)
            new_tier = data.get("new_tier")
            if new_tier:
                try:
                    config_resp = await self.http_client.get(
                        f"{api_base}/config/guild/{interaction.guild_id}", timeout=5
                    )
                    config_resp.raise_for_status()
                    config = config_resp.json()
                    guild = interaction.guild
                    new_role_id = config.get(f"{new_tier.lower()}_role_id")
                    if new_role_id:
                        new_role = guild.get_role(new_role_id)
                        if new_role and new_role not in interaction.user.roles:
                            # Remove old tier roles, add new one
                            old_tier_roles = []
                            for tier_key in ("bronze_role_id", "silver_role_id", "gold_role_id"):
                                rid = config.get(tier_key)
                                if rid:
                                    old_role = guild.get_role(rid)
                                    if old_role and old_role in interaction.user.roles and old_role != new_role:
                                        old_tier_roles.append(old_role)
                            if old_tier_roles:
                                await interaction.user.remove_roles(*old_tier_roles, reason="BountyBot tier change")
                            await interaction.user.add_roles(new_role, reason=f"BountyBot promoted to {new_tier}")
                            flogger.info(
                                f"Updated tier role for user {interaction.user.id}: {new_tier} "
                                f"in guild {interaction.guild_id}"
                            )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    flogger.warning(f"Failed to update tier role: {e}")

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
            await interaction.followup.send("⚠️ An error occurred while checking the system.", ephemeral=True)

    @staticmethod
    def _format_loadout_item(item) -> str:
        """Format a loadout item dict as 'emoji name' or '• name'."""
        if isinstance(item, str):
            return f"• {item}"
        name = item.get("name", str(item))
        emoji = item.get("emoji") or ""
        return f"{emoji} {name}" if emoji else f"• {name}"

    def _build_check_embed(self, result: str, system: str, message: str) -> discord.Embed:
        """Build an embed for the /check command based on result."""
        if result == "correct":
            embed = discord.Embed(
                title="🎯 Bounty Found!",
                description=f"**{system}** — Bounty found! Combat initiated!",
                color=discord.Color.green(),
            )
            if message:
                embed.add_field(name="Result", value=message, inline=False)
        elif result == "incorrect":
            embed = discord.Embed(
                title="❌ System Checked",
                description=f"**{system}** — System checked, bounty not here.",
                color=discord.Color.red(),
            )
            if message:
                embed.add_field(name="Intel", value=message, inline=False)
        elif result == "already_checked":
            embed = discord.Embed(
                title="🔁 Already Checked",
                description=f"**{system}** — This system has already been checked.",
                color=discord.Color.yellow(),
            )
            if message:
                embed.add_field(name="Note", value=message, inline=False)
        else:  # not_found or unknown
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
                # Only count systems actually checked (value != -1 means checked by a player)
                systems_checked = sum(1 for v in bounty.get("checked", {}).values() if v != -1)
                total_systems = len(bounty.get("route", []))
                end_time = bounty.get("end_time")
                time_str = ""
                if end_time:
                    time_str = f" | Expires {iso_to_discord_ts(end_time, 'R')}"

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
                f"/bounties success: guild={interaction.guild_id} user={interaction.user.id} count={len(bounty_list)}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/bounties API error: guild={interaction.guild_id} user={interaction.user.id}"
                f" status={e.response.status_code}"
            )
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/bounties error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching bounties.", ephemeral=True)

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
                    # A system is truly checked when its value != -1 (player_id assigned)
                    if checked.get(system_name, -1) != -1:
                        route_lines.append(f"{i}. ~~{system_name}~~ ✅")
                    else:
                        route_lines.append(f"{i}. {system_name}")
                embed.add_field(
                    name=f"Systems ({len(route_systems)} total)",
                    value="\n".join(route_lines),
                    inline=False,
                )

            # Only count systems actually checked (value != -1 means checked by a player)
            systems_checked = sum(1 for v in checked.values() if v != -1)
            embed.set_footer(text=f"{systems_checked}/{len(route_systems)} systems checked")

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
                f"/route error: guild={interaction.guild_id} user={interaction.user.id} bounty_id={bounty_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the route.", ephemeral=True)

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
            f"/criminal-loadout invoked: guild={interaction.guild_id} user={interaction.user.id} bounty={bounty}"
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

            ship_name = criminal_ship.get("ship_name", "Unknown Ship")
            ship_emoji = criminal_ship.get("ship_emoji") or ""
            ship_armour = criminal_ship.get("ship_armour", 0)
            weapons = criminal_ship.get("weapons", [])
            modules = criminal_ship.get("modules", [])
            turrets = criminal_ship.get("turrets", [])

            total_dps = sum(w.get("dps", 0) for w in weapons) + sum(t.get("dps", 0) for t in turrets)
            rounded_dps = round(total_dps, 1)
            dps_str = str(int(rounded_dps)) if rounded_dps == int(rounded_dps) else f"{rounded_dps:.1f}"

            ship_display = f"{ship_emoji} {ship_name}" if ship_emoji else ship_name

            embed = discord.Embed(
                title=f"🚀 Loadout — {criminal_name}",
                description=f"Bounty #{bounty_id} | Tech Level: **T{tech_level}**",
                color=discord.Color.dark_red(),
            )

            embed.add_field(
                name="🛸 Ship", value=f"{ship_display}\nHP: **{ship_armour}** | DPS: **{dps_str}**", inline=False
            )

            if weapons:
                weapons_str = "\n".join(self._format_loadout_item(w) for w in weapons)
                embed.add_field(name="🔫 Primary Weapons", value=weapons_str, inline=False)

            if turrets:
                turrets_str = "\n".join(self._format_loadout_item(t) for t in turrets)
                embed.add_field(name="🔫 Turrets", value=turrets_str, inline=False)

            if modules:
                modules_str = "\n".join(self._format_loadout_item(m) for m in modules)
                embed.add_field(name="⚙️ Modules", value=modules_str, inline=False)

            if not weapons and not turrets and not modules:
                embed.add_field(name="Equipment", value="*No equipment*", inline=False)

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
            await interaction.followup.send("⚠️ An error occurred while fetching the loadout.", ephemeral=True)

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
