"""Unit tests for DuelRepository – mock-based (no real database).

Targets the exception-handling paths (except Exception as e: blocks).
Missed lines: 30-32, 40-45, 59-61, 73-76, 107-111, 151-153, 170-173, 193-197
"""

import os
import sys
from datetime import UTC, datetime, timedelta
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
from persist.models.duel_request import DuelRequest
from persist.repositories.duel_repository import DuelRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_duel(**overrides) -> MagicMock:
    """Return a MagicMock with DuelRequest-like attributes."""
    defaults = dict(
        id=1,
        guild_id=111222333,
        challenger_id=100000001,
        target_id=100000002,
        stakes=500,
        status="pending",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=DuelRequest)
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


def _make_rowcount_result(count: int) -> MagicMock:
    result_mock = MagicMock()
    result_mock.rowcount = count
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> DuelRepository:
    return DuelRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = MagicMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock()
    db.flush = AsyncMock()
    return db


# ===================================================================
# get_by_id – exception path (lines 30-32)
# ===================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_exception(self, repo, mock_db):
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.get_by_id(mock_db, 1)


# ===================================================================
# list_all – exception path (lines 40-45)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))

        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path with rollback (lines 59-61)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        duel = _make_duel()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, duel)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# remove – exception path with rollback (lines 73-76)
# ===================================================================


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_exception_triggers_rollback(self, repo, mock_db):
        duel = _make_duel()
        mock_db.commit = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.remove(mock_db, duel)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_pending_by_players – exception path (lines 107-111)
# ===================================================================


class TestGetPendingByPlayers:
    @pytest.mark.asyncio
    async def test_get_pending_by_players_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("query fail"))

        with pytest.raises(Exception, match="query fail"):
            await repo.get_pending_by_players(mock_db, 100, 200, 111)


# ===================================================================
# delete_expired – exception path with rollback (lines 151-153)
# ===================================================================


class TestDeleteExpired:
    @pytest.mark.asyncio
    async def test_delete_expired_exception_triggers_rollback(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("delete expired fail"))

        with pytest.raises(Exception, match="delete expired fail"):
            await repo.delete_expired(mock_db, datetime.now(UTC))

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_active_by_guild – exception path (lines 170-173)
# ===================================================================


class TestGetActiveByGuild:
    @pytest.mark.asyncio
    async def test_get_active_by_guild_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("guild query fail"))

        with pytest.raises(Exception, match="guild query fail"):
            await repo.get_active_by_guild(mock_db, guild_id=111)


# ===================================================================
# get_pending_by_target – exception path (lines 193-197)
# ===================================================================


class TestGetPendingByTarget:
    @pytest.mark.asyncio
    async def test_get_pending_by_target_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("target query fail"))

        with pytest.raises(Exception, match="target query fail"):
            await repo.get_pending_by_target(mock_db, target_id=200, guild_id=111)
