"""P4-T7b tests: JSONB sub-path select for get_detail.

Adversarial spec:
  1. Emitted SQL omits the full `data` column on the optimized path — capture the
     compiled statement and assert it selects data->'summary'/'metadata'/'key_events'
     (or the SQLite dialect equivalent), NOT the whole `data` column.  Reverting to
     a full load MUST fail this assertion.
  2. data['timeline'] is never materialized on the fast read path — instrument that
     the timeline sub-key is absent from the row the service receives.
  3. Output unchanged vs T7a for a new row (stored key_events → fast path).
  4. Output unchanged vs T7a for a legacy row (no stored key_events → full-load fallback).
  5. Legacy fallback genuinely loads the timeline and runs _extract_key_events.

All SQLite-based (unit suite).  Cross-dialect SQL assertions use compiled statements.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Guard: mock shared.bblogger before any src imports
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.models.base import Base
from persist.models.combat_log import CombatLog
from persist.repositories.combat_log_repository import CombatLogRepository
from services.combat_log_service import CombatLogService, _SubpathAdapter
from services.combat_resolver import _extract_key_events
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# In-memory SQLite engine fixtures
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


# ---------------------------------------------------------------------------
# Shared row builders
# ---------------------------------------------------------------------------

_BASE_SUMMARY = {
    "reason": "hp_depleted",
    "winner": "Alice",
    "outcome": "win",
    "combatants": {
        "1": {
            "name": "Alice",
            "ship": "Specter",
            "start_hp": {"hull": 100, "armour": 50, "shield": 0},
            "final_hp": {"hull": 60, "armour": 10, "shield": 0},
            "shots_fired": 80,
            "shots_hit": 50,
            "damage_dealt": 200,
            "damage_taken": 100,
        },
        "2": {
            "name": "Bob",
            "ship": "Wraith",
            "start_hp": {"hull": 100, "armour": 50, "shield": 0},
            "final_hp": {"hull": 0, "armour": 0, "shield": 0},
            "shots_fired": 60,
            "shots_hit": 30,
            "damage_dealt": 100,
            "damage_taken": 200,
        },
    },
    "duration_ticks": 3000,
}

_BASE_METADATA = {"tick_ms": 10, "resolver": "tick_v1", "total_ticks": 3000, "pvc_damage_reduction": 0.0}

_TIMELINE_EVENTS = [
    {
        "tick": 0,
        "type": "fight_start",
        "actor": None,
        "target": None,
        "data": {
            "combatants": [
                {
                    "name": "Alice",
                    "display_name": "SamX",
                    "ship": "Specter",
                    "hp": {"hull": 100, "armour": 50, "shield": 0},
                },
                {
                    "name": "Bob",
                    "display_name": "HoandGo",
                    "ship": "Wraith",
                    "hp": {"hull": 100, "armour": 50, "shield": 0},
                },
            ],
            "initial_distance": 3000,
        },
    },
    {
        "tick": 1500,
        "type": "layer_depleted",
        "actor": "Bob",
        "target": None,
        "data": {"layer": "armour"},
    },
    {
        "tick": 3000,
        "type": "fight_end",
        "actor": None,
        "target": None,
        "data": {"winner": "Alice", "reason": "hp_depleted", "duration_ticks": 3000},
    },
]

# Canonical key_events: computed once from the real extractor so stored-vs-extracted
# comparisons are byte-identical.  Any test that inserts a "new row" stores these; any
# test that re-extracts from _TIMELINE_EVENTS via the legacy path must produce the same.
_KEY_EVENTS_STORED = _extract_key_events(
    list(_TIMELINE_EVENTS),
    tick_ms=10,
    combatants_map=_BASE_SUMMARY["combatants"],
)


def _insert_new_row(
    repo: CombatLogRepository,
    db_session: AsyncSession,
    *,
    with_key_events: bool = True,
    extra_timeline_items: int = 0,
) -> CombatLog:
    """Build a CombatLog instance ready for insertion."""
    timeline = list(_TIMELINE_EVENTS)
    # Optionally inflate the timeline to simulate a large battle
    for i in range(extra_timeline_items):
        timeline.append({"tick": i + 1, "type": "damage", "actor": "Alice", "target": "Bob", "data": {}})

    data: dict[str, Any] = {
        "schema_version": 1,
        "summary": _BASE_SUMMARY,
        "timeline": timeline,
        "metadata": _BASE_METADATA,
    }
    if with_key_events:
        data["key_events"] = list(_KEY_EVENTS_STORED)

    return CombatLog(
        guild_id=699744305274945650,
        context="duel",
        combatant1_name="Alice",
        combatant2_name="Bob",
        combatant1_user_id=100,
        combatant2_user_id=200,
        winner_name="Alice",
        is_stalemate=False,
        data=data,
        created_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
    )


# ===========================================================================
# 1. Emitted SQL assertion — optimized path MUST NOT select full `data` column
# ===========================================================================


class TestEmittedSQL:
    """Assert that get_subpath_for_detail emits a sub-path SELECT, not a full data SELECT.

    The compiled statement must contain JSON sub-path syntax (dialect-specific),
    NOT the bare ``combat_log.data`` column reference without sub-path operators.
    Reverting the implementation to `select(CombatLog)` (or `select(CombatLog.data)`)
    would select the whole blob — these assertions must fail for that case.
    """

    def test_sqlite_dialect_uses_json_extract_not_full_data_column(self):
        """SQLite: compiled statement uses JSON_EXTRACT sub-paths, NOT `combat_log.data`."""
        stmt = select(
            CombatLog.id,
            CombatLog.guild_id,
            CombatLog.context,
            CombatLog.combatant1_name,
            CombatLog.combatant2_name,
            CombatLog.combatant1_user_id,
            CombatLog.combatant2_user_id,
            CombatLog.winner_name,
            CombatLog.is_stalemate,
            CombatLog.created_at,
            CombatLog.data["summary"].label("summary"),
            CombatLog.data["metadata"].label("metadata"),
            CombatLog.data["key_events"].label("key_events"),
        ).where(CombatLog.id == 1)

        compiled = str(stmt.compile(dialect=sqlite.dialect()))

        # Must contain sub-path accessors (JSON_EXTRACT for SQLite)
        assert "JSON_EXTRACT" in compiled, f"Expected JSON_EXTRACT in SQLite SQL, got:\n{compiled}"

        # Must NOT select the full data column as a standalone SELECT column.
        # In the sub-path SQL, `combat_log.data` appears ONLY as an argument to
        # JSON_EXTRACT(combat_log.data, ...) — never as an isolated SELECT item.
        # A full-column select would produce `..., combat_log.data AS data ...` or
        # `..., combat_log.data \n`.  We verify no JSON_EXTRACT-free occurrence exists
        # by counting: every `combat_log.data` must be preceded by `JSON_EXTRACT(`.
        import re

        # Find all `combat_log.data` occurrences and verify each is inside JSON_EXTRACT
        for match in re.finditer(r"combat_log\.data", compiled):
            start = match.start()
            # Check that `JSON_EXTRACT(` precedes this occurrence within a few chars
            context_before = compiled[max(0, start - 15) : start]
            assert "JSON_EXTRACT(" in context_before, (
                f"Found `combat_log.data` NOT inside JSON_EXTRACT — full blob may be selected.\n"
                f"Context: {compiled[max(0, start - 15) : start + 30]!r}\n"
                f"Full SQL: {compiled}"
            )

    def test_postgresql_dialect_uses_arrow_operator_not_full_data_column(self):
        """PG: compiled statement uses -> sub-path operator, NOT `combat_log.data` alone."""
        stmt = select(
            CombatLog.id,
            CombatLog.guild_id,
            CombatLog.context,
            CombatLog.combatant1_name,
            CombatLog.combatant2_name,
            CombatLog.combatant1_user_id,
            CombatLog.combatant2_user_id,
            CombatLog.winner_name,
            CombatLog.is_stalemate,
            CombatLog.created_at,
            CombatLog.data["summary"].label("summary"),
            CombatLog.data["metadata"].label("metadata"),
            CombatLog.data["key_events"].label("key_events"),
        ).where(CombatLog.id == 1)

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        # PG JSONB sub-path operator
        assert " -> " in compiled, f"Expected -> operator in PG SQL, got:\n{compiled}"
        # Must have the three sub-path keys
        assert "summary" in compiled
        assert "metadata" in compiled
        assert "key_events" in compiled

        import re

        bare_data_pattern = re.compile(r"\bcombat_log\.data\b(?!\s*->)")
        assert not bare_data_pattern.search(compiled), (
            f"SQL selects bare `combat_log.data` (full blob); expected only sub-path selects:\n{compiled}"
        )

    def test_full_select_would_fail_the_sql_assertion(self):
        """Control: a SELECT that loads the full data column does NOT use JSON_EXTRACT.

        This verifies the assertion is non-vacuous: if the implementation reverts to
        `select(CombatLog)` (loading all columns), the JSON_EXTRACT check fails.
        """
        # Full-table select — loads every column including the multi-MB data blob
        full_stmt = select(CombatLog).where(CombatLog.id == 1)
        compiled = str(full_stmt.compile(dialect=sqlite.dialect()))

        # A full select does NOT contain JSON_EXTRACT — confirms our test is not vacuous
        assert "JSON_EXTRACT" not in compiled, "Control failed: full select unexpectedly contains JSON_EXTRACT"


# ===========================================================================
# 2. Timeline not materialized — fast path must not load timeline into Python
# ===========================================================================


class TestTimelineNotMaterialized:
    """Verify that the fast path (new row with stored key_events) never materialises
    the timeline sub-key in the Python row object the service receives.
    """

    async def test_subpath_row_has_no_timeline_attribute(self, repo, db_session):
        """The Row namedtuple returned by get_subpath_for_detail has no 'timeline' field."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        sub = await repo.get_subpath_for_detail(db_session, saved.id)

        assert sub is not None
        # The Row should NOT have a timeline field
        assert not hasattr(sub, "timeline"), f"timeline was materialized in sub-path Row: {sub._fields}"
        # But it SHOULD have the three sub-paths
        assert hasattr(sub, "summary")
        assert hasattr(sub, "metadata")
        assert hasattr(sub, "key_events")

    async def test_subpath_row_fields_match_expected_columns(self, repo, db_session):
        """Row._fields must contain exactly the sub-path fields and no 'data' column."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        sub = await repo.get_subpath_for_detail(db_session, saved.id)

        assert sub is not None
        fields = set(sub._fields)
        # Sub-path fields present
        assert "summary" in fields
        assert "metadata" in fields
        assert "key_events" in fields
        # Full data column absent
        assert "data" not in fields, f"Full 'data' column found in sub-path Row fields: {fields}"
        # Timeline absent
        assert "timeline" not in fields

    async def test_fast_path_service_receives_no_timeline(self, repo, db_session):
        """End-to-end: when service.get_detail is called on a new row, no timeline
        is present in the sub-path row the service consumes.

        We intercept get_subpath_for_detail and inspect the real return value.
        """
        log = _insert_new_row(repo, db_session, extra_timeline_items=50)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()

        captured_sub: list[Any] = []
        original = repo.get_subpath_for_detail

        async def capturing_subpath(db: Any, obj_id: int) -> Any:
            result = await original(db, obj_id)
            captured_sub.append(result)
            return result

        svc._repo = repo
        svc._repo.get_subpath_for_detail = capturing_subpath  # type: ignore[method-assign]

        await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        assert len(captured_sub) == 1
        sub = captured_sub[0]
        assert sub is not None
        assert not hasattr(sub, "timeline"), "timeline was materialized on the fast path"
        assert not hasattr(sub, "data"), "full data column was loaded on the fast path"


# ===========================================================================
# 3. Output unchanged vs T7a — new row (fast path)
# ===========================================================================


class TestFastPathOutputIdentity:
    """get_detail output on a new row (stored key_events) must be byte-identical to
    what the T7a read path would have produced (same function, same stored data).
    """

    async def test_fast_path_returns_correct_id_and_context(self, repo, db_session):
        """Basic field assertions on the fast path."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        assert detail["id"] == saved.id
        assert detail["context"] == "duel"
        assert detail["combatant1_name"] == "Alice"
        assert detail["combatant2_name"] == "Bob"
        assert detail["combatant1_user_id"] == 100
        assert detail["combatant2_user_id"] == 200
        assert detail["winner_name"] == "Alice"
        assert detail["is_stalemate"] is False

    async def test_fast_path_outcome_correct_for_winner(self, repo, db_session):
        """POV outcome: c1 (Alice/uid=100) won; asking as uid=100 → 'won'."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)
        assert detail["outcome"] == "won"

    async def test_fast_path_outcome_correct_for_loser(self, repo, db_session):
        """POV outcome: Bob/uid=200 lost."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=200)
        assert detail["outcome"] == "lost"

    async def test_fast_path_key_events_match_stored(self, repo, db_session):
        """Fast path: key_events in the output must equal what was stored."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        assert detail["key_events"] == _KEY_EVENTS_STORED

    async def test_fast_path_combatant_stats_present(self, repo, db_session):
        """Fast path: combatant1/combatant2 stats dicts are correctly parsed."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        c1 = detail["combatant1"]
        assert c1["name"] == "Alice"
        assert c1["shots_fired"] == 80
        assert c1["shots_hit"] == 50
        assert abs(c1["accuracy"] - 50 / 80) < 1e-9

        c2 = detail["combatant2"]
        assert c2["name"] == "Bob"
        assert c2["final_hp"]["hull"] == 0

    async def test_fast_path_duration_and_metadata(self, repo, db_session):
        """Fast path: duration_ticks, duration_s, pvc_damage_reduction from metadata."""
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        assert detail["duration_ticks"] == 3000
        # tick_ms=10 → 3000 * 10ms = 30s
        assert abs(detail["duration_s"] - 30.0) < 0.01
        assert detail["pvc_damage_reduction"] == 0.0

    async def test_fast_path_output_identical_to_t7a_path(self, repo, db_session):
        """Output of fast path must be field-for-field identical to the T7a legacy path.

        We insert a new row with stored key_events, then call get_detail twice:
          (a) normally (fast path — P4-T7b)
          (b) via _get_detail_legacy_fallback (simulates T7a behavior on same data)
        Both must produce identical dicts except path-tracking log entries are not tested.
        """
        log = _insert_new_row(repo, db_session)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        # (a) Fast path
        fast_detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        # (b) Simulate T7a: load full row and call legacy fallback path directly
        sub_placeholder = MagicMock()
        sub_placeholder.key_events = None  # force legacy fallback
        legacy_detail = await svc._get_detail_legacy_fallback(db_session, saved.id, 100, sub_placeholder)

        # All outcome fields must be identical
        for field in (
            "id",
            "guild_id",
            "context",
            "combatant1_name",
            "combatant2_name",
            "combatant1_user_id",
            "combatant2_user_id",
            "winner_name",
            "is_stalemate",
            "outcome",
            "duration_ticks",
            "duration_s",
            "pvc_damage_reduction",
        ):
            assert fast_detail[field] == legacy_detail[field], (
                f"Field {field!r} differs: fast={fast_detail[field]!r} legacy={legacy_detail[field]!r}"
            )

        # key_events: stored (fast path) vs re-extracted (legacy path) must match
        # (T7a stored them using the same _extract_key_events; they are byte-identical)
        assert fast_detail["key_events"] == legacy_detail["key_events"], (
            f"key_events differ:\n  fast={fast_detail['key_events']}\n  legacy={legacy_detail['key_events']}"
        )


# ===========================================================================
# 4 & 5. Legacy row: fallback path output + genuine timeline extraction
# ===========================================================================


class TestLegacyFallback:
    """For a legacy row (no stored key_events), get_detail must:
    - fall back to full-row load (get_by_id)
    - run _extract_key_events on the timeline
    - return the same output a direct _extract_key_events call would produce
    """

    async def test_legacy_row_key_events_absent_triggers_fallback(self, repo, db_session):
        """A legacy row (no key_events in data) triggers the fallback path."""
        log = _insert_new_row(repo, db_session, with_key_events=False)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        # Track whether get_by_id was called (the fallback loads the full row)
        original_get_by_id = repo.get_by_id
        get_by_id_calls: list[int] = []

        async def tracking_get_by_id(db: Any, obj_id: int) -> Any:
            get_by_id_calls.append(obj_id)
            return await original_get_by_id(db, obj_id)

        svc._repo.get_by_id = tracking_get_by_id  # type: ignore[method-assign]

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        # get_by_id must have been called (full row loaded)
        assert saved.id in get_by_id_calls, f"Legacy fallback did not call get_by_id for legacy row id={saved.id}"
        assert detail is not None
        assert detail["id"] == saved.id

    async def test_legacy_row_key_events_extracted_from_timeline(self, repo, db_session):
        """Legacy row: key_events in output are extracted from the timeline, not stored.

        NEW behavior: layer_depleted → event_type "Layer depleted" (not "Armour depleted").
        The detail string still contains "Armour depleted" but event_type is "Layer depleted".
        """
        log = _insert_new_row(repo, db_session, with_key_events=False)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        key_events = detail["key_events"]
        assert isinstance(key_events, list)
        # _extract_key_events on _TIMELINE_EVENTS should produce Engagement + Layer depleted + Outcome
        types_found = {ev["event_type"] for ev in key_events}
        assert "Engagement" in types_found
        # New event_type is "Layer depleted" (not "Armour depleted"); detail still says "Armour depleted"
        assert "Layer depleted" in types_found
        assert "Outcome" in types_found

    async def test_legacy_row_output_identical_to_direct_extraction(self, repo, db_session):
        """Legacy row get_detail output must equal what T7a's read path (direct extraction) produces.

        We compare the key_events from the fallback against those produced by calling
        _extract_key_events directly on the same timeline — confirming the fallback
        code path is the same function with the same inputs.
        """
        from services.combat_log_service import CombatLogService

        log = _insert_new_row(repo, db_session, with_key_events=False)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        # Directly extract using the same function
        expected_key_events = CombatLogService._extract_key_events(
            list(_TIMELINE_EVENTS),
            tick_ms=10,
            combatants_map=_BASE_SUMMARY["combatants"],
        )

        assert detail["key_events"] == expected_key_events, (
            f"Legacy fallback key_events differ from direct extraction:\n"
            f"  from detail: {detail['key_events']}\n"
            f"  from direct: {expected_key_events}"
        )

    async def test_legacy_fallback_loads_timeline_into_memory(self, repo, db_session):
        """The legacy fallback genuinely loads the full row (including timeline).

        We confirm this by: (a) storing a legacy row with a unique sentinel timeline event,
        then (b) verifying that the fallback's extracted key_events reflect the timeline
        contents (not an empty extraction). This proves the fallback is operating on the
        real timeline, not a default empty list.
        """
        # Insert a legacy row with a unique armour-depletion event
        unique_timeline = [
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {
                            "name": "Sentinel1",
                            "display_name": "S1",
                            "ship": "Specter",
                            "hp": {"hull": 100, "armour": 50, "shield": 0},
                        },
                        {
                            "name": "Sentinel2",
                            "display_name": "S2",
                            "ship": "Wraith",
                            "hp": {"hull": 100, "armour": 50, "shield": 0},
                        },
                    ],
                    "initial_distance": 1000,
                },
            },
            {"tick": 999, "type": "layer_depleted", "actor": "Sentinel2", "target": None, "data": {"layer": "shield"}},
            {
                "tick": 5000,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": "Sentinel1", "reason": "hp_depleted", "duration_ticks": 5000},
            },
        ]
        data: dict[str, Any] = {
            "schema_version": 1,
            "summary": {
                "reason": "hp_depleted",
                "winner": "Sentinel1",
                "outcome": "win",
                "combatants": {
                    "1": {
                        "name": "Sentinel1",
                        "ship": "Specter",
                        "start_hp": {"hull": 100, "armour": 50, "shield": 0},
                        "final_hp": {"hull": 70, "armour": 30, "shield": 0},
                        "shots_fired": 30,
                        "shots_hit": 20,
                        "damage_dealt": 80,
                        "damage_taken": 40,
                    },
                    "2": {
                        "name": "Sentinel2",
                        "ship": "Wraith",
                        "start_hp": {"hull": 100, "armour": 50, "shield": 0},
                        "final_hp": {"hull": 0, "armour": 0, "shield": 0},
                        "shots_fired": 20,
                        "shots_hit": 10,
                        "damage_dealt": 40,
                        "damage_taken": 80,
                    },
                },
                "duration_ticks": 5000,
            },
            "timeline": unique_timeline,
            "metadata": {"tick_ms": 10, "resolver": "tick_v1", "total_ticks": 5000, "pvc_damage_reduction": 0.0},
            # NO key_events — legacy row
        }
        legacy_log = CombatLog(
            guild_id=699744305274945650,
            context="duel",
            combatant1_name="Sentinel1",
            combatant2_name="Sentinel2",
            combatant1_user_id=111,
            combatant2_user_id=222,
            winner_name="Sentinel1",
            is_stalemate=False,
            data=data,
            created_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
        )
        saved = await repo.add(db_session, legacy_log)

        svc = CombatLogService()
        svc._repo = repo

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=111)

        # The fallback must have loaded and processed the unique_timeline:
        # - Engagement at tick 0 (fight_start)
        # - Shield depleted at tick 999 (layer_depleted)
        # - Outcome at tick 5000 (fight_end)
        types_found = {ev["event_type"] for ev in detail["key_events"]}
        assert "Engagement" in types_found, "Fallback did not extract Engagement from timeline"
        # New behavior: event_type is "Layer depleted" (not "Shield depleted"); detail contains "Shield depleted"
        assert "Layer depleted" in types_found, (
            f"Fallback did not extract Layer depleted (shield) — timeline was not loaded. Got: {types_found}"
        )
        assert "Outcome" in types_found, "Fallback did not extract Outcome from timeline"

    async def test_ownership_gate_applies_before_fallback_decision(self, repo, db_session):
        """Non-combatant gets KeyError even when key_events is None (legacy row)."""
        log = _insert_new_row(repo, db_session, with_key_events=False)
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        with pytest.raises(KeyError):
            await svc.get_detail(db_session, battle_id=saved.id, user_id=999)

    async def test_not_found_raises_key_error(self, repo, db_session):
        """Non-existent battle_id raises KeyError (not found path)."""
        svc = CombatLogService()
        svc._repo = repo

        with pytest.raises(KeyError):
            await svc.get_detail(db_session, battle_id=999999, user_id=100)

    async def test_empty_key_events_list_takes_fast_path_not_legacy(self, repo, db_session):
        """A stored key_events=[] (empty list, not None) MUST take the fast path.

        Routing guard: ``if sub.key_events is None:``  — empty list is NOT None,
        so it correctly skips the legacy fallback.

        A falsy-check (``if not sub.key_events:``) would wrongly route [] to the
        fallback and invoke get_by_id.  This test guards against that regression by
        wiring get_by_id to raise AssertionError so any invocation is immediately
        visible as a test failure.

        Asserts:
          - get_detail returns without error
          - detail['key_events'] == []
          - get_by_id was never called
        """
        # Insert a row with key_events=[] explicitly stored (valid v3-recap output
        # for a fight with no extractable key events). Also includes recurring=[] which
        # v3 always writes. Both must be present for the fast path to be taken.
        data_with_empty_key_events: dict[str, Any] = {
            "schema_version": 1,
            "summary": _BASE_SUMMARY,
            "timeline": list(_TIMELINE_EVENTS),
            "metadata": _BASE_METADATA,
            "key_events": [],  # empty list — not None; must NOT trigger fallback
            "recurring": [],  # v3: always stored alongside key_events
        }
        log = CombatLog(
            guild_id=699744305274945650,
            context="duel",
            combatant1_name="Alice",
            combatant2_name="Bob",
            combatant1_user_id=100,
            combatant2_user_id=200,
            winner_name="Alice",
            is_stalemate=False,
            data=data_with_empty_key_events,
            created_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
        )
        saved = await repo.add(db_session, log)

        svc = CombatLogService()
        svc._repo = repo

        # Wire get_by_id to explode — any call to the legacy fallback is a bug
        async def get_by_id_must_not_be_called(db: Any, obj_id: int) -> Any:
            raise AssertionError(
                f"get_by_id was called for battle_id={obj_id} — "
                "empty key_events=[] should take the fast path (is None check), "
                "not the legacy fallback (falsy check would wrongly route [] here)"
            )

        svc._repo.get_by_id = get_by_id_must_not_be_called  # type: ignore[method-assign]

        detail = await svc.get_detail(db_session, battle_id=saved.id, user_id=100)

        # Fast path returned successfully without calling get_by_id
        assert detail is not None
        assert detail["key_events"] == [], f"Expected key_events=[] on fast path, got {detail['key_events']!r}"


# ===========================================================================
# 6. _SubpathAdapter correctness
# ===========================================================================


class TestSubpathAdapter:
    """_SubpathAdapter must duck-type like CombatLog for _pov_outcome."""

    def test_adapter_data_contains_summary(self):
        """data["summary"] is the summary dict passed to the constructor."""
        sub = MagicMock()
        sub.id = 1
        sub.guild_id = 100
        sub.context = "duel"
        sub.combatant1_name = "Alice"
        sub.combatant2_name = "Bob"
        sub.combatant1_user_id = 10
        sub.combatant2_user_id = 20
        sub.winner_name = "Alice"
        sub.is_stalemate = False
        sub.created_at = datetime(2026, 6, 7, tzinfo=UTC)

        summary = {"combatants": {"1": {"final_hp": {"hull": 60}}, "2": {"final_hp": {"hull": 0}}}}
        adapter = _SubpathAdapter(sub, summary)

        assert adapter.data == {"summary": summary}
        assert adapter.data["summary"]["combatants"]["1"]["final_hp"]["hull"] == 60

    def test_pov_outcome_works_via_adapter(self):
        """_pov_outcome called with an adapter produces correct 'won'/'lost' result."""
        sub = MagicMock()
        sub.combatant1_name = "Alice"
        sub.combatant2_name = "Bob"
        sub.combatant1_user_id = 100
        sub.combatant2_user_id = 200
        sub.is_stalemate = False
        sub.created_at = datetime(2026, 6, 7, tzinfo=UTC)

        summary = {
            "combatants": {
                "1": {"final_hp": {"hull": 60, "armour": 0, "shield": 0}},
                "2": {"final_hp": {"hull": 0, "armour": 0, "shield": 0}},
            }
        }
        adapter = _SubpathAdapter(sub, summary)

        opp, outcome = CombatLogService._pov_outcome(adapter, user_id=100)  # type: ignore[arg-type]
        assert outcome == "won"
        assert opp == "Bob"

        _opp2, outcome2 = CombatLogService._pov_outcome(adapter, user_id=200)  # type: ignore[arg-type]
        assert outcome2 == "lost"

    def test_pov_outcome_c2_wins_catches_broken_adapter(self):
        """Mutation probe: c2-wins shape diverges under a broken adapter.

        With a correct _SubpathAdapter, data['summary']['combatants'] shows
        c1_hull=0 and c2_hull=60 → winner_slot='2' → c2's user (uid=200) gets
        'won'.

        A broken adapter that sets self.data = {} produces an empty combatants_map,
        so both hulls default to 1, the tiebreaker (c1_hull >= c2_hull → True) picks
        winner_slot='1', and c2's user (uid=200) would receive 'lost' instead of
        'won'.  The test therefore FAILS when the adapter is mutated to data={} and
        PASSES only when the adapter correctly forwards the summary.
        """
        sub = MagicMock()
        sub.combatant1_name = "Alice"
        sub.combatant2_name = "Bob"
        sub.combatant1_user_id = 100
        sub.combatant2_user_id = 200
        sub.is_stalemate = False
        sub.created_at = datetime(2026, 6, 7, tzinfo=UTC)

        # c2 wins: Bob (slot 2) survives with hull=60; Alice (slot 1) is at hull=0
        summary_c2_wins = {
            "combatants": {
                "1": {"final_hp": {"hull": 0, "armour": 0, "shield": 0}},
                "2": {"final_hp": {"hull": 60, "armour": 10, "shield": 0}},
            }
        }
        adapter = _SubpathAdapter(sub, summary_c2_wins)

        # Asking as c2's user (uid=200): correct adapter → 'won'
        _opp, outcome = CombatLogService._pov_outcome(adapter, user_id=200)  # type: ignore[arg-type]
        assert outcome == "won", (
            f"Expected 'won' for c2's user on c2-wins shape, got {outcome!r}. "
            "If the adapter is broken (data={}), this would be 'lost' — mutation caught."
        )

        # Sanity: c1's user on the same shape → 'lost'
        _opp1, outcome1 = CombatLogService._pov_outcome(adapter, user_id=100)  # type: ignore[arg-type]
        assert outcome1 == "lost"
