"""Tests for CombatLogRepository.

Uses SQLite in-memory (aiosqlite) — no PostgreSQL required.
CombatLog uses only SQLite-compatible column types (Integer, BigInteger,
String, Boolean, JSON, DateTime — no ARRAY or JSONB).
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock

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
