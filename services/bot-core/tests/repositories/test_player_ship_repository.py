"""Unit tests for PlayerShipRepository – mock-based (no real database).

Targets the exception-handling paths (except Exception as e: blocks).
Missed lines: 25-27, 38-40, 50-53, 94-97, 108-110, 124-126, 204,
              248-253, 303-305
"""

import os
import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.models.player_ship import PlayerShip
from persist.repositories.player_ship_repository import PlayerShipRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ship(**overrides) -> MagicMock:
    """Return a MagicMock with PlayerShip-like attributes."""
    defaults = dict(
        id=1,
        player_id=10,
        ship_name="Viper Mk II",
        nickname=None,
        is_active=False,
        weapons=["Laser Cannon"],
        modules=["Shield Booster"],
        turrets=[],
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=PlayerShip)
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_scalars_result(items) -> MagicMock:
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)
    scalars_mock.first = MagicMock(return_value=items[0] if items else None)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> PlayerShipRepository:
    return PlayerShipRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock()
    db.flush = AsyncMock()
    return db


# ===================================================================
# get_by_id – exception path (lines 25-27)
# ===================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_exception(self, repo, mock_db):
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.get_by_id(mock_db, 1)


# ===================================================================
# list_all – exception path (lines 38-40)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))

        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path with rollback (lines 50-53)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        ship = _make_ship()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, ship)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# remove – exception path with rollback (lines 94-97)
# ===================================================================


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_exception_triggers_rollback(self, repo, mock_db):
        ship = _make_ship()
        mock_db.commit = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.remove(mock_db, ship)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_player_ships – exception path (lines 108-110)
# ===================================================================


class TestGetPlayerShips:
    @pytest.mark.asyncio
    async def test_get_player_ships_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("ships query fail"))

        with pytest.raises(Exception, match="ships query fail"):
            await repo.get_player_ships(mock_db, player_id=10)


# ===================================================================
# get_active_ship – exception path (lines 124-126)
# ===================================================================


class TestGetActiveShip:
    @pytest.mark.asyncio
    async def test_get_active_ship_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("active ship fail"))

        with pytest.raises(Exception, match="active ship fail"):
            await repo.get_active_ship(mock_db, player_id=10)


# ===================================================================
# add_equipment – ValueError path when ship not found (line 204)
# and the outer exception re-raise (lines 227-229)
# ===================================================================


class TestAddEquipment:
    @pytest.mark.asyncio
    async def test_add_equipment_ship_not_found(self, repo, mock_db):
        """add_equipment raises ValueError when ship doesn't exist."""
        mock_db.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await repo.add_equipment(mock_db, ship_id=999, equipment_type="weapons", item_name="Laser")

    @pytest.mark.asyncio
    async def test_add_equipment_invalid_type(self, repo, mock_db):
        """add_equipment raises ValueError for an unknown equipment_type."""
        ship = _make_ship(id=1, player_id=10)
        mock_db.get = AsyncMock(return_value=ship)

        with pytest.raises(ValueError, match="Invalid equipment type"):
            await repo.add_equipment(mock_db, ship_id=1, equipment_type="shields", item_name="Laser")

    @pytest.mark.asyncio
    async def test_add_equipment_db_exception(self, repo, mock_db):
        """add_equipment propagates DB exceptions from get_by_id."""
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.add_equipment(mock_db, ship_id=1, equipment_type="weapons", item_name="Laser")


# ===================================================================
# remove_equipment – exception paths (lines 248-253)
# ===================================================================


class TestRemoveEquipment:
    @pytest.mark.asyncio
    async def test_remove_equipment_ship_not_found(self, repo, mock_db):
        """remove_equipment raises ValueError when ship doesn't exist."""
        mock_db.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await repo.remove_equipment(mock_db, ship_id=999, equipment_type="weapons", item_name="Laser")

    @pytest.mark.asyncio
    async def test_remove_equipment_invalid_type(self, repo, mock_db):
        """remove_equipment raises ValueError for unknown equipment_type."""
        ship = _make_ship(id=1, player_id=10, weapons=["Laser"])
        mock_db.get = AsyncMock(return_value=ship)

        with pytest.raises(ValueError, match="Invalid equipment type"):
            await repo.remove_equipment(mock_db, ship_id=1, equipment_type="shields", item_name="Laser")

    @pytest.mark.asyncio
    async def test_remove_equipment_item_not_equipped(self, repo, mock_db):
        """remove_equipment raises ValueError when item not in loadout."""
        ship = _make_ship(id=1, player_id=10, weapons=["Other Weapon"])
        mock_db.get = AsyncMock(return_value=ship)

        with pytest.raises(ValueError, match="not equipped"):
            await repo.remove_equipment(mock_db, ship_id=1, equipment_type="weapons", item_name="Laser")

    @pytest.mark.asyncio
    async def test_remove_equipment_db_exception(self, repo, mock_db):
        """remove_equipment propagates DB exceptions from get_by_id."""
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.remove_equipment(mock_db, ship_id=1, equipment_type="weapons", item_name="Laser")


# ===================================================================
# get_ships_by_name – exception path (lines 303-305)
# ===================================================================


class TestGetShipsByName:
    @pytest.mark.asyncio
    async def test_get_ships_by_name_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("ships by name fail"))

        with pytest.raises(Exception, match="ships by name fail"):
            await repo.get_ships_by_name(mock_db, player_id=10, ship_name="Viper Mk II")
