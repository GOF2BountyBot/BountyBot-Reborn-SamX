import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.timestamp_utils import iso_to_discord_ts

# Set up logger
flogger = bblogger.get_logger("discord-gateway-ShipsCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"shipsCog loading with API_BASE_URL: {api_base}")


class ShipsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("ShipsCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _get_player_id(self, user_id: int, guild_id: int) -> int | None:
        """Helper to get player ID from Discord user ID."""
        try:
            user_data = {"discord_id": user_id, "guild_id": guild_id, "discord_username": "temp"}

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()["id"]
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    @app_commands.command(name="ships", description="View your ships and their loadouts")
    @app_commands.describe(user="View another user's ships (admin only)")
    async def ships(self, interaction: discord.Interaction, user: discord.User | None = None):
        """Display player ships."""
        flogger.info(f"/ships: guild={interaction.guild_id}, user={interaction.user.id}")
        await interaction.response.defer(thinking=True)

        try:
            # Determine target user
            target_user = user or interaction.user
            if user and user != interaction.user:
                # Require admin permission to view another user's ships
                from cogs.adminCog import _check_is_admin

                flogger.debug(f"/ships: checking admin permission for user={interaction.user.id}")
                if not await _check_is_admin(interaction):
                    flogger.debug(f"/ships: admin permission denied for user={interaction.user.id}")
                    await interaction.followup.send(
                        "❌ You need admin permissions to view another user's ships.", ephemeral=True
                    )
                    return
                flogger.debug(f"/ships: admin permission granted, viewing {target_user.id}'s ships")

            player_id = await self._get_player_id(target_user.id, interaction.guild_id)
            if not player_id:
                flogger.debug(f"/ships: player not found for discord_id={target_user.id}")
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Get player ships
            flogger.debug(f"/ships: fetching ships for player_id={player_id}")
            resp = await self.http_client.get(f"{api_base}/ships/player/{player_id}", timeout=10)
            resp.raise_for_status()
            ships = resp.json()
            flogger.debug(f"/ships: retrieved {len(ships)} ships for player_id={player_id}")

            if not ships:
                await interaction.followup.send(f"🚫 {target_user.display_name} has no ships.", ephemeral=True)
                return

            # Create ships embed
            embed = discord.Embed(
                title=f"🚢 {target_user.display_name}'s Ships",
                description=f"Total ships: {len(ships)}",
                color=discord.Color.green(),
            )

            # Sort ships - active first, then by name
            ships.sort(key=lambda x: (not x["is_active"], x["ship_name"]))

            for ship in ships[:10]:  # Limit to prevent embed size issues
                # Create ship info
                status = "🟢 **ACTIVE**" if ship["is_active"] else "⚪ Inactive"
                nickname = f' "{ship["nickname"]}"' if ship["nickname"] else ""

                # Count loadout
                weapons_count = len(ship["weapons"]) if ship["weapons"] else 0
                modules_count = len(ship["modules"]) if ship["modules"] else 0
                turrets_count = len(ship["turrets"]) if ship["turrets"] else 0

                loadout_summary = f"W:{weapons_count} | M:{modules_count} | T:{turrets_count}"

                ship_info = (
                    f"{status}{nickname}\nLoadout: {loadout_summary}\n"
                    f"Created: {iso_to_discord_ts(ship['created_at'], 'D')}"
                )

                embed.add_field(name=f"{ship['ship_name']} (ID: {ship['id']})", value=ship_info, inline=True)

            if len(ships) > 10:
                embed.set_footer(text=f"Showing first 10 of {len(ships)} ships. Use /ship <id> for details.")
            else:
                embed.set_footer(text="Use /ship <id> for detailed loadout information.")

            embed.set_thumbnail(url=target_user.display_avatar.url)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/ships success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"target_user={target_user.id}, ships_count={len(ships)}"
            )

        except httpx.HTTPStatusError as e:
            flogger.error(
                f"/ships API error: status={e.response.status_code}, guild={interaction.guild_id}, "
                f"user={interaction.user.id}, error={e}"
            )
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/ships failed: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching ships.", ephemeral=True)

    @app_commands.command(name="ship", description="View detailed information about a specific ship")
    @app_commands.describe(ship_id="ID of the ship to view")
    async def ship(self, interaction: discord.Interaction, ship_id: int):
        """Display detailed ship information."""
        flogger.info(f"/ship: guild={interaction.guild_id}, user={interaction.user.id}, ship_id={ship_id}")
        await interaction.response.defer(thinking=True)

        try:
            # Get ship details
            flogger.debug(f"/ship: fetching ship_id={ship_id}")
            resp = await self.http_client.get(f"{api_base}/ships/{ship_id}", timeout=10)
            resp.raise_for_status()
            ship = resp.json()
            flogger.debug(f"/ship: retrieved ship_id={ship_id}, ship_name={ship.get('ship_name')}")

            # Verify ship belongs to user (basic security)
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if ship["player_id"] != player_id:
                flogger.debug(f"/ship: ownership check failed for ship_id={ship_id}, user={interaction.user.id}")
                await interaction.followup.send("❌ You don't own this ship.", ephemeral=True)
                return

            # Get detailed loadout
            flogger.debug(f"/ship: fetching loadout for ship_id={ship_id}")
            loadout_resp = await self.http_client.get(f"{api_base}/ships/{ship_id}/loadout", timeout=10)
            loadout_resp.raise_for_status()
            loadout = loadout_resp.json()
            flogger.debug(
                f"/ship: loadout retrieved - weapons={loadout.get('weapons_count')}, "
                f"modules={loadout.get('modules_count')}, turrets={loadout.get('turrets_count')}"
            )

            # Create detailed ship embed
            status_emoji = "🟢" if ship["is_active"] else "⚪"
            title = f"{status_emoji} {ship['ship_name']}"
            if ship["nickname"]:
                title += f' "{ship["nickname"]}"'

            embed = discord.Embed(
                title=title,
                description=f"Ship ID: {ship['id']}" + (" | **ACTIVE SHIP**" if ship["is_active"] else ""),
                color=discord.Color.green() if ship["is_active"] else discord.Color.greyple(),
            )

            # Basic info
            embed.add_field(name="Type", value=ship["ship_name"], inline=True)
            embed.add_field(name="Status", value="Active" if ship["is_active"] else "Inactive", inline=True)
            embed.add_field(name="Created", value=iso_to_discord_ts(ship["created_at"], "D"), inline=True)

            # Loadout details
            if loadout["weapons"]:
                weapons_text = "\n".join(f"• {weapon}" for weapon in loadout["weapons"][:10])
                if len(loadout["weapons"]) > 10:
                    weapons_text += f"\n... and {len(loadout['weapons']) - 10} more"
                embed.add_field(
                    name=f"🔫 Weapons ({loadout['weapons_count']})", value=weapons_text or "None", inline=False
                )

            if loadout["modules"]:
                modules_text = "\n".join(f"• {module}" for module in loadout["modules"][:10])
                if len(loadout["modules"]) > 10:
                    modules_text += f"\n... and {len(loadout['modules']) - 10} more"
                embed.add_field(
                    name=f"⚙️ Modules ({loadout['modules_count']})", value=modules_text or "None", inline=False
                )

            if loadout["turrets"]:
                turrets_text = "\n".join(f"• {turret}" for turret in loadout["turrets"][:10])
                if len(loadout["turrets"]) > 10:
                    turrets_text += f"\n... and {len(loadout['turrets']) - 10} more"
                embed.add_field(
                    name=f"🎯 Turrets ({loadout['turrets_count']})", value=turrets_text or "None", inline=False
                )

            embed.set_footer(
                text="Use /setactive <ship_id> to set as active ship | /nickname <ship_id> <name> to set nickname"
            )

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/ship success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"ship_id={ship_id}, ship_name={ship.get('ship_name')}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                flogger.debug(f"/ship not found: ship_id={ship_id}, guild={interaction.guild_id}")
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/ship API error: status={e.response.status_code}, ship_id={ship_id}, "
                    f"guild={interaction.guild_id}, user={interaction.user.id}, error={e}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/ship failed: ship_id={ship_id}, guild={interaction.guild_id}, user={interaction.user.id}, error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching ship details.", ephemeral=True)

    @app_commands.command(name="setactive", description="Set a ship as your active ship")
    @app_commands.describe(ship_id="ID of the ship to set as active")
    async def setactive(self, interaction: discord.Interaction, ship_id: int):
        """Set active ship."""
        flogger.info(f"/setactive: guild={interaction.guild_id}, user={interaction.user.id}, ship_id={ship_id}")
        await interaction.response.defer(thinking=True)

        try:
            flogger.debug(f"/setactive: resolving player for user={interaction.user.id}")
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                flogger.debug(f"/setactive: player not found for user={interaction.user.id}")
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Set active ship
            flogger.debug(f"/setactive: setting ship_id={ship_id} as active for player_id={player_id}")
            resp = await self.http_client.put(
                f"{api_base}/ships/{ship_id}/set-active", params={"player_id": player_id}, timeout=10
            )
            resp.raise_for_status()
            ship = resp.json()
            flogger.debug(f"/setactive: ship set active - ship_id={ship_id}, ship_name={ship.get('ship_name')}")

            # Success message
            ship_name = ship["ship_name"]
            nickname = f' "{ship["nickname"]}"' if ship["nickname"] else ""

            embed = discord.Embed(
                title="✅ Active Ship Updated",
                description=f"**{ship_name}**{nickname} is now your active ship!",
                color=discord.Color.green(),
            )

            embed.add_field(name="Ship ID", value=str(ship["id"]), inline=True)
            embed.add_field(name="Status", value="🟢 Active", inline=True)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/setactive success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"ship_id={ship_id}, ship_name={ship.get('ship_name')}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                flogger.debug(f"/setactive validation failed: ship_id={ship_id}, guild={interaction.guild_id}")
                await interaction.followup.send("❌ Invalid ship or you don't own this ship.", ephemeral=True)
            elif e.response.status_code == 404:
                flogger.debug(f"/setactive not found: ship_id={ship_id}, guild={interaction.guild_id}")
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/setactive API error: status={e.response.status_code}, ship_id={ship_id}, "
                    f"guild={interaction.guild_id}, user={interaction.user.id}, error={e}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/setactive failed: ship_id={ship_id}, guild={interaction.guild_id}, "
                f"user={interaction.user.id}, error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while setting active ship.", ephemeral=True)

    @app_commands.command(name="nickname", description="Set a nickname for your ship")
    @app_commands.describe(
        ship_id="ID of the ship to nickname", nickname="New nickname for the ship (max 50 characters)"
    )
    async def nickname(self, interaction: discord.Interaction, ship_id: int, nickname: str):
        """Set ship nickname."""
        flogger.info(f"/nickname: guild={interaction.guild_id}, user={interaction.user.id}, ship_id={ship_id}")
        flogger.debug(f"/nickname params: nickname_length={len(nickname)}")
        await interaction.response.defer(thinking=True)

        try:
            # Validate nickname length
            if len(nickname) > 50:
                flogger.debug(f"/nickname validation failed: nickname_length={len(nickname)} exceeds max 50")
                await interaction.followup.send("❌ Nickname must be 50 characters or less.", ephemeral=True)
                return

            # First check if user owns the ship
            flogger.debug(f"/nickname: fetching ship_id={ship_id} for ownership check")
            resp = await self.http_client.get(f"{api_base}/ships/{ship_id}", timeout=10)
            resp.raise_for_status()
            ship = resp.json()

            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if ship["player_id"] != player_id:
                flogger.debug(f"/nickname: ownership check failed for ship_id={ship_id}, user={interaction.user.id}")
                await interaction.followup.send("❌ You don't own this ship.", ephemeral=True)
                return

            # Update nickname
            flogger.debug(f"/nickname: updating ship_id={ship_id} with new nickname")
            nick_resp = await self.http_client.put(
                f"{api_base}/ships/{ship_id}/nickname", json={"nickname": nickname}, timeout=10
            )
            nick_resp.raise_for_status()
            updated_ship = nick_resp.json()
            flogger.debug(f"/nickname: update successful - ship_id={ship_id}, new_nickname={nickname}")

            # Success message
            embed = discord.Embed(
                title="✅ Ship Nickname Updated",
                description=f'**{updated_ship["ship_name"]}** is now nicknamed "**{nickname}**"',
                color=discord.Color.green(),
            )

            embed.add_field(name="Ship ID", value=str(ship_id), inline=True)
            embed.add_field(
                name="Status", value="🟢 Active" if updated_ship["is_active"] else "⚪ Inactive", inline=True
            )

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/nickname success: guild={interaction.guild_id}, user={interaction.user.id}, "
                f"ship_id={ship_id}, new_nickname={nickname}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                flogger.debug(f"/nickname not found: ship_id={ship_id}, guild={interaction.guild_id}")
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/nickname API error: status={e.response.status_code}, ship_id={ship_id}, "
                    f"guild={interaction.guild_id}, user={interaction.user.id}, error={e}"
                )
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/nickname failed: ship_id={ship_id}, guild={interaction.guild_id}, "
                f"user={interaction.user.id}, error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while setting ship nickname.", ephemeral=True)

    @ships.error
    async def ships_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /ships", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @ship.error
    async def ship_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /ship", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @setactive.error
    async def setactive_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /setactive", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @nickname.error
    async def nickname_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /nickname", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up ShipsCog...")
    await bot.add_cog(ShipsCog(bot))
    flogger.info("ShipsCog loaded")
