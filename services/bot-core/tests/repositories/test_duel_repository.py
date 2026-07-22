"""Tests for DuelRepository.

Behavioural tests run against a real in-memory SQLite engine with the real
DuelRequest model (only SQLite-compatible column types — no ARRAY/JSONB — so
it round-trips without PostgreSQL). This exercises the real
guild/status/players/expiry predicates instead of hard-coding the "filtered"
rows in a mock.

The expires_at > func.now() guard (B.14 sibling) is verified by seeding a
past-expiry pending duel and asserting it is excluded, plus a statement-compile
assertion that the emitted SQL references func.now(). Error/rollback paths keep
a mock session — a real SQLite commit cannot be forced to fail deterministically.
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
from persist.models.duel_request import DuelRequest
from persist.repositories.duel_repository import DuelRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_duel(**overrides) -> DuelRequest:
    """Build a real, minimally-valid DuelRequest instance."""
    defaults = dict(
        guild_id=111222333,
        challenger_id=100000001,
        target_id=100000002,
        stakes=500,
        status="pending",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    defaults.update(overrides)
    return DuelRequest(**defaults)


# ---------------------------------------------------------------------------
# Fixtures — real SQLite engine (mirrors test_combat_log_repository.py)
# ---------------------------------------------------------------------------

_DUEL_TABLES = [DuelRequest.__table__]


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_DUEL_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def repo() -> DuelRepository:
    return DuelRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock session for error-path and statement-compile assertions only."""
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
# Tests — CRUD round-trips
# ---------------------------------------------------------------------------


class TestCreateDuelRequest:
    async def test_create_duel_request(self, repo, db_session):
        duel = _make_duel()

        result = await repo.create(db_session, duel)

        assert result.id is not None
        fetched = await repo.get_by_id(db_session, result.id)
        assert fetched is not None
        assert fetched.challenger_id == 100000001

    async def test_create_duel_request_with_zero_stakes(self, repo, db_session):
        duel = _make_duel(stakes=0)

        result = await repo.create(db_session, duel)

        fetched = await repo.get_by_id(db_session, result.id)
        assert fetched.stakes == 0


class TestGetById:
    async def test_get_by_id(self, repo, db_session):
        duel = await repo.create(db_session, _make_duel())

        result = await repo.get_by_id(db_session, duel.id)

        assert result is not None
        assert result.id == duel.id

    async def test_get_by_id_not_found(self, repo, db_session):
        result = await repo.get_by_id(db_session, 9999)

        assert result is None


class TestGetPendingByPlayers:
    async def test_get_pending_by_players_matches_exact_pair(self, repo, db_session):
        target = await repo.create(
            db_session, _make_duel(challenger_id=100, target_id=200, guild_id=111, status="pending")
        )
        # Non-matches: swapped players, other guild, non-pending.
        await repo.create(db_session, _make_duel(challenger_id=200, target_id=100, guild_id=111))
        await repo.create(db_session, _make_duel(challenger_id=100, target_id=200, guild_id=222))
        await repo.create(db_session, _make_duel(challenger_id=100, target_id=200, guild_id=111, status="accepted"))

        result = await repo.get_pending_by_players(db_session, 100, 200, 111)

        assert result is not None
        assert result.id == target.id

    async def test_get_pending_by_players_not_found(self, repo, db_session):
        result = await repo.get_pending_by_players(db_session, 100, 200, 111)

        assert result is None


class TestUpdateStatus:
    async def test_update_status(self, repo, db_session):
        duel = await repo.create(db_session, _make_duel(status="pending"))

        result = await repo.update_status(db_session, duel.id, "accepted")

        assert result.status == "accepted"
        db_session.expunge_all()
        fetched = await repo.get_by_id(db_session, duel.id)
        assert fetched.status == "accepted"

    async def test_update_status_not_found(self, repo, db_session):
        result = await repo.update_status(db_session, 9999, "accepted")

        assert result is None


class TestDeleteExpired:
    async def test_delete_expired_removes_only_expired_pending(self, repo, db_session):
        now = datetime.now(UTC)
        expired = await repo.create(db_session, _make_duel(status="pending", expires_at=now - timedelta(minutes=1)))
        # Future-expiry pending, and an expired-but-accepted — both must survive.
        future = await repo.create(db_session, _make_duel(status="pending", expires_at=now + timedelta(minutes=5)))
        accepted = await repo.create(db_session, _make_duel(status="accepted", expires_at=now - timedelta(minutes=1)))

        # SQLite drops tzinfo on read, so the ORM "evaluate" sync strategy would
        # compare naive (refreshed) values against tz-aware `now`. Expunging the
        # identity map forces the bulk DELETE to run purely in SQL (as it does in
        # production Postgres, where stored values remain tz-aware).
        db_session.expunge_all()
        count = await repo.delete_expired(db_session, now)

        assert count == 1
        db_session.expunge_all()
        assert await repo.get_by_id(db_session, expired.id) is None
        assert await repo.get_by_id(db_session, future.id) is not None
        assert await repo.get_by_id(db_session, accepted.id) is not None

    async def test_delete_expired_none(self, repo, db_session):
        now = datetime.now(UTC)
        await repo.create(db_session, _make_duel(status="pending", expires_at=now + timedelta(minutes=5)))

        count = await repo.delete_expired(db_session, now)

        assert count == 0


class TestGetActiveByGuild:
    async def test_get_active_by_guild_returns_pending_non_expired(self, repo, db_session):
        now = datetime.now(UTC)
        active = await repo.create(
            db_session, _make_duel(guild_id=111, status="pending", expires_at=now + timedelta(minutes=5))
        )
        # never-expiring pending is also "active"
        never = await repo.create(db_session, _make_duel(guild_id=111, status="pending", expires_at=None))
        # excluded: accepted, other guild, past expiry
        await repo.create(db_session, _make_duel(guild_id=111, status="accepted", expires_at=None))
        await repo.create(db_session, _make_duel(guild_id=222, status="pending", expires_at=None))
        await repo.create(db_session, _make_duel(guild_id=111, status="pending", expires_at=now - timedelta(minutes=1)))

        result = await repo.get_active_by_guild(db_session, guild_id=111)

        assert {d.id for d in result} == {active.id, never.id}

    async def test_get_active_by_guild_empty(self, repo, db_session):
        result = await repo.get_active_by_guild(db_session, guild_id=999)

        assert result == []

    async def test_get_active_by_guild_excludes_past_expiry_duels(self, repo, db_session):
        """B.14 sibling: a pending duel past its expires_at is excluded."""
        now = datetime.now(UTC)
        await repo.create(db_session, _make_duel(guild_id=111, status="pending", expires_at=now - timedelta(minutes=1)))

        result = await repo.get_active_by_guild(db_session, guild_id=111)

        assert result == []

    async def test_get_active_by_guild_includes_pending_duel_with_future_expiry(self, repo, db_session):
        now = datetime.now(UTC)
        future = await repo.create(
            db_session, _make_duel(guild_id=444, status="pending", expires_at=now + timedelta(hours=1))
        )

        result = await repo.get_active_by_guild(db_session, guild_id=444)

        assert [d.id for d in result] == [future.id]

    @pytest.mark.asyncio
    async def test_get_active_by_guild_sql_uses_now(self, repo, mock_db):
        """B.14 sibling: emitted SQL must include a func.now() time guard."""
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars)
        mock_db.execute = AsyncMock(return_value=result_mock)

        await repo.get_active_by_guild(mock_db, guild_id=111)

        stmt = mock_db.execute.call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower()


class TestGetPendingByTarget:
    async def test_get_pending_by_target_excludes_past_expiry_duels(self, repo, db_session):
        """B.14 sibling: get_pending_by_target excludes pending duels past expires_at."""
        now = datetime.now(UTC)
        await repo.create(
            db_session,
            _make_duel(target_id=100, guild_id=111, status="pending", expires_at=now - timedelta(minutes=1)),
        )

        result = await repo.get_pending_by_target(db_session, target_id=100, guild_id=111)

        assert result == []

    async def test_get_pending_by_target_returns_non_expired_pending_duels(self, repo, db_session):
        now = datetime.now(UTC)
        future = await repo.create(
            db_session,
            _make_duel(target_id=555, guild_id=111, status="pending", expires_at=now + timedelta(minutes=30)),
        )
        # Wrong target / wrong guild excluded.
        await repo.create(db_session, _make_duel(target_id=999, guild_id=111, status="pending"))
        await repo.create(db_session, _make_duel(target_id=555, guild_id=222, status="pending"))

        result = await repo.get_pending_by_target(db_session, target_id=555, guild_id=111)

        assert [d.id for d in result] == [future.id]


class TestErrorHandling:
    """Error/rollback paths — justified mock use (SQLite commit can't be forced to fail)."""

    @pytest.mark.asyncio
    async def test_create_rolls_back_on_error(self, repo, mock_db):
        duel = _make_duel()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.create(mock_db, duel)

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_status_rolls_back_on_error(self, repo, mock_db):
        duel = _make_duel()
        duel.id = 10
        mock_db.get = AsyncMock(return_value=duel)
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.update_status(mock_db, 10, "accepted")

        mock_db.rollback.assert_awaited_once()
