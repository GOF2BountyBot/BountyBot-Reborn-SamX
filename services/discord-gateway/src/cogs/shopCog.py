import contextlib
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search

# Set up logger
flogger = bblogger.get_logger("discord-gateway-ShopCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"shopCog loading with API_BASE_URL: {api_base}")

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


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        self._valid_item_types = ["ship", "weapon", "module", "turret"]
        flogger.debug("ShopCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _get_player_data(self, user_id: int, guild_id: int) -> dict | None:
        """Helper to get player data from Discord user ID.

        Returns None on any error, OR raises GuildNotConfigured sentinel
        string "GUILD_NOT_CONFIGURED" so callers can surface the right message.
        """
        try:
            user_data = {"discord_id": user_id, "guild_id": guild_id, "discord_username": None}

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                raise
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    async def tier_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for tier selection."""
        norm_current = normalize_for_search(current)
        return [
            app_commands.Choice(name=tier, value=tier)
            for tier in self._valid_tiers
            if norm_current in normalize_for_search(tier)
        ]

    async def item_type_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for item type selection."""
        norm_current = normalize_for_search(current)
        return [
            app_commands.Choice(name=item_type.title(), value=item_type)
            for item_type in self._valid_item_types
            if norm_current in normalize_for_search(item_type)
        ]

    @app_commands.command(name="shop", description="Browse the guild shop")
    @app_commands.describe(
        tier="Shop tier to browse (Bronze, Silver, Gold, Platinum)",
        item_type="Filter by item type (ship, weapon, module, turret)",
    )
    @app_commands.autocomplete(tier=tier_autocomplete, item_type=item_type_autocomplete)
    async def shop(self, interaction: discord.Interaction, tier: str, item_type: str | None = None):
        """Browse guild shop by tier."""
        await interaction.response.defer(thinking=True)

        try:
            # Validate tier
            if tier not in self._valid_tiers:
                await interaction.followup.send(
                    f"❌ Invalid tier. Valid tiers: {', '.join(self._valid_tiers)}", ephemeral=True
                )
                return

            # Get player data to check tier access
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Check tier access (players can only access their tier and below)
            player_tier_level = self._valid_tiers.index(player["tier"])
            requested_tier_level = self._valid_tiers.index(tier)

            if requested_tier_level > player_tier_level:
                await interaction.followup.send(
                    f"🔒 You need to be **{tier}** tier to access this shop. Your current tier: **{player['tier']}**",
                    ephemeral=True,
                )
                return

            # Get shop items
            params = {}
            if item_type:
                params["item_type"] = item_type

            resp = await self.http_client.get(
                f"{api_base}/shops/guild/{interaction.guild_id}/tier/{tier}", params=params, timeout=10
            )
            resp.raise_for_status()
            items = resp.json()

            if not items:
                type_filter = f" ({item_type}s)" if item_type else ""
                await interaction.followup.send(f"🏪 The {tier} shop{type_filter} is currently empty.", ephemeral=True)
                return

            # Create shop embed
            title = f"🏪 {tier} Shop"
            if item_type:
                title += f" - {item_type.title()}s"

            embed = discord.Embed(
                title=title,
                description=f"💰 Your credits: {player['credits']:,} | Items available: {len(items)}",
                color=self._get_tier_color(tier),
            )

            # Group items by type
            items_by_type: dict[str, list] = {}
            for item in items:
                item_type_key = item["item_type"]
                if item_type_key not in items_by_type:
                    items_by_type[item_type_key] = []
                items_by_type[item_type_key].append(item)

            # Display in order: ships > weapons > turrets > modules
            type_order = ["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
            # Add any types not in the predefined order
            for key in items_by_type:
                if key not in type_order:
                    type_order.append(key)

            type_labels = {
                "ship": "Ships",
                "primary_weapon": "Primary Weapons",
                "secondary_weapon": "Secondary Weapons",
                "turret_weapon": "Turret Weapons",
                "module": "Modules",
            }

            for item_type_key in type_order:
                type_items = items_by_type.get(item_type_key)
                if not type_items:
                    continue

                # Sort by price
                type_items.sort(key=lambda x: x["price"])

                items_text = ""
                for item in type_items[:10]:  # Limit to prevent embed size issues
                    tech_level = f"T{item['tech_level']}" if item.get("tech_level") else ""
                    quantity = f"x{item['quantity']}" if item["quantity"] > 1 else ""
                    emoji = item.get("emoji") or ""

                    price_text = f"{item['price']:,} credits"
                    if player["credits"] < item["price"]:
                        price_text = f"~~{price_text}~~ 💸"

                    name_display = f"{emoji} **{item['item_name']}**" if emoji else f"**{item['item_name']}**"
                    items_text += f"{name_display} {tech_level} {quantity}\n    {price_text} | ID: {item['id']}\n"

                if len(type_items) > 10:
                    items_text += f"... and {len(type_items) - 10} more items\n"

                label = type_labels.get(item_type_key, f"{item_type_key.title()}s")
                embed.add_field(
                    name=f"{label} ({len(type_items)})",
                    value=items_text or "None available",
                    inline=False,
                )

            embed.set_footer(text=f"Use /buy <item_id> [quantity] to purchase items | Your tier: {player['tier']}")

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/shop {tier} by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching shop items.", ephemeral=True)

    async def buy_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Live autocomplete for shop items the player can buy."""
        try:
            # Get player data to determine accessible tier
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)
            if not player:
                return []
            player_tier_idx = self._valid_tiers.index(player["tier"])
            norm_current = normalize_for_search(current)
            choices: list[app_commands.Choice[int]] = []
            for tier_idx in range(player_tier_idx + 1):
                tier = self._valid_tiers[tier_idx]
                resp = await self.http_client.get(
                    f"{api_base}/shops/guild/{interaction.guild_id}/tier/{tier}", timeout=5
                )
                if resp.status_code != 200:
                    continue
                for item in resp.json():
                    label = f"{item['item_name']} ({item['price']:,}cr) [{tier}]"
                    if norm_current in normalize_for_search(label):
                        choices.append(app_commands.Choice(name=label[:100], value=item["id"]))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="buy", description="Purchase an item from the shop")
    @app_commands.describe(item_id="Select an item to purchase", quantity="Quantity to purchase (default: 1)")
    @app_commands.autocomplete(item_id=buy_item_autocomplete)
    async def buy(self, interaction: discord.Interaction, item_id: int, quantity: int = 1):
        """Purchase item from shop."""
        await interaction.response.defer(thinking=True)

        try:
            if quantity <= 0:
                await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                return

            # Get player data
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Get shop item details first
            item_resp = await self.http_client.get(f"{api_base}/shops/item/{item_id}", timeout=10)
            item_resp.raise_for_status()
            shop_item = item_resp.json()

            # Check tier access
            player_tier_level = self._valid_tiers.index(player["tier"])
            item_tier_level = self._valid_tiers.index(shop_item["tier"])

            if item_tier_level > player_tier_level:
                await interaction.followup.send(
                    f"🔒 You need to be **{shop_item['tier']}** tier to purchase this item.", ephemeral=True
                )
                return

            # Calculate total cost
            total_cost = shop_item["price"] * quantity

            # Check if player has enough credits
            if player["credits"] < total_cost:
                await interaction.followup.send(
                    f"💸 Insufficient credits! Cost: {total_cost:,} | You have: {player['credits']:,}", ephemeral=True
                )
                return

            # Check if enough quantity available
            if shop_item["quantity"] < quantity:
                await interaction.followup.send(
                    f"❌ Insufficient stock! Available: {shop_item['quantity']} | Requested: {quantity}", ephemeral=True
                )
                return

            # Make purchase — ships go to hangar, other items go to inventory
            is_ship = shop_item.get("item_type") == "ship"

            if is_ship:
                purchase_data = {"player_id": player["id"], "shop_item_id": item_id, "sell_old_ship": False}
                resp = await self.http_client.post(f"{api_base}/shops/purchase-ship", json=purchase_data, timeout=10)
            else:
                purchase_data = {"player_id": player["id"], "shop_item_id": item_id, "quantity": quantity}
                resp = await self.http_client.post(f"{api_base}/shops/purchase", json=purchase_data, timeout=10)

            resp.raise_for_status()
            transaction = resp.json()

            # Success message
            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=f"You bought **{quantity}x {transaction['item_name']}**",
                color=discord.Color.green(),
            )

            embed.add_field(name="Item Type", value=transaction["item_type"].title(), inline=True)
            embed.add_field(name="Quantity", value=str(quantity), inline=True)
            embed.add_field(name="Total Cost", value=f"{transaction['total_cost']:,} credits", inline=True)
            embed.add_field(name="Remaining Credits", value=f"{transaction['remaining_credits']:,}", inline=True)

            footer_text = "Ship added to your hangar!" if is_ship else "Items added to your inventory!"
            embed.set_footer(text=footer_text)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"Player {player['id']} bought {quantity}x {transaction['item_name']} "
                f"for {transaction['total_cost']} credits"
            )

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 400:
                # Try to get the error message from the response
                try:
                    error_detail = e.response.json().get("detail", "Invalid request")
                    await interaction.followup.send(f"❌ {error_detail}", ephemeral=True)
                except Exception:  # pylint: disable=broad-exception-caught
                    await interaction.followup.send("❌ Invalid purchase request.", ephemeral=True)
            elif e.response.status_code == 404:
                await interaction.followup.send("❌ Item not found in shop.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /buy: {e}")
            await interaction.followup.send("⚠️ An error occurred while processing purchase.", ephemeral=True)

    # Mapping from internal item_type values to sell-compatible type strings
    _SELL_TYPE_MAP = {
        "ship": "ship",
        "module": "module",
        "weapon": "weapon",
        "turret": "turret",
        "primary_weapon": "weapon",
        "secondary_weapon": "weapon",
        "turret_weapon": "turret",
    }

    # Human-readable labels for item types
    _ITEM_TYPE_LABELS = {
        "ship": "Ship",
        "module": "Module",
        "primary_weapon": "Primary Weapon",
        "secondary_weapon": "Secondary Weapon",
        "turret_weapon": "Turret Weapon",
        "weapon": "Weapon",
        "turret": "Turret",
    }

    async def sell_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Live autocomplete for inventory items the player can sell.

        Fetches player's full inventory and filters by item_type if provided.
        Display format: "Item Name (Type)" — value is the item_name.
        """
        try:
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)
            if not player:
                return []
            resp = await self.http_client.get(f"{api_base}/inventory/player/{player['id']}", timeout=5)
            if resp.status_code != 200:
                return []
            items = resp.json()

            # Get the current item_type filter from the interaction namespace
            item_type_filter: str | None = None
            with contextlib.suppress(AttributeError):
                item_type_filter = interaction.namespace.item_type or None

            norm_current = normalize_for_search(current)
            choices: list[app_commands.Choice[str]] = []
            for inv_item in items:
                name = inv_item.get("item_name", "")
                raw_type = inv_item.get("item_type", "")

                # Filter by item_type if one was provided
                if item_type_filter:
                    sell_type = self._SELL_TYPE_MAP.get(raw_type, raw_type)
                    if sell_type != item_type_filter and raw_type != item_type_filter:
                        continue

                type_label = self._ITEM_TYPE_LABELS.get(raw_type, raw_type.replace("_", " ").title())
                label = f"{name} ({type_label})"
                if norm_current in normalize_for_search(label):
                    choices.append(app_commands.Choice(name=label[:100], value=name))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _resolve_sell_item_type(self, player_id: int, item_name: str) -> str | None:
        """Look up the item_type for a named item in the player's inventory."""
        try:
            resp = await self.http_client.get(f"{api_base}/inventory/player/{player_id}", timeout=5)
            if resp.status_code != 200:
                return None
            for inv_item in resp.json():
                if inv_item.get("item_name") == item_name:
                    raw_type = inv_item.get("item_type", "")
                    return self._SELL_TYPE_MAP.get(raw_type, raw_type)
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    @app_commands.command(name="sell", description="Sell an item back to the shop")
    @app_commands.describe(
        item_type="Filter by item type (optional: ship, weapon, module, turret)",
        item="Item to sell — pick from your inventory",
        quantity="Quantity to sell (default: 1)",
        target_tier="Shop tier to sell to (default: Bronze)",
    )
    @app_commands.autocomplete(
        item=sell_item_autocomplete, item_type=item_type_autocomplete, target_tier=tier_autocomplete
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        item: str,
        item_type: str | None = None,
        quantity: int = 1,
        target_tier: str = "Bronze",
    ):
        """Sell item back to shop."""
        await interaction.response.defer(thinking=True)

        try:
            if quantity <= 0:
                await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                return

            if item_type is not None and item_type not in self._valid_item_types:
                await interaction.followup.send(
                    f"❌ Invalid item type. Valid types: {', '.join(self._valid_item_types)}", ephemeral=True
                )
                return

            if target_tier not in self._valid_tiers:
                await interaction.followup.send(
                    f"❌ Invalid tier. Valid tiers: {', '.join(self._valid_tiers)}", ephemeral=True
                )
                return

            # Get player data
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # If item_type was not provided, auto-detect it from the player's inventory
            resolved_item_type = item_type
            if not resolved_item_type:
                resolved_item_type = await self._resolve_sell_item_type(player["id"], item)
                if not resolved_item_type:
                    await interaction.followup.send(
                        f"❌ Could not determine item type for **{item}**. Please specify the item type manually.",
                        ephemeral=True,
                    )
                    return

            # Make sell request
            sell_data = {
                "player_id": player["id"],
                "item_type": resolved_item_type,
                "item_name": item,
                "quantity": quantity,
                "target_tier": target_tier,
            }

            resp = await self.http_client.post(f"{api_base}/shops/sell", json=sell_data, timeout=10)
            resp.raise_for_status()
            transaction = resp.json()

            # Success message
            embed = discord.Embed(
                title="✅ Sale Successful!",
                description=f"You sold **{quantity}x {item}** to the {target_tier} shop",
                color=discord.Color.green(),
            )

            embed.add_field(name="Item Type", value=resolved_item_type.title(), inline=True)
            embed.add_field(name="Quantity", value=str(quantity), inline=True)
            embed.add_field(name="Total Value", value=f"{transaction['total_value']:,} credits", inline=True)
            embed.add_field(name="New Credits", value=f"{transaction['remaining_credits']:,}", inline=True)

            await interaction.followup.send(embed=embed)
            flogger.info(f"Player {player['id']} sold {quantity}x {item} for {transaction['total_value']} credits")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    error_detail = e.response.json().get("detail", "Invalid request")
                    await interaction.followup.send(f"❌ {error_detail}", ephemeral=True)
                except Exception:  # pylint: disable=broad-exception-caught
                    await interaction.followup.send("❌ Invalid sell request.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /sell: {e}")
            await interaction.followup.send("⚠️ An error occurred while processing sale.", ephemeral=True)

    @app_commands.command(name="shops", description="View summary of all guild shops")
    async def shops(self, interaction: discord.Interaction):
        """Display summary of all guild shops."""
        await interaction.response.defer(thinking=True)

        try:
            # Get shops summary
            resp = await self.http_client.get(f"{api_base}/shops/guild/{interaction.guild_id}/summary", timeout=10)
            resp.raise_for_status()
            summary = resp.json()

            # Get player data for tier info
            player = await self._get_player_data(interaction.user.id, interaction.guild_id)

            # Create summary embed
            embed = discord.Embed(
                title="🏪 Guild Shops Summary",
                description=f"Total items across all shops: {summary['total_items']}",
                color=discord.Color.blue(),
            )

            if player:
                embed.set_author(name=f"Your tier: {player['tier']}", icon_url=interaction.user.display_avatar.url)

            # Add shop info for each tier
            for tier in self._valid_tiers:
                if tier in summary["shops"]:
                    shop_info = summary["shops"][tier]

                    # Check if player can access this tier
                    if player:
                        player_tier_level = self._valid_tiers.index(player["tier"])
                        tier_level = self._valid_tiers.index(tier)
                        accessible = "🔓" if tier_level <= player_tier_level else "🔒"
                    else:
                        accessible = "🔒"

                    embed.add_field(
                        name=f"{accessible} {tier} Shop",
                        value=f"Items: {shop_info['items']}\nTotal Stock: {shop_info['total_quantity']}",
                        inline=True,
                    )
                else:
                    embed.add_field(name=f"🔒 {tier} Shop", value="Empty", inline=True)

            embed.set_footer(text="Use /shop <tier> to browse a specific shop")

            await interaction.followup.send(embed=embed)
            flogger.debug(f"/shops by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /shops: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching shops summary.", ephemeral=True)

    def _get_tier_color(self, tier: str) -> discord.Color:
        """Get Discord color based on tier."""
        tier_colors = {
            "Bronze": discord.Color.from_rgb(205, 127, 50),
            "Silver": discord.Color.from_rgb(192, 192, 192),
            "Gold": discord.Color.from_rgb(255, 215, 0),
            "Platinum": discord.Color.from_rgb(229, 228, 226),
        }
        return tier_colors.get(tier, discord.Color.default())

    @shop.error
    async def shop_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /shop", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @buy.error
    async def buy_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /buy", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @sell.error
    async def sell_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /sell", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @shops.error
    async def shops_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /shops", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up ShopCog...")
    await bot.add_cog(ShopCog(bot))
    flogger.info("ShopCog loaded")
