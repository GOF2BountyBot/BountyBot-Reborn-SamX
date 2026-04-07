"""Unit tests for DuelRepository.

Mock-based tests (no real database needed).
Covers all CRUD methods and domain-specific queries.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger and sqlalchemy_utils BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

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
    """Mimic async execute() returning a result whose scalars().all() gives items."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)
    scalars_mock.first = MagicMock(return_value=items[0] if items else None)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_rowcount_result(count: int) -> MagicMock:
    """Mimic async execute() returning a result with rowcount."""
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
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateDuelRequest:
    @pytest.mark.asyncio
    async def test_create_duel_request(self, repo, mock_db):
        """create() should add the duel request, commit, and refresh it."""
        duel = _make_duel()
        mock_db.refresh = AsyncMock(side_effect=lambda d: None)

        result = await repo.create(mock_db, duel)

        mock_db.add.assert_called_once_with(duel)
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(duel)
        assert result is duel

    @pytest.mark.asyncio
    async def test_create_duel_request_with_zero_stakes(self, repo, mock_db):
        """create() persists duel request with zero stakes."""
        duel = _make_duel(stakes=0)
        mock_db.refresh = AsyncMock(side_effect=lambda d: None)

        result = await repo.create(mock_db, duel)

        mock_db.add.assert_called_once_with(duel)
        assert result.stakes == 0


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id(self, repo, mock_db):
        """get_by_id() should return the duel request fetched by db.get()."""
        duel = _make_duel(id=42)
        mock_db.get = AsyncMock(return_value=duel)

        result = await repo.get_by_id(mock_db, 42)

        mock_db.get.assert_awaited_once_with(DuelRequest, 42)
        assert result is duel

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, repo, mock_db):
        """get_by_id() should return None when the duel request does not exist."""
        mock_db.get = AsyncMock(return_value=None)

        result = await repo.get_by_id(mock_db, 9999)

        assert result is None


class TestGetPendingByPlayers:
    @pytest.mark.asyncio
    async def test_get_pending_by_players(self, repo, mock_db):
        """get_pending_by_players() should return a pending duel between two players."""
        duel = _make_duel(challenger_id=100, target_id=200, guild_id=111, status="pending")
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([duel]))

        result = await repo.get_pending_by_players(mock_db, 100, 200, 111)

        mock_db.execute.assert_awaited_once()
        assert result is duel

    @pytest.mark.asyncio
    async def test_get_pending_by_players_not_found(self, repo, mock_db):
        """get_pending_by_players() should return None when no pending duel exists."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_pending_by_players(mock_db, 100, 200, 111)

        assert result is None


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_status(self, repo, mock_db):
        """update_status() should change the status and commit."""
        duel = _make_duel(id=10, status="pending")
        mock_db.get = AsyncMock(return_value=duel)
        mock_db.refresh = AsyncMock(side_effect=lambda d: None)

        result = await repo.update_status(mock_db, 10, "accepted")

        mock_db.get.assert_awaited_once_with(DuelRequest, 10)
        assert duel.status == "accepted"
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(duel)
        assert result is duel

    @pytest.mark.asyncio
    async def test_update_status_not_found(self, repo, mock_db):
        """update_status() returns None when duel request does not exist."""
        mock_db.get = AsyncMock(return_value=None)

        result = await repo.update_status(mock_db, 9999, "accepted")

        assert result is None


class TestDeleteExpired:
    @pytest.mark.asyncio
    async def test_delete_expired(self, repo, mock_db):
        """delete_expired() should delete expired pending duels and return count."""
        now = datetime.now(UTC)
        mock_db.execute = AsyncMock(return_value=_make_rowcount_result(3))

        result = await repo.delete_expired(mock_db, now)

        mock_db.execute.assert_awaited_once()
        mock_db.commit.assert_awaited_once()
        assert result == 3

    @pytest.mark.asyncio
    async def test_delete_expired_none(self, repo, mock_db):
        """delete_expired() returns 0 when no expired duels exist."""
        now = datetime.now(UTC)
        mock_db.execute = AsyncMock(return_value=_make_rowcount_result(0))

        result = await repo.delete_expired(mock_db, now)

        assert result == 0


class TestGetActiveByGuild:
    @pytest.mark.asyncio
    async def test_get_active_by_guild(self, repo, mock_db):
        """get_active_by_guild() should return only pending duels for the guild."""
        pending_duel = _make_duel(status="pending", guild_id=111)

        mock_db.execute = AsyncMock(return_value=_make_scalars_result([pending_duel]))

        result = await repo.get_active_by_guild(mock_db, guild_id=111)

        mock_db.execute.assert_awaited_once()
        assert len(result) == 1
        assert result[0].status == "pending"

    @pytest.mark.asyncio
    async def test_get_active_by_guild_empty(self, repo, mock_db):
        """get_active_by_guild() returns empty list when no pending duels exist."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_active_by_guild(mock_db, guild_id=999)

        assert result == []


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_create_rolls_back_on_error(self, repo, mock_db):
        """create() should rollback on commit failure."""
        duel = _make_duel()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.create(mock_db, duel)

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_status_rolls_back_on_error(self, repo, mock_db):
        """update_status() should rollback on commit failure."""
        duel = _make_duel(id=10)
        mock_db.get = AsyncMock(return_value=duel)
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.update_status(mock_db, 10, "accepted")

        mock_db.rollback.assert_awaited_once()
