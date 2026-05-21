import os

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.http_error_handler import report_api_error
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search

from utils import autocomplete_state

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


def _format_shop_item_stats(item: dict) -> str:
    """Return a stat suffix string for a shop item, matching /loadout display style.

    Format examples:
      Primary/Secondary/Turret Weapon → " | DPS: 92.3"
      Shield Module                   → " | Shield: 380"
      Armour Module                   → " | Armour: 250"
      Ship                            → " | Hull: 1200"
      Items with no relevant stat     → ""  (empty — no trailing pipe)

    The pipe separator is only included when a stat is present.
    DPS is rounded to 1 decimal place.
    Shield and Armour are mutually exclusive per item line (first found wins).
    """
    item_type = item.get("item_type", "")

    if item_type in ("primary_weapon", "secondary_weapon", "turret_weapon"):
        dps = item.get("dps")
        if dps is not None and float(dps) != 0.0:
            return f" | DPS: {float(dps):.1f}"
        return ""

    if item_type == "module":
        shield = item.get("shield")
        if shield is not None and int(shield) != 0:
            return f" | Shield: {int(shield)}"
        armour = item.get("armour")
        if armour is not None and int(armour) != 0:
            return f" | Armour: {int(armour)}"
        return ""

    if item_type == "ship":
        hull_hp = item.get("hull_hp")
        if hull_hp is not None and int(hull_hp) != 0:
            return f" | Hull: {int(hull_hp)}"
        return ""

    return ""


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
        self._valid_item_types = ["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
        self._shop_cache: AutocompleteCache[tuple, list] = AutocompleteCache(
            ttl_seconds=3600.0,  # 60 min dead-man switch; refresh job runs every 6 min
            refresh_fn=self._fetch_tier_shop,
            name="shopCog-shop-cache",
        )
        flogger.debug("ShopCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _get_player_data(self, user_id: int, guild_id: int, display_name: str | None = None) -> dict | None:
        """Helper to get player data from Discord user ID.

        Returns None on any error, OR raises GuildNotConfigured sentinel
        string "GUILD_NOT_CONFIGURED" so callers can surface the right message.
        """
        try:
            user_data = {
                "discord_id": user_id,
                "guild_id": guild_id,
                "discord_username": None,
                "display_name": display_name,
            }

            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                raise
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    async def _fetch_tier_shop(self, key: tuple) -> list:
        """Fetch shop items for a (guild_id, tier) key.  Called by _shop_cache on miss/expiry.

        Phase 7: Pre-computes ``_norm`` on each item dict at fill time so the
        hot-path autocomplete scan never calls ``normalize_for_search`` per item.
        """
        guild_id, tier = key
        try:
            resp = await self.http_client.get(f"{api_base}/shops/guild/{guild_id}/tier/{tier}", timeout=5)
            if resp.status_code != 200:
                return []
            items = resp.json()
            # Pre-compute _norm at fill time — hot path uses pre-computed value.
            for item in items:
                label = f"{item.get('item_name', '')} ({item.get('price', 0):,}cr)"
                item["_norm"] = normalize_for_search(label)
            return items
        except Exception:  # pylint: disable=broad-exception-caught
            return []

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
            app_commands.Choice(name=item_type.replace("_", " ").title(), value=item_type)
            for item_type in self._valid_item_types
            if norm_current in normalize_for_search(item_type)
        ]

    @app_commands.command(name="shop", description="Browse your tier's guild shop")
    @app_commands.describe(
        item_type="Filter by item type (ship, primary_weapon, secondary_weapon, turret_weapon, module)",
    )
    @app_commands.autocomplete(item_type=item_type_autocomplete)
    async def shop(self, interaction: discord.Interaction, item_type: str | None = None):
        """Browse the guild shop for your current tier.

        Strict same-tier access: the shop you can see and transact at always
        matches your current tier. Promotion / demotion is the only path
        between tiers (no buy-down to lower-tier shops, no preview of
        higher-tier shops).
        """
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            player = await self._get_player_data(
                interaction.user.id,
                interaction.guild_id,
                display_name=getattr(interaction.user, "display_name", None),
            )
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            raw_player_tier: str = player.get("tier") or "Bronze"
            if raw_player_tier not in self._valid_tiers:
                flogger.warning(
                    f"/shop: player tier {raw_player_tier!r} not in valid tiers for "
                    f"guild={interaction.guild_id} user={interaction.user.id}; treating as Bronze"
                )
                raw_player_tier = "Bronze"
            tier = raw_player_tier

            # Get shop items — peek cache first for unfiltered view
            if not item_type:
                items = self._shop_cache.peek((interaction.guild_id, tier))
                if items is None:
                    resp = await self.http_client.get(
                        f"{api_base}/shops/guild/{interaction.guild_id}/tier/{tier}", timeout=10
                    )
                    resp.raise_for_status()
                    items = resp.json()
            else:
                # item_type filter requires HTTP (cache stores full unfiltered list)
                resp = await self.http_client.get(
                    f"{api_base}/shops/guild/{interaction.guild_id}/tier/{tier}",
                    params={"item_type": item_type},
                    timeout=10,
                )
                resp.raise_for_status()
                items = resp.json()

            if not items:
                type_filter = f" ({item_type.replace('_', ' ').title()}s)" if item_type else ""
                await interaction.followup.send(f"🏪 The {tier} shop{type_filter} is currently empty.", ephemeral=True)
                return

            # Create shop embed
            title = f"🏪 {tier} Shop"
            if item_type:
                title += f" - {item_type.replace('_', ' ').title()}s"

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
                    stat_suffix = _format_shop_item_stats(item)

                    price_text = f"{item['price']:,} credits"
                    if player["credits"] < item["price"]:
                        price_text = f"~~{price_text}~~ 💸"

                    name_display = f"{emoji} **{item['item_name']}**" if emoji else f"**{item['item_name']}**"
                    item_line = f"{name_display}{stat_suffix} {tech_level} {quantity}"
                    items_text += f"{item_line}\n    {price_text} | ID: {item['id']}\n"

                if len(type_items) > 10:
                    items_text += f"... and {len(type_items) - 10} more items\n"

                label = type_labels.get(item_type_key, f"{item_type_key.replace('_', ' ').title()}s")
                embed.add_field(
                    name=f"{label} ({len(type_items)})",
                    value=items_text or "None available",
                    inline=False,
                )

            embed.set_footer(text=f"Use /buy <item_id> [quantity] to purchase items | Your tier: {raw_player_tier}")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.debug(f"/shop {tier} by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            else:
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /shop: {e}")
            await interaction.followup.send("⚠️ An error occurred while fetching shop items.", ephemeral=True)

    async def buy_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Zero-HTTP autocomplete for shop items the player can buy.

        Phase 6: Both player tier and shop inventory are served from caches with
        peek() — zero HTTP calls on the hot path. On a cold miss, schedules a
        background refresh and returns [] immediately.

        Player tier comes from autocomplete_state.player_cache (keyed by
        (guild_id, discord_user_id)). Shop inventory comes from _shop_cache
        (keyed by (guild_id, tier)).
        """
        try:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            # HOT PATH: resolve player tier from shared player cache — no HTTP
            pc = autocomplete_state.player_cache
            player = pc.peek((guild_id, user_id)) if pc else None
            if player is None and pc is not None:
                player = await pc.get_with_timeout((guild_id, user_id), timeout=1.0)
            if player is None:
                return []

            # E.2: Guard against unrecognized tier values before indexing the list.
            if player.get("tier") not in self._valid_tiers:
                flogger.warning(
                    f"buy_item_autocomplete: unrecognized player tier={player.get('tier')!r} "
                    f"guild={guild_id} user={user_id}; "
                    "returning empty autocomplete"
                )
                return []

            # Strict same-tier: autocomplete only surfaces items at the player's
            # current tier. No buy-down to lower-tier shops.
            tier = player["tier"]

            # HOT PATH: peek shop cache — no HTTP
            items = self._shop_cache.peek((guild_id, tier))
            if items is None:
                items = await self._shop_cache.get_with_timeout((guild_id, tier), timeout=1.0)
            if items is None:
                return []

            norm_current = normalize_for_search(current)
            choices: list[app_commands.Choice[int]] = []
            for item in items:
                label = f"{item['item_name']} ({item['price']:,}cr)"
                # Phase 7: use pre-computed _norm; fall back to on-the-fly for items pushed
                # before this phase (e.g. via the internal push endpoint with old shape).
                norm_label = item.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
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
            player = await self._get_player_data(
                interaction.user.id,
                interaction.guild_id,
                display_name=getattr(interaction.user, "display_name", None),
            )
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Get shop item details first
            item_resp = await self.http_client.get(f"{api_base}/shops/item/{item_id}", timeout=10)
            item_resp.raise_for_status()
            shop_item = item_resp.json()

            # Strict same-tier check: a player may only buy from the shop matching
            # their current tier. Mirrors shop_service._can_access_tier (== not >=)
            # post-promote-flow-correctness PR.
            if shop_item["tier"] != player["tier"]:
                await interaction.followup.send(
                    f"🔒 This item is in the **{shop_item['tier']}** shop. "
                    f"You can only buy from your current tier (**{player['tier']}**). "
                    "Use `/promote` or `/demote` to change tiers.",
                    ephemeral=True,
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

            # Invalidate the shop cache for the purchased item's tier so the next
            # autocomplete reflects the updated stock count.
            try:
                self._shop_cache.invalidate((interaction.guild_id, shop_item["tier"]))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/buy: cache invalidation failed for guild={interaction.guild_id} "
                    f"tier={shop_item.get('tier')}; transaction still succeeded"
                )

            # Invalidate player and inventory caches — credits changed; inventory grew
            try:
                autocomplete_state.invalidate_player(interaction.guild_id, interaction.user.id)
                autocomplete_state.invalidate_inventory(interaction.guild_id, player["id"])
                if is_ship:
                    autocomplete_state.invalidate_ships(interaction.guild_id, player["id"])
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/buy: shared cache invalidation failed for player_id={player['id']}; transaction still succeeded"
                )

            # Success message
            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=f"You bought **{quantity}x {transaction['item_name']}**",
                color=discord.Color.green(),
            )

            embed.add_field(name="Item Type", value=transaction["item_type"].replace("_", " ").title(), inline=True)
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
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /buy: {e}")
            await interaction.followup.send("⚠️ An error occurred while processing purchase.", ephemeral=True)

    # Human-readable labels for item types (concrete types only — no generic aliases)
    _ITEM_TYPE_LABELS = {
        "ship": "Ship",
        "module": "Module",
        "primary_weapon": "Primary Weapon",
        "secondary_weapon": "Secondary Weapon",
        "turret_weapon": "Turret Weapon",
    }

    async def sell_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP autocomplete for inventory items and inactive ships the player can sell.

        Phase 6: Player identity, inventory, and ships are served from caches with
        peek() — zero HTTP calls on the hot path. On a cold miss, schedules a
        background refresh and returns [] immediately.

        Player record comes from autocomplete_state.player_cache to get the
        bot-core player_id. Inventory comes from autocomplete_state.inventory_cache
        (keyed by (guild_id, player_id)). Inactive ships come from
        autocomplete_state.ships_cache (keyed by (guild_id, player_id)).

        Ship choices are prefixed with "ship:" so the sell command can route
        them to the /shops/sell-ship endpoint instead of /shops/sell.
        """
        try:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            # HOT PATH: resolve player from shared player cache — no HTTP
            pc = autocomplete_state.player_cache
            player = pc.peek((guild_id, user_id)) if pc else None
            if player is None and pc is not None:
                player = await pc.get_with_timeout((guild_id, user_id), timeout=1.0)
            if player is None:
                return []

            player_id = player.get("id")
            if not player_id:
                return []

            # HOT PATH: peek inventory cache — no HTTP
            ic = autocomplete_state.inventory_cache
            items = ic.peek((guild_id, player_id)) if ic else None
            if items is None and ic is not None:
                items = await ic.get_with_timeout((guild_id, player_id), timeout=1.0)
            if items is None:
                return []

            norm_current = normalize_for_search(current)
            choices: list[app_commands.Choice[str]] = []
            for inv_item in items:
                # inventory_cache stores NormalizedChoice objects with .label, .value, .norm, .raw
                raw = inv_item.raw if hasattr(inv_item, "raw") else inv_item
                name = raw.get("item_name", "") if isinstance(raw, dict) else getattr(inv_item, "label", "")
                if hasattr(inv_item, "label"):
                    label = inv_item.label
                    norm_label = inv_item.norm
                else:
                    raw_type = raw.get("item_type", "")
                    type_label = self._ITEM_TYPE_LABELS.get(raw_type, raw_type.replace("_", " ").title())
                    label = f"{name} ({type_label})"
                    norm_label = normalize_for_search(label)

                if norm_current in norm_label:
                    choices.append(app_commands.Choice(name=label[:100], value=name))

            # HOT PATH: peek ships cache for inactive ships — no HTTP
            sc = autocomplete_state.ships_cache
            ships = sc.peek((guild_id, player_id)) if sc else None
            if ships is None and sc is not None:
                ships = await sc.get_with_timeout((guild_id, player_id), timeout=1.0)
            if ships is not None:
                for ship_choice in ships:
                    raw = ship_choice.raw if hasattr(ship_choice, "raw") else {}
                    if isinstance(raw, dict):
                        is_active = raw.get("is_active", False)
                    else:
                        is_active = getattr(raw, "is_active", False)
                    # Skip active ship — cannot sell active ship
                    if is_active:
                        continue

                    # Build "Name (inactive ship)" label from raw data — never reuse
                    # pre-computed label which may contain empty parens like "Betty ()"
                    # from a blank nickname (GROUP-A cosmetic fix).
                    if isinstance(raw, dict):
                        nickname = (raw.get("nickname") or "").strip()
                        ship_name = raw.get("name") or raw.get("ship_name") or "Unknown"
                        ship_display = nickname if nickname else ship_name
                    else:
                        nickname = getattr(raw, "nickname", None)
                        ship_display = (nickname.strip() if nickname else None) or getattr(raw, "name", "Unknown")

                    # Extract the player_ship_id for routing the sell request
                    if isinstance(raw, dict):
                        ship_id = raw.get("player_ship_id") or raw.get("id")
                    else:
                        ship_id = getattr(raw, "player_ship_id", None) or getattr(raw, "id", None)

                    if ship_id is None:
                        # Fall back to the choice value
                        ship_id = ship_choice.value if hasattr(ship_choice, "value") else None

                    if ship_id is None:
                        continue

                    label = f"{ship_display} (inactive ship)"[:100]
                    norm_label = normalize_for_search(label)
                    if norm_current in norm_label:
                        # Encode as "ship:<player_ship_id>" so sell handler can route correctly
                        value = f"ship:{ship_id}"
                        choices.append(app_commands.Choice(name=label, value=value))

            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    @app_commands.command(name="sell", description="Sell an item back to the shop")
    @app_commands.describe(
        item="Item to sell — pick from your inventory",
        quantity="Quantity to sell (default: 1)",
    )
    @app_commands.autocomplete(item=sell_item_autocomplete)
    async def sell(
        self,
        interaction: discord.Interaction,
        item: str,
        quantity: int = 1,
    ):
        """Sell item back to shop.

        The server resolves item_type from the player's inventory by item_name.
        The item is always routed to the player's current tier shop (consistent
        with /buy tier-gating — A.42b/A.42c).
        """
        await interaction.response.defer(thinking=True)

        try:
            if quantity <= 0:
                await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                return

            # Get player data
            player = await self._get_player_data(
                interaction.user.id,
                interaction.guild_id,
                display_name=getattr(interaction.user, "display_name", None),
            )
            if not player:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            # Check if this is an inactive ship sale (value encoded as "ship:<player_ship_id>")
            is_ship_sale = item.startswith("ship:")

            if is_ship_sale:
                # Route to sell-ship endpoint
                try:
                    ship_id = int(item[len("ship:") :])
                except ValueError:
                    await interaction.followup.send("❌ Invalid ship selection.", ephemeral=True)
                    return

                sell_data = {
                    "player_id": player["id"],
                    "ship_id": ship_id,
                    "clear_equipment": True,  # return equipped items to inventory
                    "target_tier": player.get("tier", "Bronze"),
                }
                resp = await self.http_client.post(f"{api_base}/shops/sell-ship", json=sell_data, timeout=10)
                resp.raise_for_status()
                transaction = resp.json()

                # Invalidate player, inventory, and ships caches
                try:
                    self._shop_cache.invalidate((interaction.guild_id, player["tier"]))
                    autocomplete_state.invalidate_player(interaction.guild_id, interaction.user.id)
                    autocomplete_state.invalidate_inventory(interaction.guild_id, player["id"])
                    autocomplete_state.invalidate_ships(interaction.guild_id, player["id"])
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"/sell ship: cache invalidation failed for player_id={player['id']}; "
                        "transaction still succeeded"
                    )

                ship_name = transaction.get("item_name", "Ship")
                embed = discord.Embed(
                    title="✅ Ship Sold!",
                    description=f"You sold your **{ship_name}**",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Item Type", value="Ship", inline=True)
                embed.add_field(name="Sale Value", value=f"{transaction.get('total_value', 0):,} credits", inline=True)
                embed.add_field(name="New Credits", value=f"{transaction.get('remaining_credits', 0):,}", inline=True)
                unequipped = transaction.get("items_unequipped_to_inventory", 0)
                if unequipped:
                    embed.add_field(
                        name="Items Returned",
                        value=f"{unequipped} item(s) returned to inventory",
                        inline=False,
                    )

                await interaction.followup.send(embed=embed)
                flogger.info(
                    f"Player {player['id']} sold ship id={ship_id} ({ship_name}) "
                    f"for {transaction.get('total_value', 0)} credits"
                )
                return

            # Regular item sell — no item_type or target_tier; server resolves both
            sell_data = {
                "player_id": player["id"],
                "item_name": item,
                "quantity": quantity,
            }

            resp = await self.http_client.post(f"{api_base}/shops/sell", json=sell_data, timeout=10)
            resp.raise_for_status()
            transaction = resp.json()

            # Invalidate the shop cache for the player's tier — sold items land there.
            try:
                self._shop_cache.invalidate((interaction.guild_id, player["tier"]))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/sell: cache invalidation failed for guild={interaction.guild_id} "
                    f"tier={player.get('tier')}; transaction still succeeded"
                )

            # Invalidate player and inventory caches — credits changed; inventory shrank
            try:
                autocomplete_state.invalidate_player(interaction.guild_id, interaction.user.id)
                autocomplete_state.invalidate_inventory(interaction.guild_id, player["id"])
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/sell: shared cache invalidation failed for player_id={player['id']}; transaction still succeeded"
                )

            # Success message
            embed = discord.Embed(
                title="✅ Sale Successful!",
                description=f"You sold **{quantity}x {item}**",
                color=discord.Color.green(),
            )

            item_type_label = self._ITEM_TYPE_LABELS.get(
                transaction.get("item_type", ""), transaction.get("item_type", "").replace("_", " ").title()
            )
            embed.add_field(name="Item Type", value=item_type_label, inline=True)
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
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error in /sell: {e}")
            await interaction.followup.send("⚠️ An error occurred while processing sale.", ephemeral=True)

    @app_commands.command(name="shops", description="View summary of all guild shops")
    async def shops(self, interaction: discord.Interaction):
        """Display summary of all guild shops."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            # Get shops summary
            resp = await self.http_client.get(f"{api_base}/shops/guild/{interaction.guild_id}/summary", timeout=10)
            resp.raise_for_status()
            summary = resp.json()

            # Get player data for tier info
            player = await self._get_player_data(
                interaction.user.id,
                interaction.guild_id,
                display_name=getattr(interaction.user, "display_name", None),
            )

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

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.debug(f"/shops by {interaction.user} in guild {interaction.guild_id}")

        except httpx.HTTPStatusError as e:
            await report_api_error(interaction, e)
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
