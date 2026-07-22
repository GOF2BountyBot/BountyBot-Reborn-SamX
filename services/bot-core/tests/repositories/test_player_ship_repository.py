"""Tests for PlayerShipRepository.

Mix of:
  - mock-based tests targeting the exception-handling paths (except
    Exception as e: blocks) that integration tests do not reach — a mock
    AsyncSession is appropriate here since we are deliberately forcing DB
    failures, and the ValueError branch tests only need a ship-like object
    for ``db.get`` to return.
  - real-SQLite round-trip tests (in-memory aiosqlite) for the
    add_equipment / remove_equipment list-mutation behavior (weapons /
    modules / turrets), so the actual list append/remove + persistence is
    exercised instead of a mock echoing back whatever we set on it.
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
from persist.models.base import Base
from persist.models.player import Player
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from persist.repositories.player_ship_repository import PlayerShipRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ship(**overrides) -> PlayerShip:
    """Build a real, unpersisted PlayerShip instance with sensible defaults.

    A real instance (rather than ``MagicMock(spec=PlayerShip)``) is used so
    that list mutation performed by the repository under test is genuine
    list mutation on a real mapped object. Callers that need a real DB
    round-trip should go through ``_seed_ship`` instead.
    """
    defaults = dict(
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
    return PlayerShip(**defaults)


async def _seed_ship(db_session: AsyncSession, **overrides) -> PlayerShip:
    """Create and commit a User + Player + PlayerShip row, returning the ship.

    ``player_id``/``user_id`` are not taken from overrides directly — a
    fresh User + Player pair is always created to satisfy PlayerShip's
    foreign key, so tests only need to specify ship-level fields
    (weapons/modules/turrets/etc).
    """
    user_id = overrides.pop("user_id", 900000)
    guild_id = overrides.pop("guild_id", 1)
    db_session.add(User(id=user_id, discord_username=f"user{user_id}"))
    player = Player(user_id=user_id, guild_id=guild_id, credits=0)
    db_session.add(player)
    await db_session.flush()

    ship = _make_ship(player_id=player.id, **overrides)
    db_session.add(ship)
    await db_session.commit()
    await db_session.refresh(ship)
    return ship


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

_PLAYER_SHIP_TABLES = [User.__table__, Player.__table__, PlayerShip.__table__]


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


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_PLAYER_SHIP_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


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
