"""
Complete Shop Service for the BountyBot inventory system.

Handles business logic for multi-tier shop management including:
- Tier-based shop access control
- Automatic shop generation and refresh
- Purchase and sell transactions
- Item pricing and availability
"""

import random
from datetime import UTC, datetime
from typing import Any

from persist.models.guild_shop import GuildShop
from persist.models.player_ship import PlayerShip
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.ship_repository import ShipRepository
from persist.repositories.shop_repository import ShopRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("shop-service")


class ShopService:
    def __init__(self):
        self.shop_repo = ShopRepository()
        self.config_repo = ConfigRepository()
        self.player_repo = PlayerRepository()
        self.inventory_repo = InventoryRepository()
        self.ship_repo = ShipRepository()
        self.player_ship_repo = PlayerShipRepository()
        self.primary_weapon_repo = PrimaryWeaponRepository()
        self.secondary_weapon_repo = SecondaryWeaponRepository()
        self.turret_weapon_repo = TurretWeaponRepository()
        self.module_repo = ModuleRepository()

        # In-memory cache for static game data, populated by
        # preload_static_data() before bulk refresh operations.
        # This avoids re-querying the same immutable item tables
        # per guild x tier (from ~420K queries down to ~4 at 1000 guilds).
        self._static_cache: dict[str, list] | None = None
        # Price lookup cache: item_name → value
        self._price_cache: dict[str, int] | None = None

    # Valid tiers and item types
    VALID_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]
    VALID_ITEM_TYPES = ["ship", "weapon", "module", "turret"]

    async def preload_static_data(self, db: AsyncSession) -> None:
        """Pre-load all static game item data into memory.

        Call this once before a bulk shop refresh cycle to avoid
        re-querying the same immutable tables for every guild x tier
        combination.  At 1000 guilds x 3 tiers this reduces ~420K
        DB queries to 4 (one per item type).
        """
        self._static_cache = {
            "ship": await self.ship_repo.list_all(db),
            "weapon": await self.primary_weapon_repo.list_all(db),
            "module": await self.module_repo.list_all(db),
            "turret": await self.turret_weapon_repo.list_all(db),
        }
        # Build price lookup from all item types (includes secondary weapons)
        self._price_cache = {}
        secondary_weapons = await self.secondary_weapon_repo.list_all(db)
        for items in self._static_cache.values():
            for item in items:
                self._price_cache[item.name] = item.value
        for item in secondary_weapons:
            self._price_cache[item.name] = item.value

        flogger.info(
            f"Preloaded static data: "
            f"{len(self._static_cache['ship'])} ships, "
            f"{len(self._static_cache['weapon'])} weapons, "
            f"{len(self._static_cache['module'])} modules, "
            f"{len(self._static_cache['turret'])} turrets, "
            f"{len(self._price_cache)} prices cached"
        )

    def clear_static_cache(self) -> None:
        """Clear the static data cache after a bulk refresh cycle."""
        self._static_cache = None
        self._price_cache = None

    async def get_shop_items(
        self, db: AsyncSession, guild_id: int, tier: str, item_type: str | None = None
    ) -> list[GuildShop]:
        """
        Get shop items for a specific guild tier.
        Optionally filter by item type.
        """
        try:
            if tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid tier: {tier}")

            if item_type and item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            # Check if shop needs refresh
            await self._check_and_refresh_shop(db, guild_id, tier)

            # Get shop items
            items = await self.shop_repo.get_shop_items(db, guild_id, tier, item_type)

            flogger.debug(f"Retrieved {len(items)} items from {tier} shop in guild {guild_id}")
            return items

        except Exception as e:
            flogger.error(f"Error getting shop items for guild {guild_id}, tier {tier}: {e}")
            raise

    async def purchase_item(
        self, db: AsyncSession, player_id: int, shop_item_id: int, quantity: int = 1
    ) -> dict[str, Any]:
        """
        Purchase an item from the shop.

        Returns transaction details including cost and remaining shop quantity.
        """
        try:
            # Get player and validate
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Get shop item
            shop_item = await self.shop_repo.get_by_id(db, shop_item_id)
            if not shop_item:
                raise ValueError(f"Shop item {shop_item_id} not found")

            # Validate tier access
            if not self._can_access_tier(player.tier, shop_item.tier):
                raise ValueError(f"Player tier {player.tier} cannot access {shop_item.tier} shop")

            # Check quantity availability
            if shop_item.quantity < quantity:
                raise ValueError(f"Insufficient quantity. Available: {shop_item.quantity}, Requested: {quantity}")

            # Calculate total cost
            total_cost = shop_item.price * quantity

            # Check player credits
            if player.credits < total_cost:
                raise ValueError(f"Insufficient credits. Cost: {total_cost}, Available: {player.credits}")

            # Lock player row and re-check credits under lock
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")
            if player.credits < total_cost:
                raise ValueError(f"Insufficient credits. Cost: {total_cost}, Available: {player.credits}")

            # Deduct credits from player
            player.credits -= total_cost

            # Add item to player inventory
            await self.inventory_repo.add_item(db, player_id, shop_item.item_type, shop_item.item_name, quantity)

            # Remove item from shop
            new_shop_quantity = shop_item.quantity - quantity
            if new_shop_quantity <= 0:
                # Remove item entirely if quantity reaches 0
                await db.delete(shop_item)
            else:
                shop_item.quantity = new_shop_quantity

            await db.commit()
            await db.refresh(player)

            transaction_details = {
                "player_id": player_id,
                "item_type": shop_item.item_type,
                "item_name": shop_item.item_name,
                "quantity": quantity,
                "unit_price": shop_item.price,
                "total_cost": total_cost,
                "remaining_credits": player.credits,
                "remaining_shop_quantity": new_shop_quantity,
            }

            flogger.info(f"Player {player_id} purchased {quantity}x {shop_item.item_name} for {total_cost} credits")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error purchasing item {shop_item_id} for player {player_id}: {e}")
            raise

    async def purchase_ship(
        self,
        db: AsyncSession,
        player_id: int,
        shop_item_id: int,
        sell_old_ship: bool = False,
    ) -> dict[str, Any]:
        """Purchase a ship from the shop with optional trade-in.

        Args:
            player_id: The purchasing player
            shop_item_id: The shop item to buy (must be a ship)
            sell_old_ship: If True, sell old active ship for credit toward purchase

        Returns:
            Transaction details dict
        """
        try:
            # Validate player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate shop item exists
            shop_item = await self.shop_repo.get_by_id(db, shop_item_id)
            if not shop_item:
                raise ValueError(f"Shop item {shop_item_id} not found")

            # Validate item is a ship
            if shop_item.item_type != "ship":
                raise ValueError(f"Shop item {shop_item_id} is not a ship (type={shop_item.item_type})")

            # Get static ship data for the new ship (slot limits)
            new_ship_static = await self.ship_repo.get_by_name(db, shop_item.item_name)
            if not new_ship_static:
                raise ValueError(f"Static ship data not found for '{shop_item.item_name}'")

            new_ship_price = shop_item.price

            # Get player's current active ship
            old_player_ship = await self.player_ship_repo.get_active_ship(db, player_id)
            old_ship_value = 0
            old_ship_static = None

            if old_player_ship:
                old_ship_static = await self.ship_repo.get_by_name(db, old_player_ship.ship_name)
                if old_ship_static:
                    old_ship_value = old_ship_static.value

            # Perform transaction atomically (credit check is done under lock below)
            async with db.begin():
                # Lock the player row to prevent concurrent credit modifications
                player = await self.player_repo.get_by_id_for_update(db, player_id)
                if not player:
                    raise ValueError(f"Player {player_id} not found")

                # Re-check credits under lock (prevents TOCTOU race)
                if sell_old_ship and old_player_ship:
                    effective_cost = new_ship_price - old_ship_value
                    if player.credits < effective_cost:
                        raise ValueError(
                            f"Insufficient credits. Cost: {new_ship_price}, "
                            f"Trade-in value: {old_ship_value}, "
                            f"Net cost: {effective_cost}, "
                            f"Available: {player.credits}"
                        )
                else:
                    if player.credits < new_ship_price:
                        raise ValueError(f"Insufficient credits. Cost: {new_ship_price}, Available: {player.credits}")

                # a. Create new PlayerShip record for the player (inactive for now)
                new_player_ship = PlayerShip(
                    player_id=player_id,
                    ship_name=shop_item.item_name,
                    is_active=False,
                    weapons=[],
                    modules=[],
                    turrets=[],
                )
                db.add(new_player_ship)
                await db.flush()  # Get the new ship's ID

                # b. Transfer equipped items from old ship to new ship
                items_transferred: dict[str, list[str]] = {
                    "weapons": [],
                    "modules": [],
                    "turrets": [],
                }
                items_unequipped: dict[str, list[str]] = {
                    "weapons": [],
                    "modules": [],
                    "turrets": [],
                }

                if old_player_ship:
                    # Determine slot limits on new ship
                    slot_limits = {
                        "weapons": new_ship_static.max_primaries,
                        "modules": new_ship_static.max_modules,
                        "turrets": new_ship_static.max_turrets,
                    }
                    inventory_type_map = {
                        "weapons": "weapon",
                        "modules": "module",
                        "turrets": "turret",
                    }

                    for equip_type in ("weapons", "modules", "turrets"):
                        old_items: list[str] = list(getattr(old_player_ship, equip_type) or [])
                        max_slots = slot_limits[equip_type]
                        inv_type = inventory_type_map[equip_type]

                        # Items that fit on new ship
                        fitting = old_items[:max_slots]
                        overflow = old_items[max_slots:]

                        items_transferred[equip_type] = fitting
                        items_unequipped[equip_type] = overflow

                        # Unequip overflow items to inventory
                        for item_name in overflow:
                            await self.inventory_repo.add_item(db, player_id, inv_type, item_name, 1)

                    # Apply transferred loadout to new ship
                    new_player_ship.weapons = items_transferred["weapons"]
                    new_player_ship.modules = items_transferred["modules"]
                    new_player_ship.turrets = items_transferred["turrets"]

                # c. Handle old ship trade-in
                if sell_old_ship and old_player_ship:
                    # Add old ship to shop stock
                    await self._add_item_to_shop(
                        db,
                        player.guild_id,
                        shop_item.tier,
                        "ship",
                        old_player_ship.ship_name,
                        1,
                        old_ship_value,
                    )
                    # Delete old PlayerShip record
                    await db.delete(old_player_ship)
                    await db.flush()

                # d. Set new ship as active (deactivate all others first)
                from sqlalchemy import update as sa_update

                await db.execute(sa_update(PlayerShip).where(PlayerShip.player_id == player_id).values(is_active=False))
                new_player_ship.is_active = True

                # e. Calculate and set final credit balance in a single update
                if sell_old_ship and old_player_ship:
                    updated_credits = player.credits + old_ship_value - new_ship_price
                else:
                    updated_credits = player.credits - new_ship_price
                await self.player_repo.update_credits(db, player_id, updated_credits, commit=False)

                # f. Remove new ship from shop stock
                new_shop_quantity = shop_item.quantity - 1
                if new_shop_quantity <= 0:
                    await self.shop_repo.remove(db, shop_item)
                else:
                    await self.shop_repo.update_quantity(db, shop_item_id, new_shop_quantity)

            total_overflow = sum(len(v) for v in items_unequipped.values())
            total_transferred = sum(len(v) for v in items_transferred.values())

            transaction_details = {
                "player_id": player_id,
                "item_type": "ship",
                "item_name": shop_item.item_name,
                "quantity": 1,
                "unit_price": new_ship_price,
                "total_cost": new_ship_price,
                "trade_in_value": old_ship_value if sell_old_ship and old_player_ship else 0,
                "net_cost": new_ship_price - (old_ship_value if sell_old_ship and old_player_ship else 0),
                "remaining_credits": updated_credits,
                "items_transferred": total_transferred,
                "items_unequipped_to_inventory": total_overflow,
                "remaining_shop_quantity": new_shop_quantity,
            }

            flogger.info(
                f"Player {player_id} purchased ship '{shop_item.item_name}' for {new_ship_price} credits"
                + (f" (trade-in: {old_ship_value})" if sell_old_ship and old_player_ship else "")
            )
            return transaction_details

        except Exception as e:
            flogger.error(f"Error purchasing ship {shop_item_id} for player {player_id}: {e}")
            raise

    async def sell_item(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1,
        target_tier: str = "Bronze",
    ) -> dict[str, Any]:
        """
        Sell an item back to the shop.

        Items are added to the specified tier shop and player receives credits.
        """
        try:
            # Get player and validate
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate inputs
            if item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            if target_tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid target tier: {target_tier}")

            # Check if player has the item
            inventory_item = await self.inventory_repo.get_player_item(db, player_id, item_type, item_name)
            if not inventory_item or inventory_item.quantity < quantity:
                available = inventory_item.quantity if inventory_item else 0
                raise ValueError(f"Insufficient item quantity. Available: {available}, Requested: {quantity}")

            # Calculate item price from static item data (full value, no sell tax)
            base_price = await self._get_item_base_price(db, item_name)
            unit_sell_price = base_price
            total_sell_value = unit_sell_price * quantity

            # Perform transaction atomically
            async with db.begin():
                # Lock player row to prevent concurrent credit modifications
                player = await self.player_repo.get_by_id_for_update(db, player_id)
                if not player:
                    raise ValueError(f"Player {player_id} not found")

                # Remove item from player inventory
                await self.inventory_repo.remove_item(db, player_id, item_type, item_name, quantity)

                # Add credits to player
                await self.player_repo.update_credits(db, player_id, player.credits + total_sell_value, commit=False)

                # Add item to target shop
                await self._add_item_to_shop(
                    db, player.guild_id, target_tier, item_type, item_name, quantity, base_price
                )

            transaction_details = {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": quantity,
                "unit_sell_price": unit_sell_price,
                "total_sell_value": total_sell_value,
                "new_credits": player.credits + total_sell_value,
                "target_shop_tier": target_tier,
            }

            flogger.info(f"Player {player_id} sold {quantity}x {item_name} for {total_sell_value} credits")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error selling item {item_name} for player {player_id}: {e}")
            raise

    async def sell_ship(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        clear_equipment: bool = False,
        target_tier: str = "Bronze",
    ) -> dict[str, Any]:
        """Sell a player's ship to the shop.

        Args:
            player_id: The selling player
            ship_id: The PlayerShip ID to sell
            clear_equipment: If True, unequip all items to inventory before selling
            target_tier: Which shop tier to add the ship to

        Raises:
            ValueError: If ship is the active ship, doesn't belong to player, etc.
        """
        try:
            if target_tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid target tier: {target_tier}")

            # Validate player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Get the PlayerShip by ID
            player_ship = await self.player_ship_repo.get_by_id(db, ship_id)
            if not player_ship:
                raise ValueError(f"Ship {ship_id} not found")

            # Validate ownership
            if player_ship.player_id != player_id:
                raise ValueError(f"Ship {ship_id} does not belong to player {player_id}")

            # Reject selling the active ship
            if player_ship.is_active:
                raise ValueError("Cannot sell active ship")

            # Get static ship data to find base value
            ship_static = await self.ship_repo.get_by_name(db, player_ship.ship_name)
            ship_value = ship_static.value if ship_static else 0

            # Track items moved to inventory (for response)
            items_unequipped: dict[str, list[str]] = {
                "weapons": [],
                "modules": [],
                "turrets": [],
            }
            inventory_type_map = {
                "weapons": "weapon",
                "modules": "module",
                "turrets": "turret",
            }

            async with db.begin():
                # Lock the player row to prevent concurrent credit modifications
                player = await self.player_repo.get_by_id_for_update(db, player_id)
                if not player:
                    raise ValueError(f"Player {player_id} not found")

                if clear_equipment:
                    # Unequip all items to player inventory before selling ship
                    for equip_type in ("weapons", "modules", "turrets"):
                        equipped: list[str] = list(getattr(player_ship, equip_type) or [])
                        inv_type = inventory_type_map[equip_type]
                        for item_name in equipped:
                            await self.inventory_repo.add_item(db, player_id, inv_type, item_name, 1)
                            items_unequipped[equip_type].append(item_name)

                # Credit player with ship's full value (no tax)
                await self.player_repo.update_credits(db, player_id, player.credits + ship_value, commit=False)

                # Remove the PlayerShip from database
                await db.delete(player_ship)
                await db.flush()

                # Add the ship to shop stock
                await self._add_item_to_shop(
                    db,
                    player.guild_id,
                    target_tier,
                    "ship",
                    player_ship.ship_name,
                    1,
                    ship_value,
                )

            total_unequipped = sum(len(v) for v in items_unequipped.values())
            transaction_details = {
                "player_id": player_id,
                "item_type": "ship",
                "item_name": player_ship.ship_name,
                "ship_id": ship_id,
                "quantity": 1,
                "sell_value": ship_value,
                "new_credits": player.credits + ship_value,
                "target_shop_tier": target_tier,
                "items_unequipped_to_inventory": total_unequipped,
                "items_unequipped_detail": items_unequipped,
            }

            flogger.info(
                f"Player {player_id} sold ship '{player_ship.ship_name}' (id={ship_id}) "
                f"for {ship_value} credits" + (f", unequipped {total_unequipped} items" if total_unequipped else "")
            )
            return transaction_details

        except Exception as e:
            flogger.error(f"Error selling ship {ship_id} for player {player_id}: {e}")
            raise

    async def refresh_shop(
        self, db: AsyncSession, guild_id: int, tier: str, force_tech_level: int | None = None
    ) -> dict[str, Any]:
        """
        Refresh a shop's inventory based on guild configuration.

        Optionally force a specific tech level instead of random selection.
        """
        try:
            if tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid tier: {tier}")

            if force_tech_level is not None and (force_tech_level < 1 or force_tech_level > 9):
                raise ValueError("Tech level must be between 1 and 9")

            # Get guild configuration
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                # Create default config if none exists
                config = await self.config_repo.create_default_config(db, guild_id)

            # Clear existing shop items for this tier
            await self.shop_repo.clear_shop_tier(db, guild_id, tier)

            # Determine tech level
            shop_tech_level = force_tech_level if force_tech_level else random.randint(1, 9)

            # Generate new shop inventory
            generated_items = []

            for item_type in self.VALID_ITEM_TYPES:
                count_range = config.get_count_range(item_type)
                quantity_range = config.get_quantity_range(item_type)

                item_count = random.randint(count_range["min"], count_range["max"])

                for _ in range(item_count):
                    # Select item based on tech level probabilities
                    item_tech_level = self._select_item_tech_level(shop_tech_level, config.tech_level_probabilities)
                    item_quantity = random.randint(quantity_range["min"], quantity_range["max"])

                    # Get random item of the selected tech level
                    item_name = await self._get_random_item_by_tech_level(db, item_type, item_tech_level)
                    if not item_name:
                        continue  # Skip if no items available at this tech level

                    # Calculate price
                    base_price = await self._get_item_base_price(db, item_name)

                    # Create shop item
                    shop_item_data = {
                        "guild_id": guild_id,
                        "tier": tier,
                        "tech_level": shop_tech_level,
                        "item_type": item_type,
                        "item_name": item_name,
                        "quantity": item_quantity,
                        "price": base_price,
                        "last_restocked": datetime.now(UTC),
                    }

                    shop_item = await self.shop_repo.create_or_update(db, shop_item_data)
                    generated_items.append(shop_item)

            refresh_details = {
                "guild_id": guild_id,
                "tier": tier,
                "tech_level": shop_tech_level,
                "items_generated": len(generated_items),
                "refresh_time": datetime.now(UTC).isoformat(),
            }

            flogger.info(f"Refreshed {tier} shop for guild {guild_id}: {len(generated_items)} items generated")
            return refresh_details

        except Exception as e:
            flogger.error(f"Error refreshing shop for guild {guild_id}, tier {tier}: {e}")
            raise

    async def _check_and_refresh_shop(self, db: AsyncSession, guild_id: int, tier: str) -> None:
        """Check if shop needs refresh and refresh if necessary."""
        try:
            # Get shop items to check last refresh time
            items = await self.shop_repo.get_shop_items(db, guild_id, tier)

            if not items:
                # No items means shop needs initial generation
                await self.refresh_shop(db, guild_id, tier)
                return

            # Check if any items are due for refresh
            needs_refresh = any(item.is_refresh_due() for item in items)

            if needs_refresh:
                await self.refresh_shop(db, guild_id, tier)
                flogger.info(f"Auto-refreshed {tier} shop for guild {guild_id}")

        except Exception as e:
            flogger.error(f"Error checking shop refresh for guild {guild_id}, tier {tier}: {e}")
            raise

    def _can_access_tier(self, player_tier: str, shop_tier: str) -> bool:
        """Check if a player tier can access a shop tier."""
        tier_levels = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
        player_level = tier_levels.get(player_tier, 1)
        shop_level = tier_levels.get(shop_tier, 1)
        return player_level >= shop_level

    def _select_item_tech_level(self, shop_tech_level: int, probabilities: dict[str, float]) -> int:
        """Select item tech level based on shop tech level and probability distribution."""
        same_level_prob = probabilities.get("same_level", 0.7)
        one_lower_prob = probabilities.get("one_lower", 0.2)
        _two_lower_prob = probabilities.get("two_lower", 0.1)

        rand = random.random()

        if rand < same_level_prob:
            return shop_tech_level
        if rand < same_level_prob + one_lower_prob:
            return max(1, shop_tech_level - 1)
        return max(1, shop_tech_level - 2)

    async def _get_random_item_by_tech_level(self, db: AsyncSession, item_type: str, tech_level: int) -> str | None:
        """Get a random item name by type and tech level.

        Uses the in-memory static cache if available (populated by
        :meth:`preload_static_data`), otherwise falls back to direct
        DB queries.
        """
        if item_type == "ship":
            all_ships = (
                self._static_cache["ship"] if self._static_cache is not None else await self.ship_repo.list_all(db)
            )
            if not all_ships:
                return None
            weights = [(s.shop_spawn_rate if s.shop_spawn_rate is not None else 1.0) for s in all_ships]
            chosen = random.choices(all_ships, weights=weights, k=1)[0]
            return chosen.name

        if item_type == "weapon":
            all_weapons = (
                self._static_cache["weapon"]
                if self._static_cache is not None
                else await self.primary_weapon_repo.list_all(db)
            )
            items = [w for w in all_weapons if w.tech_level == tech_level]
            return random.choice(items).name if items else None

        if item_type == "module":
            all_modules = (
                self._static_cache["module"] if self._static_cache is not None else await self.module_repo.list_all(db)
            )
            items = [m for m in all_modules if m.tech_level == tech_level]
            return random.choice(items).name if items else None

        if item_type == "turret":
            all_turrets = (
                self._static_cache["turret"]
                if self._static_cache is not None
                else await self.turret_weapon_repo.list_all(db)
            )
            items = [t for t in all_turrets if t.tech_level == tech_level]
            return random.choice(items).name if items else None

        return None

    async def _get_item_base_price(self, db: AsyncSession, item_name: str) -> int:
        """Look up the item's value field. Returns 0 if not found.

        Uses the in-memory price cache if available (populated by
        :meth:`preload_static_data`), otherwise falls back to direct
        DB queries.
        """
        # Fast path: use pre-built price cache
        if self._price_cache is not None:
            return self._price_cache.get(item_name, 0)

        # Slow path: try each repository in turn until we find the item
        for repo in (
            self.ship_repo,
            self.primary_weapon_repo,
            self.secondary_weapon_repo,
            self.turret_weapon_repo,
            self.module_repo,
        ):
            item = await repo.get_by_name(db, item_name)
            if item is not None:
                return item.value
        return 0

    async def _add_item_to_shop(
        self, db: AsyncSession, guild_id: int, tier: str, item_type: str, item_name: str, quantity: int, base_price: int
    ) -> None:
        """Add an item to a shop (used when players sell items)."""
        try:
            # Check if item already exists in shop
            existing_item = await self.shop_repo.get_shop_item_by_name(db, guild_id, tier, item_name)

            if existing_item:
                # Update quantity
                new_quantity = existing_item.quantity + quantity
                await self.shop_repo.update_quantity(db, existing_item.id, new_quantity)
            else:
                # Create new shop item
                shop_item_data = {
                    "guild_id": guild_id,
                    "tier": tier,
                    "tech_level": 1,  # Default tech level for sold items
                    "item_type": item_type,
                    "item_name": item_name,
                    "quantity": quantity,
                    "price": base_price,
                    "last_restocked": datetime.now(UTC),
                }
                await self.shop_repo.create_or_update(db, shop_item_data)

        except Exception as e:
            flogger.error(f"Error adding item to shop: {e}")
            raise
