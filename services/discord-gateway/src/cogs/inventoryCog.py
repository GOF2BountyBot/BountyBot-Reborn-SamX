import os

import discord
import httpx
from cogs._shared.http_error_handler import report_api_error
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_helpers import player_inventory_autocomplete
from utils.autocomplete_utils import normalize_for_search

# Set up logger
flogger = bblogger.get_logger("discord-gateway-InventoryCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"inventoryCog loading with API_BASE_URL: {api_base}")

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


# ---------------------------------------------------------------------------
# Discord UI Views for swap interactions
# ---------------------------------------------------------------------------


class WeaponSwapView(discord.ui.View):
    """Select menu view for choosing which weapon/turret to swap out.

    Presented when all weapon/turret slots are full and the player wants
    to equip a new item.  Lets them pick which equipped item to replace.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ship_id: int,
        player_id: int,
        new_item_name: str,
        equipment_type: str,
        equipped_items: list[dict],
        *,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.http_client = http_client
        self.ship_id = ship_id
        self.player_id = player_id
        self.new_item_name = new_item_name
        self.equipment_type = equipment_type
        self.equipped_items = equipped_items
        self.result: str | None = None  # "swapped" | "cancelled" | None (timeout)

        # Build the select menu options from equipped items; description clarifies the swap action
        options = [
            discord.SelectOption(label=item["name"], value=item["name"], description="Swap this item out")
            for item in equipped_items[:25]  # Discord limit: 25 options
        ]
        select = discord.ui.Select(
            placeholder="Choose an item to swap out…",
            options=options,
            custom_id="weapon_swap_select",
        )
        select.callback = self._on_select
        self.add_item(select)

        # Cancel button
        cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="weapon_swap_cancel",
        )
        cancel_btn.callback = self._on_cancel
        self.add_item(cancel_btn)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        """Called when the user selects an item to swap out."""
        old_item_name = interaction.data["values"][0]
        await interaction.response.defer(thinking=True)

        try:
            # Unequip old item
            await self.http_client.post(
                f"{api_base}/ships/{self.ship_id}/unequip",
                json={
                    "player_id": self.player_id,
                    "equipment_type": self.equipment_type,
                    "item_name": old_item_name,
                },
                timeout=10,
            )

            # Equip new item
            equip_resp = await self.http_client.post(
                f"{api_base}/ships/{self.ship_id}/equip",
                json={
                    "player_id": self.player_id,
                    "equipment_type": self.equipment_type,
                    "item_name": self.new_item_name,
                },
                timeout=10,
            )
            equip_resp.raise_for_status()
            ship_data = equip_resp.json()

            embed = discord.Embed(
                title="🔄 Items Swapped",
                description=(f"**{old_item_name}** was unequipped and **{self.new_item_name}** was equipped."),
                color=discord.Color.green(),
            )
            ship_display = ship_data.get("nickname") or ship_data.get("ship_name", "Unknown")
            embed.add_field(name="Ship", value=ship_display, inline=True)

            self.result = "swapped"
            self.stop()
            await interaction.followup.send(embed=embed)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"WeaponSwapView swap error: {exc}")
            self.result = "error"
            self.stop()
            await interaction.followup.send("⚠️ An error occurred during the swap.", ephemeral=True)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        """Called when the user clicks Cancel."""
        self.result = "cancelled"
        self.stop()
        await interaction.response.send_message("❌ Swap cancelled.", ephemeral=True)

    async def on_timeout(self) -> None:
        flogger.debug("WeaponSwapView timed out")


class UniqueModuleSwapView(discord.ui.View):
    """Buttons for swapping a unique module.

    Presented when a module with an equip limit of 1 is already equipped
    and the player wants to equip a different module of the same class.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ship_id: int,
        player_id: int,
        new_item_name: str,
        old_item_name: str,
        equipment_type: str = "modules",
        *,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.http_client = http_client
        self.ship_id = ship_id
        self.player_id = player_id
        self.new_item_name = new_item_name
        self.old_item_name = old_item_name
        self.equipment_type = equipment_type
        self.result: str | None = None  # "swapped" | "cancelled" | None (timeout)

    @discord.ui.button(label="Swap", style=discord.ButtonStyle.primary, custom_id="unique_swap_confirm")
    async def swap_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Perform the swap on confirmation."""
        _ = button
        await interaction.response.defer(thinking=True)

        try:
            # Unequip old module
            await self.http_client.post(
                f"{api_base}/ships/{self.ship_id}/unequip",
                json={
                    "player_id": self.player_id,
                    "equipment_type": self.equipment_type,
                    "item_name": self.old_item_name,
                },
                timeout=10,
            )

            # Equip new module
            equip_resp = await self.http_client.post(
                f"{api_base}/ships/{self.ship_id}/equip",
                json={
                    "player_id": self.player_id,
                    "equipment_type": self.equipment_type,
                    "item_name": self.new_item_name,
                },
                timeout=10,
            )
            equip_resp.raise_for_status()
            ship_data = equip_resp.json()

            embed = discord.Embed(
                title="🔄 Module Swapped",
                description=(f"**{self.old_item_name}** was replaced with **{self.new_item_name}**."),
                color=discord.Color.green(),
            )
            ship_display = ship_data.get("nickname") or ship_data.get("ship_name", "Unknown")
            embed.add_field(name="Ship", value=ship_display, inline=True)

            self.result = "swapped"
            self.stop()
            await interaction.followup.send(embed=embed)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"UniqueModuleSwapView swap error: {exc}")
            self.result = "error"
            self.stop()
            await interaction.followup.send("⚠️ An error occurred during the module swap.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="unique_swap_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Cancel the swap."""
        _ = button
        self.result = "cancelled"
        self.stop()
        await interaction.response.send_message("❌ Module swap cancelled.", ephemeral=True)

    async def on_timeout(self) -> None:
        flogger.debug("UniqueModuleSwapView timed out")


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class InventoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("InventoryCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _get_player_id(self, user_id: int, guild_id: int) -> int | None:
        """Helper to get player ID from Discord user ID.

        Re-raises httpx.HTTPStatusError for guild-not-configured responses so callers
        can surface a user-friendly message.
        """
        try:
            user_data = {"discord_id": user_id, "guild_id": guild_id, "discord_username": None}

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()["id"]
        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                raise
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    @app_commands.command(name="inventory", description="View your inventory")
    @app_commands.describe(
        item_type="Filter by item type",
        user="View another user's inventory (admin only)",
    )
    @app_commands.choices(
        item_type=[
            app_commands.Choice(name="Ship", value="ship"),
            app_commands.Choice(name="Primary Weapon", value="primary_weapon"),
            app_commands.Choice(name="Secondary Weapon", value="secondary_weapon"),
            app_commands.Choice(name="Turret", value="turret_weapon"),
            app_commands.Choice(name="Module", value="module"),
        ]
    )
    async def inventory(
        self, interaction: discord.Interaction, item_type: str | None = None, user: discord.User | None = None
    ):
        """Display player inventory."""
        flogger.info(f"/inventory: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/inventory params: item_type={item_type}, user={user.id if user else None}")
        await interaction.response.defer(thinking=True)

        try:
            # Determine target user
            target_user = user or interaction.user
            if user and user != interaction.user:
                # Require admin permission to view another user's inventory
                from cogs.adminCog import _check_is_admin

                if not await _check_is_admin(interaction):
                    await interaction.followup.send(
                        "❌ You need admin permissions to view another user's inventory.", ephemeral=True
                    )
                    return

            player_id = await self._get_player_id(target_user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Get inventory
            params = {}
            if item_type:
                params["item_type"] = item_type

            resp = await self.http_client.get(f"{api_base}/inventory/player/{player_id}", params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json()

            if not items:
                type_text = f" ({item_type})" if item_type else ""
                await interaction.followup.send(
                    f"📭 No items found in {target_user.display_name}'s inventory{type_text}.", ephemeral=True
                )
                return

            # Get inventory summary for overview
            summary_resp = await self.http_client.get(f"{api_base}/inventory/player/{player_id}/summary", timeout=10)
            summary_resp.raise_for_status()
            summary = summary_resp.json()

            # Create inventory embed
            title = f"🎒 {target_user.display_name}'s Inventory"
            if item_type:
                title += f" - {item_type.replace('_', ' ').title()}s"

            embed = discord.Embed(title=title, color=discord.Color.blue())

            # Add summary as description.
            # Post-A.36 the summary API returns concrete type keys; aggregate here for display.
            # Use .get(..., 0) defensively in case of partial or stale responses.
            weapons_count = summary.get("primary_weapon", 0) + summary.get("secondary_weapon", 0)
            summary_text = (
                f"**Total Items:** {summary.get('total_items', 0)}\n"
                f"Ships: {summary.get('ship', 0)} | Weapons: {weapons_count} | "
                f"Modules: {summary.get('module', 0)} | Turrets: {summary.get('turret_weapon', 0)}"
            )
            embed.description = summary_text

            # Group items by type
            items_by_type = {}
            for item in items:
                item_type_key = item["item_type"]
                if item_type_key not in items_by_type:
                    items_by_type[item_type_key] = []
                items_by_type[item_type_key].append(item)

            # Add fields for each item type
            for item_type_key, type_items in items_by_type.items():
                # Sort by name
                type_items.sort(key=lambda x: x["item_name"])

                # Format items
                items_text = ""
                for item in type_items[:20]:  # Limit to prevent embed size issues
                    quantity_text = f"x{item['quantity']}" if item["quantity"] > 1 else ""
                    items_text += f"• {item['item_name']} {quantity_text}\n"

                if len(type_items) > 20:
                    items_text += f"... and {len(type_items) - 20} more"

                embed.add_field(
                    name=f"{item_type_key.replace('_', ' ').title()}s ({len(type_items)})",
                    value=items_text or "None",
                    inline=True,
                )

            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.set_footer(text="Use /search to find specific items")

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/inventory by {interaction.user} for {target_user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /inventory: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching inventory.", ephemeral=True)

    @app_commands.command(name="search", description="Search your inventory for specific items")
    @app_commands.describe(query="Item name to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        """Search player inventory for items."""
        flogger.info(f"/search: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/search params: query={query}")
        await interaction.response.defer(thinking=True)

        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Search inventory
            resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}/search", params={"q": query}, timeout=10
            )
            resp.raise_for_status()
            items = resp.json()

            if not items:
                await interaction.followup.send(f"🔍 No items found matching '{query}'.", ephemeral=True)
                return

            # Create search results embed
            embed = discord.Embed(
                title=f"🔍 Search Results for '{query}'",
                description=f"Found {len(items)} matching items",
                color=discord.Color.green(),
            )

            # Group by type and display
            items_by_type = {}
            for item in items:
                item_type = item["item_type"]
                if item_type not in items_by_type:
                    items_by_type[item_type] = []
                items_by_type[item_type].append(item)

            for item_type, type_items in items_by_type.items():
                items_text = ""
                for item in type_items[:10]:  # Limit results
                    quantity_text = f" x{item['quantity']}" if item["quantity"] > 1 else ""
                    items_text += f"• **{item['item_name']}**{quantity_text}\n"

                if len(type_items) > 10:
                    items_text += f"... and {len(type_items) - 10} more"

                embed.add_field(name=f"{item_type.replace('_', ' ').title()}s", value=items_text, inline=True)

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/search '{query}' by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /search: {e}")
            await interaction.followup.send("⚠️ An error occurred while searching inventory.", ephemeral=True)

    async def item_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete for /item — shows items in the invoking player's inventory.

        If the user has already chosen an ``item_type`` value, we scope results
        to that type; otherwise all inventory items are returned.
        """
        # Scope to the already-chosen item_type (if any) so the dropdown is relevant.
        item_type_filter: str | None = None
        try:
            namespace = getattr(interaction, "namespace", None)
            if namespace is not None:
                selected_type = getattr(namespace, "item_type", None)
                if selected_type:
                    item_type_filter = selected_type
        except Exception:  # pylint: disable=broad-exception-caught
            item_type_filter = None

        return await player_inventory_autocomplete(
            self.http_client,
            api_base,
            interaction,
            current,
            item_type_filter=item_type_filter,
        )

    @app_commands.command(name="item", description="Get detailed information about a specific item")
    @app_commands.describe(
        item_name="Name of the item to check", item_type="Type of the item (ship, weapon, module, turret)"
    )
    @app_commands.autocomplete(item_name=item_autocomplete)
    @app_commands.choices(
        item_type=[
            app_commands.Choice(name="Ship", value="ship"),
            app_commands.Choice(name="Primary Weapon", value="primary_weapon"),
            app_commands.Choice(name="Secondary Weapon", value="secondary_weapon"),
            app_commands.Choice(name="Turret", value="turret_weapon"),
            app_commands.Choice(name="Module", value="module"),
        ]
    )
    async def item(self, interaction: discord.Interaction, item_name: str, item_type: str):
        """Get detailed item information including inventory count."""
        flogger.info(f"/item: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/item params: item_name={item_name}, item_type={item_type}")
        await interaction.response.defer(thinking=True)

        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Get item count
            resp = await self.http_client.get(
                f"{api_base}/inventory/player/{player_id}/item/{item_name}/count",
                params={"item_type": item_type},
                timeout=10,
            )
            resp.raise_for_status()
            count_data = resp.json()

            # Create item info embed
            embed = discord.Embed(title=f"📦 {item_name}", color=self._get_item_type_color(item_type))

            embed.add_field(name="Type", value=item_type.replace("_", " ").title(), inline=True)
            embed.add_field(name="Quantity Owned", value=str(count_data["quantity"]), inline=True)

            if count_data["quantity"] == 0:
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
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /item: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching item information.", ephemeral=True)

    async def _get_active_ship(self, player_id: int) -> dict | None:
        """Helper to fetch the player's active ship. Returns ship dict or None."""
        try:
            resp = await self.http_client.get(f"{api_base}/ships/player/{player_id}", timeout=10)
            resp.raise_for_status()
            ships = resp.json()
            for ship in ships:
                if ship.get("is_active"):
                    return ship
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    async def equip_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /equip — shows equippable items from the player's cargo/inventory."""
        try:
            # Resolve player ID
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": None,
            }
            player_resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=3)
            player_resp.raise_for_status()
            player_id = player_resp.json().get("id")
            if not player_id:
                return []

            # Fetch player inventory (cargo items — weapons, modules, turrets)
            inv_resp = await self.http_client.get(f"{api_base}/inventory/player/{player_id}", timeout=3)
            inv_resp.raise_for_status()
            items = inv_resp.json()

            # Filter to equippable item types using concrete types that match
            # _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES (mirrors bot-core CURRENTLY_ENABLED_TYPES
            # minus "ship").  See utils/autocomplete_helpers.py for the constant.
            from utils.autocomplete_helpers import _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES

            norm_current = normalize_for_search(current)
            choices = []
            seen: set[str] = set()

            # Fetch active ship to count already-equipped instances per item name.
            # Use a Counter so duplicate equips of the same item are counted correctly.
            # An item remains available in autocomplete as long as:
            #   inventory_quantity > equipped_count_on_active_ship
            active_ship = await self._get_active_ship(player_id)
            equipped_counts: dict[str, int] = {}
            if active_ship:
                for slot in ("weapons", "modules", "turrets", "secondary_weapons"):
                    for name in active_ship.get(slot) or []:
                        equipped_counts[name] = equipped_counts.get(name, 0) + 1

            for item in items:
                item_type = item.get("item_type", "")
                item_name = item.get("item_name", "")
                qty = item.get("quantity") or 0
                equipped_count = equipped_counts.get(item_name, 0)
                if (
                    item_type in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
                    and item_name
                    and item_name not in seen
                    and qty > equipped_count
                    and norm_current in normalize_for_search(item_name)
                ):
                    seen.add(item_name)
                    remaining = qty - equipped_count
                    qty_suffix = f" x{remaining}" if remaining > 1 else ""
                    label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
                    choices.append(app_commands.Choice(name=label[:100], value=item_name))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def unequip_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /unequip — shows items currently equipped on the player's active ship."""
        try:
            # Resolve player ID
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": None,
            }
            player_resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=3)
            player_resp.raise_for_status()
            player_id = player_resp.json().get("id")
            if not player_id:
                return []

            # Fetch the player's active ship
            ships_resp = await self.http_client.get(f"{api_base}/ships/player/{player_id}", timeout=3)
            ships_resp.raise_for_status()
            ships = ships_resp.json()
            active_ship = next((s for s in ships if s.get("is_active")), None)
            if not active_ship:
                return []

            ship_id = active_ship["id"]

            # Fetch the ship's full loadout
            loadout_resp = await self.http_client.get(f"{api_base}/ships/{ship_id}/loadout", timeout=3)
            loadout_resp.raise_for_status()
            loadout = loadout_resp.json()

            # Collect all equipped items (weapons + modules + turrets + secondary_weapons)
            equipped: list[str] = []
            equipped.extend(loadout.get("weapons") or [])
            equipped.extend(loadout.get("modules") or [])
            equipped.extend(loadout.get("turrets") or [])
            equipped.extend(loadout.get("secondary_weapons") or [])

            norm_current = normalize_for_search(current)
            choices = []
            seen: set[str] = set()
            for item_name in equipped:
                if item_name and item_name not in seen and norm_current in normalize_for_search(item_name):
                    seen.add(item_name)
                    choices.append(app_commands.Choice(name=item_name, value=item_name))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="equip", description="Equip an item from your inventory onto your active ship")
    @app_commands.describe(item_name="Name of the item to equip")
    @app_commands.autocomplete(item_name=equip_autocomplete)
    async def equip(
        self,
        interaction: discord.Interaction,
        item_name: str,
    ):
        """Equip an item onto the player's active ship.

        Calls the equip-check endpoint first to auto-detect the item type
        and handle slot-full / unique-conflict scenarios with swap UIs.
        """
        flogger.info(f"/equip: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/equip params: item_name={item_name}")
        await interaction.response.defer(thinking=True)

        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            active_ship = await self._get_active_ship(player_id)
            if not active_ship:
                await interaction.followup.send(
                    "❌ No active ship found. Use `/ships` to set an active ship.", ephemeral=True
                )
                return

            ship_id = active_ship["id"]

            # Step 1: Pre-flight check (auto-detects type, checks slots/unique limits)
            check_resp = await self.http_client.post(
                f"{api_base}/ships/{ship_id}/equip-check",
                json={"player_id": player_id, "item_name": item_name},
                timeout=10,
            )
            check_resp.raise_for_status()
            check_data = check_resp.json()
            status = check_data["status"]
            equipment_type = check_data.get("equipment_type")

            if status == "ok":
                # Step 2a: Can equip directly
                equip_resp = await self.http_client.post(
                    f"{api_base}/ships/{ship_id}/equip",
                    json={
                        "player_id": player_id,
                        "equipment_type": equipment_type,
                        "item_name": item_name,
                    },
                    timeout=10,
                )
                equip_resp.raise_for_status()
                ship_data = equip_resp.json()

                embed = discord.Embed(
                    title="⚙️ Item Equipped",
                    description=f"**{item_name}** has been equipped to your ship!",
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Ship",
                    value=ship_data.get("nickname") or ship_data.get("ship_name", "Unknown"),
                    inline=True,
                )
                embed.add_field(name="Slot", value=(equipment_type or "auto").title(), inline=True)

                weapons = ship_data.get("weapons") or []
                modules = ship_data.get("modules") or []
                turrets = ship_data.get("turrets") or []
                loadout_text = (
                    f"Weapons: {', '.join(weapons) or 'None'}\n"
                    f"Modules: {', '.join(modules) or 'None'}\n"
                    f"Turrets: {', '.join(turrets) or 'None'}"
                )
                embed.add_field(name="Current Loadout", value=loadout_text, inline=False)
                await interaction.followup.send(embed=embed)

            elif status == "slot_full":
                # Step 2b: Slots are full — show swap select menu
                equipped_items = check_data.get("equipped_items", [])
                max_slots = check_data.get("max_slots", len(equipped_items))

                embed = discord.Embed(
                    title="🔄 Slot Full — Choose an item to swap",
                    description=(
                        f"All **{equipment_type}** slots are full ({max_slots}/{max_slots}).\n"
                        f"Select an item below to replace with **{item_name}**."
                    ),
                    color=discord.Color.orange(),
                )
                view = WeaponSwapView(
                    http_client=self.http_client,
                    ship_id=ship_id,
                    player_id=player_id,
                    new_item_name=item_name,
                    equipment_type=equipment_type,
                    equipped_items=equipped_items,
                )
                await interaction.followup.send(embed=embed, view=view)

            elif status == "unique_conflict":
                # Step 2c: Unique module conflict — show Swap/Cancel buttons
                conflicting_item = check_data.get("conflicting_item", {})
                old_name = conflicting_item.get("name", "Unknown")
                module_class = check_data.get("module_class", "")

                embed = discord.Embed(
                    title="🔄 Unique Module Conflict",
                    description=(
                        f"You already have **{old_name}** equipped (class: {module_class}).\n"
                        f"Only 1 module of this class can be equipped at a time.\n\n"
                        f"Swap **{old_name}** → **{item_name}**?"
                    ),
                    color=discord.Color.orange(),
                )
                view = UniqueModuleSwapView(
                    http_client=self.http_client,
                    ship_id=ship_id,
                    player_id=player_id,
                    new_item_name=item_name,
                    old_item_name=old_name,
                    equipment_type=equipment_type or "modules",
                )
                await interaction.followup.send(embed=embed, view=view)

            else:
                await interaction.followup.send(f"❌ Unexpected equip-check status: {status!r}", ephemeral=True)

            flogger.debug(f"/equip {item_name} (status={status}) by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Cannot equip item.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Cannot equip item."
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send(f"❌ Ship or item '{item_name}' not found.", ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /equip: {e}")
            await interaction.followup.send("⚠️ An error occurred while equipping the item.", ephemeral=True)

    @app_commands.command(name="unequip", description="Unequip an item from your active ship to inventory")
    @app_commands.describe(item_name="Name of the item to unequip")
    @app_commands.autocomplete(item_name=unequip_autocomplete)
    async def unequip(
        self,
        interaction: discord.Interaction,
        item_name: str,
    ):
        """Unequip an item from the player's active ship.

        equipment_type is auto-detected from the item name.
        """
        flogger.info(f"/unequip: guild={interaction.guild_id}, user={interaction.user.id}")
        flogger.debug(f"/unequip params: item_name={item_name}")
        await interaction.response.defer(thinking=True)

        try:
            player_id = await self._get_player_id(interaction.user.id, interaction.guild_id)
            if not player_id:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            active_ship = await self._get_active_ship(player_id)
            if not active_ship:
                await interaction.followup.send(
                    "❌ No active ship found. Use `/ships` to set an active ship.", ephemeral=True
                )
                return

            ship_id = active_ship["id"]

            resp = await self.http_client.post(
                f"{api_base}/ships/{ship_id}/unequip",
                json={
                    "player_id": player_id,
                    "item_name": item_name,
                    # No equipment_type — auto-detected by bot-core
                },
                timeout=10,
            )
            resp.raise_for_status()
            ship_data = resp.json()

            embed = discord.Embed(
                title="📦 Item Unequipped",
                description=f"**{item_name}** has been moved back to your inventory.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Ship", value=ship_data.get("nickname") or ship_data.get("ship_name", "Unknown"), inline=True
            )

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/unequip {item_name} by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Cannot unequip item.")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = "Cannot unequip item."
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send(f"❌ Ship or item '{item_name}' not found.", ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /unequip: {e}")
            await interaction.followup.send("⚠️ An error occurred while unequipping the item.", ephemeral=True)

    def _get_item_type_color(self, item_type: str) -> discord.Color:
        """Get Discord color based on item type (concrete vocab, A.46)."""
        type_colors = {
            "ship": discord.Color.green(),
            "primary_weapon": discord.Color.red(),
            "secondary_weapon": discord.Color.orange(),
            "turret_weapon": discord.Color.purple(),
            "module": discord.Color.blue(),
        }
        return type_colors.get(item_type, discord.Color.default())

    # ---------------------------------------------------------------------------
    # /give command — player-to-player transfers
    # ---------------------------------------------------------------------------

    async def give_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /give item — shows player's inventory items with type labels."""
        try:
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": None,
            }
            player_resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=3)
            player_resp.raise_for_status()
            player_id = player_resp.json().get("id")
            if not player_id:
                return []

            inv_resp = await self.http_client.get(f"{api_base}/inventory/player/{player_id}", timeout=3)
            inv_resp.raise_for_status()
            items = inv_resp.json()

            norm_current = normalize_for_search(current)
            choices = []
            seen: set[str] = set()
            for item in items:
                item_name = item.get("item_name", "")
                item_type = item.get("item_type", "")
                key = f"{item_name}|{item_type}"
                if item_name and key not in seen and norm_current in normalize_for_search(item_name):
                    seen.add(key)
                    label = f"{item_name} [{item_type}]"
                    # value encodes "item_name::item_type" for parsing
                    choices.append(app_commands.Choice(name=label[:100], value=f"{item_name}::{item_type}"))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def give_ship_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /give ship — shows player's non-active ships."""
        try:
            user_data = {
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "discord_username": None,
            }
            player_resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=3)
            player_resp.raise_for_status()
            player_id = player_resp.json().get("id")
            if not player_id:
                return []

            ships_resp = await self.http_client.get(f"{api_base}/ships/player/{player_id}", timeout=3)
            ships_resp.raise_for_status()
            ships = ships_resp.json()

            norm_current = normalize_for_search(current)
            choices = []
            for ship in ships:
                if ship.get("is_active"):
                    continue  # cannot give away active ship
                ship_name = ship.get("ship_name", "")
                ship_id = ship.get("id")
                if ship_name and norm_current in normalize_for_search(ship_name):
                    label = ship.get("nickname") or ship_name
                    choices.append(app_commands.Choice(name=label[:100], value=str(ship_id)))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="give", description="Give credits, an item, or a ship to another player")
    @app_commands.describe(
        target="The player to give to",
        give_type="What to give: credits, item, or ship",
        amount="Amount of credits to give (for credits only)",
        item="Item to give — use autocomplete to pick from your inventory",
        ship="Ship to give — use autocomplete to pick a non-active ship",
    )
    @app_commands.choices(
        give_type=[
            app_commands.Choice(name="Credits", value="credits"),
            app_commands.Choice(name="Item", value="item"),
            app_commands.Choice(name="Ship", value="ship"),
        ]
    )
    @app_commands.rename(give_type="type")
    @app_commands.autocomplete(item=give_item_autocomplete, ship=give_ship_autocomplete)
    async def give(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        give_type: str,
        amount: int | None = None,
        item: str | None = None,
        ship: str | None = None,
    ):
        """Give credits, an item, or a ship to another player in the same guild."""
        flogger.info(f"/give: guild={interaction.guild_id} user={interaction.user.id} type={give_type}")
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            # Cannot give to self
            if target.id == interaction.user.id:
                await interaction.followup.send("❌ You cannot give to yourself.", ephemeral=True)
                return

            # Resolve both players
            source_player_resp = await self.http_client.post(
                f"{api_base}/players/",
                json={"discord_id": interaction.user.id, "guild_id": interaction.guild_id, "discord_username": None},
                timeout=5,
            )
            source_player_resp.raise_for_status()
            source_player = source_player_resp.json()

            target_player_resp = await self.http_client.post(
                f"{api_base}/players/",
                json={"discord_id": target.id, "guild_id": interaction.guild_id, "discord_username": None},
                timeout=5,
            )
            target_player_resp.raise_for_status()
            target_player = target_player_resp.json()

            if give_type == "credits":
                if amount is None or amount <= 0:
                    await interaction.followup.send("❌ Please provide a positive credits amount.", ephemeral=True)
                    return

                if source_player["credits"] < amount:
                    await interaction.followup.send(
                        f"❌ You only have {source_player['credits']:,} credits.", ephemeral=True
                    )
                    return

                transfer_resp = await self.http_client.post(
                    f"{api_base}/players/transfer",
                    json={
                        "source_player_id": source_player["id"],
                        "target_player_id": target_player["id"],
                        "amount": amount,
                    },
                    timeout=10,
                )
                if transfer_resp.status_code == 400:
                    detail = transfer_resp.json().get("detail", "Transfer failed.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                transfer_resp.raise_for_status()

                embed = discord.Embed(
                    title="💰 Credits Given",
                    description=f"You gave **{amount:,}** credits to {target.mention}.",
                    color=discord.Color.gold(),
                )
                embed.add_field(
                    name="Your Remaining Credits", value=f"{source_player['credits'] - amount:,}", inline=True
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif give_type == "item":
                if not item:
                    await interaction.followup.send("❌ Please select an item to give.", ephemeral=True)
                    return

                # Parse item name and type from autocomplete value ("name::type").
                # If "::" is absent the user typed freehand instead of picking from autocomplete;
                # reject with a friendly message — A.46 spec §4.2 "reject freehand" path.
                if "::" in item:
                    item_name, item_type = item.split("::", 1)
                else:
                    await interaction.followup.send(
                        "❌ Please pick the item from the autocomplete list.", ephemeral=True
                    )
                    return

                transfer_resp = await self.http_client.post(
                    f"{api_base}/inventory/transfer",
                    json={
                        "from_player_id": source_player["id"],
                        "to_player_id": target_player["id"],
                        "item_type": item_type,
                        "item_name": item_name,
                        "quantity": 1,
                    },
                    timeout=10,
                )
                if transfer_resp.status_code == 400:
                    detail = transfer_resp.json().get("detail", "Transfer failed.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                if transfer_resp.status_code == 422:
                    # Translate 422 validation errors into user-friendly messages.
                    # A.46: secondary_weapon is present in the choice list but may be
                    # surface-gated on the server (InvalidItemTypeError → 422).
                    try:
                        raw_detail = transfer_resp.json().get("detail", "")
                        detail_str = str(raw_detail).lower()
                    except Exception:  # pylint: disable=broad-exception-caught
                        detail_str = ""
                    if "secondary_weapon" in detail_str or "not currently available" in detail_str:
                        msg = "❌ Secondary weapons are not currently available."
                    else:
                        msg = "❌ That item type is not valid. Please pick a valid item from the autocomplete list."
                    await interaction.followup.send(msg, ephemeral=True)
                    return
                transfer_resp.raise_for_status()

                embed = discord.Embed(
                    title="📦 Item Given",
                    description=f"You gave **{item_name}** to {target.mention}.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Item Type", value=item_type.replace("_", " ").title(), inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif give_type == "ship":
                if not ship:
                    await interaction.followup.send("❌ Please select a ship to give.", ephemeral=True)
                    return

                # ship value is the ship ID (int as string)
                try:
                    ship_id = int(ship)
                except ValueError:
                    await interaction.followup.send("❌ Invalid ship selection.", ephemeral=True)
                    return

                transfer_resp = await self.http_client.post(
                    f"{api_base}/ships/transfer",
                    json={
                        "from_player_id": source_player["id"],
                        "to_player_id": target_player["id"],
                        "ship_id": ship_id,
                    },
                    timeout=10,
                )
                if transfer_resp.status_code == 400:
                    detail = transfer_resp.json().get("detail", "Transfer failed.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                if transfer_resp.status_code == 404:
                    detail = transfer_resp.json().get("detail", "Ship not found.")
                    await interaction.followup.send(f"❌ {detail}", ephemeral=True)
                    return
                transfer_resp.raise_for_status()
                result = transfer_resp.json()

                embed = discord.Embed(
                    title="🚀 Ship Given",
                    description=f"You gave **{result.get('ship_name', 'Unknown')}** to {target.mention}.",
                    color=discord.Color.blue(),
                )
                items_returned = result.get("items_returned_to_source", [])
                if items_returned:
                    embed.add_field(
                        name="Items Returned to You",
                        value=", ".join(items_returned[:10]) + ("..." if len(items_returned) > 10 else ""),
                        inline=False,
                    )
                await interaction.followup.send(embed=embed, ephemeral=True)

            else:
                await interaction.followup.send(f"❌ Unknown give type: {give_type}", ephemeral=True)

            flogger.info(
                f"/give {give_type} success: guild={interaction.guild_id} from={interaction.user.id} to={target.id}"
            )

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /give: {e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @give.error
    async def give_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /give", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

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

    @equip.error
    async def equip_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /equip", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @unequip.error
    async def unequip_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /unequip", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up InventoryCog...")
    await bot.add_cog(InventoryCog(bot))
    flogger.info("InventoryCog loaded")
