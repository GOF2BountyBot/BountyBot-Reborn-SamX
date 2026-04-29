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
from persist.repositories.item_repository import ItemRepository
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

from services._item_type_normalizer import expand_item_type_to_concrete
from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants

flogger = bblogger.get_logger("shop-service")

# Map concrete item_type → GuildConfig key used by get_count_range() / get_quantity_range().
# These config keys are generic (legacy) and GuildConfig hasn't been updated yet.
# When GuildConfig gains a "secondary_weapon" key, add it here.
_CONCRETE_TO_CONFIG_KEY: dict[str, str] = {
    "ship": "ship",
    "primary_weapon": "weapon",
    "module": "module",
    "turret_weapon": "turret",
    # secondary_weapon → future: "secondary_weapon"
}


class ShopService:  # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.shop_repo = ShopRepository()
        self.config_repo = ConfigRepository()
        self.player_repo = PlayerRepository()
        self.inventory_repo = InventoryRepository()
        self.item_repo = ItemRepository()
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

    # Valid tiers
    VALID_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]

    async def preload_static_data(self, db: AsyncSession) -> None:
        """Pre-load all static game item data into memory.

        Call this once before a bulk shop refresh cycle to avoid
        re-querying the same immutable tables for every guild x tier
        combination.  At 1000 guilds x 3 tiers this reduces ~420K
        DB queries to 4 (one per item type).
        """
        # Internal cache keys (not user-facing item_type values).
        # See A.45 spec §2 for the wire-boundary vocab rule.
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
        """Get shop items for a specific guild tier, optionally filtered by item type.

        *item_type* may be a concrete type (``"primary_weapon"``) or a generic
        alias (``"weapon"``).  Generic aliases are expanded to all
        currently-enabled concrete types.  Unknown types raise
        ``InvalidItemTypeError`` (mapped to HTTP 422 by the router).
        """
        try:
            if tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid tier: {tier}")

            # Check if shop needs refresh
            await self._check_and_refresh_shop(db, guild_id, tier)

            # Get shop items
            if item_type is None:
                items = await self.shop_repo.get_shop_items(db, guild_id, tier)
            else:
                concrete_types = expand_item_type_to_concrete(item_type, context="playable")
                if len(concrete_types) == 1:
                    items = await self.shop_repo.get_shop_items(db, guild_id, tier, concrete_types[0])
                else:
                    items = await self.shop_repo.get_shop_items_by_types(db, guild_id, tier, concrete_types)

            flogger.debug(f"Retrieved {len(items)} items from {tier} shop in guild {guild_id}")
            return items

        except (InvalidItemTypeError, ValueError):
            raise
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

            # Transaction is owned by the caller (router).
            # Lock the player row to prevent concurrent credit modifications.
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
                secondary_weapons=[],
            )
            db.add(new_player_ship)
            await db.flush()  # Get the new ship's ID

            # b. Transfer equipped items from old ship to new ship via the
            # LoadoutConsistencyService choke-point (B.19 fix).
            # The service clears the old ship's slot lists as part of the
            # transfer — fixes the cross-ship duplication bug observed in
            # B.19 (recon root cause #2).  Overflow items go to inventory
            # using concrete item types resolved via STI discriminator.
            from services.loadout_consistency_service import LoadoutConsistencyService

            # Share repo handles so service-level test mocks propagate.
            consistency = LoadoutConsistencyService(
                player_ship_repo=self.player_ship_repo,
                inventory_repo=self.inventory_repo,
                item_repo=self.item_repo,
                ship_repo=self.ship_repo,
            )

            slot_limits = {
                "weapons": new_ship_static.max_primaries,
                "modules": new_ship_static.max_modules,
                "turrets": new_ship_static.max_turrets,
                "secondary_weapons": getattr(new_ship_static, "max_secondaries", 0) or 0,
            }
            transfer_result = await consistency.transfer_loadout_to_new_ship(
                db,
                player_id=player_id,
                src_ship=old_player_ship,
                dst_ship=new_player_ship,
                slot_limits=slot_limits,
            )
            # Translate breakdown to legacy shape used by transaction_details below.
            items_transferred: dict[str, list[str]] = {
                kind: list(transfer_result["breakdown"][kind]["transferred"]) for kind in slot_limits
            }
            items_unequipped: dict[str, list[str]] = {
                kind: list(transfer_result["breakdown"][kind]["overflowed"]) for kind in slot_limits
            }

            # c. Handle old ship trade-in
            if sell_old_ship and old_player_ship:
                # Add old ship to shop stock (commit=False — caller's transaction controls commit)
                await self._add_item_to_shop(
                    db,
                    player.guild_id,
                    shop_item.tier,
                    "ship",
                    old_player_ship.ship_name,
                    1,
                    old_ship_value,
                    commit=False,
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

            # f. Remove new ship from shop stock (commit=False — caller's transaction controls commit)
            new_shop_quantity = shop_item.quantity - 1
            if new_shop_quantity <= 0:
                await self.shop_repo.remove(db, shop_item, commit=False)
            else:
                await self.shop_repo.update_quantity(db, shop_item_id, new_shop_quantity, commit=False)

            total_overflow = sum(len(v) for v in items_unequipped.values()) if old_player_ship else 0
            total_transferred = sum(len(v) for v in items_transferred.values()) if old_player_ship else 0

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
        item_name: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Sell an item back to the shop.

        item_type is resolved from the player's inventory row by item_name (A.42b).
        Items always land in the player's current tier shop (consistent with /buy
        tier-gating — A.42c).

        Raises:
            ValueError: If player not found, item not in inventory, or insufficient quantity.
            InvalidItemTypeError: If multiple inventory rows match item_name with different
                concrete types (cross-type name collision — impossible in current catalog,
                but guarded defensively).
        """
        try:
            # Get player and validate
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Items always land in the player's current tier shop (consistent with /buy tier-gating)
            target_tier = player.tier
            if target_tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid player tier: {target_tier}")

            # Resolve concrete item_type from the player's inventory by item_name (A.42b).
            # Writes must use a single concrete type (never a generic alias).
            all_matching = await self.inventory_repo.get_player_items_by_name(db, player_id, item_name)
            if not all_matching:
                raise ValueError(f"Item '{item_name}' not found in your inventory")

            # Guard: cross-type name collision is impossible in the current catalog (verified:
            # 146 items, 146 distinct names, zero cross-type name collisions), but defensively
            # checked here.  If two rows exist for the same name with different concrete types,
            # we cannot safely pick one without ambiguity.
            unique_types = {row.item_type for row in all_matching}
            if len(unique_types) > 1:
                raise InvalidItemTypeError(
                    f"Ambiguous item '{item_name}': found in inventory under multiple concrete types "
                    f"{sorted(unique_types)}. Cannot determine which to sell. This should not occur "
                    f"with the current item catalog; please report this as a data integrity issue."
                )
            concrete_type = next(iter(unique_types))

            # Sum quantities across all rows with the same name+type (should be exactly 1 row)
            inventory_item = all_matching[0]
            if inventory_item.quantity < quantity:
                available = inventory_item.quantity
                raise ValueError(f"Insufficient item quantity. Available: {available}, Requested: {quantity}")

            # Calculate item price from static item data (full value, no sell tax)
            base_price = await self._get_item_base_price(db, item_name)
            unit_sell_price = base_price
            total_sell_value = unit_sell_price * quantity

            # Transaction is owned by the caller (router).
            # Lock player row to prevent concurrent credit modifications.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Remove item from player inventory (commit=False — caller's transaction controls commit)
            await self.inventory_repo.remove_item(db, player_id, concrete_type, item_name, quantity, commit=False)

            # Compute the new credit balance ONCE, locally, BEFORE the update.
            # Reading player.credits AFTER update_credits() would yield the post-update value
            # (the ORM-mutation refactor preserves this contract, and the locally-captured-value
            # pattern matches buy_ship / transfer_credits / consolidate_inventory).
            new_credits = player.credits + total_sell_value
            await self.player_repo.update_credits(db, player_id, new_credits, commit=False)

            # Add item to target shop (using concrete type)
            await self._add_item_to_shop(
                db, player.guild_id, target_tier, concrete_type, item_name, quantity, base_price, commit=False
            )

            transaction_details = {
                "player_id": player_id,
                "item_type": concrete_type,
                "item_name": item_name,
                "quantity": quantity,
                "unit_sell_price": unit_sell_price,
                "total_sell_value": total_sell_value,
                "new_credits": new_credits,
                "target_shop_tier": target_tier,
            }

            flogger.info(
                f"Player {player_id} sold {quantity}x {item_name} ({concrete_type}) for {total_sell_value} credits"
            )
            return transaction_details

        except (InvalidItemTypeError, ValueError):
            raise
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
                "secondary_weapons": [],
            }

            # Transaction is owned by the caller (router).
            # Lock the player row to prevent concurrent credit modifications.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if clear_equipment:
                # Package G (B.19): use the LoadoutConsistencyService choke-point
                # (anti-duplication guard prevents legacy phantom-item exploit).
                from services.loadout_consistency_service import LoadoutConsistencyService

                consistency = LoadoutConsistencyService(
                    player_ship_repo=self.player_ship_repo,
                    inventory_repo=self.inventory_repo,
                    item_repo=self.item_repo,
                    ship_repo=self.ship_repo,
                )
                evac = await consistency.evacuate_ship_loadout_to_inventory(db, ship=player_ship)
                items_unequipped = {kind: list(v) for kind, v in evac["items_returned_detail"].items()}

            # Credit player with ship's full value (no tax; commit=False — caller's transaction).
            # Compute new_credits ONCE, locally, BEFORE the update. See sell_item for rationale.
            new_credits = player.credits + ship_value
            await self.player_repo.update_credits(db, player_id, new_credits, commit=False)

            # Remove the PlayerShip from database
            await db.delete(player_ship)
            await db.flush()

            # Add the ship to shop stock (commit=False — caller's transaction controls commit)
            await self._add_item_to_shop(
                db,
                player.guild_id,
                target_tier,
                "ship",
                player_ship.ship_name,
                1,
                ship_value,
                commit=False,
            )

            total_unequipped = sum(len(v) for v in items_unequipped.values())
            transaction_details = {
                "player_id": player_id,
                "item_type": "ship",
                "item_name": player_ship.ship_name,
                "ship_id": ship_id,
                "quantity": 1,
                "sell_value": ship_value,
                "new_credits": new_credits,
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

            # Get guild configuration — fail if guild hasn't been set up
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                from services.exceptions import GuildNotConfiguredError

                raise GuildNotConfiguredError(guild_id)

            # Clear existing shop items for this tier
            await self.shop_repo.clear_shop_tier(db, guild_id, tier)

            # Determine tech level
            shop_tech_level = force_tech_level if force_tech_level else random.randint(1, 9)

            # Generate new shop inventory.
            # Use concrete item types derived from CURRENTLY_ENABLED_TYPES to avoid
            # writing generic aliases to guild_shops.item_type.
            # Only types that have a GuildConfig count_range key are generated;
            # secondary_weapon is excluded until mechanics ship (no config key yet).
            _generation_types = tuple(t for t in GameConstants.CURRENTLY_ENABLED_TYPES if t in _CONCRETE_TO_CONFIG_KEY)
            generated_items = []

            for concrete_type in _generation_types:
                config_key = _CONCRETE_TO_CONFIG_KEY[concrete_type]
                count_range = config.get_count_range(config_key)
                quantity_range = config.get_quantity_range(config_key)
                item_type = concrete_type  # alias for _get_random_item_by_tech_level

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
        """Get a random item name by (concrete) type and tech level.

        Accepts both concrete types (``"primary_weapon"``, ``"turret_weapon"``)
        and the legacy generic aliases (``"weapon"``, ``"turret"``) for backward
        compatibility with callers that haven't been migrated.

        Uses the in-memory static cache if available (populated by
        :meth:`preload_static_data`), otherwise falls back to direct DB queries.
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

        if item_type in ("weapon", "primary_weapon"):
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

        if item_type in ("turret", "turret_weapon"):
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
        self,
        db: AsyncSession,
        guild_id: int,
        tier: str,
        item_type: str,
        item_name: str,
        quantity: int,
        base_price: int,
        commit: bool = True,
    ) -> None:
        """Add an item to a shop (used when players sell items).

        Args:
            commit: When False, flush without committing (use when the caller owns
                the transaction, e.g. inside a router-level db.begin() context).
        """
        try:
            # Check if item already exists in shop
            existing_item = await self.shop_repo.get_shop_item_by_name(db, guild_id, tier, item_name)

            if existing_item:
                # Update quantity
                new_quantity = existing_item.quantity + quantity
                await self.shop_repo.update_quantity(db, existing_item.id, new_quantity, commit=commit)
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
                await self.shop_repo.create_or_update(db, shop_item_data, commit=commit)

        except Exception as e:
            flogger.error(f"Error adding item to shop: {e}")
            raise
