"""
Inventory Service for the BountyBot inventory system.

Handles business logic for inventory management including
item storage, quantity tracking, and inventory operations.

Vocabulary contract (A.36):
- The database stores CONCRETE item types: ship, primary_weapon, secondary_weapon,
  turret_weapon, module.  Generic aliases (weapon, turret) are NEVER persisted.
- This service accepts both generic aliases and concrete types on READ paths
  (expansion via _item_type_normalizer).
- WRITE paths (add_item_to_inventory, remove_item_from_inventory) require a
  single concrete type — generic aliases are rejected with InvalidItemTypeError.
"""

from typing import Any

from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.ship_repository import ShipRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services._item_type_normalizer import expand_item_type_to_concrete
from services.exceptions import InvalidItemTypeError

flogger = bblogger.get_logger("inventory-service")


class InventoryService:
    def __init__(self):
        self.inventory_repo = InventoryRepository()
        self.player_repo = PlayerRepository()
        self.player_ship_repo = PlayerShipRepository()
        self.ship_repo = ShipRepository()
        self.primary_weapon_repo = PrimaryWeaponRepository()
        self.secondary_weapon_repo = SecondaryWeaponRepository()
        self.turret_weapon_repo = TurretWeaponRepository()
        self.module_repo = ModuleRepository()

    async def get_player_inventory(
        self, db: AsyncSession, player_id: int, item_type: str | None = None, include_ships: bool = False
    ) -> list[dict[str, Any]]:
        """
        Get a player's inventory, optionally filtered by item type.

        *item_type* may be a concrete type (``"primary_weapon"``) or a generic
        alias (``"weapon"``).  Generic aliases are expanded via the normalizer to
        all currently-enabled concrete types.  An unknown or disabled type raises
        ``InvalidItemTypeError`` (mapped to HTTP 422 by the router).

        *include_ships* additionally lists the player's INACTIVE ships as
        inventory entries.  Ships live in player_ships, not player_inventories;
        the active ship is "equipped" and excluded, mirroring the cargo-only
        invariant for items.  Default False so existing consumers (equip/sell
        autocomplete cache, /search, item-count resolution) are unaffected —
        only the /inventory display opts in.

        Returns formatted inventory data with item details.
        """
        try:
            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Get inventory items
            concrete_types: tuple[str, ...] | None = None
            if item_type is None:
                items = await self.inventory_repo.get_player_items(db, player_id)
            else:
                concrete_types = expand_item_type_to_concrete(item_type, context="playable")
                if len(concrete_types) == 1:
                    items = await self.inventory_repo.get_player_items(db, player_id, concrete_types[0])
                else:
                    items = await self.inventory_repo.get_player_items_by_types(db, player_id, concrete_types)

            # Inactive ships as cargo entries (no type filter, or filter includes "ship")
            inactive_ships: list[Any] = []
            if include_ships and (concrete_types is None or "ship" in concrete_types):
                player_ships = await self.player_ship_repo.get_player_ships(db, player_id)
                inactive_ships = [ps for ps in player_ships if not ps.is_active]

            # Format items for response — batch-fetch all item details in 5 queries
            # (P6-T2: replaces N×5 sequential per-item lookups).
            item_names = [item.item_name for item in items] + [ps.ship_name for ps in inactive_ships]
            details_by_name = await self._get_items_details_batch(db, item_names)

            formatted_items = []
            for item in items:
                formatted_item = {
                    "id": item.id,
                    "item_type": item.item_type,
                    "item_name": item.item_name,
                    "quantity": item.quantity,
                    "acquired_at": item.acquired_at.isoformat(),
                    "item_details": details_by_name.get(item.item_name),
                }
                formatted_items.append(formatted_item)

            # Aggregate duplicate inactive hulls into one entry with a quantity,
            # matching how stacked items display. The id is the first PlayerShip id
            # (display-only — NOT a player_inventories row id).
            ships_by_name: dict[str, dict[str, Any]] = {}
            for ps in inactive_ships:
                entry = ships_by_name.get(ps.ship_name)
                if entry is None:
                    ships_by_name[ps.ship_name] = {
                        "id": ps.id,
                        "item_type": "ship",
                        "item_name": ps.ship_name,
                        "quantity": 1,
                        "acquired_at": ps.created_at.isoformat(),
                        "item_details": details_by_name.get(ps.ship_name),
                    }
                else:
                    entry["quantity"] += 1
            formatted_items.extend(ships_by_name.values())

            flogger.debug(
                f"Retrieved {len(formatted_items)} inventory items for player {player_id} "
                f"(inactive ships included: {len(inactive_ships) if include_ships else 'off'})"
            )
            return formatted_items

        except (InvalidItemTypeError, ValueError):
            raise
        except Exception as e:
            flogger.error(f"Error getting inventory for player {player_id}: {e}")
            raise

    async def add_item_to_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1,
        commit: bool = True,
    ) -> dict[str, Any]:
        """
        Add items to a player's inventory.

        *item_type* MUST be a concrete type.  Generic aliases are rejected with
        ``InvalidItemTypeError`` — callers must resolve the concrete type before
        calling this method.

        Args:
            commit: When False, flush changes without committing (use when the caller
                owns the transaction, e.g. inside a router-level db.begin() context).

        Returns transaction details.
        """
        try:
            # Validate that item_type is a single concrete type (no generic aliases on writes)
            concrete_types = expand_item_type_to_concrete(item_type, context="playable")
            if len(concrete_types) != 1:
                raise InvalidItemTypeError(
                    f"Write operations require a concrete item type; "
                    f"got generic alias '{item_type}' which expands to multiple types. "
                    f"Use one of: {concrete_types}"
                )
            concrete_type = concrete_types[0]

            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE), before
            # the inventory read-modify-write in inventory_repo.add_item (read qty
            # → +delta → write).  This serialises the naked entry points that reach
            # this method without an outer lock — POST /inventory/add and the admin
            # add-item / give-item routes — against any other same-player cargo
            # mutation, closing the add-side lost-update window.  When this method
            # is reached from transfer_item_between_players (which already holds
            # this player's lock from its ascending-order acquisition) the re-lock
            # is an intra-transaction no-op.  Wrap so DB/ORM exceptions surface as a
            # friendly ValueError (HTTP 400) rather than leaking as raw 500s.
            try:
                player = await self.player_repo.get_by_id_for_update(db, player_id)
            except Exception as exc:
                flogger.error(f"DB error fetching player_id={player_id}: {exc}", exc_info=True)
                raise ValueError(f"Player with ID {player_id} could not be retrieved.") from exc
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate item exists in static data
            if not await self._validate_item_exists(db, item_name, concrete_type):
                raise ValueError(f"Item {item_name} does not exist or is not of type {concrete_type}")

            # Add item to inventory using the concrete type
            inventory_item = await self.inventory_repo.add_item(
                db, player_id, concrete_type, item_name, quantity, commit=commit
            )

            transaction_details = {
                "player_id": player_id,
                "item_type": concrete_type,
                "item_name": item_name,
                "quantity_added": quantity,
                "new_total_quantity": inventory_item.quantity,
                "transaction_time": inventory_item.acquired_at.isoformat(),
            }

            flogger.info(f"Added {quantity}x {item_name} ({concrete_type}) to player {player_id} inventory")
            return transaction_details

        except (InvalidItemTypeError, ValueError):
            raise
        except Exception as e:
            flogger.error(f"Error adding item to inventory: {e}")
            raise

    async def remove_item_from_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1,
        commit: bool = True,
    ) -> dict[str, Any]:
        """
        Remove items from a player's inventory.

        *item_type* MUST be a concrete type on write paths.  Generic aliases are
        rejected with ``InvalidItemTypeError``.

        Args:
            commit: When False, flush changes without committing (use when the caller
                owns the transaction, e.g. inside a router-level db.begin() context).

        Returns transaction details.
        """
        try:
            # Validate that item_type is a single concrete type (no generic aliases on writes)
            concrete_types = expand_item_type_to_concrete(item_type, context="playable")
            if len(concrete_types) != 1:
                raise InvalidItemTypeError(
                    f"Write operations require a concrete item type; "
                    f"got generic alias '{item_type}' which expands to multiple types. "
                    f"Use one of: {concrete_types}"
                )
            concrete_type = concrete_types[0]

            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE), before
            # the get_player_item read that feeds the inventory_repo.remove_item
            # read-modify-write (read qty → −delta → write/delete).  This serialises
            # the naked entry points that reach this method without an outer lock —
            # POST /inventory/remove and the admin remove-item route — against any
            # other same-player cargo mutation, closing the remove-side lost-update
            # window (and the last-copy duplication window when paired with the add
            # side via transfer).  When reached from transfer_item_between_players
            # (which already holds this player's lock) the re-lock is an
            # intra-transaction no-op.  Wrap so DB/ORM exceptions surface as a
            # friendly ValueError (HTTP 400) rather than leaking as raw 500s.
            try:
                player = await self.player_repo.get_by_id_for_update(db, player_id)
            except Exception as exc:
                flogger.error(f"DB error fetching player_id={player_id}: {exc}", exc_info=True)
                raise ValueError(f"Player with ID {player_id} could not be retrieved.") from exc
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Check if player has the item (exact match on concrete type)
            existing_item = await self.inventory_repo.get_player_item(db, player_id, concrete_type, item_name)

            if not existing_item:
                raise ValueError(f"Player does not have {item_name} in inventory")

            if existing_item.quantity < quantity:
                raise ValueError(f"Insufficient quantity. Available: {existing_item.quantity}, Requested: {quantity}")

            old_quantity = existing_item.quantity

            # Remove item from inventory
            await self.inventory_repo.remove_item(db, player_id, concrete_type, item_name, quantity, commit=commit)

            # Get updated item (or None if completely removed)
            updated_item = await self.inventory_repo.get_player_item(db, player_id, concrete_type, item_name)

            transaction_details = {
                "player_id": player_id,
                "item_type": concrete_type,
                "item_name": item_name,
                "quantity_removed": quantity,
                "old_quantity": old_quantity,
                "new_quantity": updated_item.quantity if updated_item else 0,
                "item_completely_removed": updated_item is None,
            }

            flogger.info(f"Removed {quantity}x {item_name} ({concrete_type}) from player {player_id} inventory")
            return transaction_details

        except (InvalidItemTypeError, ValueError):
            raise
        except Exception as e:
            flogger.error(f"Error removing item from inventory: {e}")
            raise

    async def transfer_item_between_players(
        self,
        db: AsyncSession,
        from_player_id: int,
        to_player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """
        Transfer items between players (future feature for trading).
        Returns transfer details.
        """
        try:
            # D5-T2 (D-015): LOCK ORDERING — acquire BOTH players' aggregate-root
            # rows FOR UPDATE FIRST, in ascending player_id order, BEFORE any read
            # that feeds the cargo read-modify-write.  This is the same rule used by
            # ``player_service.transfer_credits`` and ``duel_service.accept_duel``.
            #
            # Why this fixes the live-confirmed item-duplication bug: previously
            # both players were read UNLOCKED (get_by_id), so two concurrent
            # transfers of a player's LAST copy both passed the source cargo check
            # and both committed (remove 1 on the source, add 1 on each target) —
            # net +1 item minted out of nothing.  Holding the source player's row
            # lock serialises the two transfers: the second one reads the already
            # decremented cargo and fails the "insufficient quantity" guard.
            #
            # Ascending-ID ordering avoids the AB-BA deadlock that call-order
            # locking (source-then-target) would create against a reverse-direction
            # transfer (target-then-source).  The remove/add helpers below also take
            # the per-player lock as their first act; because we already hold both
            # locks in ascending order, those re-acquisitions are intra-transaction
            # no-ops in the already-established order (Postgres FOR UPDATE on a row
            # the txn already locks is a no-op) — no new ordering hazard.
            ids_ordered = sorted({from_player_id, to_player_id})
            locked: dict[int, Any] = {}
            for pid in ids_ordered:
                player = await self.player_repo.get_by_id_for_update(db, pid)
                if not player:
                    raise ValueError("One or both players not found")
                locked[pid] = player

            from_player = locked[from_player_id]
            to_player = locked[to_player_id]

            if from_player.guild_id != to_player.guild_id:
                raise ValueError("Players must be in the same guild to trade")

            # Transaction is owned by the caller (router).
            # Remove from source player (commit=False — caller's transaction controls commit).
            remove_result = await self.remove_item_from_inventory(
                db, from_player_id, item_type, item_name, quantity, commit=False
            )

            # Add to target player (commit=False — caller's transaction controls commit).
            add_result = await self.add_item_to_inventory(
                db, to_player_id, item_type, item_name, quantity, commit=False
            )

            transfer_details = {
                "from_player_id": from_player_id,
                "to_player_id": to_player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": quantity,
                "from_player_result": remove_result,
                "to_player_result": add_result,
            }

            flogger.info(f"Transferred {quantity}x {item_name} from player {from_player_id} to {to_player_id}")
            return transfer_details

        except (InvalidItemTypeError, ValueError):
            raise
        except Exception as e:
            flogger.error(f"Error transferring item between players: {e}")
            raise

    async def get_inventory_summary(
        self, db: AsyncSession, player_id: int, include_ships: bool = False
    ) -> dict[str, Any]:
        """Get a summary of a player's inventory by item type.

        *include_ships* adds the player's INACTIVE ship count to the ``ship``
        key and ``total_items`` (same semantics as get_player_inventory —
        ships live in player_ships, the active ship counts as "equipped").
        """
        try:
            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            summary = await self.inventory_repo.get_inventory_summary(db, player_id)

            if include_ships:
                player_ships = await self.player_ship_repo.get_player_ships(db, player_id)
                inactive_count = sum(1 for ps in player_ships if not ps.is_active)
                summary["ship"] += inactive_count
                summary["total_items"] += inactive_count

            # Add player context
            summary["player_id"] = player_id
            summary["player_tier"] = player.tier
            summary["guild_id"] = player.guild_id

            return summary

        except Exception as e:
            flogger.error(f"Error getting inventory summary for player {player_id}: {e}")
            raise

    async def search_inventory(self, db: AsyncSession, player_id: int, search_term: str) -> list[dict[str, Any]]:
        """Search player's inventory for items matching a search term."""
        try:
            # Get all inventory items
            all_items = await self.get_player_inventory(db, player_id)

            # Filter by search term (case-insensitive)
            search_term_lower = search_term.lower()
            matching_items = [item for item in all_items if search_term_lower in item["item_name"].lower()]

            flogger.debug(f"Found {len(matching_items)} items matching '{search_term}' for player {player_id}")
            return matching_items

        except Exception as e:
            flogger.error(f"Error searching inventory for player {player_id}: {e}")
            raise

    async def validate_item_compatibility(
        self,
        db: AsyncSession,
        player_id: int,
        ship_name: str,
        item_type: str,
        item_name: str,
        player_ship: Any | None = None,
    ) -> dict[str, Any]:
        """
        Validate if an item can be equipped on a specific ship.
        Returns compatibility information including slot availability.

        If *player_ship* (a PlayerShip ORM object) is supplied, the current
        equipped count is read from ``player_ship.get_equipped_count()``
        (which counts items actually equipped on the ship).  When it is not
        supplied the method falls back to the legacy behaviour of querying
        the global inventory count — kept for backward compatibility but
        deprecated for slot checking.
        """
        try:
            compatibility = {
                "compatible": True,
                "ship_name": ship_name,
                "item_type": item_type,
                "item_name": item_name,
                "reason": None,
            }

            # Look up ship slot limits
            ship_details = await self._get_ship_details(db, ship_name)
            if not ship_details:
                compatibility["compatible"] = False
                compatibility["reason"] = f"Ship {ship_name} not found in database"
                return compatibility

            # Map item_type to the ship's slot limit and the equipment_type key
            # used by PlayerShip.get_equipped_count()
            item_type_lower = item_type.lower()
            if item_type_lower in ("weapon", "primary_weapon"):
                max_slots = ship_details["max_primaries"]
                equipment_type_key = "weapons"
            elif item_type_lower == "secondary_weapon":
                max_slots = ship_details["max_secondaries"]
                equipment_type_key = "secondary_weapons"
            elif item_type_lower in ("turret", "turret_weapon"):
                max_slots = ship_details["max_turrets"]
                equipment_type_key = "turrets"
            elif item_type_lower == "module":
                max_slots = ship_details["max_modules"]
                equipment_type_key = "modules"
            else:
                # Unknown type — no slot restriction, allow it
                return compatibility

            # Use actual equipped count from the PlayerShip object when available;
            # otherwise fall back to global inventory count (deprecated path).
            if player_ship is not None:
                current_count = player_ship.get_equipped_count(equipment_type_key)
            else:
                # Fallback: query global inventory count (inaccurate for slot checks)
                inventory_type_map = {
                    "weapon": "primary_weapon",
                    "primary_weapon": "primary_weapon",
                    "secondary_weapon": "secondary_weapon",
                    "turret": "turret_weapon",
                    "turret_weapon": "turret_weapon",
                    "module": "module",
                }
                inventory_type = inventory_type_map.get(item_type_lower, item_type_lower)
                current_count = await self.inventory_repo.get_item_count_by_type(db, player_id, inventory_type)

            if current_count >= max_slots:
                compatibility["compatible"] = False
                compatibility["reason"] = (
                    f"No available {item_type} slots on {ship_name} ({current_count}/{max_slots} used)"
                )

            return compatibility

        except Exception as e:
            flogger.error(f"Error validating item compatibility: {e}")
            raise

    async def _get_items_details_batch(
        self, db: AsyncSession, item_names: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Batch-fetch item details for a list of item names.

        P6-T2: replaces N×5 sequential ``_get_item_details`` calls (one per item
        name × five repos) with 5 batched ``WHERE name IN (...)`` queries — one
        per repo type.  For an inventory with N distinct item names this reduces
        the query count from up to 5·N to exactly 5.

        The priority ordering mirrors ``_get_item_details``: primary_weapon →
        secondary_weapon → turret_weapon → module → ship.  If the same name
        somehow appears in two repos (shouldn't happen in practice) the first
        match wins, matching the old sequential-scan behaviour.

        Args:
            db:         Async database session.
            item_names: Unique item names to look up (duplicates tolerated).

        Returns:
            Mapping of item_name → detail dict (or ``None`` if not found in any
            repo).  Every name in *item_names* has a key in the result — unknown
            names map to ``None``.
        """
        if not item_names:
            return {}

        unique_names = list(dict.fromkeys(item_names))  # preserve order, drop dupes

        # Pre-fill with None so all queried names are present in the result.
        details: dict[str, dict[str, Any] | None] = dict.fromkeys(unique_names, None)

        # Repos in priority order (mirrors _get_item_details lookup sequence).
        repo_type_pairs = [
            (self.primary_weapon_repo, "primary_weapon"),
            (self.secondary_weapon_repo, "secondary_weapon"),
            (self.turret_weapon_repo, "turret_weapon"),
            (self.module_repo, "module"),
            (self.ship_repo, "ship"),
        ]

        for repo, item_type in repo_type_pairs:
            # Only look up names we haven't resolved yet.
            unresolved = [n for n in unique_names if details[n] is None]
            if not unresolved:
                break  # all resolved — no more queries needed
            found_items = await repo.get_by_names(db, unresolved)
            for item in found_items:
                name = item.name
                if details[name] is None:  # first-match wins
                    details[name] = {
                        "name": name,
                        "tech_level": getattr(item, "tech_level", None) if item_type != "ship" else None,
                        "value": getattr(item, "value", None),
                        "type": item_type,
                    }

        return details

    async def _get_item_details(self, db: AsyncSession, item_name: str) -> dict[str, Any] | None:
        """Get item details by searching all item repositories.

        Single-item convenience wrapper around ``_get_items_details_batch``.
        Retained for callers that look up a single item by name.
        Use ``_get_items_details_batch`` when processing multiple items to
        avoid N×5 sequential repo calls.
        """
        result = await self._get_items_details_batch(db, [item_name])
        return result.get(item_name)

    async def _get_ship_details(self, db: AsyncSession, ship_name: str) -> dict[str, Any] | None:
        """Get ship details from the database."""
        ship = await self.ship_repo.get_by_name(db, ship_name)
        if not ship:
            return None
        return {
            "name": ship.name,
            "max_primaries": ship.max_primaries,
            "max_modules": ship.max_modules,
            "max_secondaries": ship.max_secondaries,
            "max_turrets": ship.max_turrets,
            "value": getattr(ship, "value", None),
        }

    async def _validate_item_exists(
        self,
        db: AsyncSession,
        item_name: str,
        item_type: str,  # pylint: disable=unused-argument
    ) -> bool:
        """Validate that an item exists in the database across all item repositories."""
        repos = [
            self.ship_repo,
            self.primary_weapon_repo,
            self.secondary_weapon_repo,
            self.turret_weapon_repo,
            self.module_repo,
        ]
        for repo in repos:
            if await repo.get_by_name(db, item_name):
                return True
        return False

    async def get_player_item_count(self, db: AsyncSession, player_id: int, item_type: str, item_name: str) -> int:
        """Get the quantity of a specific item a player owns.

        *item_type* may be a concrete type or a generic alias.  The lookup
        checks all concrete types the alias expands to, returning the quantity
        of the first matching row.  This fixes A.36 (quantity 0 for owned items
        when generic aliases were passed).
        """
        try:
            concrete_types = expand_item_type_to_concrete(item_type, context="playable")
            item = await self.inventory_repo.get_player_item_by_types(db, player_id, concrete_types, item_name)
            return item.quantity if item else 0
        except InvalidItemTypeError:
            raise
        except Exception as e:
            flogger.error(f"Error getting item count for player {player_id}: {e}")
            raise

    async def consolidate_inventory(self, db: AsyncSession, player_id: int, *, commit: bool = True) -> dict[str, Any]:
        """
        Consolidate duplicate inventory entries (maintenance function).

        Groups items by (item_type, item_name), keeps one entry per group with
        the summed quantity, and deletes the rest.

        This is a multi-row read-modify-write across ``player_inventories``: it
        reads all of the player's cargo rows, merges duplicate (type, name)
        groups, deletes the redundant rows, and updates the surviving row's
        quantity.  Under READ COMMITTED with no lock it is a lost-update window
        (D5 path 18).

        Concurrency contract (the caller/router is responsible for both):
          * The aggregate-root Player ``FOR UPDATE`` lock, acquired FIRST, is
            what serialises concurrent same-player RMWs and so prevents the lost
            update. (The session's autobegin holds that lock until commit/close;
            an explicit ``db.begin()`` does not change the lock's duration.)
          * An explicit ``db.begin()`` provides atomicity: the lock acquisition
            and these flush-only writes commit/roll back together as one unit of
            work — the project's transaction-discipline contract requires that
            explicit boundary rather than relying on get_db_session's AC-7
            auto-commit-on-clean-exit safety net.

        Args:
            commit: When False, the underlying repo writes flush instead of
                commit, so the caller's ``db.begin()`` owns the transaction.

        Returns consolidation results.
        """
        try:
            all_items = await self.inventory_repo.get_player_items(db, player_id)

            # Group items by (item_type, item_name)
            groups: dict[tuple[str, str], list] = {}
            for item in all_items:
                key = (item.item_type, item.item_name)
                groups.setdefault(key, []).append(item)

            items_consolidated = 0
            for (_itype, _iname), group in groups.items():
                if len(group) <= 1:
                    continue

                # Keep the first entry, merge all others into it
                primary = group[0]
                total_quantity = sum(i.quantity for i in group)

                # Delete all duplicate entries (all but the primary)
                for duplicate in group[1:]:
                    await self.inventory_repo.remove(db, duplicate, commit=commit)
                    items_consolidated += 1

                # Update the primary with the summed quantity
                await self.inventory_repo.update_quantity(db, primary.id, total_quantity, commit=commit)

            message = (
                f"Consolidated {items_consolidated} duplicate item(s)"
                if items_consolidated > 0
                else "Inventory is already consolidated"
            )

            return {
                "player_id": player_id,
                "items_consolidated": items_consolidated,
                "message": message,
            }

        except Exception as e:
            flogger.error(f"Error consolidating inventory for player {player_id}: {e}")
            raise
