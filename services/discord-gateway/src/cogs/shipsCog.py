import os
import discord
from discord import app_commands
from discord.ext import commands
import shared.bblogger as bblogger
import requests
from typing import Optional, List, Dict, Any

# Set up logger
flogger = bblogger.get_logger("discord-gateway-ShipsCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"shipsCog loading with API_BASE_URL: {api_base}")

class ShipsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        flogger.debug("ShipsCog initialized")

    async def _get_player_id(self, user_id: int, guild_id: int) -> Optional[int]:
        """Helper to get player ID from Discord user ID."""
        try:
            user_data = {
                "discord_id": user_id,
                "guild_id": guild_id,
                "discord_username": "temp"
            }
            
            resp = requests.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()['id']
        except:
            return None

    @app_commands.command(name="ships", description="View your ships and their loadouts")
    @app_commands.describe(user="View another user's ships (admin only)")
    async def ships(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        """Display player ships."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Determine target user
            target_user = user or interaction.user
            if user and user != interaction.user:
                # TODO: Add admin permission check here
                pass
                
            player_id = await self._get_player_id(target_user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return
                
            # Get player ships
            resp = requests.get(f"{api_base}/ships/player/{player_id}", timeout=10)
            resp.raise_for_status()
            ships = resp.json()
            
            if not ships:
                await interaction.followup.send(
                    f"🚫 {target_user.display_name} has no ships.",
                    ephemeral=True
                )
                return
                
            # Create ships embed
            embed = discord.Embed(
                title=f"🚢 {target_user.display_name}'s Ships",
                description=f"Total ships: {len(ships)}",
                color=discord.Color.green()
            )
            
            # Sort ships - active first, then by name
            ships.sort(key=lambda x: (not x['is_active'], x['ship_name']))
            
            for ship in ships[:10]:  # Limit to prevent embed size issues
                # Create ship info
                status = "🟢 **ACTIVE**" if ship['is_active'] else "⚪ Inactive"
                nickname = f" \"{ship['nickname']}\"" if ship['nickname'] else ""
                
                # Count loadout
                weapons_count = len(ship['weapons']) if ship['weapons'] else 0
                modules_count = len(ship['modules']) if ship['modules'] else 0
                turrets_count = len(ship['turrets']) if ship['turrets'] else 0
                
                loadout_summary = f"W:{weapons_count} | M:{modules_count} | T:{turrets_count}"
                
                ship_info = (
                    f"{status}{nickname}\n"
                    f"Loadout: {loadout_summary}\n"
                    f"Created: {ship['created_at'][:10]}"
                )
                
                embed.add_field(
                    name=f"{ship['ship_name']} (ID: {ship['id']})",
                    value=ship_info,
                    inline=True
                )
                
            if len(ships) > 10:
                embed.set_footer(text=f"Showing first 10 of {len(ships)} ships. Use /ship <id> for details.")
            else:
                embed.set_footer(text="Use /ship <id> for detailed loadout information.")
                
            embed.set_thumbnail(url=target_user.display_avatar.url)
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/ships by {interaction.user} for {target_user} in guild {interaction.guild_id}")
            
        except requests.HTTPError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /ships: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching ships.", ephemeral=True)

    @app_commands.command(name="ship", description="View detailed information about a specific ship")
    @app_commands.describe(ship_id="ID of the ship to view")
    async def ship(self, interaction: discord.Interaction, ship_id: int):
        """Display detailed ship information."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Get ship details
            resp = requests.get(f"{api_base}/ships/{ship_id}", timeout=10)
            resp.raise_for_status()
            ship = resp.json()
            
            # Verify ship belongs to user (basic security)
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if ship['player_id'] != player_id:
                await interaction.followup.send("❌ You don't own this ship.", ephemeral=True)
                return
                
            # Get detailed loadout
            loadout_resp = requests.get(f"{api_base}/ships/{ship_id}/loadout", timeout=10)
            loadout_resp.raise_for_status()
            loadout = loadout_resp.json()
            
            # Create detailed ship embed
            status_emoji = "🟢" if ship['is_active'] else "⚪"
            title = f"{status_emoji} {ship['ship_name']}"
            if ship['nickname']:
                title += f" \"{ship['nickname']}\""
                
            embed = discord.Embed(
                title=title,
                description=f"Ship ID: {ship['id']}" + (" | **ACTIVE SHIP**" if ship['is_active'] else ""),
                color=discord.Color.green() if ship['is_active'] else discord.Color.greyple()
            )
            
            # Basic info
            embed.add_field(name="Type", value=ship['ship_name'], inline=True)
            embed.add_field(name="Status", value="Active" if ship['is_active'] else "Inactive", inline=True)
            embed.add_field(name="Created", value=ship['created_at'][:10], inline=True)
            
            # Loadout details
            if loadout['weapons']:
                weapons_text = "\n".join(f"• {weapon}" for weapon in loadout['weapons'][:10])
                if len(loadout['weapons']) > 10:
                    weapons_text += f"\n... and {len(loadout['weapons']) - 10} more"
                embed.add_field(
                    name=f"🔫 Weapons ({loadout['weapons_count']})",
                    value=weapons_text or "None",
                    inline=False
                )
                
            if loadout['modules']:
                modules_text = "\n".join(f"• {module}" for module in loadout['modules'][:10])
                if len(loadout['modules']) > 10:
                    modules_text += f"\n... and {len(loadout['modules']) - 10} more"
                embed.add_field(
                    name=f"⚙️ Modules ({loadout['modules_count']})",
                    value=modules_text or "None",
                    inline=False
                )
                
            if loadout['turrets']:
                turrets_text = "\n".join(f"• {turret}" for turret in loadout['turrets'][:10])
                if len(loadout['turrets']) > 10:
                    turrets_text += f"\n... and {len(loadout['turrets']) - 10} more"
                embed.add_field(
                    name=f"🎯 Turrets ({loadout['turrets_count']})",
                    value=turrets_text or "None",
                    inline=False
                )
                
            embed.set_footer(text="Use /setactive <ship_id> to set as active ship | /nickname <ship_id> <name> to set nickname")
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/ship {ship_id} by {interaction.user} in guild {interaction.guild_id}")
            
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /ship: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching ship details.", ephemeral=True)

    @app_commands.command(name="setactive", description="Set a ship as your active ship")
    @app_commands.describe(ship_id="ID of the ship to set as active")
    async def setactive(self, interaction: discord.Interaction, ship_id: int):
        """Set active ship."""
        await interaction.response.defer(thinking=True)
        
        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return
                
            # Set active ship
            resp = requests.put(
                f"{api_base}/ships/{ship_id}/set-active",
                params={"player_id": player_id},
                timeout=10
            )
            resp.raise_for_status()
            ship = resp.json()
            
            # Success message
            ship_name = ship['ship_name']
            nickname = f" \"{ship['nickname']}\"" if ship['nickname'] else ""
            
            embed = discord.Embed(
                title="✅ Active Ship Updated",
                description=f"**{ship_name}**{nickname} is now your active ship!",
                color=discord.Color.green()
            )
            
            embed.add_field(name="Ship ID", value=str(ship['id']), inline=True)
            embed.add_field(name="Status", value="🟢 Active", inline=True)
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/setactive {ship_id} by {interaction.user} in guild {interaction.guild_id}")
            
        except requests.HTTPError as e:
            if e.response.status_code == 400:
                await interaction.followup.send("❌ Invalid ship or you don't own this ship.", ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /setactive: {e}")
            await interaction.followup.send("⚠️ An error occurred while setting active ship.", ephemeral=True)

    @app_commands.command(name="nickname", description="Set a nickname for your ship")
    @app_commands.describe(
        ship_id="ID of the ship to nickname",
        nickname="New nickname for the ship (max 50 characters)"
    )
    async def nickname(self, interaction: discord.Interaction, ship_id: int, nickname: str):
        """Set ship nickname."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Validate nickname length
            if len(nickname) > 50:
                await interaction.followup.send("❌ Nickname must be 50 characters or less.", ephemeral=True)
                return
                
            # First check if user owns the ship
            resp = requests.get(f"{api_base}/ships/{ship_id}", timeout=10)
            resp.raise_for_status()
            ship = resp.json()
            
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if ship['player_id'] != player_id:
                await interaction.followup.send("❌ You don't own this ship.", ephemeral=True)
                return
                
            # Update nickname
            nick_resp = requests.put(
                f"{api_base}/ships/{ship_id}/nickname",
                json={"nickname": nickname},
                timeout=10
            )
            nick_resp.raise_for_status()
            updated_ship = nick_resp.json()
            
            # Success message
            embed = discord.Embed(
                title="✅ Ship Nickname Updated",
                description=f"**{updated_ship['ship_name']}** is now nicknamed \"**{nickname}**\"",
                color=discord.Color.green()
            )
            
            embed.add_field(name="Ship ID", value=str(ship_id), inline=True)
            embed.add_field(name="Status", value="🟢 Active" if updated_ship['is_active'] else "⚪ Inactive", inline=True)
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/nickname {ship_id} '{nickname}' by {interaction.user} in guild {interaction.guild_id}")
            
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Ship not found.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /nickname: {e}")
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