"""
Unit tests for LoadoutConsistencyService — Package G B.19 choke-point.

Each test uses at most 2 mocks (per ``tests/AGENTS.md``).  The standard
approach is:
  - 1 mock: ``mock_db`` (async DB session)
  - 1 mock: a configured ``LoadoutConsistencyService`` instance whose repos
    are AsyncMocks (the consolidated "service-with-mocked-repos" fixture).

The service uses ``commit=False`` everywhere; we do not assert commit.
"""

import sys
import types
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mocks (mirroring tests/services/test_equipment_service.py).
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services.loadout_consistency_service import LoadoutConsistencyService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player_ship(
    ship_id: int = 1,
    player_id: int = 42,
    ship_name: str = "Sidewinder",
    weapons: list[str] | None = None,
    modules: list[str] | None = None,
    turrets: list[str] | None = None,
    secondary_weapons: list[str] | None = None,
    is_active: bool = False,
) -> MagicMock:
    """Build a mock PlayerShip whose slot lists are real Python lists."""
    ship = MagicMock()
    ship.id = ship_id
    ship.player_id = player_id
    ship.ship_name = ship_name
    ship.is_active = is_active
    ship.weapons = list(weapons) if weapons is not None else []
    ship.modules = list(modules) if modules is not None else []
    ship.turrets = list(turrets) if turrets is not None else []
    ship.secondary_weapons = list(secondary_weapons) if secondary_weapons is not None else []
    ship.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return ship


def _make_static_ship(
    name: str = "Sidewinder",
    max_primaries: int = 2,
    max_modules: int = 3,
    max_turrets: int = 1,
    max_secondaries: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
        max_secondaries=max_secondaries,
    )


def _make_inv_item(item_name: str, item_type: str = "primary_weapon", quantity: int = 1) -> SimpleNamespace:
    return SimpleNamespace(item_name=item_name, item_type=item_type, quantity=quantity)


def _make_base_item(name: str, type_str: str) -> SimpleNamespace:
    """Stand-in for an Item ORM row — exposes name + STI discriminator."""
    return SimpleNamespace(name=name, type=type_str)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def svc() -> LoadoutConsistencyService:
    """LoadoutConsistencyService with all repositories replaced by AsyncMocks."""
    s = LoadoutConsistencyService.__new__(LoadoutConsistencyService)
    s.player_ship_repo = AsyncMock()
    s.inventory_repo = AsyncMock()
    s.item_repo = AsyncMock()
    s.ship_repo = AsyncMock()

    s.player_ship_repo.get_by_id = AsyncMock(return_value=None)
    s.player_ship_repo.get_player_ships = AsyncMock(return_value=[])
    s.player_ship_repo.add_equipment = AsyncMock()
    s.player_ship_repo.remove_equipment = AsyncMock()
    s.inventory_repo.get_player_item = AsyncMock(return_value=None)
    s.inventory_repo.add_item = AsyncMock()
    s.inventory_repo.remove_item = AsyncMock()
    s.item_repo.get_by_name = AsyncMock(return_value=None)
    s.item_repo.get_by_name_any_type = AsyncMock(return_value=None)
    s.ship_repo.get_by_name = AsyncMock(return_value=None)
    return s


# ---------------------------------------------------------------------------
# equip_one
# ---------------------------------------------------------------------------


class TestEquipOne:
    """Atomic equip via the consistency service."""

    @pytest.mark.asyncio
    async def test_equip_one_happy_path(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=[])
        static = _make_static_ship(max_primaries=2)
        inv = _make_inv_item("Pulse Laser", "primary_weapon")

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Pulse Laser", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)

        result = await svc.equip_one(
            mock_db, player_id=42, ship_id=1, item_name="Pulse Laser", equipment_type="weapons"
        )
        assert result["success"] is True
        svc.inventory_repo.remove_item.assert_called_once_with(
            mock_db, 42, "primary_weapon", "Pulse Laser", quantity=1, commit=False
        )
        svc.player_ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "weapons", "Pulse Laser", commit=False)

    @pytest.mark.asyncio
    async def test_equip_one_item_not_in_inventory_raises(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=_make_static_ship())
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Pulse Laser", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found in your inventory"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="Pulse Laser", equipment_type="weapons")
        svc.inventory_repo.remove_item.assert_not_called()
        svc.player_ship_repo.add_equipment.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_one_slot_full_raises(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["A", "B"])
        static = _make_static_ship(max_primaries=2)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("C", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=_make_inv_item("C", "primary_weapon"))

        with pytest.raises(ValueError, match="slots"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="C", equipment_type="weapons")

    @pytest.mark.asyncio
    async def test_equip_one_auto_detect_equipment_type(self, svc, mock_db):
        """When equipment_type is None, the service derives it from the item's STI type."""
        ship = _make_player_ship(ship_id=1, player_id=42, modules=[])
        static = _make_static_ship(max_modules=3)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        # First call resolves the type; the same item is returned again for game-data validation
        base = _make_base_item("Shield Generator", "ShieldModule")
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=base)
        svc.item_repo.get_by_name = AsyncMock(return_value=base)
        svc.inventory_repo.get_player_item = AsyncMock(return_value=_make_inv_item("Shield Generator", "module"))

        result = await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="Shield Generator")
        assert result["equipment_type"] == "modules"

    @pytest.mark.asyncio
    async def test_equip_one_module_class_limit_conflict_raises(self, svc, mock_db):
        """Equipping a second module of a unique class raises."""
        ship = _make_player_ship(ship_id=1, player_id=42, modules=["Shield A"])
        static = _make_static_ship(max_modules=3)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Shield B", "ShieldModule"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=_make_inv_item("Shield B", "module"))

        # The unique-class scan looks up the already-equipped item.
        async def _by_name_any(_db, name):
            if name == "Shield A":
                return _make_base_item("Shield A", "ShieldModule")
            return _make_base_item(name, "ShieldModule")

        svc.item_repo.get_by_name_any_type = _by_name_any

        # ShieldModule has limit=1 in MODULE_EQUIP_LIMITS.
        with pytest.raises(ValueError, match="limited to"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="Shield B", equipment_type="modules")

    @pytest.mark.asyncio
    async def test_equip_one_ship_not_owned_raises(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=99)  # owned by 99
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        with pytest.raises(ValueError, match="does not belong to player"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="X", equipment_type="weapons")

    @pytest.mark.asyncio
    async def test_equip_one_ship_db_error_surfaces_as_value_error(self, svc, mock_db):
        """B.15 contract preserved: DB errors surface as ValueError."""
        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=RuntimeError("connection lost"))
        with pytest.raises(ValueError, match="could not be retrieved"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="X", equipment_type="weapons")

    @pytest.mark.asyncio
    async def test_equip_one_same_item_swap_slot_full_raises_slot_full_not_b41(self, svc, mock_db):
        """Regression: B.41 guard must NOT fire when slots are full (swap flow).

        Scenario: player has 1× Ridil Blaster in inventory (qty=1) and 1× Ridil
        Blaster already equipped on a 1-slot ship.  The swap UI is valid — the
        cog will unequip first, then re-equip.  The equip call with full slots
        must raise the slot-full ValueError (step 5), NOT the B.41 ValueError.
        """
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["Ridil Blaster"])
        static = _make_static_ship(max_primaries=1)  # single-slot ship
        inv = _make_inv_item("Ridil Blaster", "primary_weapon", quantity=1)

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Ridil Blaster", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        with pytest.raises(ValueError, match="slots"):
            await svc.equip_one(mock_db, player_id=42, ship_id=1, item_name="Ridil Blaster", equipment_type="weapons")

        # B.41 guard was skipped (slots full): get_player_ships must NOT have been called
        svc.player_ship_repo.get_player_ships.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_one_cargo_copy_available_with_one_already_equipped(self, svc, mock_db):
        """B.41 regression: 1 equipped + 1 in cargo must NOT raise.

        The player has 1 Ridil Blaster equipped on a 2-slot ship and 1 in cargo
        (quantity=1).  Equipping the cargo copy into the second slot must succeed.
        The old (broken) condition `already_equipped >= quantity` (1 >= 1 = True)
        incorrectly blocked this.  The correct condition is `quantity <= 0`.
        """
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["Ridil Blaster"])
        static = _make_static_ship(max_primaries=2)  # two-slot ship — second slot free
        inv = _make_inv_item("Ridil Blaster", "primary_weapon", quantity=1)
        updated_ship = _make_player_ship(ship_id=1, player_id=42, weapons=["Ridil Blaster", "Ridil Blaster"])

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=[ship, updated_ship])
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Ridil Blaster", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        result = await svc.equip_one(
            mock_db, player_id=42, ship_id=1, item_name="Ridil Blaster", equipment_type="weapons"
        )
        assert result["success"] is True
        svc.inventory_repo.remove_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_equip_one_zero_cargo_raises(self, svc, mock_db):
        """B.41 — zero-quantity cargo row blocks equip regardless of how many are equipped.

        Legacy DB inconsistency: inventory row exists (not None) but quantity=0.
        Must be rejected — no cargo copy to consume.
        """
        ship = _make_player_ship(ship_id=6, player_id=1, weapons=["Raccoon"])
        static = _make_static_ship(max_primaries=4)
        inv = _make_inv_item("Raccoon", "primary_weapon", quantity=0)

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Raccoon", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        with pytest.raises(ValueError, match="No unequipped copies remain"):
            await svc.equip_one(mock_db, player_id=1, ship_id=6, item_name="Raccoon", equipment_type="weapons")
        svc.inventory_repo.remove_item.assert_not_called()
        svc.player_ship_repo.add_equipment.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_one_positive_cargo_with_multiple_equipped_allowed(self, svc, mock_db):
        """B.41 — equip is allowed as long as quantity > 0 in cargo.

        Player has 1 Raccoon in cargo and 2 already equipped across ships.
        The old guard (already_equipped >= quantity → 2 >= 1 = True) incorrectly
        blocked this.  The new guard (quantity <= 0 → False) correctly allows it.
        """
        ship = _make_player_ship(ship_id=6, player_id=1, weapons=["Raccoon", "Raccoon"])
        static = _make_static_ship(max_primaries=4)
        inv = _make_inv_item("Raccoon", "primary_weapon", quantity=1)
        updated_ship = _make_player_ship(ship_id=6, player_id=1, weapons=["Raccoon", "Raccoon", "Raccoon"])

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=[ship, updated_ship])
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Raccoon", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        result = await svc.equip_one(mock_db, player_id=1, ship_id=6, item_name="Raccoon", equipment_type="weapons")
        assert result["success"] is True
        svc.inventory_repo.remove_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_equip_one_boundary_exactly_one_cargo_allowed(self, svc, mock_db):
        """B.41 boundary: quantity == 1 — exactly one cargo copy available.

        Regardless of how many are already equipped, quantity=1 means there IS
        a cargo copy to consume and the equip must succeed.
        """
        ship_a = _make_player_ship(ship_id=1, player_id=1, weapons=["Raccoon"])
        ship_b = _make_player_ship(ship_id=2, player_id=1, weapons=["Raccoon"])
        ship_c = _make_player_ship(ship_id=3, player_id=1, weapons=[])
        updated_ship_c = _make_player_ship(ship_id=3, player_id=1, weapons=["Raccoon"])
        static = _make_static_ship(max_primaries=4)
        inv = _make_inv_item("Raccoon", "primary_weapon", quantity=1)

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=[ship_c, updated_ship_c])
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name = AsyncMock(return_value=_make_base_item("Raccoon", "PrimaryWeapon"))
        svc.inventory_repo.get_player_item = AsyncMock(return_value=inv)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship_a, ship_b, ship_c])

        result = await svc.equip_one(mock_db, player_id=1, ship_id=3, item_name="Raccoon", equipment_type="weapons")
        assert result["success"] is True
        svc.inventory_repo.remove_item.assert_called_once()


# ---------------------------------------------------------------------------
# unequip_one
# ---------------------------------------------------------------------------


class TestUnequipOne:
    @pytest.mark.asyncio
    async def test_unequip_one_happy_path(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["Pulse Laser"])
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("Pulse Laser", "PrimaryWeapon"))

        result = await svc.unequip_one(
            mock_db, player_id=42, ship_id=1, item_name="Pulse Laser", equipment_type="weapons"
        )
        assert result["success"] is True
        svc.player_ship_repo.remove_equipment.assert_called_once_with(
            mock_db, 1, "weapons", "Pulse Laser", commit=False
        )
        svc.inventory_repo.add_item.assert_called_once_with(
            mock_db, 42, "primary_weapon", "Pulse Laser", quantity=1, commit=False
        )

    @pytest.mark.asyncio
    async def test_unequip_one_not_equipped_raises(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=[])
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("Pulse Laser", "PrimaryWeapon"))

        with pytest.raises(ValueError, match="not equipped"):
            await svc.unequip_one(mock_db, player_id=42, ship_id=1, item_name="Pulse Laser", equipment_type="weapons")

    @pytest.mark.asyncio
    async def test_unequip_one_auto_detect_with_fallback_scan(self, svc, mock_db):
        """When item is not in catalog, the service scans equipped slots."""
        ship = _make_player_ship(ship_id=1, player_id=42, modules=["Mystery Module"])
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        # Catalog says we have no idea what type this is.
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=None)

        result = await svc.unequip_one(mock_db, player_id=42, ship_id=1, item_name="Mystery Module")
        assert result["equipment_type"] == "modules"


# ---------------------------------------------------------------------------
# transfer_loadout_to_new_ship
# ---------------------------------------------------------------------------


class TestTransferLoadoutToNewShip:
    @pytest.mark.asyncio
    async def test_src_none_returns_zero_counts(self, svc, mock_db):
        dst = _make_player_ship(ship_id=2, player_id=42)
        result = await svc.transfer_loadout_to_new_ship(
            mock_db,
            player_id=42,
            src_ship=None,
            dst_ship=dst,
            slot_limits={"weapons": 2, "modules": 3, "turrets": 1, "secondary_weapons": 0},
        )
        assert result["transferred"] == 0
        assert result["overflowed"] == 0

    @pytest.mark.asyncio
    async def test_fitting_subset_transfers_and_clears_src(self, svc, mock_db):
        """Items that fit move to dst; src slots are cleared (B.19 fix)."""
        src = _make_player_ship(ship_id=1, player_id=42, weapons=["A", "B"], modules=["M1", "M2"])
        dst = _make_player_ship(ship_id=2, player_id=42)
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("X", "PrimaryWeapon"))

        result = await svc.transfer_loadout_to_new_ship(
            mock_db,
            player_id=42,
            src_ship=src,
            dst_ship=dst,
            slot_limits={"weapons": 2, "modules": 2, "turrets": 1, "secondary_weapons": 0},
        )

        assert dst.weapons == ["A", "B"]
        assert dst.modules == ["M1", "M2"]
        # B.19 fix: src must be cleared.
        assert src.weapons == []
        assert src.modules == []
        assert result["transferred"] == 4
        assert result["overflowed"] == 0

    @pytest.mark.asyncio
    async def test_overflow_items_pushed_to_inventory(self, svc, mock_db):
        src = _make_player_ship(ship_id=1, player_id=42, weapons=["A", "B", "C"])
        dst = _make_player_ship(ship_id=2, player_id=42)
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("C", "PrimaryWeapon"))

        await svc.transfer_loadout_to_new_ship(
            mock_db,
            player_id=42,
            src_ship=src,
            dst_ship=dst,
            slot_limits={"weapons": 2, "modules": 0, "turrets": 0, "secondary_weapons": 0},
        )

        assert dst.weapons == ["A", "B"]
        assert src.weapons == []
        # Overflow C went to inventory
        svc.inventory_repo.add_item.assert_any_call(mock_db, 42, "primary_weapon", "C", 1, commit=False)


# ---------------------------------------------------------------------------
# evacuate_ship_loadout_to_inventory
# ---------------------------------------------------------------------------


class TestEvacuateShipLoadoutToInventory:
    @pytest.mark.asyncio
    async def test_happy_path_moves_items_and_clears_slots(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["W1"], modules=["M1"])
        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=[_make_base_item("W1", "PrimaryWeapon"), _make_base_item("M1", "ShieldModule")]
        )
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        result = await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=ship)

        assert "W1" in result["items_returned"]
        assert "M1" in result["items_returned"]
        # Slots cleared
        assert ship.weapons == []
        assert ship.modules == []

    @pytest.mark.asyncio
    async def test_empty_ship_no_op(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42)
        result = await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=ship)
        assert result["items_returned"] == []
        svc.inventory_repo.add_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_anti_duplication_guard_drops_duplicate_on_other_ship(self, svc, mock_db):
        """B.19 exploit closure: duplicate slot ref on another ship is dropped without minting."""
        target = _make_player_ship(ship_id=1, player_id=42, weapons=["Phantom"])
        other = _make_player_ship(ship_id=2, player_id=42, weapons=["Phantom"])
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("Phantom", "PrimaryWeapon"))
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[target, other])

        await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=target)

        # The "winner" goes to inventory exactly once
        assert svc.inventory_repo.add_item.await_count == 1
        # The duplicate reference on the OTHER ship was removed
        assert other.weapons == []

    @pytest.mark.asyncio
    async def test_none_entries_filtered_from_slot_lists(self, svc, mock_db):
        """G.4: None entries in ship JSON slot lists are silently filtered before processing.

        A legacy corrupt row with weapons=["A", None, "B"] must behave as if
        it only contained ["A", "B"] — None is never passed to _resolve_concrete_type
        (which would call item_repo.get_by_name_any_type(db, None)).
        """
        # Build a ship with a corrupt weapons list containing None.
        ship = _make_player_ship(ship_id=1, player_id=42)
        ship.weapons = ["Pulse Laser", None, "Rail Gun"]
        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda _db, name: _make_base_item(name, "PrimaryWeapon") if name is not None else None
        )
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship])

        result = await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=ship)

        # Only the two real items should be returned (None is skipped).
        assert "Pulse Laser" in result["items_returned"]
        assert "Rail Gun" in result["items_returned"]
        assert None not in result["items_returned"]
        # Exactly 2 add_item calls — one for each non-None entry.
        assert svc.inventory_repo.add_item.await_count == 2
        # Ship's weapons slot is now empty.
        assert ship.weapons == []


# ---------------------------------------------------------------------------
# reconcile_active_ship_slots
# ---------------------------------------------------------------------------


class TestReconcileActiveShipSlots:
    @pytest.mark.asyncio
    async def test_no_overflow_is_no_op(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["A"], modules=["M1"])
        static = _make_static_ship(max_primaries=2, max_modules=3)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)

        result = await svc.reconcile_active_ship_slots(mock_db, player_id=42, target_ship_id=1)
        assert result["any_evacuated"] is False
        assert ship.weapons == ["A"]
        assert ship.modules == ["M1"]

    @pytest.mark.asyncio
    async def test_weapons_overflow_evacuated_to_cargo(self, svc, mock_db):
        """Switching to a smaller ship: overflow weapons go to inventory."""
        # Pre-state: ship has 2 weapons but new cap is 1
        ship = _make_player_ship(ship_id=1, player_id=42, weapons=["A", "B"])
        static = _make_static_ship(max_primaries=1, max_modules=3)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("B", "PrimaryWeapon"))

        result = await svc.reconcile_active_ship_slots(mock_db, player_id=42, target_ship_id=1)
        assert result["any_evacuated"] is True
        assert "B" in result["evacuated_items"]["weapons"]
        assert ship.weapons == ["A"]
        svc.inventory_repo.add_item.assert_called_once_with(mock_db, 42, "primary_weapon", "B", 1, commit=False)

    @pytest.mark.asyncio
    async def test_target_ship_not_owned_raises(self, svc, mock_db):
        ship = _make_player_ship(ship_id=1, player_id=99)
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        with pytest.raises(ValueError, match="does not belong to player"):
            await svc.reconcile_active_ship_slots(mock_db, player_id=42, target_ship_id=1)


# ---------------------------------------------------------------------------
# repair_player
# ---------------------------------------------------------------------------


class TestRepairPlayer:
    @pytest.mark.asyncio
    async def test_clean_state_is_noop(self, svc, mock_db):
        s1 = _make_player_ship(ship_id=1, player_id=42, weapons=["A"], is_active=True)
        s2 = _make_player_ship(ship_id=2, player_id=42, weapons=["B"])
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[s1, s2])

        result = await svc.repair_player(mock_db, player_id=42)
        assert result["duplicates_removed"] == 0
        assert result["ships_modified"] == 0
        assert s1.weapons == ["A"]
        assert s2.weapons == ["B"]

    @pytest.mark.asyncio
    async def test_single_weapon_duplicate_removed_from_loser(self, svc, mock_db):
        """The active ship wins; the duplicate on the non-active ship is dropped."""
        active = _make_player_ship(ship_id=1, player_id=42, weapons=["Dup"], is_active=True)
        loser = _make_player_ship(ship_id=2, player_id=42, weapons=["Dup"], is_active=False)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[active, loser])

        result = await svc.repair_player(mock_db, player_id=42)
        assert result["duplicates_removed"] == 1
        assert active.weapons == ["Dup"]
        assert loser.weapons == []

    @pytest.mark.asyncio
    async def test_modules_duplicated_across_three_ships(self, svc, mock_db):
        """Recreates the empirical B.19 corrupt state: E2/Telta on all 3 ships."""
        s1 = _make_player_ship(ship_id=1, player_id=42, modules=["E2 Exoclad", "Telta Quickscan"], is_active=True)
        s2 = _make_player_ship(ship_id=2, player_id=42, modules=["E2 Exoclad", "Telta Quickscan"])
        s3 = _make_player_ship(ship_id=3, player_id=42, modules=["E2 Exoclad", "Telta Quickscan"])
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[s1, s2, s3])

        result = await svc.repair_player(mock_db, player_id=42)
        # Each name appears 3 times → 2 duplicates per name × 2 names = 4 removed.
        assert result["duplicates_removed"] == 4
        assert s1.modules == ["E2 Exoclad", "Telta Quickscan"]
        assert s2.modules == []
        assert s3.modules == []

    @pytest.mark.asyncio
    async def test_dry_run_reports_without_mutating(self, svc, mock_db):
        active = _make_player_ship(ship_id=1, player_id=42, weapons=["Dup"], is_active=True)
        loser = _make_player_ship(ship_id=2, player_id=42, weapons=["Dup"], is_active=False)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[active, loser])

        result = await svc.repair_player(mock_db, player_id=42, dry_run=True)
        assert result["duplicates_removed"] == 1
        # Dry run preserves the loser's slot list
        assert loser.weapons == ["Dup"]

    @pytest.mark.asyncio
    async def test_mixed_duplicates_across_kinds(self, svc, mock_db):
        s1 = _make_player_ship(ship_id=1, player_id=42, weapons=["W"], modules=["M"], is_active=True)
        s2 = _make_player_ship(ship_id=2, player_id=42, weapons=["W"], modules=["M"], turrets=["T"])
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[s1, s2])

        result = await svc.repair_player(mock_db, player_id=42)
        # W and M are duplicated; T is only on s2.
        assert result["duplicates_removed"] == 2
        assert s1.weapons == ["W"]
        assert s1.modules == ["M"]
        assert s2.weapons == []
        assert s2.modules == []
        assert s2.turrets == ["T"]

    @pytest.mark.asyncio
    async def test_post_condition_check_is_clean_after_successful_repair(self, svc, mock_db):
        """G.3: The debug-mode post-condition check logs 'OK' (no residual duplicates)
        after a successful live repair.  This test verifies that repair_player correctly
        modifies the in-memory ship objects so the post-scan finds zero duplicates.

        If the post-condition check fires a WARNING (i.e. residual_duplicates > 0), the
        ORM mutation (_set_slot) didn't take effect — which would also break the main
        assertions below.
        """
        active = _make_player_ship(ship_id=1, player_id=42, weapons=["Dup"], is_active=True)
        loser = _make_player_ship(ship_id=2, player_id=42, weapons=["Dup"], is_active=False)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[active, loser])

        result = await svc.repair_player(mock_db, player_id=42)

        # Primary repair outcome
        assert result["duplicates_removed"] == 1
        assert active.weapons == ["Dup"]
        assert loser.weapons == []  # G.3: if _set_slot failed, this would still be ["Dup"]
        # Post-condition: the loser's slot is cleared so a re-scan finds no residual duplicates.
        # (The post-condition check inside repair_player already ran; if it had found duplicates
        #  it would have logged a WARNING, which we can't assert on directly here, but the
        #  mutation assertions above prove the in-memory state is correct.)


# ---------------------------------------------------------------------------
# Adversarial / exploit closure: legacy phantom-item state
# ---------------------------------------------------------------------------


class TestAntiDuplicationExploitClosure:
    """B.19 exploit-closure regression suite.

    Demonstrates that the OLD (pre-fix) inline evacuation logic produced an
    item-generation exploit on phantom-duplicated state, and the NEW
    consistency service prevents it.
    """

    @pytest.mark.asyncio
    async def test_admin_remove_ship_does_not_mint_phantom_duplicate_twice(self, svc, mock_db):
        """Two ships referencing the same phantom item produce ONE inventory row.

        Pre-fix behaviour: each admin_remove_ship call inlined an
        ``inventory.add_item`` for every JSON entry, which materialised the
        same phantom item twice into real inventory rows.  The consistency
        service's anti-duplication guard removes the duplicate from the OTHER
        ship before adding to inventory, ensuring exactly one mint.
        """
        # Pre-state: two ships each carry a phantom 'M6 A4' (no inventory row).
        ship_a = _make_player_ship(ship_id=1, player_id=42, weapons=["M6 A4"])
        ship_b = _make_player_ship(ship_id=2, player_id=42, weapons=["M6 A4"])

        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_base_item("M6 A4", "PrimaryWeapon"))

        # First evacuation (admin_remove_ship targeting ship_a) — sees the
        # duplicate on ship_b and removes it, then mints exactly once.
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship_a, ship_b])
        result_a = await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=ship_a)

        assert result_a["items_returned"] == ["M6 A4"]
        assert svc.inventory_repo.add_item.await_count == 1
        # Anti-duplication guard removed the duplicate from ship_b
        assert ship_b.weapons == []
        # G.1: counter must reflect the removal (was always 0 before fix)
        assert result_a["duplicates_dropped"] == 1

        # Second evacuation (admin_remove_ship targeting ship_b) — slots are
        # already empty; no further inventory rows are minted.
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[ship_a, ship_b])
        result_b = await svc.evacuate_ship_loadout_to_inventory(mock_db, ship=ship_b)
        assert result_b["items_returned"] == []
        # Total mint count after both calls is still 1.
        assert svc.inventory_repo.add_item.await_count == 1
