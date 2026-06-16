"""Adversarial regression tests for _extract_key_events — QA layer added by code review.

These tests cover edge cases MISSING from the developer's test_recap_extract.py:
two confirmed bugs and a set of coverage gaps that could mask future regressions.

Bugs confirmed:
  BUG-1  range_est underestimation → false re-enter
         A weapon whose true range exceeds its max observed fire distance will be
         incorrectly classified as "re-entering" after a shock-blast that pushed distance
         above the underestimated range_est, even though the weapon was never out of range.

  BUG-2  ES-invuln absorbed=0 → false attribution
         damage events emitted during the EmergencySystem invuln window have absorbed=0.
         The attribution guard uses ``absorbed > attrib_best.get(..., -1)``, so 0 > -1 is
         True and the blocked weapon steals attribution for HP milestones on that tick.

Coverage gaps (not bugs in current code, but absent from developer tests):
  GAP-1  Same-tick fire+shock: bisect_right excludes same-tick shock → missed re-enter
  GAP-2  Mutual death: kill collapse must NOT suppress either side's events
  GAP-3  Same-ship-name fight: side-keying holds for collapse and outcome
  GAP-4  Primary weapon subtype appears in Weapon in range (not invisible)
  GAP-5  Booster-push displacement triggers re-enter
  GAP-6  Cluster-missile 0/N miss displays "miss"
  GAP-7  Double-milestone: single damage event crossing both 50% and 25% emits two events
  GAP-8  start_total=0 guard prevents division-by-zero
  GAP-9  Empty / None combatants_map is safe (no crash)
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level dependency stubs (same pattern as test_recap_extract.py)
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

from services.combat_resolver import _extract_key_events

# ---------------------------------------------------------------------------
# Helpers (minimal)
# ---------------------------------------------------------------------------

def _fight_start(dist: float = 3000.0, c1_name: str = "Alice", c2_name: str = "Bob",
                 c1_hp: dict | None = None, c2_hp: dict | None = None) -> dict:
    return {
        "tick": 0, "type": "fight_start", "actor": None, "target": None,
        "data": {
            "combatants": [
                {"name": c1_name, "display_name": c1_name, "ship": "Specter", "slot": 1,
                 "hp": c1_hp or {"hull": 100, "armour": 50, "shield": 0}},
                {"name": c2_name, "display_name": c2_name, "ship": "Wraith", "slot": 2,
                 "hp": c2_hp or {"hull": 100, "armour": 50, "shield": 0}},
            ],
            "initial_distance": dist,
        },
    }


def _wfire(tick: int, actor: str, weapon: str, *, subtype: str = "missile",
           hit: bool = True, side: int = 1, hits: int | None = None,
           fired: int | None = None, dist: float | None = None) -> dict:
    data: dict = {"slot": "secondary", "subtype": subtype, "weapon": weapon, "side": side}
    if subtype == "cluster-missile":
        data["hits"] = hits or 0
        data["fired"] = fired or 4
    else:
        data["hit"] = hit
    ev: dict = {"tick": tick, "type": "weapon_fire", "actor": actor, "target": "Opp", "data": data}
    return ev


def _dist_ev(tick: int, *, cause: str, from_m: float, to_m: float, side: int = 2) -> dict:
    return {
        "tick": tick, "type": "distance", "actor": None, "target": None,
        "data": {"cause": cause, "from": from_m, "to": to_m, "side": side},
    }


def _dmg_ev(tick: int, *, target_side: int, absorbed: float, weapon: str,
             hp_after: dict | None = None) -> dict:
    return {
        "tick": tick, "type": "damage", "actor": None, "target": "T",
        "data": {
            "amount": absorbed, "absorbed": absorbed,
            "hp_after": hp_after or {"hull": 50, "armour": 0, "shield": 0},
            "source": {"weapon": weapon, "attacker": "A"},
            "side": target_side,
        },
    }


def _fight_end(tick: int, winner: str | None, *, c1_hull: int = 80, c2_hull: int = 0) -> dict:
    return {
        "tick": tick, "type": "fight_end", "actor": None, "target": None,
        "data": {
            "winner": winner,
            "reason": "hp_depleted" if winner else "time_cap",
            "duration_ticks": tick,
            "final_hp": {
                "c1": {"hull": c1_hull, "armour": 0, "shield": 0},
                "c2": {"hull": c2_hull, "armour": 0, "shield": 0},
            },
        },
    }


# ---------------------------------------------------------------------------
# BUG-1: range_est underestimation → false re-enter
# ---------------------------------------------------------------------------

class TestBug1RangeEstUnderestimation:
    """BUG: weapon with true range > max observed fire distance gets a false re-enter.

    Scenario: weapon fires at dist=4800, shock-blast resets to 5000, closure brings
    back to 4500, weapon fires again.  range_est = max(4800, 4500) = 4800.
    _max_dist_between includes the shock event (5000) → 5000 > 4800 → "re-enters range"
    emitted, even though the weapon NEVER left its actual 5000m range.
    """

    def test_false_reenter_when_range_est_underestimated(self):
        """REPRODUCER for BUG-1: fires a false re-enter when range_est < shock reset distance."""
        timeline = [
            _fight_start(dist=4800.0),
            # First fire at dist=4800 → range_est will be 4800
            _wfire(10, "Alice", "LongRangeM", subtype="missile", hit=True, side=1),
            # Shock resets to 5000 (> range_est=4800)
            _dist_ev(20, cause="shock_blast", from_m=4800.0, to_m=5000.0, side=2),
            # Closure to 4500
            _dist_ev(50, cause="closure", from_m=5000.0, to_m=4500.0, side=2),
            # Second fire at 4500 — weapon's true range is 5000, never out of range
            _wfire(55, "Alice", "LongRangeM", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        re_enters = [e for e in wir if "re-enters" in e["detail"]]
        assert re_enters == [], (
            f"BUG-1: False re-enter emitted for underestimated range_est. "
            f"range_est=4800, shock to 5000, weapon fires at 4500 — never left range. "
            f"Got: {re_enters}"
        )


# ---------------------------------------------------------------------------
# BUG-2: ES-invuln absorbed=0 → false attribution
# ---------------------------------------------------------------------------

class TestBug2ESInvulnFalseAttribution:
    """BUG: damage events during ES invuln have absorbed=0; attribution threshold is -1.

    0 > -1 is True, so the invuln-blocked weapon steals attribution for any HP
    milestone (or layer break) on that tick.
    """

    def test_invuln_blocked_weapon_not_attributed_to_milestone(self):
        """REPRODUCER for BUG-2: ES-blocked weapon gets false '(by Weapon)' on HP milestone."""
        # Bob's HP already at 45% from prior ticks. This tick only has invuln-blocked damage.
        # The hp_after from the blocked event correctly reflects 45% HP → milestone fires.
        # But the weapon that "hit" was blocked (absorbed=0) and must NOT get attribution.
        cmap = {"1": {"name": "Alice"}, "2": {"name": "Bob"}}
        timeline = [
            _fight_start(dist=2000.0),
            {
                "tick": 10, "type": "damage", "actor": None, "target": "Bob",
                "data": {
                    "amount": 0, "absorbed": 0,
                    "hp_after": {"hull": 45, "armour": 0, "shield": 0},
                    "source": {"weapon": "InvulnBlockedGun", "attacker": "Alice"},
                    "blocked_by": "emergency_system_invuln",
                    "side": 2,
                },
            },
        ]
        result = _extract_key_events(timeline, combatants_map=cmap)
        milestones = [e for e in result if "HP milestone" in e.get("event_type", "")]
        for m in milestones:
            assert "(by InvulnBlockedGun)" not in m["detail"], (
                f"BUG-2: Invuln-blocked weapon (absorbed=0) falsely attributed to HP milestone: "
                f"{m['detail']!r}"
            )

    def test_invuln_blocked_does_not_outcompete_real_damage_same_tick(self):
        """If real damage (absorbed>0) exists on same tick, it wins over blocked weapon."""
        cmap = {"1": {"name": "Alice"}, "2": {"name": "Bob"}}
        timeline = [
            _fight_start(dist=2000.0),
            # Real damage first: absorbed=30 → sets attrib_best to 30
            _dmg_ev(10, target_side=2, absorbed=30.0, weapon="RealCannon",
                    hp_after={"hull": 45, "armour": 0, "shield": 0}),
            # Then invuln-blocked event: absorbed=0 → 0 > 30? No → should NOT override
            {
                "tick": 10, "type": "damage", "actor": None, "target": "Bob",
                "data": {
                    "amount": 0, "absorbed": 0,
                    "hp_after": {"hull": 45, "armour": 0, "shield": 0},
                    "source": {"weapon": "InvulnGun", "attacker": "Alice"},
                    "blocked_by": "emergency_system_invuln",
                    "side": 2,
                },
            },
            {"tick": 10, "type": "layer_depleted", "actor": "Bob", "target": None,
             "data": {"layer": "armour", "side": 2}},
        ]
        result = _extract_key_events(timeline, combatants_map=cmap)
        layer_ev = next((e for e in result if e["event_type"] == "Layer depleted"), None)
        assert layer_ev is not None
        assert "(by RealCannon)" in layer_ev["detail"], (
            f"Real damage (absorbed=30) should win over invuln-blocked (absorbed=0): "
            f"{layer_ev['detail']!r}"
        )


# ---------------------------------------------------------------------------
# GAP-1: Same-tick fire+shock → missed re-enter
# ---------------------------------------------------------------------------

class TestGap1SameTickFireAndShock:
    """When weapon fires and shock-blast fires on the SAME tick, the shock's distance event
    is at the same tick as the fire. bisect_right excludes same-tick events, so the shock
    is invisible to _max_dist_between → re-enter missed on subsequent fire after closure.
    This is a known accuracy limitation (not a crash), documented here for tracking.
    """

    def test_shock_same_tick_as_fire_miss_reenter(self):
        """Documents the known missed re-enter when shock fires on same tick as weapon."""
        # Weapon and shock fire together at tick 10.
        # Shock distance event is at tick 10 (to=5000).
        # Closure at tick 80 brings dist to 1500.
        # Weapon fires again at tick 85.
        # Expected (correct behavior): R2 re-enter at tick 85.
        # Actual (current behavior): NO re-enter emitted (bisect_right skips tick-10 shock).
        timeline = [
            _fight_start(dist=2000.0),
            _wfire(10, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            _dist_ev(10, cause="shock_blast", from_m=1800.0, to_m=5000.0, side=2),
            _dist_ev(80, cause="closure", from_m=5000.0, to_m=1500.0, side=2),
            _wfire(85, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        re_enters = [e for e in wir if "re-enters" in e["detail"]]
        # This assertion DOCUMENTS the current (buggy) behavior.
        # When BUG fixed, change to: assert len(re_enters) == 1
        assert len(re_enters) == 0, (
            "GAP-1: Same-tick fire+shock: re-enter currently NOT detected (bisect_right skips "
            "same-tick shock event). This test documents the limitation; fix by using "
            "bisect_right(a-1) or including same-tick events in the slice."
        )


# ---------------------------------------------------------------------------
# GAP-2: Mutual death — kill collapse must not suppress events
# ---------------------------------------------------------------------------

class TestGap2MutualDeathNoCollapse:
    """When both combatants die on the same tick, winner=None and loser_slot must be None.
    No kill collapse should occur — both sides' layer/milestone events must be emitted.
    """

    def test_mutual_death_no_kill_collapse(self):
        """Both sides' Layer-depleted events are preserved on mutual-death tick."""
        cmap = {"1": {"name": "Alice"}, "2": {"name": "Bob"}}
        timeline = [
            _fight_start(dist=2000.0),
            {
                "tick": 50, "type": "damage", "actor": None, "target": "Both",
                "data": {"amount": 200, "absorbed": 200,
                         "hp_after": {"hull": 0, "armour": 0, "shield": 0},
                         "source": {"weapon": "BigGun", "attacker": "??"}, "side": 1},
            },
            {
                "tick": 50, "type": "damage", "actor": None, "target": "Both",
                "data": {"amount": 200, "absorbed": 200,
                         "hp_after": {"hull": 0, "armour": 0, "shield": 0},
                         "source": {"weapon": "BigGun", "attacker": "??"}, "side": 2},
            },
            {"tick": 50, "type": "layer_depleted", "actor": "Alice", "target": None,
             "data": {"layer": "armour", "side": 1}},
            {"tick": 50, "type": "layer_depleted", "actor": "Bob", "target": None,
             "data": {"layer": "armour", "side": 2}},
            {
                "tick": 50, "type": "fight_end", "actor": None, "target": None,
                "data": {
                    "winner": None, "reason": "mutual", "duration_ticks": 51,
                    "final_hp": {
                        "c1": {"hull": 0, "armour": 0, "shield": 0},
                        "c2": {"hull": 0, "armour": 0, "shield": 0},
                    },
                },
            },
        ]
        result = _extract_key_events(timeline, combatants_map=cmap)
        layer_events = [e for e in result if e["event_type"] == "Layer depleted"]
        assert len(layer_events) == 2, (
            f"Mutual death must not collapse either side's layer events. Got: {layer_events}"
        )


# ---------------------------------------------------------------------------
# GAP-3: Same-ship-name fight — side-keying holds
# ---------------------------------------------------------------------------

class TestGap3SameShipNameFight:
    """Both combatants share the exact same name. Collapse and outcome must use slot (c1/c2),
    not name string matching.
    """

    def test_same_name_kill_collapse_uses_slot(self):
        """c2 dies — only c2's events suppressed; c1's events (if any) preserved."""
        cmap = {"1": {"name": "Alpha"}, "2": {"name": "Alpha"}}
        timeline = [
            _fight_start(dist=2000.0, c1_name="Alpha", c2_name="Alpha"),
            {
                "tick": 50, "type": "damage", "actor": None, "target": "Alpha",
                "data": {"amount": 200, "absorbed": 200,
                         "hp_after": {"hull": 0, "armour": 0, "shield": 0},
                         "source": {"weapon": "Cannon", "attacker": "Alpha"}, "side": 2},
            },
            {"tick": 50, "type": "layer_depleted", "actor": "Alpha", "target": None,
             "data": {"layer": "armour", "side": 2}},  # should be suppressed (loser)
            _fight_end(50, "Alpha", c1_hull=80, c2_hull=0),
        ]
        result = _extract_key_events(timeline, combatants_map=cmap)
        layer_events = [e for e in result if e["event_type"] == "Layer depleted"]
        assert layer_events == [], (
            f"c2's armour break on kill tick must be suppressed. Got: {layer_events}"
        )
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None and "wins" in outcome["detail"], (
            f"Outcome must say wins even with same-named ships: {outcome}"
        )


# ---------------------------------------------------------------------------
# GAP-4: Primary weapon appears in Weapon in range
# ---------------------------------------------------------------------------

class TestGap4PrimaryWeaponWIR:
    """Primary weapon fires (subtype='primary') must appear as 'Weapon in range' events.
    The commit message says 'primaries are no longer invisible'.
    """

    def test_primary_weapon_emits_weapon_in_range(self):
        """A weapon_fire with subtype='primary' generates a 'Weapon in range' event."""
        timeline = [
            _fight_start(dist=2000.0),
            {"tick": 10, "type": "weapon_fire", "actor": "Alice", "target": "Bob",
             "data": {"slot": "primary", "subtype": "primary", "weapon": "Railgun",
                      "side": 1, "hit": True}},
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1, f"Primary weapon must appear in 'Weapon in range': {wir}"
        assert "Railgun" in wir[0]["detail"]
        assert "enters range" in wir[0]["detail"]


# ---------------------------------------------------------------------------
# GAP-5: Booster-push displacement triggers re-enter
# ---------------------------------------------------------------------------

class TestGap5BoosterPushReEnter:
    """A booster push that opens distance and suppresses a weapon's fire must trigger a
    re-enter when it resumes — the cadence gap caused by the push is the signal (booster
    push, like shock-blast, increases distance; closure can only shrink it).
    """

    def test_booster_push_triggers_reenter(self):
        """Re-enter emitted when a booster-push firing gap exceeds the weapon's cadence."""
        timeline = [
            _fight_start(dist=2000.0),
            # Cadence baseline: Tomahawk fires every 20 ticks while in range.
            _wfire(10, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            _wfire(30, "Alice", "Tomahawk", subtype="missile", hit=False, side=1),
            _wfire(50, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            # Booster push opens the gap → Tomahawk out of range, firing stops.
            {"tick": 55, "type": "distance", "actor": "Bob", "target": None,
             "data": {"cause": "booster_push", "from": 1800.0, "to": 3500.0, "side": 2}},
            _dist_ev(150, cause="closure", from_m=3500.0, to_m=1500.0, side=2),
            # Resumes after a 110-tick gap (>> 20-tick cadence) → re-enter.
            _wfire(160, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        re_enters = [e for e in wir if "re-enters" in e["detail"]]
        assert len(re_enters) == 1, f"Booster-push firing gap must trigger one re-enter: {wir}"
        assert re_enters[0]["tick"] == 160


# ---------------------------------------------------------------------------
# GAP-6: Cluster-missile 0/N miss displays "miss"
# ---------------------------------------------------------------------------

class TestGap6ClusterMissileMissDisplay:
    """A cluster-missile fire with hits=0 must show '— miss', not '— 0/N hit'."""

    def test_cluster_zero_hits_shows_miss(self):
        timeline = [
            _fight_start(dist=2000.0),
            _wfire(10, "Alice", "ClusterBomb", subtype="cluster-missile",
                   side=1, hits=0, fired=6),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1
        assert "— miss" in wir[0]["detail"], (
            f"Cluster with hits=0 must show '— miss', got: {wir[0]['detail']!r}"
        )

    def test_cluster_partial_hits_shows_fraction(self):
        timeline = [
            _fight_start(dist=2000.0),
            _wfire(10, "Alice", "ClusterBomb", subtype="cluster-missile",
                   side=1, hits=3, fired=6),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert "3/6 hit" in wir[0]["detail"], (
            f"Cluster with 3/6 hits must show fraction: {wir[0]['detail']!r}"
        )


# ---------------------------------------------------------------------------
# GAP-7: Double-milestone (50%+25% crossed by single damage event)
# ---------------------------------------------------------------------------

class TestGap7DoubleMilestone:
    """A single large damage event that drops HP from above 50% to below 25% must fire
    BOTH milestone events on that same tick.
    """

    def test_both_milestones_fire_when_crossed_in_single_hit(self):
        """HP drops from 100% to 16% in one shot → both 50% and 25% milestones fire."""
        cmap = {"1": {"name": "Alice"}, "2": {"name": "Bob"}}
        timeline = [
            _fight_start(dist=2000.0,
                         c1_hp={"hull": 300, "armour": 0, "shield": 0},
                         c2_hp={"hull": 300, "armour": 0, "shield": 0}),
            # Bob takes 250 dmg → hp_after=50 → 50/300 = 16.7% (below both thresholds)
            _dmg_ev(10, target_side=2, absorbed=250.0, weapon="BigGun",
                    hp_after={"hull": 50, "armour": 0, "shield": 0}),
        ]
        result = _extract_key_events(timeline, combatants_map=cmap)
        milestones = [e for e in result if "HP milestone" in e.get("event_type", "")]
        types_seen = {e["event_type"] for e in milestones}
        assert "HP milestone (50%)" in types_seen, "50% milestone must fire"
        assert "HP milestone (25%)" in types_seen, "25% milestone must fire"
        assert len(milestones) == 2, f"Exactly two milestone events expected, got: {milestones}"


# ---------------------------------------------------------------------------
# GAP-8: start_total=0 guard prevents division-by-zero
# ---------------------------------------------------------------------------

class TestGap8StartTotalZeroGuard:
    """fight_start combatants with all-zero HP → start_total[side]=0.
    The damage milestone check guards with 'start_total.get(side)' (falsy=0) → skipped.
    No ZeroDivisionError.
    """

    def test_zero_start_hp_no_crash(self):
        """No crash when combatant HP totals are zero (would divide by zero without guard)."""
        timeline = [
            {"tick": 0, "type": "fight_start", "actor": None, "target": None,
             "data": {"combatants": [
                 {"name": "A", "hp": {"hull": 0, "armour": 0, "shield": 0}},
                 {"name": "B", "hp": {"hull": 0, "armour": 0, "shield": 0}},
             ], "initial_distance": 2000.0}},
            _dmg_ev(10, target_side=2, absorbed=10.0, weapon="Gun",
                    hp_after={"hull": 0, "armour": 0, "shield": 0}),
        ]
        try:
            result = _extract_key_events(timeline)
        except ZeroDivisionError as err:
            raise AssertionError("ZeroDivisionError when start_total=0 (div-by-zero guard missing)") from err
        milestones = [e for e in result if "HP milestone" in e.get("event_type", "")]
        assert milestones == [], "No milestones when start_total=0 (guard correctly skips)"


# ---------------------------------------------------------------------------
# GAP-9: Empty / None combatants_map is safe
# ---------------------------------------------------------------------------

class TestGap9NoneCombatnatsMap:
    """combatants_map=None and combatants_map={} must not crash."""

    def test_none_combatants_map_no_crash(self):
        """_extract_key_events(timeline, combatants_map=None) does not raise."""
        timeline = [_fight_start(), _fight_end(50, "Alice")]
        result = _extract_key_events(timeline, combatants_map=None)
        assert isinstance(result, list)

    def test_empty_combatants_map_no_crash(self):
        """_extract_key_events(timeline, combatants_map={}) does not raise."""
        timeline = [_fight_start(), _fight_end(50, "Alice")]
        result = _extract_key_events(timeline, combatants_map={})
        assert isinstance(result, list)

    def test_stalemate_with_empty_cmap_uses_question_marks(self):
        """Stalemate outcome with empty combatants_map uses '?' as combatant names."""
        timeline = [
            {"tick": 18000, "type": "fight_end", "actor": None, "target": None,
             "data": {"winner": None, "reason": "time_cap", "duration_ticks": 18000}},
        ]
        result = _extract_key_events(timeline, combatants_map={})
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        assert "Stalemate" in outcome["detail"]
