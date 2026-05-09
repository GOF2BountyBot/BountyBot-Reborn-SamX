"""Unit tests for BountyRepository.

Mock-based tests (no real database needed).
Covers all CRUD methods and domain-specific queries.
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
from persist.models.bounty import Bounty
from persist.repositories.bounty_repository import BountyRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(**overrides) -> MagicMock:
    """Return a MagicMock with Bounty-like attributes."""
    defaults = dict(
        id=1,
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
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=Bounty)
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_scalars_result(items) -> MagicMock:
    """Mimic async execute() returning a result whose scalars().all() gives items."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_scalar_one_result(value) -> MagicMock:
    """Mimic async execute() returning a result whose scalar_one() gives value."""
    result_mock = MagicMock()
    result_mock.scalar_one = MagicMock(return_value=value)
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> BountyRepository:
    return BountyRepository()


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
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateBounty:
    @pytest.mark.asyncio
    async def test_create_bounty(self, repo, mock_db):
        """create() should add the bounty, commit, and refresh it."""
        bounty = _make_bounty()
        mock_db.refresh = AsyncMock(side_effect=lambda b: None)

        result = await repo.create(mock_db, bounty)

        mock_db.add.assert_called_once_with(bounty)
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(bounty)
        assert result is bounty

    @pytest.mark.asyncio
    async def test_create_bounty_with_json_fields(self, repo, mock_db):
        """create() persists bounty with JSON route and checked fields."""
        route = ["Sol", "Sirius", "Alpha Centauri"]
        checked = {"Sol": -1, "Sirius": -1, "Alpha Centauri": -1}
        bounty = _make_bounty(route=route, checked=checked)
        mock_db.refresh = AsyncMock(side_effect=lambda b: None)

        result = await repo.create(mock_db, bounty)

        mock_db.add.assert_called_once_with(bounty)
        assert result.route == route
        assert result.checked == checked


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id(self, repo, mock_db):
        """get_by_id() should return the bounty fetched by db.get()."""
        bounty = _make_bounty(id=42)
        mock_db.get = AsyncMock(return_value=bounty)

        result = await repo.get_by_id(mock_db, 42)

        mock_db.get.assert_awaited_once_with(Bounty, 42)
        assert result is bounty

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, repo, mock_db):
        """get_by_id() should return None when the bounty does not exist."""
        mock_db.get = AsyncMock(return_value=None)

        result = await repo.get_by_id(mock_db, 9999)

        assert result is None


class TestGetActiveByGuild:
    @pytest.mark.asyncio
    async def test_get_active_by_guild(self, repo, mock_db):
        """get_active_by_guild() should return only active bounties for the guild."""
        active_bounty = _make_bounty(status="active", guild_id=111)
        _make_bounty(status="expired", guild_id=111)  # expired; not returned by mock

        # The mock returns only the active one (simulating DB filter)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([active_bounty]))

        result = await repo.get_active_by_guild(mock_db, guild_id=111)

        mock_db.execute.assert_awaited_once()
        assert len(result) == 1
        assert result[0].status == "active"

    @pytest.mark.asyncio
    async def test_get_active_by_guild_returns_empty_when_none(self, repo, mock_db):
        """get_active_by_guild() returns empty list when no active bounties exist."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_active_by_guild(mock_db, guild_id=999)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_active_by_guild_excludes_stale_active_bounties(self, repo, mock_db):
        """B.14: get_active_by_guild() must exclude bounties with status='active' AND end_time < NOW().

        The mock simulates the DB returning no rows (i.e. the time filter excluded stale rows).
        We also verify the SQL statement emitted includes a func.now() call so regressions
        would be caught at the statement level.
        """
        # Mock returns empty — simulating that DB filtered out the stale bounty
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_active_by_guild(mock_db, guild_id=111)

        assert result == []
        # Inspect the compiled SQL statement — it must reference 'now' (func.now() → NOW())
        call_args = mock_db.execute.call_args
        stmt = call_args[0][0]  # positional arg 0: the SQLAlchemy select statement
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower(), (
            f"B.14: get_active_by_guild() WHERE clause must include func.now() time filter. Got SQL: {stmt_str}"
        )

    @pytest.mark.asyncio
    async def test_get_active_by_guild_includes_bounty_with_future_end_time(self, repo, mock_db):
        """B.14: get_active_by_guild() must include bounties with status='active' AND end_time > NOW()."""
        future_bounty = _make_bounty(
            status="active",
            guild_id=222,
            end_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([future_bounty]))

        result = await repo.get_active_by_guild(mock_db, guild_id=222)

        assert len(result) == 1
        assert result[0].status == "active"


class TestGetActiveByGuildAndDivision:
    @pytest.mark.asyncio
    async def test_get_active_by_guild_and_division(self, repo, mock_db):
        """get_active_by_guild_and_division() filters by guild, division, and status."""
        bronze_bounty = _make_bounty(status="active", guild_id=111, division="bronze")
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([bronze_bounty]))

        result = await repo.get_active_by_guild_and_division(mock_db, guild_id=111, division="bronze")

        mock_db.execute.assert_awaited_once()
        assert len(result) == 1
        assert result[0].division == "bronze"

    @pytest.mark.asyncio
    async def test_get_active_by_guild_and_division_empty(self, repo, mock_db):
        """Returns empty list when no bounties match the guild+division+active criteria."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_active_by_guild_and_division(mock_db, guild_id=111, division="gold")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_active_by_guild_and_division_excludes_stale_active_bounties(self, repo, mock_db):
        """B.14: get_active_by_guild_and_division() must exclude status='active' bounties past end_time.

        Verifies the emitted SQL contains a func.now() time filter clause.
        """
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_active_by_guild_and_division(mock_db, guild_id=111, division="silver")

        assert result == []
        call_args = mock_db.execute.call_args
        stmt = call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower(), (
            f"B.14: get_active_by_guild_and_division() WHERE clause must include func.now() time filter. "
            f"Got SQL: {stmt_str}"
        )

    @pytest.mark.asyncio
    async def test_get_active_by_guild_and_division_includes_future_end_time(self, repo, mock_db):
        """B.14: bounties with status='active' and end_time in the future are included."""
        future_bounty = _make_bounty(
            status="active",
            guild_id=333,
            division="gold",
            end_time=datetime(2099, 6, 1, tzinfo=UTC),
        )
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([future_bounty]))

        result = await repo.get_active_by_guild_and_division(mock_db, guild_id=333, division="gold")

        assert len(result) == 1
        assert result[0].division == "gold"


class TestUpdateBounty:
    @pytest.mark.asyncio
    async def test_update_bounty(self, repo, mock_db):
        """update() should commit and refresh the bounty."""
        bounty = _make_bounty(status="escaped")
        mock_db.refresh = AsyncMock(side_effect=lambda b: None)

        result = await repo.update(mock_db, bounty)

        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(bounty)
        assert result is bounty


class TestDeleteBounty:
    @pytest.mark.asyncio
    async def test_delete_bounty(self, repo, mock_db):
        """delete() should remove the bounty and commit."""
        bounty = _make_bounty(id=7)

        await repo.delete(mock_db, bounty)

        mock_db.delete.assert_called_once_with(bounty)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_bounty_verify_not_retrievable(self, repo, mock_db):
        """After delete, get_by_id returns None (simulated)."""
        bounty = _make_bounty(id=7)

        await repo.delete(mock_db, bounty)

        # Now simulate that the bounty is gone
        mock_db.get = AsyncMock(return_value=None)
        result = await repo.get_by_id(mock_db, 7)
        assert result is None


class TestCount:
    @pytest.mark.asyncio
    async def test_count(self, repo, mock_db):
        """count() should return total number of bounties."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(5))

        result = await repo.count(mock_db)

        mock_db.execute.assert_awaited_once()
        assert result == 5

    @pytest.mark.asyncio
    async def test_count_zero(self, repo, mock_db):
        """count() returns 0 when no bounties exist."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(0))

        result = await repo.count(mock_db)

        assert result == 0


class TestCountActiveByGuildAndDivision:
    @pytest.mark.asyncio
    async def test_count_active_by_guild_and_division(self, repo, mock_db):
        """count_active_by_guild_and_division() filters correctly."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(3))

        result = await repo.count_active_by_guild_and_division(mock_db, guild_id=111, division="silver")

        mock_db.execute.assert_awaited_once()
        assert result == 3

    @pytest.mark.asyncio
    async def test_count_active_by_guild_and_division_zero(self, repo, mock_db):
        """Returns 0 when no matching active bounties exist."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(0))

        result = await repo.count_active_by_guild_and_division(mock_db, guild_id=111, division="platinum")

        assert result == 0

    @pytest.mark.asyncio
    async def test_count_active_by_guild_and_division_uses_time_filter(self, repo, mock_db):
        """B.14: count_active_by_guild_and_division() must include func.now() time filter.

        This ensures stale expired bounties don't count against the spawn slot limit,
        preventing the spawn executor from being permanently blocked.
        """
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(0))

        await repo.count_active_by_guild_and_division(mock_db, guild_id=111, division="bronze")

        call_args = mock_db.execute.call_args
        stmt = call_args[0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "now" in stmt_str.lower(), (
            f"B.14: count_active_by_guild_and_division() WHERE clause must include func.now() time filter. "
            f"Got SQL: {stmt_str}"
        )


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_create_rolls_back_on_error(self, repo, mock_db):
        """create() should rollback on commit failure."""
        bounty = _make_bounty()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.create(mock_db, bounty)

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_rolls_back_on_error(self, repo, mock_db):
        """delete() should rollback on commit failure."""
        bounty = _make_bounty()
        mock_db.commit = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await repo.delete(mock_db, bounty)

        mock_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# clear_active_by_guild
# ---------------------------------------------------------------------------


class TestClearActiveByGuild:
    """Tests for BountyRepository.clear_active_by_guild."""

    @pytest.fixture
    def repo(self):
        return BountyRepository()

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_clear_all_tiers_returns_ids(self, repo, mock_db):
        """Without tier filter, clears all active bounties and returns their IDs."""
        # First execute (SELECT id) returns 2 IDs
        id_scalars = MagicMock()
        id_scalars.all = MagicMock(return_value=[1, 2])
        id_result = MagicMock()
        id_result.scalars = MagicMock(return_value=id_scalars)

        # Second execute (UPDATE) returns nothing significant
        update_result = MagicMock()

        mock_db.execute = AsyncMock(side_effect=[id_result, update_result])

        result = await repo.clear_active_by_guild(mock_db, guild_id=1000)

        assert result == [1, 2]
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_with_tier_filter_returns_ids(self, repo, mock_db):
        """With tier filter, only bounties of that tier are cleared."""
        id_scalars = MagicMock()
        id_scalars.all = MagicMock(return_value=[5, 6])
        id_result = MagicMock()
        id_result.scalars = MagicMock(return_value=id_scalars)
        update_result = MagicMock()
        mock_db.execute = AsyncMock(side_effect=[id_result, update_result])

        result = await repo.clear_active_by_guild(mock_db, guild_id=1000, tier="bronze")

        assert result == [5, 6]
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_no_active_bounties_returns_empty(self, repo, mock_db):
        """When there are no active bounties, returns an empty list without running UPDATE."""
        id_scalars = MagicMock()
        id_scalars.all = MagicMock(return_value=[])
        id_result = MagicMock()
        id_result.scalars = MagicMock(return_value=id_scalars)
        mock_db.execute = AsyncMock(return_value=id_result)

        result = await repo.clear_active_by_guild(mock_db, guild_id=999)

        assert result == []
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clear_rolls_back_on_error(self, repo, mock_db):
        """On database error, rollback is called and exception re-raised."""
        mock_db.execute = AsyncMock(side_effect=Exception("DB failure"))

        with pytest.raises(Exception, match="DB failure"):
            await repo.clear_active_by_guild(mock_db, guild_id=777)

        mock_db.rollback.assert_awaited_once()
