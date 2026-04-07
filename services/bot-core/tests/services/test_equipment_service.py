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
    ship.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    # Delegate to the real PlayerShip logic
    def _get_equipped_count(equipment_type: str) -> int:
        mapping = {
            "weapons": ship.weapons,
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
) -> SimpleNamespace:
    """Build a simple namespace that mimics the Ship static model."""
    return SimpleNamespace(
        name=name,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
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


@pytest.fixture
def svc() -> EquipmentService:
    """EquipmentService with all repositories replaced by AsyncMocks.

    This is *mock 2* — a single consolidated mock that replaces all four
    repos so individual tests need only configure the relevant methods.
    """
    service = EquipmentService.__new__(EquipmentService)

    service.ship_repo = AsyncMock()
    service.inventory_repo = AsyncMock()
    service.item_repo = AsyncMock()
    service.ship_data_repo = AsyncMock()

    # Default: nothing found (tests override what they need)
    service.ship_repo.get_by_id = AsyncMock(return_value=None)
    service.item_repo.get_by_name = AsyncMock(return_value=None)
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
        inv_item = _make_inventory_item("Pulse Laser", "weapon")

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
        svc.inventory_repo.remove_item.assert_called_once_with(mock_db, 42, "weapon", "Pulse Laser", quantity=1)

    @pytest.mark.asyncio
    async def test_equip_module_to_ship_with_available_module_slot(self, mock_db, svc):
        """Module is equipped when ship has an open module slot."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", modules=[])
        static_ship = _make_static_ship(name="Sidewinder", max_modules=3)
        game_item = _make_game_item("Shield Generator")
        inv_item = _make_inventory_item("Shield Generator", "module")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
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
        inv_item = _make_inventory_item("Turreted Beam Laser", "turret")

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
        svc.inventory_repo.remove_item.assert_called_once_with(mock_db, 42, "turret", "Turreted Beam Laser", quantity=1)


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
        """ValueError is raised when the item is not in player's inventory."""
        player_ship = _make_player_ship(player_id=42, ship_name="Sidewinder", weapons=[])
        static_ship = _make_static_ship(name="Sidewinder", max_primaries=2)
        game_item = _make_game_item("Pulse Laser")

        svc.ship_repo.get_by_id.return_value = player_ship
        svc.ship_data_repo.get_by_name.return_value = static_ship
        svc.item_repo.get_by_name.return_value = game_item
        svc.inventory_repo.get_player_item.return_value = None  # not owned

        with pytest.raises(ValueError, match="not found in player"):
            await svc.equip_item(
                mock_db,
                player_id=42,
                ship_id=1,
                equipment_type="weapons",
                item_name="Pulse Laser",
            )

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
        svc.inventory_repo.add_item.assert_called_once_with(mock_db, 42, "weapon", "Pulse Laser", quantity=1)

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
        """Turret is unequipped and added to inventory with 'turret' type."""
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
        svc.inventory_repo.add_item.assert_called_once_with(mock_db, 42, "turret", "Turreted Beam Laser", quantity=1)


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
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("weapons") == "weapon"

    def test_map_equipment_type_to_inventory_type_modules(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("modules") == "module"

    def test_map_equipment_type_to_inventory_type_turrets(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        assert svc_instance._map_equipment_type_to_inventory_type("turrets") == "turret"

    def test_invalid_equipment_type_raises_for_slot_map(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        with pytest.raises(ValueError):
            svc_instance._map_equipment_type_to_slot("armour")

    def test_invalid_equipment_type_raises_for_inventory_map(self):
        svc_instance = EquipmentService.__new__(EquipmentService)
        with pytest.raises(ValueError):
            svc_instance._map_equipment_type_to_inventory_type("shields")
