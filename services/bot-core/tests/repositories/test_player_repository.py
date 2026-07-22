"""Tests for PlayerRepository.

Mix of:
  - mock-based tests targeting exception-handling paths that integration
    tests do not reach (a mock AsyncSession is appropriate here — we are
    deliberately forcing DB failures).
  - real-SQLite round-trip tests (in-memory aiosqlite) for behavioral paths,
    notably the ``active_within_days`` date predicate in
    ``get_players_by_guild`` and the update_* happy paths, so real attribute
    mutation / persistence is exercised instead of a mock echoing back
    whatever the test told it to return.
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
from persist.models.base import Base
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.player_repository import PlayerRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(**overrides) -> Player:
    """Build a real, unpersisted Player instance with sensible defaults.

    A real instance (rather than ``MagicMock(spec=Player)``) is used so that
    attribute mutation performed by the repository under test (e.g.
    ``player.credits = new_credits``) is genuine attribute mutation on a real
    mapped object, not a mock recording a call. Callers that need a real DB
    round-trip should go through ``_seed_player`` instead.
    """
    defaults = dict(
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
    return Player(**defaults)


async def _seed_player(db_session: AsyncSession, **overrides) -> Player:
    """Create and commit a User + Player row, returning the persisted Player.

    ``user_id`` may be supplied via overrides; a User row is created to
    satisfy Player.user_id's foreign key.
    """
    user_id = overrides.pop("user_id", 12345)
    db_session.add(User(id=user_id, discord_username=f"user{user_id}"))
    player = _make_player(user_id=user_id, **overrides)
    db_session.add(player)
    await db_session.commit()
    await db_session.refresh(player)
    return player


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

_PLAYER_TABLES = [User.__table__, Player.__table__]


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


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_PLAYER_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


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
# get_players_by_guild – exception path + active_within_days filter
# ===================================================================


class TestGetPlayersByGuild:
    @pytest.mark.asyncio
    async def test_get_players_by_guild_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("guild query fail"))
        with pytest.raises(Exception, match="guild query fail"):
            await repo.get_players_by_guild(mock_db, guild_id=456)

    @pytest.mark.asyncio
    async def test_get_players_by_guild_no_filter_returns_all(self, repo, db_session):
        """active_within_days=None: no date filter applied, all players returned
        regardless of how stale their updated_at is (real SQLite round-trip)."""
        guild_id = 456
        now = datetime.now(UTC)
        recent = await _seed_player(db_session, user_id=1, guild_id=guild_id, updated_at=now)
        ancient = await _seed_player(db_session, user_id=2, guild_id=guild_id, updated_at=now - timedelta(days=1000))

        result = await repo.get_players_by_guild(db_session, guild_id=guild_id)

        ids = {p.id for p in result}
        assert len(result) == 2
        assert recent.id in ids
        assert ancient.id in ids

    @pytest.mark.asyncio
    async def test_get_players_by_guild_active_within_days_filters_by_updated_at(self, repo, db_session):
        """active_within_days=7: only the player updated within the window is
        returned — a real SQLite round-trip that exercises the actual SQL
        WHERE clause (Player.updated_at >= cutoff), not a mock echoing back
        whatever the test told it to return."""
        guild_id = 456
        now = datetime.now(UTC)
        recent = await _seed_player(db_session, user_id=1, guild_id=guild_id, updated_at=now)
        stale = await _seed_player(db_session, user_id=2, guild_id=guild_id, updated_at=now - timedelta(days=30))

        result = await repo.get_players_by_guild(db_session, guild_id=guild_id, active_within_days=7)

        ids = {p.id for p in result}
        assert recent.id in ids
        assert stale.id not in ids

    @pytest.mark.asyncio
    async def test_get_players_by_guild_active_within_days_zero_no_filter(self, repo, db_session):
        """active_within_days=0: treat as 'no filter' (same as None) — a
        player last updated a thousand days ago must still come back."""
        guild_id = 456
        now = datetime.now(UTC)
        recent = await _seed_player(db_session, user_id=1, guild_id=guild_id, updated_at=now)
        ancient = await _seed_player(db_session, user_id=2, guild_id=guild_id, updated_at=now - timedelta(days=1000))

        result = await repo.get_players_by_guild(db_session, guild_id=guild_id, active_within_days=0)

        ids = {p.id for p in result}
        assert len(result) == 2
        assert recent.id in ids
        assert ancient.id in ids

    @pytest.mark.asyncio
    async def test_get_players_by_guild_active_within_days_one_day(self, repo, db_session):
        """active_within_days=1: a player updated 2 hours ago is inside the
        window, a player updated 3 days ago is outside it."""
        guild_id = 456
        now = datetime.now(UTC)
        within_window = await _seed_player(
            db_session, user_id=1, guild_id=guild_id, updated_at=now - timedelta(hours=2)
        )
        outside_window = await _seed_player(
            db_session, user_id=2, guild_id=guild_id, updated_at=now - timedelta(days=3)
        )

        result = await repo.get_players_by_guild(db_session, guild_id=guild_id, active_within_days=1)

        ids = {p.id for p in result}
        assert within_window.id in ids
        assert outside_window.id not in ids


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
        # Post Option-B refactor: failure surface is db.commit (the ORM-tracked
        # mutation flushes on commit). db.execute is no longer used by this method.
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        mock_db.commit = AsyncMock(side_effect=Exception("credit fail"))
        with pytest.raises(Exception, match="credit fail"):
            await repo.update_credits(mock_db, player_id=1, new_credits=500)
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_credits_commit_false_exception(self, repo, mock_db):
        # In commit=False mode, the failure surface is db.flush.
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        mock_db.flush = AsyncMock(side_effect=Exception("credit fail"))
        with pytest.raises(Exception, match="credit fail"):
            await repo.update_credits(mock_db, player_id=1, new_credits=500, commit=False)
        # When commit=False, rollback should NOT be called (caller owns transaction)
        mock_db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_credits_commit_false_uses_flush(self, repo, db_session):
        """commit=False flushes the mutation (visible in-session) but does not
        commit it — proven by rolling back and re-fetching, which must show
        the ORIGINAL credits. A mock can't catch a real missing-commit bug;
        a genuine rollback can."""
        player = await _seed_player(db_session, user_id=1, credits=1000)
        player_id = player.id  # capture before rollback expires the instance

        result = await repo.update_credits(db_session, player_id=player_id, new_credits=500, commit=False)
        assert result.credits == 500

        await db_session.rollback()
        refetched = await repo.get_by_id(db_session, player_id)
        assert refetched.credits == 1000

    @pytest.mark.asyncio
    async def test_update_credits_player_not_found_raises_value_error(self, repo, mock_db):
        # Post Option-B: explicit ValueError when the player ID doesn't exist
        # (was previously a silent no-op via Core UPDATE matching zero rows).
        mock_db.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Player 999 not found"):
            await repo.update_credits(mock_db, player_id=999, new_credits=500)


# ===================================================================
# update_xp – exception path (lines 178, 183-187)
# ===================================================================


class TestUpdateXp:
    @pytest.mark.asyncio
    async def test_update_xp_exception(self, repo, mock_db):
        # Post Option-B refactor: failure surface is db.commit, not db.execute.
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        mock_db.commit = AsyncMock(side_effect=Exception("xp fail"))
        with pytest.raises(Exception, match="xp fail"):
            await repo.update_xp(mock_db, player_id=1, xp=1000)
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_xp_player_not_found_raises_value_error(self, repo, mock_db):
        # Post Option-B: explicit ValueError when the player ID doesn't exist.
        mock_db.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Player 999 not found"):
            await repo.update_xp(mock_db, player_id=999, xp=1000)


# ===================================================================
# update_tier – exception + validation (lines 202-205)
# ===================================================================


class TestUpdateTier:
    @pytest.mark.asyncio
    async def test_update_tier_invalid_tier(self, repo, mock_db):
        # Post Option-B: ValueError raised before any DB I/O, so no rollback.
        with pytest.raises(ValueError, match="Invalid tier"):
            await repo.update_tier(mock_db, player_id=1, tier="Diamond")
        mock_db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_tier_exception(self, repo, mock_db):
        # Post Option-B refactor: failure surface is db.commit.
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        mock_db.commit = AsyncMock(side_effect=Exception("tier fail"))
        with pytest.raises(Exception, match="tier fail"):
            await repo.update_tier(mock_db, player_id=1, tier="Gold")
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_tier_player_not_found_raises_value_error(self, repo, mock_db):
        # Post Option-B: explicit ValueError when the player ID doesn't exist.
        # Use a valid tier ("Silver") so the invalid-tier branch is NOT triggered —
        # this exercises the player-not-found branch.
        mock_db.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Player 999 not found"):
            await repo.update_tier(mock_db, player_id=999, tier="Silver")


# ===================================================================
# update_active_ship – exception path (lines 242-245)
# ===================================================================


class TestUpdateActiveShip:
    @pytest.mark.asyncio
    async def test_update_active_ship_exception(self, repo, mock_db):
        # Post Option-B refactor: failure surface is db.commit.
        player = _make_player(id=1)
        mock_db.get = AsyncMock(return_value=player)
        mock_db.commit = AsyncMock(side_effect=Exception("ship fail"))
        with pytest.raises(Exception, match="ship fail"):
            await repo.update_active_ship(mock_db, player_id=1, ship_id=5)
        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_active_ship_clear(self, repo, db_session):
        """ship_id=None really clears (and commits) active_ship_id in the DB —
        not just an in-memory mutation on a mock that would happily accept a
        no-op commit."""
        player = await _seed_player(db_session, user_id=1, active_ship_id=42)

        result = await repo.update_active_ship(db_session, player_id=player.id, ship_id=None)
        assert result.active_ship_id is None

        # Re-fetch to prove the NULL was actually committed, not just held in memory.
        refetched = await repo.get_by_id(db_session, player.id)
        assert refetched.active_ship_id is None

    @pytest.mark.asyncio
    async def test_update_active_ship_player_not_found_raises_value_error(self, repo, mock_db):
        # Post Option-B: explicit ValueError when the player ID doesn't exist.
        mock_db.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Player 999 not found"):
            await repo.update_active_ship(mock_db, player_id=999, ship_id=5)
