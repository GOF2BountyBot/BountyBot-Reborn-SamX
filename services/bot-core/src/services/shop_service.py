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
from persist.repositories.commodity_repository import CommodityRepository
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
from services.bounty_service import get_secondary_subtype
from services.combat_models import DEFERRED_SECONDARY_SUBTYPES
from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants, resolve_constant
from services.game_maths import ship_tech_level_for_value

flogger = bblogger.get_logger("shop-service")

# Map concrete item_type → GuildConfig key used by get_count_range() / get_quantity_range().
# Each type has its own dedicated key so primaries and secondaries draw from independent ranges.
_CONCRETE_TO_CONFIG_KEY: dict[str, str] = {
    "ship": "ship",
    "primary_weapon": "weapon",
    "secondary_weapon": "secondary_weapon",  # dedicated key — mirrors weapon range (min:3/max:5)
    "module": "module",
    "turret_weapon": "turret",
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
        # C-1 (PvC loot): commodities are priced from Item.value like any other item,
        # but are never stocked in a GuildShop (no _CONCRETE_TO_CONFIG_KEY entry).
        self.commodity_repo = CommodityRepository()

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
            "secondary": await self.secondary_weapon_repo.list_all(db),
            "module": await self.module_repo.list_all(db),
            "turret": await self.turret_weapon_repo.list_all(db),
        }
        # Build price lookup from all item types (includes secondary weapons).
        self._price_cache = {}
        for items in self._static_cache.values():
            for item in items:
                self._price_cache[item.name] = item.value

        # C-1 (PvC loot): commodities are priced from Item.value like any other item
        # so a commodity sell resolves a real face value (not 0). They are added to the
        # PRICE cache only — NOT to _static_cache's shop-generation keys — so refresh_shop
        # (which iterates _CONCRETE_TO_CONFIG_KEY, with no commodity entry) never stocks them.
        _commodities = await self.commodity_repo.list_all(db)
        for item in _commodities:
            self._price_cache[item.name] = item.value

        flogger.info(
            f"Preloaded static data: "
            f"{len(self._static_cache['ship'])} ships, "
            f"{len(self._static_cache['weapon'])} weapons, "
            f"{len(self._static_cache['secondary'])} secondary weapons, "
            f"{len(self._static_cache['module'])} modules, "
            f"{len(self._static_cache['turret'])} turrets, "
            f"{len(_commodities)} commodities, "
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
            # D5-T2 (lock-ordering rule 1): lock the Player row FIRST so the locked
            # read is the first player access feeding the credit read-modify-write.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Get shop item
            shop_item = await self.shop_repo.get_by_id(db, shop_item_id)
            if not shop_item:
                raise ValueError(f"Shop item {shop_item_id} not found")

            # Validate tier access (reads the locked player row)
            if not self._can_access_tier(player.tier, shop_item.tier):
                raise ValueError(f"Player tier {player.tier} cannot access {shop_item.tier} shop")

            # Check quantity availability
            if shop_item.quantity < quantity:
                raise ValueError(f"Insufficient quantity. Available: {shop_item.quantity}, Requested: {quantity}")

            # Calculate total cost
            total_cost = shop_item.price * quantity

            # Check player credits under lock (prevents TOCTOU race)
            if player.credits < total_cost:
                raise ValueError(f"Insufficient credits. Cost: {total_cost}, Available: {player.credits}")

            # Deduct credits from player
            player.credits -= total_cost

            # Auto-cancel any pending duels the player can no longer cover after
            # this purchase. Must run before commit so both mutations are atomic.
            # Non-fatal: a failure here must never block a legitimate purchase.
            try:
                from services.duel_service import DuelService  # deferred to avoid circular import

                await DuelService().cancel_underfunded_duels(db, player_id, commit=False)
            except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"cancel_underfunded_duels failed after buy_item for player_id={player_id}: {_duel_exc}"
                )

            # CI-16: secondary_weapon top-up — if name is already equipped on the active ship,
            # add rounds directly to secondary_ammo instead of cargo.
            # Keep atomic: all mutations are commit=False; single db.commit() below covers all.
            _topped_up_ammo = False
            if shop_item.item_type == "secondary_weapon":
                active_ship = await self.player_ship_repo.get_active_ship(db, player_id)
                if active_ship is not None and shop_item.item_name in (active_ship.secondary_weapons or []):
                    # Top up ammo sidecar (reassign — never mutate in place)
                    _ship_ammo: dict[str, int] = dict(getattr(active_ship, "secondary_ammo", None) or {})
                    _ship_ammo[shop_item.item_name] = _ship_ammo.get(shop_item.item_name, 0) + quantity
                    active_ship.secondary_ammo = _ship_ammo
                    await db.flush()
                    flogger.info(
                        f"Player {player_id} top-up secondary '{shop_item.item_name}' +{quantity} rounds "
                        f"on ship {active_ship.id} (ammo now {_ship_ammo[shop_item.item_name]})"
                    )
                    _topped_up_ammo = True

            if not _topped_up_ammo:
                # Add item to player inventory (commit=False — this service owns the
                # explicit single commit below). B.34 closeout: previously this used
                # the default commit=True, which committed the credit deduction
                # mid-flow and left a window where the shop-quantity update could
                # fail and leave player credit-deducted-with-item but shop unchanged.
                await self.inventory_repo.add_item(
                    db, player_id, shop_item.item_type, shop_item.item_name, quantity, commit=False
                )

            # Remove item from shop
            new_shop_quantity = shop_item.quantity - quantity
            if new_shop_quantity <= 0:
                # Remove item entirely if quantity reaches 0
                await db.delete(shop_item)
            else:
                shop_item.quantity = new_shop_quantity

            # Single atomic commit covering: player credits, inventory add, shop quantity update.
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
    ) -> dict[str, Any]:
        """Purchase a ship from the shop.

        The old active ship remains in the player's fleet as an inactive PlayerShip.
        Gear is transferred from the old ship to the new one (B.95).

        Args:
            player_id: The purchasing player
            shop_item_id: The shop item to buy (must be a ship)

        Returns:
            Transaction details dict
        """
        try:
            # D5-T2 (lock-ordering rule 1): acquire the Player-row aggregate lock
            # FIRST — before any read whose value feeds the credit/loadout
            # read-modify-write below.  ``activate_ship`` (called later) re-locks the
            # same Player row via the choke-point's ``_lock_player``; that re-acquire
            # is an intra-transaction no-op (a txn may re-hold its own row lock), so
            # the loadout lock and this credit lock collapse into one lock class with
            # no A-then-B hazard.  Previously this method read ``get_by_id`` (unlocked)
            # for the tier-access validation and only locked at the credit re-check,
            # leaving the lock as the SECOND player access; the locked read is now the
            # FIRST player access, satisfying "first lock = most restrictive mode".
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate shop item exists
            shop_item = await self.shop_repo.get_by_id(db, shop_item_id)
            if not shop_item:
                raise ValueError(f"Shop item {shop_item_id} not found")

            # Validate item is a ship
            if shop_item.item_type != "ship":
                raise ValueError(f"Shop item {shop_item_id} is not a ship (type={shop_item.item_type})")

            # Validate tier access (mirrors purchase_item — closes a privilege-escalation
            # gap where ships from any tier shop could be purchased without restriction).
            # Reads the locked player row.
            if not self._can_access_tier(player.tier, shop_item.tier):
                raise ValueError(f"Player tier {player.tier} cannot access {shop_item.tier} shop")

            # Get static ship data for the new ship (slot limits)
            new_ship_static = await self.ship_repo.get_by_name(db, shop_item.item_name)
            if not new_ship_static:
                raise ValueError(f"Static ship data not found for '{shop_item.item_name}'")

            new_ship_price = shop_item.price

            # Re-check credits under lock (prevents TOCTOU race)
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

            # b. Activate the new ship via the canonical choke-point (B.94/B.95 fix).
            # The old active ship remains in the fleet as an inactive PlayerShip — it is
            # never deleted here.  Gear transfers from the old ship to the new one (B.95).
            # The choke-point: reconciles slots → transfers loadout from current active
            # ship (if any) → flips is_active → updates active_ship_id.
            from services.loadout_consistency_service import LoadoutConsistencyService

            # Share repo handles so service-level test mocks propagate.
            consistency = LoadoutConsistencyService(
                player_ship_repo=self.player_ship_repo,
                inventory_repo=self.inventory_repo,
                item_repo=self.item_repo,
                ship_repo=self.ship_repo,
                player_repo=self.player_repo,
            )
            activation_result = await consistency.activate_ship(
                db,
                player_id=player_id,
                target_ship_id=new_player_ship.id,
                player_repo=self.player_repo,
            )

            # Translate breakdown to legacy shape used by transaction_details below.
            transfer_breakdown = activation_result.get("transfer_breakdown", {})
            slot_kinds = ("weapons", "modules", "turrets", "secondary_weapons")
            items_transferred: dict[str, list[str]] = {
                kind: list(transfer_breakdown.get(kind, {}).get("transferred", [])) for kind in slot_kinds
            }
            items_unequipped: dict[str, list[str]] = {
                kind: list(transfer_breakdown.get(kind, {}).get("overflowed", [])) for kind in slot_kinds
            }

            # c. Calculate and set final credit balance in a single update
            updated_credits = player.credits - new_ship_price
            await self.player_repo.update_credits(db, player_id, updated_credits, commit=False)

            # Auto-cancel any pending duels the player can no longer cover.
            # Non-fatal: a failure here must never block a legitimate ship purchase.
            try:
                from services.duel_service import DuelService  # deferred to avoid circular import

                await DuelService().cancel_underfunded_duels(db, player_id, commit=False)
            except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"cancel_underfunded_duels failed after purchase_ship for player_id={player_id}: {_duel_exc}"
                )

            # d. Remove new ship from shop stock (commit=False — caller's transaction controls commit)
            new_shop_quantity = shop_item.quantity - 1
            if new_shop_quantity <= 0:
                await self.shop_repo.remove(db, shop_item, commit=False)
            else:
                await self.shop_repo.update_quantity(db, shop_item_id, new_shop_quantity, commit=False)

            total_overflow = sum(len(v) for v in items_unequipped.values())
            total_transferred = sum(len(v) for v in items_transferred.values())

            transaction_details = {
                "player_id": player_id,
                "item_type": "ship",
                "item_name": shop_item.item_name,
                "quantity": 1,
                "unit_price": new_ship_price,
                "total_cost": new_ship_price,
                "trade_in_value": 0,
                "net_cost": new_ship_price,
                "remaining_credits": updated_credits,
                "items_transferred": total_transferred,
                "items_unequipped_to_inventory": total_overflow,
                "remaining_shop_quantity": new_shop_quantity,
            }

            flogger.info(f"Player {player_id} purchased ship '{shop_item.item_name}' for {new_ship_price} credits")
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
            # D5-T2 (lock-ordering rule 1): lock the Player row FIRST so the locked
            # read is the first player access feeding the credit/inventory
            # read-modify-write below.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
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

            # C-1 (PvC loot): commodities sell as a PURE FACE-VALUE SINK — the player is
            # paid Item.value × qty × LOOT_COMMODITY_SELL_FRACTION and the units are
            # DESTROYED. A commodity must NEVER be added to a GuildShop (it cannot be
            # bought), so this branch removes from inventory + credits the player but does
            # NOT call _add_item_to_shop. Weapon/Module selling is unchanged (the else
            # path below stocks the shop).
            if concrete_type == "commodity":
                # LOOT_COMMODITY_SELL_FRACTION is a tunable knob (T2): env-overridable via
                # BOUNTYBOT_LOOT_COMMODITY_SELL_FRACTION + a per-guild GuildConfig column
                # (guild_configs.loot_commodity_sell_fraction). Resolve the per-guild
                # override here off the seller's guild config (NULL column ⇒ env-resolved
                # GameConstants default), consistent with the other 18 loot knobs. The
                # config lookup is best-effort: an unconfigured guild falls back to the
                # default rather than blocking a sell.
                guild_config = await self.config_repo.get_by_guild_id(db, player.guild_id)
                sell_fraction = resolve_constant(
                    guild_config, "loot_commodity_sell_fraction", GameConstants.LOOT_COMMODITY_SELL_FRACTION
                )
                # §5.7 / §9 C-2: payout = Item.value × qty × fraction with the
                # truncation applied ONCE to the full product. Truncating per-unit
                # before multiplying silently underpays once the fraction is tunable
                # below 1.0 (e.g. value=1, fraction=0.5, qty=10 → per-unit int(0.5)=0
                # would credit 0; the single-truncation product correctly credits 5).
                total_sell_value = int(base_price * sell_fraction * quantity)
                # Display-only per-unit figure, derived AFTER from the credited total
                # so it can never diverge from what was actually paid. Guard qty=0
                # (callers reject quantity<1 upstream, but keep the division safe).
                unit_sell_price = total_sell_value // quantity if quantity else 0

                # Player row already locked (FOR UPDATE) at the top of this method (D5-T2).
                # Destroy the sold units (cargo-only remove) — no shop write.
                await self.inventory_repo.remove_item(db, player_id, concrete_type, item_name, quantity, commit=False)

                new_credits = player.credits + total_sell_value
                await self.player_repo.update_credits(db, player_id, new_credits, commit=False)

                transaction_details = {
                    "player_id": player_id,
                    "item_type": concrete_type,
                    "item_name": item_name,
                    "quantity": quantity,
                    "unit_sell_price": unit_sell_price,
                    "total_sell_value": total_sell_value,
                    "new_credits": new_credits,
                    # No store involved: commodities are a sink, never stocked.
                    "target_shop_tier": None,
                    "sunk": True,
                }

                flogger.info(
                    f"Player {player_id} sold (sink) {quantity}x {item_name} (commodity) "
                    f"for {total_sell_value} credits; units destroyed (no shop)"
                )
                return transaction_details

            unit_sell_price = base_price
            total_sell_value = unit_sell_price * quantity

            # Player row already locked (FOR UPDATE) at the top of this method (D5-T2).
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

            # D5-T2 (lock-ordering rule 1): lock the Player row FIRST.  The evacuate
            # choke-point (when clear_equipment) re-locks the SAME player row via
            # ``_lock_player``; that re-acquire is an intra-transaction no-op, so the
            # loadout lock and this credit lock collapse into one lock class.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
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

            # Player row already locked (FOR UPDATE) at the top of this method (D5-T2).
            if clear_equipment:
                # Package G (B.19): use the LoadoutConsistencyService choke-point
                # (anti-duplication guard prevents legacy phantom-item exploit).
                from services.loadout_consistency_service import LoadoutConsistencyService

                consistency = LoadoutConsistencyService(
                    player_ship_repo=self.player_ship_repo,
                    inventory_repo=self.inventory_repo,
                    item_repo=self.item_repo,
                    ship_repo=self.ship_repo,
                    player_repo=self.player_repo,
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

            if force_tech_level is not None and (
                force_tech_level < GameConstants.MIN_TECH_LEVEL or force_tech_level > GameConstants.MAX_TECH_LEVEL
            ):
                raise ValueError(
                    f"Tech level must be between {GameConstants.MIN_TECH_LEVEL} and {GameConstants.MAX_TECH_LEVEL}"
                )

            # Get guild configuration — fail if guild hasn't been set up
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                from services.exceptions import GuildNotConfiguredError

                raise GuildNotConfiguredError(guild_id)

            # Clear existing shop items for this tier
            await self.shop_repo.clear_shop_tier(db, guild_id, tier)

            # Determine tech level
            shop_tech_level = (
                force_tech_level
                if force_tech_level
                else random.randint(GameConstants.MIN_TECH_LEVEL, GameConstants.MAX_TECH_LEVEL)
            )

            # Resolve per-guild combat/filler module-draw probability once for the whole refresh.
            combat_module_prob = resolve_constant(
                config, "shop_combat_module_prob", GameConstants.SHOP_COMBAT_MODULE_PROB
            )

            # Generate new shop inventory.
            # Use concrete item types derived from CURRENTLY_ENABLED_TYPES to avoid
            # writing generic aliases to guild_shops.item_type.
            # secondary_weapon is now included; deferred subtypes (emp-bomb, mine, sentry-gun)
            # are excluded at the item-selection layer in _get_random_item_by_tech_level.
            _generation_types = tuple(t for t in GameConstants.CURRENTLY_ENABLED_TYPES if t in _CONCRETE_TO_CONFIG_KEY)
            generated_items = []
            # Track drawn item_names to avoid duplicate upserts: a name drawn more
            # than once in the same refresh would otherwise hit create_or_update()
            # multiple times for the same row.  Deduplicate here before the upsert
            # so each unique item_name is written exactly once.
            _seen_item_names: set[str] = set()

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
                    item_name = await self._get_random_item_by_tech_level(
                        db, item_type, item_tech_level, combat_module_prob=combat_module_prob
                    )
                    if not item_name:
                        continue  # Skip if no items available at this tech level

                    # Skip duplicate draws (draws-with-replacement): only the first
                    # occurrence of each item_name is upserted in this refresh cycle.
                    if item_name in _seen_item_names:
                        continue
                    _seen_item_names.add(item_name)

                    # Secondaries are consumable rounds — scale the rolled quantity
                    # so one refresh cycle can supply multiple players.
                    if concrete_type == "secondary_weapon":
                        subtype = await self._get_secondary_subtype_by_name(db, item_name)
                        scaler = (
                            GameConstants.SHOP_SECONDARY_QTY_SCALER_HEAVY
                            if subtype in GameConstants.SHOP_HEAVY_SECONDARY_SUBTYPES
                            else GameConstants.SHOP_SECONDARY_QTY_SCALER_STANDARD
                        )
                        item_quantity *= scaler

                    # Calculate price
                    base_price = await self._get_item_base_price(db, item_name)

                    # Row tech_level is the ITEM's actual TL (shown per-item in the
                    # shop listing) — NOT the batch shop_tech_level: draws may land
                    # at TL-1/TL-2, and ships are drawn by spawn-rate weight with a
                    # value-derived TL. The batch TL lives in refresh_details below.
                    # Modules use _get_item_tech_level so the row reflects the actual
                    # catalog TL after step-down (may be < item_tech_level).
                    if concrete_type == "ship":
                        row_tech_level = ship_tech_level_for_value(base_price)
                    elif concrete_type == "module":
                        row_tech_level = await self._get_item_tech_level(db, "module", item_name, base_price)
                    else:
                        row_tech_level = item_tech_level

                    # Create shop item
                    shop_item_data = {
                        "guild_id": guild_id,
                        "tier": tier,
                        "tech_level": row_tech_level,
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
                "items": generated_items,  # include items so executor can announce without re-fetch
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
        """Check if a player tier can access a shop tier.

        Strict same-tier policy: a player may only transact at the shop
        matching their current tier. Promotion / demotion is the only path
        between tiers (no buy-down to lower tiers, no preview of higher
        tiers). Sells are already routed to ``player.tier`` server-side
        (see A.42c), so this guard primarily gates the buy paths.
        """
        tier_levels = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
        player_level = tier_levels.get(player_tier, 1)
        shop_level = tier_levels.get(shop_tier, 1)
        return player_level == shop_level

    def _select_item_tech_level(self, shop_tech_level: int, probabilities: dict[str, float]) -> int:
        """Select item tech level based on shop tech level and probability distribution."""
        same_level_prob = probabilities.get("same_level", 0.7)
        one_lower_prob = probabilities.get("one_lower", 0.2)

        rand = random.random()

        if rand < same_level_prob:
            return shop_tech_level
        if rand < same_level_prob + one_lower_prob:
            return max(1, shop_tech_level - 1)
        return max(1, shop_tech_level - 2)

    async def _get_random_item_by_tech_level(
        self,
        db: AsyncSession,
        item_type: str,
        tech_level: int,
        combat_module_prob: float | None = None,
    ) -> str | None:
        """Get a random item name by (concrete) type and tech level.

        Accepts both concrete types (``"primary_weapon"``, ``"turret_weapon"``)
        and the legacy generic aliases (``"weapon"``, ``"turret"``) for backward
        compatibility with callers that haven't been migrated.

        Uses the in-memory static cache if available (populated by
        :meth:`preload_static_data`), otherwise falls back to direct DB queries.

        For ``item_type == "module"``, ``combat_module_prob`` controls the
        combat-vs-filler bucket split (defaults to
        ``GameConstants.SHOP_COMBAT_MODULE_PROB`` when ``None``).
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
            if combat_module_prob is None:
                combat_module_prob = GameConstants.SHOP_COMBAT_MODULE_PROB
            all_modules = (
                self._static_cache["module"] if self._static_cache is not None else await self.module_repo.list_all(db)
            )
            # Exclude JUNK modules — they are never stocked in the shop.
            pool = [m for m in all_modules if getattr(m, "type", "") not in GameConstants.SHOP_JUNK_MODULE_TYPES]
            # Choose bucket by probability; step down from requested TL until candidates found.
            bucket = (
                GameConstants.SHOP_COMBAT_MODULE_TYPES
                if random.random() < combat_module_prob
                else GameConstants.SHOP_FILLER_MODULE_TYPES
            )
            for tl in range(tech_level, 0, -1):
                candidates = [m for m in pool if getattr(m, "type", "") in bucket and m.tech_level == tl]
                if candidates:
                    return random.choice(candidates).name
            return None

        if item_type == "secondary_weapon":
            all_secondary = (
                self._static_cache["secondary"]
                if self._static_cache is not None
                else await self.secondary_weapon_repo.list_all(db)
            )

            # Filter by tech level and exclude deferred subtypes (emp-bomb, mine, sentry-gun).
            # Subtype unwrap is single-sourced from bounty_service.get_secondary_subtype
            # (avoids drift between generation and shop filtering).
            items = [
                sw
                for sw in all_secondary
                if sw.tech_level == tech_level and get_secondary_subtype(sw) not in DEFERRED_SECONDARY_SUBTYPES
            ]
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

    async def _get_secondary_subtype_by_name(self, db: AsyncSession, item_name: str) -> str:
        """Resolve a secondary weapon's subtype from its name.

        Uses the in-memory static cache when warm (bulk-refresh path),
        otherwise falls back to a direct repository lookup. Returns ""
        if the item cannot be found or carries no subtype.
        """
        if self._static_cache is not None:
            for sw in self._static_cache["secondary"]:
                if sw.name == item_name:
                    return get_secondary_subtype(sw)
            return ""

        sw = await self.secondary_weapon_repo.get_by_name(db, item_name)
        return get_secondary_subtype(sw) if sw is not None else ""

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
            # C-1 (PvC loot): commodities are priced from Item.value too (used by the
            # face-value sell sink). Returned 0 before this repo was wired in.
            self.commodity_repo,
        ):
            item = await repo.get_by_name(db, item_name)
            if item is not None:
                return item.value
        return 0

    # Concrete item_type -> internal _static_cache key (see preload_static_data).
    # Ships are excluded: they carry no tech_level column (derived from value).
    _ITEM_TYPE_TO_CACHE_KEY: dict[str, str] = {
        "primary_weapon": "weapon",
        "secondary_weapon": "secondary",
        "turret_weapon": "turret",
        "module": "module",
    }

    async def _get_item_tech_level(self, db: AsyncSession, item_type: str, item_name: str, base_price: int) -> int:
        """Resolve the item's actual tech level for shop-row display.

        Ships have no tech_level column — theirs is derived from credit value
        (same rule as bounty ship selection). Other types read the catalog
        row's tech_level. Falls back to 1 when the item can't be found.

        Uses the in-memory static cache when warm (populated by
        :meth:`preload_static_data`) — these tables are immutable, so a bulk
        refresh resolves the actual TL from memory rather than re-querying the
        DB once per drawn item. Falls back to a direct repo lookup when cold.
        """
        if item_type == "ship":
            return ship_tech_level_for_value(base_price)

        # Fast path: resolve from the warm static cache (no DB round-trip).
        cache_key = self._ITEM_TYPE_TO_CACHE_KEY.get(item_type)
        if self._static_cache is not None and cache_key is not None:
            for cached in self._static_cache[cache_key]:
                if cached.name == item_name:
                    tech_level = getattr(cached, "tech_level", None)
                    if tech_level is not None:
                        return tech_level
                    break

        # Slow path: direct repo lookup (cold cache / non-bulk callers).
        repo = {
            "primary_weapon": self.primary_weapon_repo,
            "secondary_weapon": self.secondary_weapon_repo,
            "turret_weapon": self.turret_weapon_repo,
            "module": self.module_repo,
        }.get(item_type)
        if repo is not None:
            item = await repo.get_by_name(db, item_name)
            tech_level = getattr(item, "tech_level", None) if item is not None else None
            if tech_level is not None:
                return tech_level
        flogger.warning(f"Could not resolve tech level for {item_name!r} ({item_type}); defaulting to 1")
        return 1

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
                # Create new shop item carrying the item's REAL tech level — the
                # shop listing renders T{tech_level} per item, so the old hardcoded
                # default of 1 displayed every freshly-sold item as T1.
                shop_item_data = {
                    "guild_id": guild_id,
                    "tier": tier,
                    "tech_level": await self._get_item_tech_level(db, item_type, item_name, base_price),
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
