"""Tests for CombatLogRepository.

Uses SQLite in-memory (aiosqlite) — no PostgreSQL required.
CombatLog uses only SQLite-compatible column types (Integer, BigInteger,
String, Boolean, JSON, DateTime — no ARRAY or JSONB).
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger before any src imports
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
from persist.models.base import Base
from persist.models.combat_log import CombatLog
from persist.repositories.combat_log_repository import CombatLogRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_COMBAT_LOG_TABLES = [CombatLog.__table__]


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_COMBAT_LOG_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def repo() -> CombatLogRepository:
    return CombatLogRepository()


def _make_log(**overrides) -> CombatLog:
    """Build a minimal valid CombatLog instance."""
    defaults = dict(
        guild_id=111222333444,
        context="duel",
        combatant1_name="Specter",
        combatant2_name="Vossk Raider",
        combatant1_user_id=402296276617527306,
        combatant2_user_id=None,
        winner_name="Specter",
        is_stalemate=False,
        data={"schema_version": 1, "summary": {}, "timeline": [], "metadata": {}},
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return CombatLog(**defaults)


# ---------------------------------------------------------------------------
# Test: add / get_by_id round-trip
# ---------------------------------------------------------------------------


async def test_add_and_get_by_id(repo, db_session):
    log = _make_log()
    saved = await repo.add(db_session, log)

    assert saved.id is not None
    fetched = await repo.get_by_id(db_session, saved.id)
    assert fetched is not None
    assert fetched.context == "duel"
    assert fetched.combatant1_name == "Specter"


# ---------------------------------------------------------------------------
# Test: list_for_player — returns matching rows newest-first, respects limit
# ---------------------------------------------------------------------------


async def test_list_for_player_matches_both_sides(repo, db_session):
    uid = 999000111
    # Player is combatant1 in fight A
    a = _make_log(
        combatant1_user_id=uid,
        combatant2_user_id=None,
        combatant1_name="Hero",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    # Player is combatant2 in fight B
    b = _make_log(
        combatant1_user_id=None,
        combatant2_user_id=uid,
        combatant2_name="Hero",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # Unrelated fight
    c = _make_log(combatant1_user_id=888000777, combatant2_user_id=777000888)
    await repo.add(db_session, a)
    await repo.add(db_session, b)
    await repo.add(db_session, c)

    results = await repo.list_for_player(db_session, uid)
    ids = [r.id for r in results]
    assert b.id in ids
    assert a.id in ids
    assert c.id not in ids
    # Newest first (fight B is more recent than fight A)
    assert ids.index(b.id) < ids.index(a.id)


async def test_list_for_player_respects_limit(repo, db_session):
    uid = 500600700
    for i in range(5):
        await repo.add(
            db_session,
            _make_log(combatant1_user_id=uid, created_at=datetime.now(UTC) - timedelta(hours=i)),
        )

    results = await repo.list_for_player(db_session, uid, limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# Test: delete_older_than — returns count, leaves newer rows intact
# ---------------------------------------------------------------------------


async def test_delete_older_than(repo, db_session):
    cutoff = datetime.now(UTC)
    old = _make_log(created_at=cutoff - timedelta(hours=100))
    new = _make_log(created_at=cutoff + timedelta(hours=1))
    saved_old = await repo.add(db_session, old)
    saved_new = await repo.add(db_session, new)

    deleted = await repo.delete_older_than(db_session, cutoff)
    assert deleted == 1
    assert await repo.get_by_id(db_session, saved_old.id) is None
    assert await repo.get_by_id(db_session, saved_new.id) is not None


# ---------------------------------------------------------------------------
# Test: context filtering (issue #86) — list_for_player + delete_older_than
# ---------------------------------------------------------------------------


async def test_list_for_player_filters_by_context(repo, db_session):
    """contexts= restricts the result to the given battle types."""
    uid = 555000222
    duel = _make_log(context="duel", combatant1_user_id=uid, combatant2_user_id=123)
    pvc = _make_log(context="bounty_pvc", combatant1_user_id=uid, combatant2_user_id=None)
    bonus = _make_log(context="bounty_bonus", combatant1_user_id=uid, combatant2_user_id=None)
    for row in (duel, pvc, bonus):
        await repo.add(db_session, row)

    pvp = await repo.list_for_player(db_session, uid, contexts=("duel",))
    assert {r.context for r in pvp} == {"duel"}

    bounty = await repo.list_for_player(db_session, uid, contexts=("bounty_pvc", "bounty_bonus"))
    assert {r.context for r in bounty} == {"bounty_pvc", "bounty_bonus"}

    all_rows = await repo.list_for_player(db_session, uid)  # contexts=None → everything
    assert {r.context for r in all_rows} == {"duel", "bounty_pvc", "bounty_bonus"}


async def test_delete_older_than_filters_by_context(repo, db_session):
    """contexts= only deletes matching-context rows; others survive even if older."""
    cutoff = datetime.now(UTC)
    old_duel = _make_log(context="duel", created_at=cutoff - timedelta(hours=100))
    old_bounty = _make_log(context="bounty_pvc", combatant2_user_id=None, created_at=cutoff - timedelta(hours=100))
    saved_duel = await repo.add(db_session, old_duel)
    saved_bounty = await repo.add(db_session, old_bounty)

    deleted = await repo.delete_older_than(db_session, cutoff, contexts=("bounty_pvc", "bounty_bonus"))
    assert deleted == 1
    # Old bounty row pruned; old duel row untouched (different context).
    assert await repo.get_by_id(db_session, saved_bounty.id) is None
    assert await repo.get_by_id(db_session, saved_duel.id) is not None


# ---------------------------------------------------------------------------
# Test: NPC invariant — both NULL user_ids raises ValueError
# ---------------------------------------------------------------------------


async def test_npc_invariant_both_null_raises(repo, db_session):
    log = _make_log(combatant1_user_id=None, combatant2_user_id=None)
    with pytest.raises(ValueError, match="NPC invariant"):
        await repo.add(db_session, log)


async def test_npc_invariant_one_null_ok(repo, db_session):
    # combatant2 is NPC (NULL) — should succeed
    log = _make_log(combatant1_user_id=402296276617527306, combatant2_user_id=None)
    saved = await repo.add(db_session, log)
    assert saved.id is not None


# ---------------------------------------------------------------------------
# Test: data JSON passthrough (§12 representative shape)
# ---------------------------------------------------------------------------


async def test_data_json_roundtrip(repo, db_session):
    payload = {
        "schema_version": 1,
        "summary": {
            "outcome": "win",
            "reason": "hp_depleted",
            "duration_ticks": 8421,
            "winner": "Specter",
            "combatants": {
                "1": {
                    "name": "Specter",
                    "ship": "Specter",
                    "start_hp": {"shield": 120, "armour": 300, "hull": 200},
                    "final_hp": {"shield": 0, "armour": 0, "hull": 140},
                    "damage_dealt": 620,
                    "damage_taken": 480,
                    "shots_fired": 240,
                    "shots_hit": 168,
                    "accuracy": 0.70,
                    "module_activations": {"cloak": 2, "booster": 3},
                    "secondary_fired": {"rocket": 12},
                },
                "2": {"name": "Vossk Raider"},
            },
        },
        "timeline": [{"tick": 0, "type": "fight_start", "actor": None, "target": None, "data": {}}],
        "metadata": {"tick_ms": 10, "total_ticks": 8421, "resolver": "tick_v1", "pvc_damage_reduction": 0.33},
    }
    log = _make_log(data=payload)
    saved = await repo.add(db_session, log)
    fetched = await repo.get_by_id(db_session, saved.id)
    assert fetched.data == payload
    assert fetched.data["summary"]["combatants"]["1"]["accuracy"] == 0.70


# ---------------------------------------------------------------------------
# Test: get_by_name raises NotImplementedError
# ---------------------------------------------------------------------------


async def test_get_by_name_raises(repo, db_session):
    with pytest.raises(NotImplementedError):
        await repo.get_by_name(db_session, "anything")


# ---------------------------------------------------------------------------
# Test: create_or_update raises NotImplementedError
# ---------------------------------------------------------------------------


async def test_create_or_update_raises(repo, db_session):
    with pytest.raises(NotImplementedError):
        await repo.create_or_update(db_session, {})


# ---------------------------------------------------------------------------
# Test: list_all and remove
# ---------------------------------------------------------------------------


async def test_list_all_and_remove(repo, db_session):
    a = await repo.add(db_session, _make_log(combatant1_user_id=1))
    b = await repo.add(db_session, _make_log(combatant1_user_id=2))

    all_rows = await repo.list_all(db_session)
    assert len(all_rows) >= 2

    await repo.remove(db_session, a)
    assert await repo.get_by_id(db_session, a.id) is None
    assert await repo.get_by_id(db_session, b.id) is not None


# ---------------------------------------------------------------------------
# Test: list_for_player — guild scoping (new guild_id param)
# ---------------------------------------------------------------------------


async def test_list_for_player_guild_scoped(repo, db_session):
    """list_for_player with guild_id only returns rows for that guild."""
    uid = 111222333444555
    guild_a = 111111111111111
    guild_b = 222222222222222

    fight_a = _make_log(combatant1_user_id=uid, guild_id=guild_a)
    fight_b = _make_log(combatant1_user_id=uid, guild_id=guild_b)
    await repo.add(db_session, fight_a)
    await repo.add(db_session, fight_b)

    results_a = await repo.list_for_player(db_session, uid, guild_id=guild_a)
    assert all(r.guild_id == guild_a for r in results_a)
    assert len(results_a) >= 1

    results_b = await repo.list_for_player(db_session, uid, guild_id=guild_b)
    assert all(r.guild_id == guild_b for r in results_b)
    assert len(results_b) >= 1


async def test_list_for_player_no_guild_returns_all_guilds(repo, db_session):
    """list_for_player without guild_id returns rows across all guilds."""
    uid = 999888777666555
    guild_a = 333333333333333
    guild_b = 444444444444444

    a = _make_log(combatant1_user_id=uid, guild_id=guild_a)
    b = _make_log(combatant1_user_id=uid, guild_id=guild_b)
    await repo.add(db_session, a)
    await repo.add(db_session, b)

    results = await repo.list_for_player(db_session, uid)
    guild_ids = {r.guild_id for r in results}
    assert guild_a in guild_ids
    assert guild_b in guild_ids


async def test_list_for_player_npc_fight_included(repo, db_session):
    """NPC fights (NULL combatant2) appear in list when player is combatant1."""
    uid = 101010101010
    npc_fight = _make_log(
        combatant1_user_id=uid,
        combatant2_user_id=None,
        combatant1_name="Hero",
        combatant2_name="Vossk Soldier",
        guild_id=111222333444,
    )
    await repo.add(db_session, npc_fight)

    results = await repo.list_for_player(db_session, uid, guild_id=111222333444)
    assert any(r.combatant2_user_id is None for r in results)


# ---------------------------------------------------------------------------
# Test: delete_by_guild_id — uninstall hard-delete
# ---------------------------------------------------------------------------


async def test_delete_by_guild_id_deletes_only_target_guild(repo, db_session):
    """delete_by_guild_id removes all rows for the target guild only."""
    guild_a = 111000111000111
    guild_b = 222000222000222

    # Insert 2 rows for guild_a, 1 row for guild_b
    a1 = await repo.add(db_session, _make_log(guild_id=guild_a, combatant1_user_id=1001))
    a2 = await repo.add(db_session, _make_log(guild_id=guild_a, combatant1_user_id=1002))
    b1 = await repo.add(db_session, _make_log(guild_id=guild_b, combatant1_user_id=2001))

    deleted = await repo.delete_by_guild_id(db_session, guild_a)

    assert deleted == 2
    assert await repo.get_by_id(db_session, a1.id) is None
    assert await repo.get_by_id(db_session, a2.id) is None
    # Guild B row must be untouched
    assert await repo.get_by_id(db_session, b1.id) is not None


async def test_delete_by_guild_id_returns_zero_when_no_rows(repo, db_session):
    """delete_by_guild_id returns 0 when the guild has no combat_log rows."""
    deleted = await repo.delete_by_guild_id(db_session, guild_id=999888777666555)

    assert deleted == 0


async def test_delete_by_guild_id_returns_count(repo, db_session):
    """delete_by_guild_id returns the exact count of deleted rows."""
    guild_id = 333444555666777

    for i in range(4):
        await repo.add(db_session, _make_log(guild_id=guild_id, combatant1_user_id=i + 1))

    deleted = await repo.delete_by_guild_id(db_session, guild_id)

    assert deleted == 4
    # Confirm table is empty for that guild
    all_rows = await repo.list_for_player(db_session, 1, guild_id=guild_id)
    assert all_rows == []


# ---------------------------------------------------------------------------
# Test: delete_by_guild_id — error handling (mock-db, mirrors BountyRepository)
# ---------------------------------------------------------------------------


class TestDeleteByGuildIdErrorHandling:
    """Error-handling tests for CombatLogRepository.delete_by_guild_id.

    These tests use a mock AsyncSession to exercise the rollback / no-commit
    branches without needing a live database.  They mirror the equivalents in
    test_bounty_repository.py :: TestDeleteByGuildId.
    """

    @pytest.fixture
    def repo(self) -> CombatLogRepository:
        return CombatLogRepository()

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_delete_by_guild_id_rollback_on_error(self, repo, mock_db):
        """On database error, rollback is called and the exception is re-raised."""
        mock_db.execute = AsyncMock(side_effect=Exception("DB gone"))

        with pytest.raises(Exception, match="DB gone"):
            await repo.delete_by_guild_id(mock_db, guild_id=555666777888)

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_guild_id_no_commit_when_commit_false(self, repo, mock_db):
        """When commit=False, flush is called but commit is not."""
        mock_db.flush = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 2
        mock_db.execute = AsyncMock(return_value=result_mock)

        await repo.delete_by_guild_id(mock_db, guild_id=777888999000, commit=False)

        mock_db.flush.assert_awaited_once()
        mock_db.commit.assert_not_awaited()
