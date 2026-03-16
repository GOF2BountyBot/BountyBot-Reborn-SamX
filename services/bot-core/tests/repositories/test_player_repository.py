"""Unit tests for PlayerRepository – mock-based (no real database).

Targets the exception-handling paths that integration tests do not reach.
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
from persist.models.player import Player
from persist.repositories.player_repository import PlayerRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_player(**overrides) -> MagicMock:
    defaults = dict(
        id=1,
        user_id=12345,
        guild_id=67890,
        credits=1000,
        lifetime_credits=5000,
        systems_checked=10,
        bounty_wins=3,
        xp=500,
        tier="Bronze",
        prestige_count=0,
        duel_wins=1,
        duel_losses=0,
        active_ship_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=Player)
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
def repo() -> PlayerRepository:
    return PlayerRepository()


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
            await repo.get_by_id(mock_db, 1)


# ===================================================================
# get_by_id_for_update – exception path (lines 35-42)
# ===================================================================

class TestGetByIdForUpdate:
    @pytest.mark.asyncio
    async def test_get_by_id_for_update_success(self, repo, mock_db):
        player = _make_player()
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([player]))
        result = await repo.get_by_id_for_update(mock_db, 1)
        assert result is player

    @pytest.mark.asyncio
    async def test_get_by_id_for_update_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("lock fail"))
        with pytest.raises(Exception, match="lock fail"):
            await repo.get_by_id_for_update(mock_db, 1)


# ===================================================================
# count – exception path (lines 50-55)
# ===================================================================

class TestCount:
    @pytest.mark.asyncio
    async def test_count_success(self, repo, mock_db):
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(42))
        result = await repo.count(mock_db)
        assert result == 42

    @pytest.mark.asyncio
    async def test_count_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("count fail"))
        with pytest.raises(Exception, match="count fail"):
            await repo.count(mock_db)


# ===================================================================
# list_all – exception path (lines 62-64)
# ===================================================================

class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))
        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path (lines 74-77)
# ===================================================================

class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        player = _make_player()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))
        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, player)
        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_by_user_and_guild – exception path (lines 116-119)
# ===================================================================

class TestGetByUserAndGuild:
    @pytest.mark.asyncio
    async def test_get_by_user_and_guild_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("lookup fail"))
        with pytest.raises(Exception, match="lookup fail"):
            await repo.get_by_user_and_guild(mock_db, user_id=123, guild_id=456)


# ===================================================================
# get_players_by_guild – exception path (lines 130-132)
# ===================================================================

class TestGetPlayersByGuild:
    @pytest.mark.asyncio
    async def test_get_players_by_guild_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("guild query fail"))
        with pytest.raises(Exception, match="guild query fail"):
            await repo.get_players_by_guild(mock_db, guild_id=456)


# ===================================================================
# get_players_by_user – exception path (lines 141-143)
# ===================================================================

class TestGetPlayersByUser:
    @pytest.mark.asyncio
    async def test_get_players_by_user_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("user query fail"))
        with pytest.raises(Exception, match="user query fail"):
            await repo.get_players_by_user(mock_db, user_id=123)


# ===================================================================
# update_credits – exception paths (lines 152-154, 178, 183-187)
# ===================================================================

class TestUpdateCredits:
    @pytest.mark.asyncio
    async def test_update_credits_commit_true_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("credit fail"))
        with pytest.raises(Exception, match="credit fail"):
            await repo.update_credits(mock_db, player_id=1, new_credits=500)
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_credits_commit_false_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("credit fail"))
        with pytest.raises(Exception, match="credit fail"):
            await repo.update_credits(mock_db, player_id=1, new_credits=500, commit=False)
        # When commit=False, rollback should NOT be called
        mock_db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_credits_commit_false_uses_flush(self, repo, mock_db):
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        await repo.update_credits(mock_db, player_id=1, new_credits=500, commit=False)
        mock_db.flush.assert_awaited_once()
        mock_db.commit.assert_not_awaited()


# ===================================================================
# update_xp – exception path (lines 178, 183-187)
# ===================================================================

class TestUpdateXp:
    @pytest.mark.asyncio
    async def test_update_xp_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("xp fail"))
        with pytest.raises(Exception, match="xp fail"):
            await repo.update_xp(mock_db, player_id=1, xp=1000)
        mock_db.rollback.assert_awaited()


# ===================================================================
# update_tier – exception + validation (lines 202-205)
# ===================================================================

class TestUpdateTier:
    @pytest.mark.asyncio
    async def test_update_tier_invalid_tier(self, repo, mock_db):
        with pytest.raises(ValueError, match="Invalid tier"):
            await repo.update_tier(mock_db, player_id=1, tier="Diamond")
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_tier_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("tier fail"))
        with pytest.raises(Exception, match="tier fail"):
            await repo.update_tier(mock_db, player_id=1, tier="Gold")
        mock_db.rollback.assert_awaited()


# ===================================================================
# update_active_ship – exception path (lines 242-245)
# ===================================================================

class TestUpdateActiveShip:
    @pytest.mark.asyncio
    async def test_update_active_ship_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("ship fail"))
        with pytest.raises(Exception, match="ship fail"):
            await repo.update_active_ship(mock_db, player_id=1, ship_id=5)
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_active_ship_clear(self, repo, mock_db):
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        result = await repo.update_active_ship(mock_db, player_id=1, ship_id=None)
        mock_db.commit.assert_awaited()
        assert result is player
