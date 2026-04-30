"""Unit tests for UserRepository – mock-based (no real database).

Targets the exception-handling paths (except Exception as e: blocks).
Missed lines: 24-26, 35-37, 44-46, 53-55, 65-68, 107-110, 128-130
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
from persist.models.user import User
from persist.repositories.user_repository import UserRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> MagicMock:
    """Return a MagicMock with User-like attributes."""
    defaults = dict(
        id=123456789,
        discord_username="TestUser",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=User)
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


def _make_scalar_one_result(value) -> MagicMock:
    result_mock = MagicMock()
    result_mock.scalar_one = MagicMock(return_value=value)
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> UserRepository:
    return UserRepository()


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
# get_by_id – exception path (lines 24-26)
# ===================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_exception(self, repo, mock_db):
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.get_by_id(mock_db, 123456789)


# ===================================================================
# get_by_name – exception path (lines 35-37)
# ===================================================================


class TestGetByName:
    @pytest.mark.asyncio
    async def test_get_by_name_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("name lookup fail"))

        with pytest.raises(Exception, match="name lookup fail"):
            await repo.get_by_name(mock_db, "TestUser")


# ===================================================================
# get_by_discord_id – exception path (re-raises from get_by_id)
# ===================================================================


class TestGetByDiscordId:
    @pytest.mark.asyncio
    async def test_get_by_discord_id_exception(self, repo, mock_db):
        """get_by_discord_id must propagate exceptions from the underlying get_by_id call."""
        mock_db.get = AsyncMock(side_effect=Exception("snowflake lookup fail"))

        with pytest.raises(Exception, match="snowflake lookup fail"):
            await repo.get_by_discord_id(mock_db, 123456789)


# ===================================================================
# count – exception path (lines 44-46)
# ===================================================================


class TestCount:
    @pytest.mark.asyncio
    async def test_count_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("count fail"))

        with pytest.raises(Exception, match="count fail"):
            await repo.count(mock_db)


# ===================================================================
# list_all – exception path (lines 53-55)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))

        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path with rollback (lines 65-68)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        user = _make_user()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, user)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# remove – exception path with rollback (lines 107-110)
# ===================================================================


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_exception_triggers_rollback(self, repo, mock_db):
        user = _make_user()
        mock_db.commit = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.remove(mock_db, user)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_or_create_user – exception path (lines 128-130)
# ===================================================================


class TestGetOrCreateUser:
    @pytest.mark.asyncio
    async def test_get_or_create_user_exception(self, repo, mock_db):
        """get_or_create_user should propagate exceptions from get_by_id."""
        mock_db.get = AsyncMock(side_effect=Exception("get fail"))

        with pytest.raises(Exception, match="get fail"):
            await repo.get_or_create_user(mock_db, discord_id=123456789, username="TestUser")

    @pytest.mark.asyncio
    async def test_get_or_create_user_add_exception_triggers_rollback(self, repo, mock_db):
        """When user doesn't exist and add() fails, rollback should be called."""
        # get_by_id returns None → need to create user
        mock_db.get = AsyncMock(return_value=None)
        # commit fails when add() calls it
        mock_db.commit = AsyncMock(side_effect=Exception("create fail"))

        with pytest.raises(Exception, match="create fail"):
            await repo.get_or_create_user(mock_db, discord_id=123456789, username="NewUser")

        mock_db.rollback.assert_awaited()
