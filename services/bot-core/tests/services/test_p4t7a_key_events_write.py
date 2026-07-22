"""P4-T7a — key_events stored at write time, fallback for legacy rows.

X2 parity gate: the value stored in data["key_events"] at write time MUST be
byte-identical to _extract_key_events run on the same timeline with the same
arguments.  This is guaranteed by construction: persist() calls
_extract_key_events from services.combat_resolver (the same resolver leaf the
combat worker imports) with (serialised_timeline, tick_ms, combatants_map).

Tests:
  - X2 parity: stored key_events == _extract_key_events(same_timeline,
    same_tick_ms, same_combatants_map) for multiple timeline shapes.
  - Legacy fallback: a row with NO stored key_events (legacy row: data without
    the "key_events" key) resolves correctly via _extract_key_events; output
    is identical to a row that DID store them.
  - Write idempotence: storing key_events does NOT alter any other persisted
    field in data_blob (schema_version, summary, timeline, metadata unchanged).

Max 2 mocks per test.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

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

import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.models.combat_log import CombatLog
from services.combat_log_service import CombatLogService
from services.combat_models import (
    CombatMeta,
    FightResults,
    FightStats,
)
from services.combat_resolver import _extract_key_events

# ---------------------------------------------------------------------------
# Representative timelines for X2 parity tests
# ---------------------------------------------------------------------------

_COMBATANTS_MAP = {
    "1": {"name": "SamX", "ship": "Betty"},
    "2": {"name": "H'Soc", "ship": "Vossk Soldier"},
}


def _make_minimal_timeline() -> list[dict]:
    """Empty timeline — no events."""
    return []


def _make_standard_timeline() -> list[dict]:
    """Representative timeline with multiple event kinds."""
    return [
        {
            "tick": 0,
            "type": "fight_start",
            "actor": None,
            "target": None,
            "data": {
                "combatants": [
                    {
                        "name": "SamX",
                        "display_name": "SamX",
                        "ship": "Betty",
                        "hp": {"hull": 100, "armour": 50, "shield": 0},
                    },
                    {
                        "name": "H'Soc",
                        "display_name": "H'Soc",
                        "ship": "Vossk Soldier",
                        "hp": {"hull": 80, "armour": 30, "shield": 20},
                    },
                ],
                "initial_distance": 5000,
            },
        },
        # Secondary weapon fire (rocket)
        {
            "tick": 120,
            "type": "weapon_fire",
            "actor": "SamX",
            "target": "H'Soc",
            "data": {
                "slot": "secondary",
                "subtype": "rocket",
                "weapon": "S'koon Rockets",
                "hit": True,
                "side": 1,
            },
        },
        # Primary weapon first-hit for side 2
        {
            "tick": 150,
            "type": "weapon_fire",
            "actor": "H'Soc",
            "target": "SamX",
            "data": {
                "slot": "primary",
                "subtype": "primary",
                "weapon": "Vossk Plasma Cannon",
                "hit": True,
                "side": 2,
            },
        },
        # Damage → crosses 50% HP milestone for side 2
        {
            "tick": 600,
            "type": "damage",
            "actor": "SamX",
            "target": "H'Soc",
            "data": {
                "side": 2,
                "amount": 65,
                "absorbed": 65,
                "source": {"attacker": "SamX", "subtype": "primary", "weapon": "Nirai"},
                "hp_after": {"hull": 30, "armour": 0, "shield": 0},  # ~23% → crosses 25% too
            },
        },
        # Module activation
        {
            "tick": 800,
            "type": "module_activation",
            "actor": "SamX",
            "target": None,
            "data": {"module": "U'tool Cloak", "module_type": "CloakModule", "side": 1},
        },
        # Layer depleted
        {
            "tick": 1200,
            "type": "layer_depleted",
            "actor": "H'Soc",
            "target": None,
            "data": {"layer": "armour", "side": 2},
        },
        # Secondary depleted
        {
            "tick": 1500,
            "type": "secondary_depleted",
            "actor": "SamX",
            "target": None,
            "data": {"weapon": "S'koon Rockets", "side": 1},
        },
        # Fight end
        {
            "tick": 3488,
            "type": "fight_end",
            "actor": None,
            "target": None,
            "data": {
                "winner": "SamX",
                "reason": "hp_depleted",
                "duration_ticks": 3488,
                "final_hp": {
                    "c1": {"hull": 55, "armour": 20, "shield": 0},
                    "c2": {"hull": 0, "armour": 0, "shield": 0},
                },
            },
        },
    ]


def _make_large_timeline(n_events: int = 200) -> list[dict]:
    """Large timeline with repeated secondary fires to test scale.

    Includes a `damage` event that crosses the 50% HP milestone for side 2,
    so the combatants_map-aware HP-milestone labeling (e.g. "H'Soc dropped to ≤50% HP"
    vs "2 dropped to ≤50% HP") is exercised by the X2 parity assertion.
    """
    # fight_start: side 2 starts at hull=200, armour=100, shield=50 → total=350 HP.
    # The damage event below sets hp_after total=0 → crosses both 50% (≤175) and 25% (≤87) thresholds.
    events: list[dict] = [
        {
            "tick": 0,
            "type": "fight_start",
            "actor": None,
            "target": None,
            "data": {
                "combatants": [
                    {
                        "name": "SamX",
                        "display_name": "SamX",
                        "ship": "Betty",
                        "hp": {"hull": 200, "armour": 100, "shield": 50},
                    },
                    {
                        "name": "H'Soc",
                        "display_name": "H'Soc",
                        "ship": "Vossk Soldier",
                        "hp": {"hull": 200, "armour": 100, "shield": 50},
                    },
                ],
                "initial_distance": 3000,
            },
        }
    ]
    for i in range(n_events):
        events.append(
            {
                "tick": 10 + i * 5,
                "type": "weapon_fire",
                "actor": "SamX",
                "target": "H'Soc",
                "data": {
                    "slot": "secondary",
                    "subtype": "missile",
                    "weapon": "Kaamo Missiles",
                    "hit": bool(i % 2 == 0),
                    "side": 1,
                },
            }
        )
    # Damage event: crosses 50% and 25% HP milestones for side 2 (H'Soc).
    # With combatants_map, label reads "H'Soc dropped to ≤50% HP".
    # Without combatants_map, label reads "2 dropped to ≤50% HP".
    # This makes the X2 parity assertion sensitive to combatants_map corruption.
    events.append(
        {
            "tick": 1100,
            "type": "damage",
            "actor": "SamX",
            "target": "H'Soc",
            "data": {
                "side": 2,
                "amount": 350,
                "absorbed": 0,
                "source": {"attacker": "SamX", "subtype": "primary", "weapon": "Kaamo Missiles"},
                "hp_after": {"hull": 0, "armour": 0, "shield": 0},  # 0% → crosses both 50% and 25%
            },
        }
    )
    events.append(
        {
            "tick": 20000,
            "type": "fight_end",
            "actor": None,
            "target": None,
            "data": {
                "winner": None,
                "reason": "time_cap",
                "duration_ticks": 20000,
            },
        }
    )
    return events


def _make_fight_results(
    timeline: list[dict],
    *,
    tick_ms: int = 10,
    c1_user_id: int | None = 111,
    c2_user_id: int | None = 222,
) -> FightResults:
    """Build a minimal FightResults with a synthetic timeline and combatants_map."""
    combatants_summary = {
        "1": {
            "name": "SamX",
            "ship": "Betty",
            "start_hp": {"hull": 100, "armour": 50, "shield": 0},
            "final_hp": {"hull": 55, "armour": 20, "shield": 0},
            "shots_fired": 60,
            "shots_hit": 40,
            "damage_dealt": 120,
            "damage_taken": 80,
        },
        "2": {
            "name": "H'Soc",
            "ship": "Vossk Soldier",
            "start_hp": {"hull": 80, "armour": 30, "shield": 20},
            "final_hp": {"hull": 0, "armour": 0, "shield": 0},
            "shots_fired": 55,
            "shots_hit": 35,
            "damage_dealt": 80,
            "damage_taken": 120,
        },
    }
    summary = {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 3488,
        "winner": "SamX",
        "combatants": combatants_summary,
    }
    stats = FightStats(ship_name="Betty", raw_hp=150, raw_dps=8.0, varied_hp=150, varied_dps=8.0, ttk=None)
    stats2 = FightStats(ship_name="Vossk Soldier", raw_hp=130, raw_dps=6.0, varied_hp=130, varied_dps=6.0, ttk=4.0)
    return FightResults(
        winner_name="SamX",
        loser_name="H'Soc",
        is_stalemate=False,
        ship1_stats=stats,
        ship2_stats=stats2,
        winner_side=1,
        combat_log=timeline,
        metadata={
            "schema_version": 1,
            "summary": summary,
            "metadata": {
                "tick_ms": tick_ms,
                "total_ticks": 3488,
                "resolver": "tick_v1",
                "pvc_damage_reduction": 0.0,
            },
            "combatant_user_ids": {"c1": c1_user_id, "c2": c2_user_id},
        },
    )


def _make_row_no_key_events(timeline: list[dict]) -> CombatLog:
    """Build a real CombatLog row WITHOUT stored key_events (legacy row)."""
    from datetime import UTC, datetime

    return CombatLog(
        id=42,
        guild_id=699744305274945650,
        context="duel",
        combatant1_name="SamX",
        combatant2_name="H'Soc",
        combatant1_user_id=111,
        combatant2_user_id=222,
        winner_name="SamX",
        is_stalemate=False,
        created_at=datetime.now(UTC),
        data={
            "schema_version": 1,
            "summary": {
                "outcome": "win",
                "reason": "hp_depleted",
                "duration_ticks": 3488,
                "winner": "SamX",
                "combatants": _COMBATANTS_MAP,
            },
            "timeline": timeline,
            "metadata": {
                "tick_ms": 10,
                "total_ticks": 3488,
                "resolver": "tick_v1",
                "pvc_damage_reduction": 0.0,
            },
            # NOTE: no "key_events" key — simulates a legacy row
        },
    )


def _make_row_with_key_events(timeline: list[dict]) -> CombatLog:
    """Build a real CombatLog row WITH pre-stored key_events (new-style row)."""
    from datetime import UTC, datetime

    combatants_map = _COMBATANTS_MAP
    stored_key_events = _extract_key_events(timeline, tick_ms=10, combatants_map=combatants_map)

    return CombatLog(
        id=43,
        guild_id=699744305274945650,
        context="duel",
        combatant1_name="SamX",
        combatant2_name="H'Soc",
        combatant1_user_id=111,
        combatant2_user_id=222,
        winner_name="SamX",
        is_stalemate=False,
        created_at=datetime.now(UTC),
        data={
            "schema_version": 1,
            "summary": {
                "outcome": "win",
                "reason": "hp_depleted",
                "duration_ticks": 3488,
                "winner": "SamX",
                "combatants": combatants_map,
            },
            "timeline": timeline,
            "metadata": {
                "tick_ms": 10,
                "total_ticks": 3488,
                "resolver": "tick_v1",
                "pvc_damage_reduction": 0.0,
            },
            "key_events": stored_key_events,
        },
    )


def _make_sub_row(row: CombatLog, *, key_events: list | None) -> MagicMock:
    """Build a sub-path Row mock as returned by get_subpath_for_detail.

    P4-T7b: get_detail now calls get_subpath_for_detail() first.  Tests that
    mock the repo must mock get_subpath_for_detail (not get_by_id) for the fast
    path; for the legacy fallback (key_events=None) they must mock BOTH methods.
    """
    sub = MagicMock()
    sub.id = row.id
    sub.guild_id = row.guild_id
    sub.context = row.context
    sub.combatant1_name = row.combatant1_name
    sub.combatant2_name = row.combatant2_name
    sub.combatant1_user_id = row.combatant1_user_id
    sub.combatant2_user_id = row.combatant2_user_id
    sub.winner_name = row.winner_name
    sub.is_stalemate = row.is_stalemate
    sub.created_at = row.created_at
    sub.summary = row.data["summary"]
    sub.metadata = row.data["metadata"]
    sub.key_events = key_events
    return sub


# ---------------------------------------------------------------------------
# X2 parity tests
# ---------------------------------------------------------------------------


class TestX2KeyEventsParity:
    """P4-T7a X2 gate: stored key_events == _extract_key_events on same input."""

    @pytest.mark.asyncio
    async def test_parity_empty_timeline(self):
        """Empty timeline: stored key_events == _extract_key_events([]).

        Intentionally degenerate: an empty timeline produces no events regardless
        of combatants_map, so this test is not sensitive to map-aware labeling.
        Its purpose is to verify the write path handles the zero-event edge case
        without error (no KeyError, no IndexError, empty list stored and returned).
        """
        timeline = _make_minimal_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 1
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        stored = captured_row.data["key_events"]

        # Compute expected using same function + same inputs as persist()
        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        expected = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert stored == expected, f"X2 parity failed for empty timeline. stored={stored!r} expected={expected!r}"

    @pytest.mark.asyncio
    async def test_parity_standard_timeline(self):
        """Standard timeline (mixed event kinds): stored == _extract_key_events."""
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 2
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        stored = captured_row.data["key_events"]

        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        expected = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert stored == expected, f"X2 parity failed for standard timeline. stored={stored!r} expected={expected!r}"
        # Non-vacuous: standard timeline produces key events
        assert len(stored) > 0, "Standard timeline must produce non-empty key_events (test is vacuous otherwise)"

    @pytest.mark.asyncio
    async def test_parity_large_timeline(self):
        """Large timeline (200 missile events + damage): stored == _extract_key_events.

        The timeline includes a damage event that triggers HP-milestone labels (50%/25%
        for side 2), so the X2 parity assertion exercises combatants_map-aware labeling.
        A map-corruption mutation (e.g. passing combatants_map={}) would produce
        "2 dropped to ≤50% HP" instead of "H'Soc dropped to ≤50% HP", failing the
        equality check.
        """
        timeline = _make_large_timeline(200)
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 3
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        stored = captured_row.data["key_events"]

        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        expected = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert stored == expected, "X2 parity failed for large timeline"
        assert len(stored) > 0, "Large timeline must produce non-empty key_events (test is vacuous otherwise)"

        # Non-vacuous map-sensitivity check: verify a map-aware HP-milestone label
        # is present. This would be "2 dropped to ≤50% HP" without the map.
        milestone_details = [ev["detail"] for ev in stored if "dropped to ≤" in ev.get("detail", "")]
        assert any("H'Soc" in d for d in milestone_details), (
            "Expected map-aware HP-milestone label 'H'Soc dropped to ...' in large-timeline key_events. "
            f"Got milestone details: {milestone_details!r}. "
            "This assertion catches combatants_map=None/empty mutations."
        )

    @pytest.mark.asyncio
    async def test_parity_non_default_tick_ms(self):
        """Non-default tick_ms: stored uses tick_ms from metadata, same as fallback."""
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline, tick_ms=20)  # non-default 20ms

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 4
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        stored = captured_row.data["key_events"]

        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        expected = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert stored == expected, "X2 parity failed for non-default tick_ms"
        # Verify the tick_ms=20 was actually used (time_s for tick 120 should be 2.4, not 1.2)
        for ev in stored:
            if ev.get("tick") == 120:
                assert ev["time_s"] == pytest.approx(2.4), (
                    "tick_ms=20: tick 120 should produce time_s=2.4 (120*20/1000)"
                )

    @pytest.mark.asyncio
    async def test_parity_pvc_context(self):
        """PvC context (c2_user_id=None): parity holds for NPC-vs-player fights."""
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline, c2_user_id=None)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 5
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "bounty_pvc", session=AsyncMock())

        assert captured_row is not None
        stored = captured_row.data["key_events"]

        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        expected = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert stored == expected, "X2 parity failed for PvC context"

    @pytest.mark.asyncio
    async def test_t7a_output_matches_head_read_path(self):
        """Regression: T7a stored key_events are byte-identical to HEAD read-path output.

        At HEAD (pre-T7a), get_detail() ALWAYS called _extract_key_events with
        combatants_map — producing map-aware labels (e.g. "H'Soc dropped to ≤50% HP").
        T7a stores these same map-aware labels at write time.

        This test pins the invariant: the stored key_events from persist() MUST equal
        what HEAD's get_detail() read-path would have computed for the same timeline and
        combatants_map.  If either path changes its labeling, this test fails.

        Evidence: HEAD combat_log_service.get_detail() called
          _extract_key_events(timeline, tick_ms, combatants_map=combatants_map)
        T7a persist() calls the identical function with the identical arguments.
        Therefore T7a is a pure store-optimization with no user-visible output change.
        """
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 99
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        t7a_stored = captured_row.data["key_events"]

        # HEAD read-path: _extract_key_events(timeline, tick_ms, combatants_map=combatants_map)
        combatants_map = fr.metadata["summary"]["combatants"]
        tick_ms = fr.metadata["metadata"]["tick_ms"]
        head_read_path_output = _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)

        assert t7a_stored == head_read_path_output, (
            "T7a stored key_events MUST be byte-identical to HEAD read-path output. "
            f"T7a stored={t7a_stored!r} "
            f"HEAD read-path={head_read_path_output!r}"
        )
        # Verify map-aware labels are present (not the slot-number fallback)
        milestone_details = [ev["detail"] for ev in t7a_stored if "dropped to ≤" in ev.get("detail", "")]
        assert any("H'Soc" in d for d in milestone_details), (
            f"Expected map-aware HP-milestone labels in T7a output. Milestone details found: {milestone_details!r}"
        )


# ---------------------------------------------------------------------------
# Legacy fallback tests
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    """Legacy rows (no stored key_events) resolve correctly via _extract_key_events.

    P4-T7b note: get_detail now calls get_subpath_for_detail() first (fast path).
    Legacy rows are detected when key_events is None in the sub-path row; the service
    then falls back to get_by_id() + _extract_key_events (the T7a path).  Tests mock
    get_subpath_for_detail to control which path is taken.
    """

    @pytest.mark.asyncio
    async def test_legacy_row_resolves_via_fallback(self):
        """A row without key_events in data still produces correct key_events via fallback.

        P4-T7b: simulate legacy row by setting key_events=None in the sub-path mock;
        service falls back to get_by_id + _extract_key_events.
        """
        timeline = _make_standard_timeline()
        row = _make_row_no_key_events(timeline)
        sub = _make_sub_row(row, key_events=None)  # None → triggers fallback

        svc = CombatLogService()
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        mock_repo.get_by_id = AsyncMock(return_value=row)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=42, user_id=111)

        # Must have key_events
        assert "key_events" in detail
        key_events = detail["key_events"]
        assert isinstance(key_events, list)

    @pytest.mark.asyncio
    async def test_legacy_output_identical_to_stored(self):
        """Legacy row fallback produces IDENTICAL key_events to a stored row with the same timeline.

        P4-T7b: legacy row uses key_events=None sub-path → full load fallback.
        Stored row uses key_events=<list> sub-path → fast path.
        Both must produce identical key_events (same extractor function, same inputs).
        """
        timeline = _make_standard_timeline()

        # Row WITHOUT stored key_events (legacy)
        legacy_row = _make_row_no_key_events(timeline)
        legacy_sub = _make_sub_row(legacy_row, key_events=None)

        # Row WITH stored key_events (new-style)
        stored_row = _make_row_with_key_events(timeline)
        stored_ke = stored_row.data["key_events"]
        stored_sub = _make_sub_row(stored_row, key_events=stored_ke)

        svc = CombatLogService()

        # Get detail from legacy row (fallback path)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=legacy_sub)
        mock_repo.get_by_id = AsyncMock(return_value=legacy_row)
        svc._repo = mock_repo
        legacy_detail = await svc.get_detail(MagicMock(), battle_id=42, user_id=111)

        # Get detail from stored row (fast path)
        mock_repo2 = AsyncMock()
        mock_repo2.get_subpath_for_detail = AsyncMock(return_value=stored_sub)
        svc._repo = mock_repo2
        stored_detail = await svc.get_detail(MagicMock(), battle_id=43, user_id=111)

        # Key_events from both paths must be identical
        assert legacy_detail["key_events"] == stored_detail["key_events"], (
            "Legacy fallback and stored path must produce identical key_events. "
            f"legacy={legacy_detail['key_events']!r} "
            f"stored={stored_detail['key_events']!r}"
        )

    @pytest.mark.asyncio
    async def test_legacy_empty_timeline_produces_empty_key_events(self):
        """Legacy row with empty timeline: fallback produces empty list.

        P4-T7b: key_events=None in sub-path → fallback → empty extraction.
        """
        timeline = _make_minimal_timeline()
        row = _make_row_no_key_events(timeline)
        sub = _make_sub_row(row, key_events=None)

        svc = CombatLogService()
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        mock_repo.get_by_id = AsyncMock(return_value=row)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=42, user_id=111)
        assert detail["key_events"] == []

    @pytest.mark.asyncio
    async def test_stored_key_events_preferred_over_fallback(self):
        """When data["key_events"] is present, it is returned without calling _extract_key_events.

        P4-T7b: fast path — the sub-path row carries the sentinel key_events directly.
        The service returns it without loading the timeline or calling _extract_key_events.
        """
        timeline = _make_standard_timeline()
        row = _make_row_with_key_events(timeline)

        # Inject a sentinel value that would differ from real extraction
        sentinel = [{"tick": 9999, "event_type": "Sentinel", "detail": "STORED_SENTINEL"}]
        row.data["key_events"] = sentinel
        sub = _make_sub_row(row, key_events=sentinel)

        svc = CombatLogService()
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=43, user_id=111)

        # Must return the stored sentinel, NOT what _extract_key_events would produce
        assert detail["key_events"] == sentinel, (
            "Stored key_events must be returned as-is; fallback extractor must not override it."
        )


# ---------------------------------------------------------------------------
# Write idempotence / no other fields altered
# ---------------------------------------------------------------------------


class TestWriteIdempotence:
    """Storing key_events at write time does not alter any other persisted field."""

    @pytest.mark.asyncio
    async def test_other_fields_unchanged_by_key_events_addition(self):
        """data_blob fields other than key_events are identical before and after P4-T7a."""
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 10
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        data = captured_row.data

        # All pre-existing fields must be present and correct
        assert "schema_version" in data
        assert "summary" in data
        assert "timeline" in data
        assert "metadata" in data
        assert "key_events" in data  # new field from P4-T7a

        # schema_version correct
        assert data["schema_version"] == 1

        # summary matches what was passed in
        assert data["summary"] == fr.metadata["summary"]

        # timeline is the serialised form (all dicts)
        assert isinstance(data["timeline"], list)
        for item in data["timeline"]:
            assert isinstance(item, dict), f"timeline item must be dict, got {type(item)}"

        # metadata matches the inner metadata block
        assert data["metadata"] == fr.metadata["metadata"]

        # key_events is a list of dicts
        assert isinstance(data["key_events"], list)
        for ev in data["key_events"]:
            assert isinstance(ev, dict), f"key_event item must be dict, got {type(ev)}"

    @pytest.mark.asyncio
    async def test_no_extra_fields_in_data_blob(self):
        """data_blob has exactly the 6 expected top-level keys (v3 adds 'recurring')."""
        timeline = _make_minimal_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 11
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=1), fr, "duel", session=AsyncMock())

        assert captured_row is not None
        # v3 recap redesign adds "recurring" alongside "key_events".
        expected_keys = {"schema_version", "summary", "timeline", "metadata", "key_events", "recurring"}
        actual_keys = set(captured_row.data.keys())
        assert actual_keys == expected_keys, (
            f"data_blob has unexpected keys. expected={expected_keys} actual={actual_keys}"
        )

    @pytest.mark.asyncio
    async def test_row_orm_fields_unchanged(self):
        """Storing key_events does not affect CombatLog ORM row field assignments."""
        timeline = _make_standard_timeline()
        fr = _make_fight_results(timeline)

        svc = CombatLogService()
        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 12
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(CombatMeta(guild_id=99), fr, "bounty_bonus", session=AsyncMock())

        assert captured_row is not None
        assert captured_row.guild_id == 99
        assert captured_row.context == "bounty_bonus"
        assert captured_row.combatant1_name == "SamX"
        assert captured_row.combatant2_name == "H'Soc"
        assert captured_row.combatant1_user_id == 111
        assert captured_row.combatant2_user_id == 222
        assert captured_row.winner_name == "SamX"
        assert captured_row.is_stalemate is False
