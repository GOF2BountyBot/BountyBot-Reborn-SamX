import os
import discord
from discord import app_commands
from discord.ext import commands
import shared.bblogger as bblogger
import httpx
from typing import Optional, List, Dict, Any

# Set up logger
flogger = bblogger.get_logger("discord-gateway-InventoryCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"inventoryCog loading with API_BASE_URL: {api_base}")

class InventoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient()
        flogger.debug("InventoryCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _get_player_id(self, user_id: int, guild_id: int) -> Optional[int]:
        """Helper to get player ID from Discord user ID."""
        try:
            user_data = {
                "discord_id": user_id,
                "guild_id": guild_id,
                "discord_username": "temp"
            }
            
            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()['id']
        except:
            return None

    @app_commands.command(name="inventory", description="View your inventory")
    @app_commands.describe(
        item_type="Filter by item type (ship, weapon, module, turret)",
        user="View another user's inventory (admin only)"
    )
    async def inventory(
        self, 
        interaction: discord.Interaction,
        item_type: Optional[str] = None,
        user: Optional[discord.User] = None
    ):
        """Display player inventory."""
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
                
            # Get inventory
            params = {}
            if item_type:
                params['item_type'] = item_type
                
            resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}",
                params=params,
                timeout=10
            )
            resp.raise_for_status()
            items = resp.json()
            
            if not items:
                type_text = f" ({item_type})" if item_type else ""
                await interaction.followup.send(
                    f"📭 No items found in {target_user.display_name}'s inventory{type_text}.",
                    ephemeral=True
                )
                return
                
            # Get inventory summary for overview
            summary_resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}/summary",
                timeout=10
            )
            summary_resp.raise_for_status()
            summary = summary_resp.json()
            
            # Create inventory embed
            title = f"🎒 {target_user.display_name}'s Inventory"
            if item_type:
                title += f" - {item_type.title()}s"
                
            embed = discord.Embed(
                title=title,
                color=discord.Color.blue()
            )
            
            # Add summary as description
            summary_text = (
                f"**Total Items:** {summary['total_items']}\n"
                f"Ships: {summary['ship']} | Weapons: {summary['weapon']} | "
                f"Modules: {summary['module']} | Turrets: {summary['turret']}"
            )
            embed.description = summary_text
            
            # Group items by type
            items_by_type = {}
            for item in items:
                item_type_key = item['item_type']
                if item_type_key not in items_by_type:
                    items_by_type[item_type_key] = []
                items_by_type[item_type_key].append(item)
                
            # Add fields for each item type
            for item_type_key, type_items in items_by_type.items():
                # Sort by name
                type_items.sort(key=lambda x: x['item_name'])
                
                # Format items
                items_text = ""
                for item in type_items[:20]:  # Limit to prevent embed size issues
                    quantity_text = f"x{item['quantity']}" if item['quantity'] > 1 else ""
                    items_text += f"• {item['item_name']} {quantity_text}\n"
                    
                if len(type_items) > 20:
                    items_text += f"... and {len(type_items) - 20} more"
                    
                embed.add_field(
                    name=f"{item_type_key.title()}s ({len(type_items)})",
                    value=items_text or "None",
                    inline=True
                )
                
            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.set_footer(text=f"Use /search to find specific items")
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/inventory by {interaction.user} for {target_user} in guild {interaction.guild_id}")
            
        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /inventory: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching inventory.", ephemeral=True)

    @app_commands.command(name="search", description="Search your inventory for specific items")
    @app_commands.describe(query="Item name to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        """Search player inventory for items."""
        await interaction.response.defer(thinking=True)
        
        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return
                
            # Search inventory
            resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}/search",
                params={'q': query},
                timeout=10
            )
            resp.raise_for_status()
            items = resp.json()
            
            if not items:
                await interaction.followup.send(
                    f"🔍 No items found matching '{query}'.",
                    ephemeral=True
                )
                return
                
            # Create search results embed
            embed = discord.Embed(
                title=f"🔍 Search Results for '{query}'",
                description=f"Found {len(items)} matching items",
                color=discord.Color.green()
            )
            
            # Group by type and display
            items_by_type = {}
            for item in items:
                item_type = item['item_type']
                if item_type not in items_by_type:
                    items_by_type[item_type] = []
                items_by_type[item_type].append(item)
                
            for item_type, type_items in items_by_type.items():
                items_text = ""
                for item in type_items[:10]:  # Limit results
                    quantity_text = f" x{item['quantity']}" if item['quantity'] > 1 else ""
                    items_text += f"• **{item['item_name']}**{quantity_text}\n"
                    
                if len(type_items) > 10:
                    items_text += f"... and {len(type_items) - 10} more"
                    
                embed.add_field(
                    name=f"{item_type.title()}s",
                    value=items_text,
                    inline=True
                )
                
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/search '{query}' by {interaction.user} in guild {interaction.guild_id}")
            
        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /search: {e}")
            await interaction.followup.send("⚠️ An error occurred while searching inventory.", ephemeral=True)

    @app_commands.command(name="item", description="Get detailed information about a specific item")
    @app_commands.describe(
        item_name="Name of the item to check",
        item_type="Type of the item (ship, weapon, module, turret)"
    )
    async def item(self, interaction: discord.Interaction, item_name: str, item_type: str):
        """Get detailed item information including inventory count."""
        await interaction.response.defer(thinking=True)
        
        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return
                
            # Get item count
            resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}/item/{item_name}/count",
                params={'item_type': item_type},
                timeout=10
            )
            resp.raise_for_status()
            count_data = resp.json()
            
            # Create item info embed
            embed = discord.Embed(
                title=f"📦 {item_name}",
                color=self._get_item_type_color(item_type)
            )
            
            embed.add_field(name="Type", value=item_type.title(), inline=True)
            embed.add_field(name="Quantity Owned", value=str(count_data['quantity']), inline=True)
            
            if count_data['quantity'] == 0:
                embed.add_field(name="Status", value="❌ Not Owned", inline=True)
            else:
                embed.add_field(name="Status", value="✅ Owned", inline=True)
                
            # Add item details from static data (if integrated)
            embed.set_footer(text="Use /about to get detailed item statistics")
            
            await interaction.followup.send(embed=embed)
            flogger.debug(f"/item {item_name} by {interaction.user} in guild {interaction.guild_id}")
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(f"❌ Item '{item_name}' not found.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:
            flogger.error(f"Error in /item: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching item information.", ephemeral=True)

    def _get_item_type_color(self, item_type: str) -> discord.Color:
        """Get Discord color based on item type."""
        type_colors = {
            "ship": discord.Color.green(),
            "weapon": discord.Color.red(),
            "module": discord.Color.blue(),
            "turret": discord.Color.purple()
        }
        return type_colors.get(item_type, discord.Color.default())

    @inventory.error
    async def inventory_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /inventory", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @search.error
    async def search_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /search", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @item.error
    async def item_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /item", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    flogger.debug("Setting up InventoryCog...")
    await bot.add_cog(InventoryCog(bot))
    flogger.info("InventoryCog loaded")
