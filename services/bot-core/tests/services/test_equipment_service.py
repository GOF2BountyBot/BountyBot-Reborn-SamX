"""
Unit tests for EquipmentService.

Each test uses at most 2 mocks, preferring real objects with deterministic
inputs where possible.  The standard approach here is:
  - 1 mock: ``mock_db`` (async DB session — always a mock since there is no test DB)
  - 1 mock: ``mock_svc`` (an EquipmentService with all repos replaced by AsyncMocks)

All repo calls are controlled via the ``svc`` fixture which patches repos on the
service instance after construction.
"""

import sys
import types
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure shared.bblogger and sqlalchemy_utils are mocked before importing
# any service code (same pattern as test_inventory_service.py).
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

from services.equipment_service import EquipmentService

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_player_ship(
    ship_id: int = 1,
    player_id: int = 42,
    ship_name: str = "Sidewinder",
    weapons: list[str] | None = None,
    modules: list[str] | None = None,
    turrets: list[str] | None = None,
) -> MagicMock:
    """Build a mock PlayerShip that delegates get_equipped_count to real lists."""
    ship = MagicMock()
    ship.id = ship_id
    ship.player_id = player_id
    ship.ship_name = ship_name
    ship.nickname = None
    ship.is_active = False
    ship.weapons = list(weapons) if weapons is not None else []
    ship.modules = list(modules) if modules is not None else []
    ship.turrets = list(turrets) if turrets is not None else []
    ship.secondary_weapons = []
    ship.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    # Delegate to the real PlayerShip logic
    def _get_equipped_count(equipment_type: str) -> int:
        mapping = {
            "weapons": ship.weapons,
            "secondary_weapons": ship.secondary_weapons,
            "modules": ship.modules,
            "turrets": ship.turrets,
        }
        lst = mapping.get(equipment_type, [])
        return len(lst) if lst else 0

    ship.get_equipped_count = _get_equipped_count
    return ship


def _make_static_ship(
    name: str = "Sidewinder",
    max_primaries: int = 2,
    max_modules: int = 3,
    max_turrets: int = 1,
    max_secondaries: int = 0,
) -> SimpleNamespace:
    """Build a simple namespace that mimics the Ship static model."""
    return SimpleNamespace(
        name=name,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
        max_secondaries=max_secondaries,
    )


def _make_game_item(name: str = "Pulse Laser") -> SimpleNamespace:
    """Minimal game item (from ItemRepository)."""
    return SimpleNamespace(name=name)


def _make_inventory_item(
    item_name: str = "Pulse Laser",
    item_type: str = "weapon",
    quantity: int = 1,
    player_id: int = 42,
) -> SimpleNamespace:
    """Minimal inventory item."""
    return SimpleNamespace(
        item_name=item_name,
        item_type=item_type,
        quantity=quantity,
        player_id=player_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock 1 — async DB session."""
    return AsyncMock()


def _make_base_item(name: str = "Pulse Laser", item_type: str = "PrimaryWeapon") -> SimpleNamespace:
    """Minimal base item (from get_by_name_any_type)."""
    return SimpleNamespace(name=name, type=item_type)


@pytest.fixture
def svc() -> EquipmentService:
    """EquipmentService with all repositories replaced by AsyncMocks.

    This is *mock 2* — a single consolidated mock that replaces all five
    repos so individual tests need only configure the relevant methods.
    """
    service = EquipmentService.__new__(EquipmentService)

    service.ship_repo = AsyncMock()
    service.inventory_repo = AsyncMock()
    service.item_repo = AsyncMock()
    service.module_repo = AsyncMock()
    service.ship_data_repo = AsyncMock()

    # Default: nothing found (tests override what they need)
    service.ship_repo.get_by_id = AsyncMock(return_value=None)
    service.item_repo.get_by_name = AsyncMock(return_value=None)
    service.item_repo.get_by_name_any_type = AsyncMock(return_value=None)
    service.inventory_repo.get_player_item = AsyncMock(return_value=None)
    service.ship_data_repo.get_by_name = AsyncMock(return_value=None)
    service.ship_repo.add_equipment = AsyncMock()
    service.ship_repo.remove_equipment = AsyncMock()
    service.inventory_repo.add_item = AsyncMock()
    service.inventory_repo.remove_item = AsyncMock()

    return service


# ---------------------------------------------------------------------------
# Equip tests
# ---------------------------------------------------------------------------


class TestEquipItemSuccess:
    """Happy-path equip scenarios."""

    @pytest.mark.asyncio
    async def test_equip_weapon_to_ship_with_available_slot(self, mock_db, svc):
        """Weapon is equipped when ship has an open primary slot.

        Acceptance criteria:
        - equip_item returns success
        - add_equipment called with correct args
        - remove_item called with correct args
        """
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        game_item = _make_game_item("Pulse Laser")
        inv_item = _make_inventory_item("Pulse Laser", "primary_weapon")  # A.36: concrete type

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="weapons",
            item_name="Pulse Laser",
        )

        assert result["success"] is True
        svc.ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "weapons", "Pulse Laser")
        # A.36 fix: remove_item now called with concrete type "primary_weapon", not generic "weapon"
        svc.inventory_repo.remove_item.assert_called_once_with(mock_db, 42, "primary_weapon", "Pulse Laser", quantity=1)

    @pytest.mark.asyncio
    async def test_equip_module_to_ship_with_available_module_slot(self, mock_db, svc):
        """Module is equipped when ship has an open module slot."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=[])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        game_item = _make_game_item("Shield Generator")
        inv_item = _make_inventory_item("Shield Generator", "module")
        # CabinModule has unlimited equip limit (-1)
        base_item = _make_base_item("Shield Generator", "CabinModule")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="modules",
            item_name="Shield Generator",
        )

        assert result["success"] is True
        svc.ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "modules", "Shield Generator")
        svc.inventory_repo.remove_item.assert_called_once_with(mock_db, 42, "module", "Shield Generator", quantity=1)

    @pytest.mark.asyncio
    async def test_equip_turret_to_ship_with_available_turret_slot(self, mock_db, svc):
        """Turret is equipped when ship has an open turret slot."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", turrets=[])
        static_ship = _make_static_ship(name="Sidewinder", max_turrets=1)
        game_item = _make_game_item("Turreted Beam Laser")
        inv_item = _make_inventory_item("Turreted Beam Laser", "turret_weapon")  # A.36: concrete type

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="turrets",
            item_name="Turreted Beam Laser",
        )

        assert result["success"] is True
        # A.36 fix: remove_item now called with concrete type "turret_weapon", not generic "turret"
        svc.inventory_repo.remove_item.assert_called_once_with(
            mock_db, 42, "turret_weapon", "Turreted Beam Laser", quantity=1
        )


class TestEquipItemValidationErrors:
    """Equip error scenarios: slot full, not owned, wrong ship, etc."""

    @pytest.mark.asyncio
    async def test_equip_weapon_to_ship_with_full_weapon_slots_raises(self, mock_db, svc):
        """ValueError is raised when all weapon slots are occupied.

        Acceptance criteria: slot full returns an error.
        """
        # Ship already has max_primaries=2 weapons filled
        player_ship = _make_player_ship(
            player_id=42,
            ship_name="Sidewinder",
            weapons=["Pulse Laser", "Burst Laser"],
        )
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        game_item = _make_game_item("Rail Gun")
        inv_item = _make_inventory_item("Rail Gun", "weapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item

        with pytest.raises(ValueError, match="slots"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Rail Gun",
            )

        svc.ship_repo.add_equipment.assert_not_called()
        svc.inventory_repo.remove_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_item_player_does_not_own_item_raises(self, mock_db, svc):
        """ValueError is raised when the item is not in player's inventory.

        B.7: Error message must NOT contain numeric player_id; must use 'your inventory'.
        """
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        game_item = _make_game_item("Pulse Laser")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = None  # not owned

        with pytest.raises(ValueError, match="not found in your inventory") as exc_info:
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Pulse Laser",
            )

        # B.7: numeric player_id must NOT appear in user-facing error text
        assert "42" not in str(exc_info.value), "player_id must not appear in error message"
        svc.ship_repo.add_equipment.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_to_ship_not_belonging_to_player_raises(self, mock_db, svc):
        """ValueError is raised when the ship belongs to a different player."""
        # Ship owned by player_id=99, but we call with player_id=42
        player_ship = _make_player_ship(player_id=99, ship_name="Sidewinder")
        svc.ship_repo.get_by_id.return_value = player_ship

        with pytest.raises(ValueError, match="does not belong to player"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Pulse Laser",
            )

    @pytest.mark.asyncio
    async def test_equip_nonexistent_ship_raises(self, mock_db, svc):
        """ValueError is raised when ship ID is not found."""
        svc.ship_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="not found"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=999,
                equipment_type="weapons",
                item_name="Pulse Laser",
            )

    @pytest.mark.asyncio
    async def test_equip_nonexistent_game_item_raises(self, mock_db, svc):
        """ValueError is raised when the item doesn't exist in the game catalogue."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.item_repo.get_by_name.return_value = None  # Not in game data

        with pytest.raises(ValueError, match="not found in game data"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="FakeGun",
            )

    @pytest.mark.asyncio
    async def test_equip_with_invalid_equipment_type_raises(self, mock_db, svc):
        """ValueError is raised for an unrecognised equipment_type."""
        with pytest.raises(ValueError, match="Invalid equipment_type"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="armour",
                item_name="Reactive Armour",
            )


# ---------------------------------------------------------------------------
# Unequip tests
# ---------------------------------------------------------------------------


class TestUnequipItemSuccess:
    """Happy-path unequip scenarios."""

    @pytest.mark.asyncio
    async def test_unequip_weapon_from_ship_success(self, mock_db, svc):
        """Weapon is removed from ship and added back to inventory.

        Acceptance criteria:
        - unequip_item returns success
        - remove_equipment called with correct args
        - add_item called with correct inventory_type
        """
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=["Pulse Laser"])
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_repo.remove_equipment.return_value = player_ship

        result = await svc.unequip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="weapons",
            item_name="Pulse Laser",
        )

        assert result["success"] is True
        svc.ship_repo.remove_equipment.assert_called_once_with(mock_db, 1, "weapons", "Pulse Laser")
        # A.36 fix: add_item now called with concrete type "primary_weapon", not generic "weapon"
        svc.inventory_repo.add_item.assert_called_once_with(mock_db, 42, "primary_weapon", "Pulse Laser", quantity=1)

    @pytest.mark.asyncio
    async def test_unequip_module_from_ship_success(self, mock_db, svc):
        """Module is removed from ship and added back to inventory with correct type."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["Shield Generator"])
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_repo.remove_equipment.return_value = player_ship

        result = await svc.unequip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="modules",
            item_name="Shield Generator",
        )

        assert result["success"] is True
        svc.inventory_repo.add_item.assert_called_once_with(mock_db, 42, "module", "Shield Generator", quantity=1)

    @pytest.mark.asyncio
    async def test_unequip_turret_from_ship_success(self, mock_db, svc):
        """Turret is unequipped and added to inventory with concrete 'turret_weapon' type (A.36 fix)."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", turrets=["Turreted Beam Laser"])
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_repo.remove_equipment.return_value = player_ship

        result = await svc.unequip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="turrets",
            item_name="Turreted Beam Laser",
        )

        assert result["success"] is True
        # A.36 fix: add_item now called with concrete "turret_weapon", not generic "turret"
        svc.inventory_repo.add_item.assert_called_once_with(
            mock_db, 42, "turret_weapon", "Turreted Beam Laser", quantity=1
        )


class TestUnequipItemValidationErrors:
    """Unequip error scenarios."""

    @pytest.mark.asyncio
    async def test_unequip_item_not_on_ship_raises(self, mock_db, svc):
        """ValueError is raised when the item is not equipped on the ship."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        svc.ship_repo.get_by_id.return_value = player_ship

        with pytest.raises(ValueError, match="not equipped"):
            await svc.unequip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Rail Gun",
            )

        svc.ship_repo.remove_equipment.assert_not_called()
        svc.inventory_repo.add_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_unequip_from_ship_not_belonging_to_player_raises(self, mock_db, svc):
        """ValueError is raised when the ship belongs to a different player."""
        player_ship = _make_player_ship(player_id=99, ship_name="Sidewinder")
        svc.ship_repo.get_by_id.return_value = player_ship

        with pytest.raises(ValueError, match="does not belong to player"):
            await svc.unequip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Pulse Laser",
            )

    @pytest.mark.asyncio
    async def test_unequip_with_invalid_equipment_type_raises(self, mock_db, svc):
        """ValueError is raised for an unrecognised equipment_type."""
        # Ship must exist and belong to player so we reach the equipment_type validation
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder")
        svc.ship_repo.get_by_id.return_value = player_ship

        with pytest.raises(ValueError, match="Invalid equipment_type"):
            await svc.unequip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="shields",
                item_name="Shield Cell",
            )


# ---------------------------------------------------------------------------
# Helper / mapping tests
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """Unit tests for EquipmentService helper methods."""

    def test_map_equipment_type_to_slot_weapons(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_slot("weapons") == "max_primaries"

    def test_map_equipment_type_to_slot_modules(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_slot("modules") == "max_modules"

    def test_map_equipment_type_to_slot_turrets(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_slot("turrets") == "max_turrets"

    def test_map_equipment_type_to_inventory_type_weapons(self):
        """A.36 fix: weapons slot now maps to concrete 'primary_weapon', not generic 'weapon'."""
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("weapons") == "primary_weapon"

    def test_map_equipment_type_to_inventory_type_modules(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("modules") == "module"

    def test_map_equipment_type_to_inventory_type_turrets(self):
        """A.36 fix: turrets slot now maps to concrete 'turret_weapon', not generic 'turret'."""
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("turrets") == "turret_weapon"

    def test_invalid_equipment_type_raises_for_slot_map(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        with pytest.raises(ValueError):
            svc_instance._map_equipment_type_to_slot("armour")

    def test_invalid_equipment_type_raises_for_inventory_map(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        with pytest.raises(ValueError):
            svc_instance._map_equipment_type_to_inventory_type("shields")


# ---------------------------------------------------------------------------
# Auto-detection tests
# ---------------------------------------------------------------------------


class TestEquipItemAutoDetect:
    """Tests for equipment_type auto-detection."""

    @pytest.mark.asyncio
    async def test_equip_weapon_auto_detected_from_primary_weapon_type(self, mock_db, svc):
        """When equipment_type=None, auto-detects 'weapons' from PrimaryWeapon item type."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        base_item = _make_base_item("Pulse Laser", "PrimaryWeapon")
        game_item = _make_game_item("Pulse Laser")
        inv_item = _make_inventory_item("Pulse Laser", "weapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            item_name="Pulse Laser",
            # No equipment_type — auto-detect
        )

        assert result["success"] is True
        svc.ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "weapons", "Pulse Laser")

    @pytest.mark.asyncio
    async def test_equip_turret_auto_detected_from_turret_weapon_type(self, mock_db, svc):
        """When equipment_type=None, auto-detects 'turrets' from TurretWeapon item type."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", turrets=[])
        static_ship = _make_static_ship(name="Sidewinder", max_turrets=1)
        base_item = _make_base_item("Berger AGT 20mm", "TurretWeapon")
        game_item = _make_game_item("Berger AGT 20mm")
        inv_item = _make_inventory_item("Berger AGT 20mm", "turret")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            item_name="Berger AGT 20mm",
        )

        assert result["success"] is True
        svc.ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "turrets", "Berger AGT 20mm")

    @pytest.mark.asyncio
    async def test_equip_module_auto_detected_from_module_suffix(self, mock_db, svc):
        """When equipment_type=None, auto-detects 'modules' from ArmourModule type suffix."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=[])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        base_item = _make_base_item("D'iol", "ArmourModule")
        game_item = _make_game_item("D'iol")
        inv_item = _make_inventory_item("D'iol", "module")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            item_name="D'iol",
        )

        assert result["success"] is True
        svc.ship_repo.add_equipment.assert_called_once_with(mock_db, 1, "modules", "D'iol")

    @pytest.mark.asyncio
    async def test_equip_auto_detect_item_not_found_raises(self, mock_db, svc):
        """Auto-detect raises ValueError when item not in game data."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder")
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.item_repo.get_by_name_any_type.return_value = None

        with pytest.raises(ValueError, match="not found in game data"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                item_name="FakeItem",
            )


class TestUnequipItemAutoDetect:
    """Tests for equipment_type auto-detection in unequip."""

    @pytest.mark.asyncio
    async def test_unequip_weapon_auto_detected(self, mock_db, svc):
        """When equipment_type=None for unequip, searches equipped slots."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=["Pulse Laser"])
        base_item = _make_base_item("Pulse Laser", "PrimaryWeapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.ship_repo.remove_equipment.return_value = player_ship

        result = await svc.unequip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            item_name="Pulse Laser",
        )

        assert result["success"] is True
        svc.ship_repo.remove_equipment.assert_called_once_with(mock_db, 1, "weapons", "Pulse Laser")

    @pytest.mark.asyncio
    async def test_unequip_auto_detect_fallback_to_slot_scan(self, mock_db, svc):
        """When item lookup fails, fallback scans equipped slots to find it."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["Mystery Module"])

        svc.ship_repo.get_by_id.return_value = player_ship
        # Item not in item table
        svc.item_repo.get_by_name_any_type.return_value = None
        svc.ship_repo.remove_equipment.return_value = player_ship

        result = await svc.unequip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            item_name="Mystery Module",
        )

        assert result["success"] is True
        svc.ship_repo.remove_equipment.assert_called_once_with(mock_db, 1, "modules", "Mystery Module")

    @pytest.mark.asyncio
    async def test_unequip_auto_detect_item_not_in_any_slot_raises(self, mock_db, svc):
        """When item not found in any slot, raises ValueError."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[], modules=[], turrets=[])

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.item_repo.get_by_name_any_type.return_value = None

        with pytest.raises(ValueError, match="not found in any equipped slot"):
            await svc.unequip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                item_name="Ghost Item",
            )


# ---------------------------------------------------------------------------
# MODULE_EQUIP_LIMITS enforcement tests
# ---------------------------------------------------------------------------


class TestModuleEquipLimits:
    """Tests for MODULE_EQUIP_LIMITS enforcement during equip."""

    @pytest.mark.asyncio
    async def test_equip_unique_module_when_slot_empty_succeeds(self, mock_db, svc):
        """Unique module (limit=1) can be equipped when no conflict exists."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=[])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        base_item = _make_base_item("D'iol", "ArmourModule")
        game_item = _make_game_item("D'iol")
        inv_item = _make_inventory_item("D'iol", "module")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="modules",
            item_name="D'iol",
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_equip_unique_module_when_same_class_equipped_raises(self, mock_db, svc):
        """Equipping a second ArmourModule raises ValueError (limit=1)."""
        # Already has D'iol (ArmourModule) equipped
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["D'iol"])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        new_armour = _make_game_item("E2 Exoclad")
        inv_item = _make_inventory_item("E2 Exoclad", "module")
        # Both are ArmourModule
        armour_item_type = _make_base_item("D'iol", "ArmourModule")
        new_armour_type = _make_base_item("E2 Exoclad", "ArmourModule")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = new_armour
        svc.inventory_repo.get_player_item.return_value = inv_item

        # get_by_name_any_type: first call for "E2 Exoclad" (limit check), then "D'iol" (conflict scan)
        svc.item_repo.get_by_name_any_type.side_effect = [
            new_armour_type,  # called in _validate_module_equip_limit for the new item
            armour_item_type,  # called in _find_conflicting_module for "D'iol"
        ]

        with pytest.raises(ValueError, match="ArmourModule"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="modules",
                item_name="E2 Exoclad",
            )

        svc.ship_repo.add_equipment.assert_not_called()

    @pytest.mark.asyncio
    async def test_equip_unlimited_module_no_limit_check(self, mock_db, svc):
        """CabinModule has unlimited equip limit (-1) — can equip multiple."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["Cabin1"])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        game_item = _make_game_item("Cabin2")
        inv_item = _make_inventory_item("Cabin2", "module")
        # CabinModule has limit=-1 (unlimited)
        cabin_item = _make_base_item("Cabin2", "CabinModule")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.item_repo.get_by_name_any_type.return_value = cabin_item
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        result = await svc.equip_item(
            mock_db,
            player_id=42,
            ship_id=1,
            equipment_type="modules",
            item_name="Cabin2",
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_equip_module_with_zero_limit_raises(self, mock_db, svc):
        """Module with limit=0 (JumpDriveModule) cannot be equipped."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=[])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        game_item = _make_game_item("Jump Drive Mk1")
        inv_item = _make_inventory_item("Jump Drive Mk1", "module")
        # JumpDriveModule has limit=0
        jump_item = _make_base_item("Jump Drive Mk1", "JumpDriveModule")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.item_repo.get_by_name_any_type.return_value = jump_item
        svc.inventory_repo.get_player_item.return_value = inv_item

        with pytest.raises(ValueError, match="limit=0"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="modules",
                item_name="Jump Drive Mk1",
            )


# ---------------------------------------------------------------------------
# equip_check tests
# ---------------------------------------------------------------------------


class TestEquipCheck:
    """Tests for EquipmentService.equip_check()."""

    @pytest.mark.asyncio
    async def test_equip_check_ok_status(self, mock_db, svc):
        """Returns status='ok' when item can be equipped."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        base_item = _make_base_item("Pulse Laser", "PrimaryWeapon")
        inv_item = _make_inventory_item("Pulse Laser", "weapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.inventory_repo.get_player_item.return_value = inv_item

        result = await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Pulse Laser")

        assert result["status"] == "ok"
        assert result["equipment_type"] == "weapons"
        assert result["item_type"] == "PrimaryWeapon"

    @pytest.mark.asyncio
    async def test_equip_check_slot_full_returns_equipped_items(self, mock_db, svc):
        """Returns status='slot_full' with equipped items list when all slots used."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=["Gun A", "Gun B"])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        base_item = _make_base_item("Gun C", "PrimaryWeapon")
        inv_item = _make_inventory_item("Gun C", "weapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.inventory_repo.get_player_item.return_value = inv_item

        result = await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Gun C")

        assert result["status"] == "slot_full"
        assert result["max_slots"] == 2
        assert len(result["equipped_items"]) == 2
        names = [e["name"] for e in result["equipped_items"]]
        assert "Gun A" in names
        assert "Gun B" in names

    @pytest.mark.asyncio
    async def test_equip_check_unique_conflict_for_armour_module(self, mock_db, svc):
        """Returns status='unique_conflict' when ArmourModule already equipped."""
        # D'iol (ArmourModule) is already equipped
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["D'iol"])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        new_armour = _make_base_item("E2 Exoclad", "ArmourModule")
        existing_armour = _make_base_item("D'iol", "ArmourModule")
        inv_item = _make_inventory_item("E2 Exoclad", "module")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.inventory_repo.get_player_item.return_value = inv_item

        # get_by_name_any_type: first for E2 Exoclad, then for D'iol (conflict scan)
        svc.item_repo.get_by_name_any_type.side_effect = [new_armour, existing_armour]

        result = await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="E2 Exoclad")

        assert result["status"] == "unique_conflict"
        assert result["module_class"] == "ArmourModule"
        assert result["max_equipped"] == 1
        assert result["conflicting_item"]["name"] == "D'iol"

    @pytest.mark.asyncio
    async def test_equip_check_item_not_found_raises(self, mock_db, svc):
        """Raises ValueError when item not found in game data."""
        svc.item_repo.get_by_name_any_type.return_value = None

        with pytest.raises(ValueError, match="not found in game data"):
            await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Nonexistent")

    @pytest.mark.asyncio
    async def test_equip_check_not_equippable_type_raises(self, mock_db, svc):
        """Raises ValueError when item type is not equippable."""
        # Ship item type is not equippable via /equip
        base_item = _make_base_item("Eagle", "Ship")
        svc.item_repo.get_by_name_any_type.return_value = base_item

        with pytest.raises(ValueError, match="not equippable"):
            await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Eagle")

    @pytest.mark.asyncio
    async def test_equip_check_player_does_not_own_item_raises(self, mock_db, svc):
        """Raises ValueError when player does not own the item.

        B.7: Error message must NOT contain numeric player_id; must use 'your inventory'.
        """
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        base_item = _make_base_item("Pulse Laser", "PrimaryWeapon")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.inventory_repo.get_player_item.return_value = None

        with pytest.raises(ValueError, match="not found in your inventory") as exc_info:
            await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Pulse Laser")

        # B.7: numeric player_id must NOT appear in user-facing error text
        assert "42" not in str(exc_info.value), "player_id must not appear in error message"

    @pytest.mark.asyncio
    async def test_equip_check_unlimited_module_ok(self, mock_db, svc):
        """CabinModule has unlimited limit — equip_check returns ok even with one equipped."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=["Cabin1"])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        base_item = _make_base_item("Cabin2", "CabinModule")
        inv_item = _make_inventory_item("Cabin2", "module")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.inventory_repo.get_player_item.return_value = inv_item

        # get_by_name_any_type called once for the initial lookup
        svc.item_repo.get_by_name_any_type.return_value = base_item

        result = await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Cabin2")

        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# get_by_name_any_type integration test (module-level function test)
# ---------------------------------------------------------------------------


class TestItemTypeMappingHelpers:
    """Tests for the module-level helper functions."""

    def test_primary_weapon_maps_to_weapons(self):
        from services.equipment_service import _item_type_to_equipment_category

        assert _item_type_to_equipment_category("PrimaryWeapon") == "weapons"

    def test_secondary_weapon_maps_to_secondary_weapons(self):
        """A.38 fix: SecondaryWeapon now routes to 'secondary_weapons' slot, not 'weapons'."""
        from services.equipment_service import _item_type_to_equipment_category

        assert _item_type_to_equipment_category("SecondaryWeapon") == "secondary_weapons"

    def test_turret_weapon_maps_to_turrets(self):
        from services.equipment_service import _item_type_to_equipment_category

        assert _item_type_to_equipment_category("TurretWeapon") == "turrets"

    def test_armour_module_maps_to_modules(self):
        from services.equipment_service import _item_type_to_equipment_category

        assert _item_type_to_equipment_category("ArmourModule") == "modules"

    def test_unknown_suffix_maps_to_none(self):
        from services.equipment_service import _item_type_to_equipment_category

        assert _item_type_to_equipment_category("SomethingElse") is None

    def test_item_type_to_inventory_type_weapons(self):
        """A.36 fix: PrimaryWeapon now maps to concrete 'primary_weapon', not generic 'weapon'."""
        from services.equipment_service import _item_type_to_inventory_type

        assert _item_type_to_inventory_type("PrimaryWeapon") == "primary_weapon"

    def test_item_type_to_inventory_type_modules(self):
        from services.equipment_service import _item_type_to_inventory_type

        assert _item_type_to_inventory_type("ArmourModule") == "module"

    def test_item_type_to_inventory_type_turrets(self):
        """A.36 fix: TurretWeapon now maps to concrete 'turret_weapon', not generic 'turret'."""
        from services.equipment_service import _item_type_to_inventory_type

        assert _item_type_to_inventory_type("TurretWeapon") == "turret_weapon"

    def test_item_type_to_inventory_type_unknown_returns_none(self):
        from services.equipment_service import _item_type_to_inventory_type

        assert _item_type_to_inventory_type("Ship") is None


# ---------------------------------------------------------------------------
# Secondary weapons slot routing tests (A.38 spec §12.2)
# ---------------------------------------------------------------------------


class TestSecondaryWeaponSlotRouting:
    """Tests for secondary_weapons slot routing and A.38 surface gate.

    Spec §12.2: test_equip_secondary_weapon_routes_to_secondary_weapons_slot
                test_equip_secondary_weapon_rejected_when_gated
    """

    @pytest.mark.asyncio
    async def test_equip_secondary_weapon_rejected_when_gated(self, mock_db, svc):
        """Default CURRENTLY_ENABLED_TYPES excludes secondary_weapon → InvalidItemTypeError."""
        from services.exceptions import InvalidItemTypeError
        from services.game_constants import GameConstants

        assert "secondary_weapon" not in GameConstants.CURRENTLY_ENABLED_TYPES

        # Item is resolved as SecondaryWeapon via item_repo
        base_item = _make_base_item("Seeker Missile", "SecondaryWeapon")
        svc.item_repo.get_by_name_any_type.return_value = base_item
        player_ship = _make_player_ship(player_id=42, ship_name="Betty")
        svc.ship_repo.get_by_id.return_value = player_ship

        with pytest.raises(InvalidItemTypeError, match="not currently enabled"):
            await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Seeker Missile")

    @pytest.mark.asyncio
    async def test_equip_secondary_weapon_routes_to_secondary_weapons_slot(self, mock_db, svc, monkeypatch):
        """When secondary_weapon is enabled (monkeypatched), equip routes to secondary_weapons slot."""
        from services.game_constants import GameConstants

        # Enable secondary_weapon via monkeypatch
        monkeypatch.setattr(
            GameConstants,
            "CURRENTLY_ENABLED_TYPES",
            GameConstants.CURRENTLY_ENABLED_TYPES | {"secondary_weapon"},
        )

        base_item = _make_base_item("Seeker Missile", "SecondaryWeapon")
        player_ship = _make_player_ship(player_id=42, ship_name="Betty", weapons=[], modules=[], turrets=[])
        player_ship.secondary_weapons = []
        static_ship = _make_static_ship(name="Betty", max_primaries=2, max_modules=4, max_turrets=1)
        static_ship.max_secondaries = 2

        inv_item = _make_inventory_item("Seeker Missile", "secondary_weapon")

        svc.item_repo.get_by_name_any_type.return_value = base_item
        svc.item_repo.get_by_name.return_value = base_item
        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.inventory_repo.get_player_item.return_value = inv_item
        svc.ship_repo.add_equipment.return_value = player_ship

        # equip_check should not raise and should return status="ok" with equipment_type="secondary_weapons"
        result = await svc.equip_check(mock_db, player_id=42, ship_id=1, item_name="Seeker Missile")
        assert result["status"] == "ok"
        assert result["equipment_type"] == "secondary_weapons"


# ---------------------------------------------------------------------------
# item_discriminator_to_concrete_type tests (write-site helper)
# ---------------------------------------------------------------------------


class TestItemDiscriminatorToConcrete:
    """Tests for item_discriminator_to_concrete_type (write-site type resolver)."""

    def test_primary_weapon(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        assert item_discriminator_to_concrete_type("PrimaryWeapon") == "primary_weapon"

    def test_secondary_weapon(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        assert item_discriminator_to_concrete_type("SecondaryWeapon") == "secondary_weapon"

    def test_turret_weapon(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        assert item_discriminator_to_concrete_type("TurretWeapon") == "turret_weapon"

    def test_module_subclasses(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        for module_type in ("ArmourModule", "ShieldModule", "CabinModule", "BoosterModule"):
            assert item_discriminator_to_concrete_type(module_type) == "module", f"Failed for {module_type}"

    def test_ship(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        assert item_discriminator_to_concrete_type("Ship") == "ship"

    def test_unknown_returns_none(self):
        from services.equipment_service import item_discriminator_to_concrete_type

        assert item_discriminator_to_concrete_type("SomethingUnknown") is None
