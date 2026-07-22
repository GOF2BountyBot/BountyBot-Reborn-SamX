"""Tests for BountyRepository.

Behavioural tests run against a real in-memory SQLite engine with the real
Bounty model (Bounty uses only SQLite-compatible column types — Integer,
BigInteger, String, DateTime, and JSON-with-variant — so it round-trips
without PostgreSQL). This exercises the real WHERE/status/division/time
predicates instead of hard-coding the "filtered" rows in a mock.

The func.now() time filter (B.14) is verified two ways: a real round-trip that
seeds a stale active bounty and asserts it is excluded, AND statement-compile
assertions that the emitted SQL references func.now() (kept from the original
suite). Error/rollback paths keep a mock session — a real SQLite commit cannot
be forced to fail deterministically.
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
from persist.models.bounty import Bounty
from persist.repositories.bounty_repository import BountyRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(**overrides) -> Bounty:
    """Build a real, minimally-valid Bounty instance."""
    defaults = dict(
        guild_id=111222333,
        division="bronze",
        criminal_name="Pirate Pete",
        criminal_faction="Void Raiders",
        route=["Alpha", "Beta", "Gamma"],
        answer="Gamma",
        reward=50000,
        reward_per_sys=5000,
        checked={"Alpha": -1, "Beta": -1, "Gamma": -1},
        issue_time=datetime.now(UTC),
        end_time=None,
        tech_level=3,
        criminal_ship=None,
        status="active",
        escape_count=0,
        win_user_id=None,
        respawn_time=None,
    )
    defaults.update(overrides)
    return Bounty(**defaults)


def _make_scalar_one_result(value) -> MagicMock:
    """Mimic async execute() returning a result whose scalar_one() gives value."""
    result_mock = MagicMock()
    result_mock.scalar_one = MagicMock(return_value=value)
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures — real SQLite engine (mirrors test_combat_log_repository.py)
# ---------------------------------------------------------------------------

_BOUNTY_TABLES = [Bounty.__table__]


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_BOUNTY_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def repo() -> BountyRepository:
    return BountyRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock session for error-path and statement-compile assertions only."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests — CRUD round-trips
# ---------------------------------------------------------------------------


class TestCreateBounty:
    async def test_create_bounty(self, repo, db_session):
        """create() persists the bounty and it is retrievable by id."""
        bounty = _make_bounty()

        result = await repo.create(db_session, bounty)

        assert result.id is not None
        fetched = await repo.get_by_id(db_session, result.id)
        assert fetched is not None
        assert fetched.criminal_name == "Pirate Pete"

    async def test_create_bounty_with_json_fields(self, repo, db_session):
        """create() round-trips JSON route and checked fields."""
        route = ["Sol", "Sirius", "Alpha Centauri"]
        checked = {"Sol": -1, "Sirius": -1, "Alpha Centauri": -1}
        bounty = _make_bounty(route=route, checked=checked)

        result = await repo.create(db_session, bounty)

        fetched = await repo.get_by_id(db_session, result.id)
        assert fetched.route == route
        assert fetched.checked == checked


class TestGetById:
    async def test_get_by_id(self, repo, db_session):
        bounty = await repo.create(db_session, _make_bounty())

        result = await repo.get_by_id(db_session, bounty.id)

        assert result is not None
        assert result.id == bounty.id

    async def test_get_by_id_not_found(self, repo, db_session):
        result = await repo.get_by_id(db_session, 9999)

        assert result is None


class TestGetActiveByGuild:
    async def test_get_active_by_guild_returns_only_active_for_guild(self, repo, db_session):
        """Only status='active', non-stale bounties of the target guild are returned."""
        now = datetime.now(UTC)
        active = await repo.create(
            db_session, _make_bounty(guild_id=111, status="active", end_time=now + timedelta(hours=1))
        )
        await repo.create(db_session, _make_bounty(guild_id=111, status="expired", end_time=now + timedelta(hours=1)))
        await repo.create(db_session, _make_bounty(guild_id=222, status="active", end_time=now + timedelta(hours=1)))

        result = await repo.get_active_by_guild(db_session, guild_id=111)

        assert [b.id for b in result] == [active.id]

    async def test_get_active_by_guild_returns_empty_when_none(self, repo, db_session):
        result = await repo.get_active_by_guild(db_session, guild_id=999)

        assert result == []

    async def test_get_active_by_guild_excludes_stale_active_bounties(self, repo, db_session):
        """B.14: a status='active' bounty whose end_time is in the PAST is excluded."""
        now = datetime.now(UTC)
        await repo.create(db_session, _make_bounty(guild_id=111, status="active", end_time=now - timedelta(hours=1)))

        result = await repo.get_active_by_guild(db_session, guild_id=111)

        assert result == []

    async def test_get_active_by_guild_includes_bounty_with_future_end_time(self, repo, db_session):
        now = datetime.now(UTC)
        future = await repo.create(
            db_session, _make_bounty(guild_id=222, status="active", end_time=now + timedelta(hours=5))
        )

        result = await repo.get_active_by_guild(db_session, guild_id=222)

        assert [b.id for b in result] == [future.id]

    @pytest.mark.asyncio
    async def test_get_active_by_guild_sql_uses_now(self, repo, mock_db):
        """B.14: the emitted SQL must include a func.now() time filter."""
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars)
        mock_db.execute = AsyncMock(return_value=result_mock)

        await repo.get_active_by_guild(mock_db, guild_id=111)

        stmt = mock_db.execute.call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower()


class TestGetActiveByGuildAndDivision:
    async def test_filters_by_guild_division_and_status(self, repo, db_session):
        now = datetime.now(UTC)
        bronze = await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="bronze", status="active", end_time=now + timedelta(hours=1)),
        )
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="gold", status="active", end_time=now + timedelta(hours=1)),
        )
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="bronze", status="expired", end_time=now + timedelta(hours=1)),
        )

        result = await repo.get_active_by_guild_and_division(db_session, guild_id=111, division="bronze")

        assert [b.id for b in result] == [bronze.id]

    async def test_returns_empty_when_no_match(self, repo, db_session):
        result = await repo.get_active_by_guild_and_division(db_session, guild_id=111, division="gold")

        assert result == []

    async def test_excludes_stale_active_bounties(self, repo, db_session):
        """B.14: division query also excludes active bounties past end_time."""
        now = datetime.now(UTC)
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="silver", status="active", end_time=now - timedelta(hours=1)),
        )

        result = await repo.get_active_by_guild_and_division(db_session, guild_id=111, division="silver")

        assert result == []

    async def test_includes_future_end_time(self, repo, db_session):
        now = datetime.now(UTC)
        future = await repo.create(
            db_session,
            _make_bounty(guild_id=333, division="gold", status="active", end_time=now + timedelta(hours=6)),
        )

        result = await repo.get_active_by_guild_and_division(db_session, guild_id=333, division="gold")

        assert [b.id for b in result] == [future.id]

    @pytest.mark.asyncio
    async def test_sql_uses_now(self, repo, mock_db):
        """B.14: emitted SQL includes func.now() time filter."""
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars)
        mock_db.execute = AsyncMock(return_value=result_mock)

        await repo.get_active_by_guild_and_division(mock_db, guild_id=111, division="silver")

        stmt = mock_db.execute.call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower()


class TestUpdateBounty:
    async def test_update_bounty(self, repo, db_session):
        """update() persists a mutated field."""
        bounty = await repo.create(db_session, _make_bounty(status="active"))

        bounty.status = "escaped"
        result = await repo.update(db_session, bounty)

        assert result.status == "escaped"
        # Re-fetch from a clean identity map to confirm persistence.
        db_session.expunge_all()
        fetched = await repo.get_by_id(db_session, bounty.id)
        assert fetched.status == "escaped"


class TestDeleteBounty:
    async def test_delete_bounty(self, repo, db_session):
        bounty = await repo.create(db_session, _make_bounty())

        await repo.delete(db_session, bounty)

        assert await repo.get_by_id(db_session, bounty.id) is None


class TestCount:
    async def test_count(self, repo, db_session):
        for _ in range(5):
            await repo.create(db_session, _make_bounty())

        assert await repo.count(db_session) == 5

    async def test_count_zero(self, repo, db_session):
        assert await repo.count(db_session) == 0


class TestCountActiveByGuildAndDivision:
    async def test_counts_only_matching_active(self, repo, db_session):
        now = datetime.now(UTC)
        # 3 matching active
        for _ in range(3):
            await repo.create(
                db_session,
                _make_bounty(guild_id=111, division="silver", status="active", end_time=now + timedelta(hours=1)),
            )
        # non-matching: wrong division, wrong status, stale
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="gold", status="active", end_time=now + timedelta(hours=1)),
        )
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="silver", status="expired", end_time=now + timedelta(hours=1)),
        )
        await repo.create(
            db_session,
            _make_bounty(guild_id=111, division="silver", status="active", end_time=now - timedelta(hours=1)),
        )

        result = await repo.count_active_by_guild_and_division(db_session, guild_id=111, division="silver")

        assert result == 3

    async def test_count_zero_when_none(self, repo, db_session):
        result = await repo.count_active_by_guild_and_division(db_session, guild_id=111, division="platinum")

        assert result == 0

    @pytest.mark.asyncio
    async def test_uses_time_filter(self, repo, mock_db):
        """B.14: count query must include func.now() so stale rows don't block spawn slots."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(0))

        await repo.count_active_by_guild_and_division(mock_db, guild_id=111, division="bronze")

        stmt = mock_db.execute.call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower()


class TestErrorHandling:
    """Error/rollback paths — justified mock use (SQLite commit can't be forced to fail)."""

    @pytest.mark.asyncio
    async def test_create_rolls_back_on_error(self, repo, mock_db):
        bounty = _make_bounty()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.create(mock_db, bounty)

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_rolls_back_on_error(self, repo, mock_db):
        bounty = _make_bounty()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.delete(mock_db, bounty)

        mock_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# clear_active_by_guild — real round-trips
# ---------------------------------------------------------------------------


class TestClearActiveByGuild:
    async def test_clear_all_tiers_sets_status_cleared(self, repo, db_session):
        """Without a tier filter, all active bounties for the guild become 'cleared'."""
        b1 = await repo.create(db_session, _make_bounty(guild_id=1000, division="bronze", status="active"))
        b2 = await repo.create(db_session, _make_bounty(guild_id=1000, division="gold", status="active"))
        # Another guild — untouched.
        other = await repo.create(db_session, _make_bounty(guild_id=2000, status="active"))

        result = await repo.clear_active_by_guild(db_session, guild_id=1000)

        assert sorted(result) == sorted([b1.id, b2.id])
        db_session.expunge_all()
        assert (await repo.get_by_id(db_session, b1.id)).status == "cleared"
        assert (await repo.get_by_id(db_session, b2.id)).status == "cleared"
        assert (await repo.get_by_id(db_session, other.id)).status == "active"

    async def test_clear_with_tier_filter_only_that_tier(self, repo, db_session):
        bronze = await repo.create(db_session, _make_bounty(guild_id=1000, division="bronze", status="active"))
        gold = await repo.create(db_session, _make_bounty(guild_id=1000, division="gold", status="active"))

        result = await repo.clear_active_by_guild(db_session, guild_id=1000, tier="bronze")

        assert result == [bronze.id]
        db_session.expunge_all()
        assert (await repo.get_by_id(db_session, bronze.id)).status == "cleared"
        assert (await repo.get_by_id(db_session, gold.id)).status == "active"

    async def test_clear_no_active_bounties_returns_empty(self, repo, db_session):
        await repo.create(db_session, _make_bounty(guild_id=999, status="expired"))

        result = await repo.clear_active_by_guild(db_session, guild_id=999)

        assert result == []

    @pytest.mark.asyncio
    async def test_clear_rolls_back_on_error(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("DB failure"))

        with pytest.raises(Exception, match="DB failure"):
            await repo.clear_active_by_guild(mock_db, guild_id=777)

        mock_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_by_guild_id — real round-trips
# ---------------------------------------------------------------------------


class TestDeleteByGuildId:
    async def test_deletes_only_target_guild_and_returns_count(self, repo, db_session):
        keep = await repo.create(db_session, _make_bounty(guild_id=222))
        for _ in range(3):
            await repo.create(db_session, _make_bounty(guild_id=111))

        count = await repo.delete_by_guild_id(db_session, guild_id=111)

        assert count == 3
        db_session.expunge_all()
        assert await repo.get_by_id(db_session, keep.id) is not None
        remaining = [b for b in await repo.list_all(db_session) if b.guild_id == 111]
        assert remaining == []

    async def test_returns_zero_when_no_rows(self, repo, db_session):
        count = await repo.delete_by_guild_id(db_session, guild_id=999000)

        assert count == 0

    @pytest.mark.asyncio
    async def test_sql_filters_by_guild_id(self, repo, mock_db):
        result = MagicMock()
        result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=result)

        await repo.delete_by_guild_id(mock_db, guild_id=424242)

        stmt = mock_db.execute.call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "guild_id" in stmt_str.lower()

    @pytest.mark.asyncio
    async def test_rollback_on_error(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("DB gone"))

        with pytest.raises(Exception, match="DB gone"):
            await repo.delete_by_guild_id(mock_db, guild_id=555666)

        mock_db.rollback.assert_awaited_once()

    async def test_no_commit_when_commit_false(self, repo, db_session):
        """commit=False flushes without committing — row is gone within the session."""
        b = await repo.create(db_session, _make_bounty(guild_id=777888))

        count = await repo.delete_by_guild_id(db_session, guild_id=777888, commit=False)

        assert count == 1
        assert await repo.get_by_id(db_session, b.id) is None
