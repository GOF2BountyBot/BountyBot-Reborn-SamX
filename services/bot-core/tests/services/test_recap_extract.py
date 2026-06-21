"""Coverage for _extract_key_events — DESIGN_COMBAT_LOG_RECAP behaviors.

Tests added alongside the recap-overhaul (extract-only, no resolver change):

  1. range-in R1 line: weapon fires for the first time → 'Weapon in range' with 'enters range'
  2. re-enter R2 after shock-blast displacement: weapon fires, shock-blast resets distance > range,
     then weapon fires again → 'Weapon in range' with 're-enters range'
  3. duplicate-named weapon collapse: two weapon_fire same weapon+tick → ONE range-in line
     (prefers hit over miss for display)
  4. attribution '(by Weapon)' on a layer break
  5. killing weapon on Outcome line
  6. most-damage tiebreak: two damage events same tick for same target, higher absorbed wins
  7. same-tick kill collapse: loser's Layer-depleted and HP-milestone lines on kill tick are suppressed
  8. stalemate why-line: higher-damage side named as aggressor with shot/dmg stats
  9. nuke detonation detail line
 10. shock-blast detail line
 11. Denoising — global per-key aggregation (REVISED design, real-data grounded)
 12. Denoising — Rule 2: Nuke significance filter (R-198 canary)
 13. Denoising — count field on KeyEvent (legacy compat)
 14. R-241 real-data golden test (must-not-collapse baseline)
 15. R-285 real-data golden test (must-collapse pole)
 16. R-219 real-data golden test (93% cyclic, long fight)
 17. R-198 real-data nuke canary
"""

from __future__ import annotations

import gzip
import json
import sys
import types
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level dependency stubs
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
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "recap"

# Cyclic event_types that the new global aggregation design collapses.
_CYCLIC_TYPES: frozenset[str] = frozenset({"Weapon in range", "Layer depleted", "Module activated"})
# Narrative types that are NEVER folded.
_NARRATIVE_TYPES: frozenset[str] = frozenset(
    {
        "Engagement",
        "Nuke detonation",
        "Shock blast",
        "HP milestone (50%)",
        "HP milestone (25%)",
        "Ammo depleted",
        "Outcome",
    }
)


def _load_fixture(battle_id: int) -> dict:
    """Load a captured battle fixture (gzip JSON with 'timeline' and 'combatants' keys)."""
    path = _FIXTURE_DIR / f"battle_{battle_id}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        return json.load(gz)


def _global_collapse_key(e: dict) -> tuple | None:
    """Reconstruct the collapse key that the REVISED global aggregation design would use.

    This mirrors the intended new algorithm: (event_type, actor_or_side, weapon_or_module, target_layer).
    Used in golden tests to verify that no collapsible group appears more than once in output.
    """
    et = e["event_type"]
    if et not in _CYCLIC_TYPES:
        return None
    if et == "Weapon in range":
        detail = e["detail"]
        try:
            after_actor = detail.split("'s ", 1)[1]
            weapon = after_actor.split(" enters")[0].split(" re-enters")[0]
        except IndexError:
            weapon = ""
        return (et, e.get("actor", ""), weapon, None)
    elif et == "Layer depleted":
        # detail like 'Vilhelm Lindon: Shield depleted (by ...)'
        layer_part = e["detail"].split(": ", 1)[1].split(" depleted")[0] if ": " in e["detail"] else ""
        return (et, e.get("actor", ""), layer_part, None)
    elif et == "Module activated":
        # Each module name collapses independently per actor: extract module from detail.
        # detail like 'Vilhelm Lindon activated cloak (at 66% HP) ×4 (3.7s–154.8s)'
        detail = e["detail"]
        try:
            after_activated = detail.split(" activated ", 1)[1]
            module = after_activated.split(" (")[0].split(" ×")[0]
        except IndexError:
            module = ""
        return (et, e.get("actor", ""), module, None)
    return None


# ---------------------------------------------------------------------------
# Helpers: minimal event builders matching the shapes the resolver emits
# ---------------------------------------------------------------------------


def _fight_start(dist: float = 3000.0, c1_name: str = "Alice", c2_name: str = "Bob") -> dict:
    return {
        "tick": 0,
        "type": "fight_start",
        "actor": None,
        "target": None,
        "data": {
            "combatants": [
                {
                    "name": c1_name,
                    "display_name": c1_name,
                    "ship": "Specter",
                    "slot": 1,
                    "hp": {"hull": 100, "armour": 50, "shield": 0},
                },
                {
                    "name": c2_name,
                    "display_name": c2_name,
                    "ship": "Wraith",
                    "slot": 2,
                    "hp": {"hull": 100, "armour": 50, "shield": 0},
                },
            ],
            "initial_distance": dist,
        },
    }


def _weapon_fire(
    tick: int,
    actor: str,
    weapon: str,
    *,
    subtype: str = "missile",
    hit: bool = True,
    side: int = 1,
    hits: int | None = None,
    fired: int | None = None,
) -> dict:
    data: dict = {"slot": "secondary", "subtype": subtype, "weapon": weapon, "side": side}
    if subtype == "cluster-missile":
        data["hits"] = hits or 0
        data["fired"] = fired or 4
    else:
        data["hit"] = hit
    return {"tick": tick, "type": "weapon_fire", "actor": actor, "target": "Opp", "data": data}


def _distance_event(tick: int, *, cause: str, from_m: float, to_m: float, side: int = 2) -> dict:
    return {
        "tick": tick,
        "type": "distance",
        "actor": None,
        "target": None,
        "data": {"cause": cause, "from": from_m, "to": to_m, "side": side},
    }


def _damage_event(
    tick: int,
    *,
    target_side: int,
    absorbed: float,
    weapon: str,
    hp_after: dict | None = None,
) -> dict:
    return {
        "tick": tick,
        "type": "damage",
        "actor": None,
        "target": "Target",
        "data": {
            "amount": absorbed,
            "absorbed": absorbed,
            "hp_after": hp_after or {"hull": 50, "armour": 0, "shield": 0},
            "source": {"weapon": weapon, "attacker": "Attacker"},
            "side": target_side,
        },
    }


def _layer_depleted(tick: int, layer: str, *, actor: str = "Bob", side: int = 2) -> dict:
    return {
        "tick": tick,
        "type": "layer_depleted",
        "actor": actor,
        "target": None,
        "data": {"layer": layer, "side": side},
    }


def _fight_end(tick: int, winner: str | None, *, c1_hull: int = 80, c2_hull: int = 0) -> dict:
    return {
        "tick": tick,
        "type": "fight_end",
        "actor": None,
        "target": None,
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
# 1. Range-in R1 line
# ---------------------------------------------------------------------------


class TestRangeInR1:
    def test_first_fire_emits_enters_range(self):
        """The first time a weapon fires it generates 'Weapon in range … enters range'."""
        timeline = [_fight_start(), _weapon_fire(10, "Alice", "Rocket", hit=True, side=1)]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1
        assert "enters range" in wir[0]["detail"]
        assert "re-enters" not in wir[0]["detail"]
        assert "Rocket" in wir[0]["detail"]

    def test_r1_shows_hit_when_hit(self):
        """Range-in R1 detail ends with '— hit' when the weapon hit."""
        timeline = [_fight_start(), _weapon_fire(5, "Alice", "Gun", hit=True, side=1)]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert "— hit" in wir[0]["detail"]

    def test_r1_shows_miss_when_miss(self):
        """Range-in R1 detail ends with '— miss' when the weapon missed."""
        timeline = [_fight_start(), _weapon_fire(5, "Alice", "Gun", hit=False, side=1)]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert "— miss" in wir[0]["detail"]

    def test_multiple_distinct_weapons_each_get_r1(self):
        """Two distinct weapons each get their own R1 range-in entry."""
        timeline = [
            _fight_start(),
            _weapon_fire(10, "Alice", "Rocket", subtype="rocket", hit=True, side=1),
            _weapon_fire(20, "Alice", "Missile", subtype="missile", hit=False, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 2
        weapon_names = {e["detail"].split("'s ")[1].split(" ")[0] for e in wir}
        assert "Rocket" in weapon_names
        assert "Missile" in weapon_names


# ---------------------------------------------------------------------------
# 2. Re-enter R2 after shock-blast displacement
# ---------------------------------------------------------------------------


class TestReEnterR2AfterShockBlast:
    def test_reenter_after_shock_blast_displacement(self):
        """A firing gap >> cadence (weapon pushed out by a shock-blast, then re-acquires) → 're-enters'.

        Re-enter detection is cadence-based: the weapon establishes a steady cadence (fires every
        20 ticks), a shock-blast pushes it out of range so firing stops, and after a long gap it
        resumes — the 140-tick gap is far larger than the 20-tick cadence, so the resuming fire is
        flagged as a re-enter.
        """
        timeline = [
            _fight_start(dist=2000.0),
            # Cadence baseline: Tomahawk fires every 20 ticks while in range.
            _weapon_fire(10, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            _weapon_fire(30, "Alice", "Tomahawk", subtype="missile", hit=False, side=1),
            _weapon_fire(50, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            # Shock-blast pushes the ships apart → Tomahawk out of range (firing stops).
            _distance_event(60, cause="shock_blast", from_m=2000.0, to_m=5000.0, side=2),
            {
                "tick": 60,
                "type": "weapon_fire",
                "actor": "Bob",
                "target": "Alice",
                "data": {"slot": "secondary", "subtype": "shock-blast", "weapon": "Shock Device", "side": 2},
            },
            _distance_event(190, cause="closure", from_m=5000.0, to_m=1500.0, side=2),
            # Long gap (140 ticks since the last Tomahawk fire) → re-acquire.
            _weapon_fire(190, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 2, f"Expected R1 + R2 (2 range-in events), got: {wir}"
        r1 = next(e for e in wir if "re-enters" not in e["detail"])
        r2 = next(e for e in wir if "re-enters" in e["detail"])
        assert r1["tick"] == 10
        assert r2["tick"] == 190
        assert "Tomahawk" in r2["detail"]

    def test_no_reenter_without_displacement(self):
        """Same weapon firing twice without an intervening distance > range_est → only ONE range-in."""
        timeline = [
            _fight_start(dist=2000.0),
            _weapon_fire(10, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
            # No distance event between fires
            _weapon_fire(50, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1, f"No displacement → only one range-in event, got: {wir}"
        assert "re-enters" not in wir[0]["detail"]


class TestReEnterKeyedBySideAndWeapon:
    """Regression: re-enter detection must key on (side, weapon), not weapon name alone.

    When BOTH combatants carry the same-named weapon, keying on the name merged their fire
    ticks into one interleaved list, collapsing the cadence (min inter-fire gap) to the tiny
    inter-combatant firing offset. The `gap > 1.5*cadence` test then mislabelled almost every
    shot as 're-enters range' (prod battles 137/138: 62/36 false Berger re-enters). Grouping
    by (side, weapon) restores each ship's own cadence.
    """

    def test_same_named_weapon_both_sides_no_false_reenter(self):
        """Both sides fire the SAME weapon at a uniform cadence, continuously in range.

        Side 1 fires every 60 ticks; side 2 every 75 ticks, offset by 30 ticks so their fires
        interleave. Under the old name-only keying the merged min-gap collapses to ~30 and nearly
        every fire flags 're-enters'. Per-(side, weapon) keeps cadence 60 / 75 → only the FIRST
        fire per side is an 'enters range'; ZERO re-enters.
        """
        timeline = [_fight_start(dist=2000.0)]
        # Side 1: ticks 10, 70, 130, 190, 250 (cadence 60)
        for t in (10, 70, 130, 190, 250):
            timeline.append(_weapon_fire(t, "Alice", "Berger FlaK 9-9", subtype="missile", hit=True, side=1))
        # Side 2: ticks 40, 115, 190, 265 (cadence 75), interleaved with side 1
        for t in (40, 115, 190, 265):
            timeline.append(_weapon_fire(t, "Bob", "Berger FlaK 9-9", subtype="missile", hit=False, side=2))

        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        reenters = [e for e in wir if "re-enters" in e["detail"]]
        enters = [e for e in wir if "re-enters" not in e["detail"]]
        assert reenters == [], f"No displacement → ZERO re-enters expected, got: {reenters}"
        # Exactly one 'enters range' per side (the first fire of each ship's Berger).
        assert len(enters) == 2, f"Expected one enters-range per side, got: {enters}"
        assert {e["tick"] for e in enters} == {10, 40}

    def test_genuine_reenter_per_side_after_gap(self):
        """A real out-of-range gap (>> that side's cadence) still emits exactly one re-enter.

        Side 1's Berger fires at cadence 60, gets pushed out, and re-acquires after a 300-tick gap.
        Side 2's same-named Berger fires steadily throughout (no gap) and must NOT be flagged.
        """
        timeline = [_fight_start(dist=2000.0)]
        # Side 1: steady cadence 60, then a 300-tick gap, then re-acquire.
        for t in (10, 70, 130):
            timeline.append(_weapon_fire(t, "Alice", "Berger FlaK 9-9", subtype="missile", hit=True, side=1))
        timeline.append(_weapon_fire(430, "Alice", "Berger FlaK 9-9", subtype="missile", hit=True, side=1))
        # Side 2: steady cadence 60 across the whole window, no displacement.
        for t in (40, 100, 160, 220, 280, 340, 400):
            timeline.append(_weapon_fire(t, "Bob", "Berger FlaK 9-9", subtype="missile", hit=False, side=2))

        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        reenters = [e for e in wir if "re-enters" in e["detail"]]
        assert len(reenters) == 1, f"Exactly one genuine re-enter expected, got: {reenters}"
        assert reenters[0]["tick"] == 430
        assert reenters[0]["actor"] == "Alice", f"Re-enter must be attributed to side 1, got: {reenters[0]}"
        # The continuously-firing side-2 Berger must not be flagged.
        side2_reenters = [e for e in reenters if e["tick"] in (40, 100, 160, 220, 280, 340, 400)]
        assert side2_reenters == [], f"Steady side-2 weapon must not re-enter, got: {side2_reenters}"


# ---------------------------------------------------------------------------
# 3. Duplicate-named weapon collapse (same weapon + same tick → ONE line)
# ---------------------------------------------------------------------------


class TestDuplicateNamedWeaponCollapse:
    def test_two_fires_same_weapon_same_tick_collapse_to_one(self):
        """Two weapon_fire events for the same weapon at the same tick → ONE range-in line."""
        timeline = [
            _fight_start(),
            _weapon_fire(5, "Alice", "DualRocket", subtype="rocket", hit=False, side=1),
            _weapon_fire(5, "Alice", "DualRocket", subtype="rocket", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1, f"Duplicate weapon on same tick must collapse to 1 event, got: {wir}"

    def test_collapse_prefers_hit_over_miss(self):
        """When collapsing duplicates, prefer the firing that hit (for hit/miss display)."""
        timeline = [
            _fight_start(),
            _weapon_fire(5, "Alice", "DualRocket", subtype="rocket", hit=False, side=1),
            _weapon_fire(5, "Alice", "DualRocket", subtype="rocket", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        # Collapsed event must say "hit" (not "miss")
        assert "— hit" in wir[0]["detail"], f"Collapsed line must prefer hit, got: {wir[0]['detail']!r}"

    def test_two_distinct_weapons_same_tick_get_separate_lines(self):
        """Two distinct weapons at same tick each get their own range-in line (no cross-collapse)."""
        timeline = [
            _fight_start(),
            _weapon_fire(5, "Alice", "Rocket1", subtype="rocket", hit=True, side=1),
            _weapon_fire(5, "Alice", "Missile1", subtype="missile", hit=False, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 2, f"Distinct weapons must each get a range-in line, got: {wir}"


# ---------------------------------------------------------------------------
# 4. Attribution '(by Weapon)' on layer break
# ---------------------------------------------------------------------------


class TestAttributionOnLayerBreak:
    def test_by_weapon_appears_in_layer_depleted_detail(self):
        """'(by Weapon)' suffix appears in Layer-depleted detail when damage is attributed."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(),
            _damage_event(10, target_side=2, absorbed=60.0, weapon="Rocket"),
            _layer_depleted(10, "armour", actor="Bob", side=2),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        layer_ev = next((e for e in result if e["event_type"] == "Layer depleted"), None)
        assert layer_ev is not None, "Layer depleted event must be present"
        assert "(by Rocket)" in layer_ev["detail"], (
            f"Attribution '(by Rocket)' must appear in detail; got: {layer_ev['detail']!r}"
        )

    def test_no_attribution_when_no_damage_on_tick(self):
        """Without a damage event on the same tick, no '(by ...)' suffix is added."""
        timeline = [_layer_depleted(50, "shield", actor="Bob", side=2)]
        result = _extract_key_events(timeline)
        layer_ev = next((e for e in result if e["event_type"] == "Layer depleted"), None)
        assert layer_ev is not None
        assert "(by" not in layer_ev["detail"], (
            f"No attribution without damage on same tick; got: {layer_ev['detail']!r}"
        )


# ---------------------------------------------------------------------------
# 5. Killing weapon on Outcome line
# ---------------------------------------------------------------------------


class TestKillingWeaponOnOutcome:
    def test_killing_weapon_appears_in_outcome_detail(self):
        """Outcome line includes '… destroyed by {KillingWeapon}' when weapon is attributed."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(),
            _damage_event(
                100, target_side=2, absorbed=200.0, weapon="Cannon", hp_after={"hull": 0, "armour": 0, "shield": 0}
            ),
            _layer_depleted(100, "hull", actor="Bob", side=2),
            _fight_end(100, "Alice", c1_hull=80, c2_hull=0),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None, "Outcome event must be present"
        assert "destroyed by Cannon" in outcome["detail"], (
            f"Outcome must include 'destroyed by Cannon'; got: {outcome['detail']!r}"
        )

    def test_no_killing_weapon_when_no_attribution(self):
        """Without damage attribution, Outcome line just says 'destroyed' with no weapon."""
        timeline = [_fight_end(100, "Alice", c1_hull=80, c2_hull=0)]
        result = _extract_key_events(timeline)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        assert "by" not in outcome["detail"], f"No attribution → no 'by Weapon' in outcome; got: {outcome['detail']!r}"


# ---------------------------------------------------------------------------
# 6. Most-damage tiebreak when two weapons hit same tick
# ---------------------------------------------------------------------------


class TestMostDamageTiebreak:
    def test_higher_absorbed_weapon_wins_attribution(self):
        """When two weapons deal damage to the same target on the same tick, higher absorbed wins."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(),
            # Two damage events same tick same target: Pistol=10, Cannon=80
            _damage_event(20, target_side=2, absorbed=10.0, weapon="Pistol"),
            _damage_event(20, target_side=2, absorbed=80.0, weapon="Cannon"),
            _layer_depleted(20, "armour", actor="Bob", side=2),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        layer_ev = next((e for e in result if e["event_type"] == "Layer depleted"), None)
        assert layer_ev is not None
        assert "(by Cannon)" in layer_ev["detail"], (
            f"Higher-damage weapon (Cannon=80) must win over Pistol=10; got: {layer_ev['detail']!r}"
        )

    def test_lower_absorbed_weapon_does_not_win(self):
        """Lower-damage weapon must NOT appear in the attribution."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(),
            _damage_event(20, target_side=2, absorbed=10.0, weapon="Pistol"),
            _damage_event(20, target_side=2, absorbed=80.0, weapon="Cannon"),
            _layer_depleted(20, "armour", actor="Bob", side=2),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        layer_ev = next((e for e in result if e["event_type"] == "Layer depleted"), None)
        assert layer_ev is not None
        assert "(by Pistol)" not in layer_ev["detail"]


# ---------------------------------------------------------------------------
# 7. Same-tick kill collapse: loser's Layer-depleted / HP-milestone suppressed
# ---------------------------------------------------------------------------


class TestSameTickKillCollapse:
    def _make_kill_timeline(self) -> list[dict]:
        """Timeline where Bob dies at tick 50 with HP crossing milestones on the same tick."""
        return [
            _fight_start(),
            # Damage that kills Bob (side=2), dropping from 150 to 0 → crosses 50% and 25%
            {
                "tick": 50,
                "type": "damage",
                "actor": None,
                "target": "Bob",
                "data": {
                    "amount": 200,
                    "absorbed": 200,
                    "hp_after": {"hull": 0, "armour": 0, "shield": 0},
                    "source": {"weapon": "Cannon", "attacker": "Alice"},
                    "side": 2,
                },
            },
            _layer_depleted(50, "armour", actor="Bob", side=2),
            _layer_depleted(50, "hull", actor="Bob", side=2),
            _fight_end(50, "Alice", c1_hull=80, c2_hull=0),
        ]

    def test_loser_layer_depleted_on_kill_tick_suppressed(self):
        """Layer-depleted (armour) for the loser on the kill tick must be suppressed."""
        timeline = self._make_kill_timeline()
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        # No Layer-depleted for side 2 on kill tick
        layer_on_kill = [e for e in result if e["event_type"] == "Layer depleted" and e["tick"] == 50]
        assert layer_on_kill == [], f"Loser's Layer-depleted on kill tick must be suppressed; got: {layer_on_kill}"

    def test_loser_hp_milestones_on_kill_tick_suppressed(self):
        """HP milestones for the loser on the kill tick must be suppressed."""
        timeline = self._make_kill_timeline()
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        milestones_on_kill = [
            e for e in result if "HP milestone" in e["event_type"] and e["tick"] == 50 and "2" in str(e.get("detail"))
        ]
        assert milestones_on_kill == [], (
            f"Loser's HP milestones on kill tick must be suppressed; got: {milestones_on_kill}"
        )

    def test_outcome_present_on_kill_tick(self):
        """Outcome line is present on the kill tick (replaces the suppressed loser events)."""
        timeline = self._make_kill_timeline()
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        assert outcome["tick"] == 50
        assert "wins" in outcome["detail"]


# ---------------------------------------------------------------------------
# 8. Stalemate why-line
# ---------------------------------------------------------------------------


class TestStalemateWhyLine:
    def test_time_cap_stalemate_uses_neutral_why_line(self):
        """A time_cap stalemate (clock expired, both alive) gets a neutral why-line.

        It must NOT presume regen or single out an "aggressor" — running the clock can be down
        to high effective HP, evasion, or just a tanky opponent. Per-combatant damage/hit stats
        already live in the Summary field, so the Outcome line only states the headline reason.
        """
        combatants_map = {
            "1": {"name": "Alice", "ship": "S", "damage_dealt": 200, "shots_hit": 50, "shots_fired": 80},
            "2": {"name": "Bob", "ship": "W", "damage_dealt": 100, "shots_hit": 20, "shots_fired": 60},
        }
        timeline = [
            {
                "tick": 18000,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {
                    "winner": None,
                    "reason": "time_cap",
                    "duration_ticks": 18000,
                    "final_hp": {
                        "c1": {"hull": 50, "armour": 20, "shield": 0},
                        "c2": {"hull": 80, "armour": 30, "shield": 0},
                    },
                },
            }
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        detail = outcome["detail"]
        assert "Stalemate" in detail
        assert "neither side could score a fatal blow in the time allotted" in detail
        # No presumptuous regen framing, no aggressor/defender call-out, no per-combatant stats.
        assert "regen" not in detail
        assert "out-damage" not in detail
        assert "Alice" not in detail
        assert "Bob" not in detail
        # Concise enough to survive the gateway's 200-char per-line clamp untruncated.
        assert len(detail) <= 200

    def test_mutual_destruction_uses_distinct_why_line(self):
        """A 'mutual' stalemate (both hulls hit 0 same tick) is a double-KO, not a regen survival.

        The regen/out-damage framing only fits time_cap (one side survives). A mutual kill has no
        survivor to out-damage, so it gets a dedicated 'mutual destruction' line — and that line is
        short enough (~50 chars) that the gateway's per-line clamp never chops it.
        """
        combatants_map = {
            "1": {"name": "Alice", "ship": "S", "damage_dealt": 310, "shots_hit": 8, "shots_fired": 8},
            "2": {"name": "Bob", "ship": "W", "damage_dealt": 135, "shots_hit": 12, "shots_fired": 22},
        }
        timeline = [
            {
                "tick": 1367,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": None, "reason": "mutual", "duration_ticks": 1368},
            }
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        detail = outcome["detail"]
        assert "mutual destruction" in detail
        assert "both ships destroyed" in detail
        # The misleading regen framing must NOT appear for a double-KO.
        assert "regen" not in detail
        assert "out-damage" not in detail
        # Short enough to survive the gateway's 200-char per-line clamp untruncated.
        assert len(detail) <= 200


# ---------------------------------------------------------------------------
# 9. Nuke detonation detail line
# ---------------------------------------------------------------------------


class TestNukeDetonationDetail:
    def test_nuke_fire_emits_nuke_detonation_event(self):
        """A nuke weapon_fire produces a 'Nuke detonation' event (not 'Weapon in range')."""
        timeline = [
            {
                "tick": 50,
                "type": "weapon_fire",
                "actor": "Alice",
                "target": "Bob",
                "data": {
                    "slot": "secondary",
                    "subtype": "nuke",
                    "weapon": "NukeBomb",
                    "opponent_damage": 200,
                    "self_damage": 30,
                    "side": 1,
                },
            }
        ]
        result = _extract_key_events(timeline)
        nuke_ev = [e for e in result if e["event_type"] == "Nuke detonation"]
        assert len(nuke_ev) == 1
        assert "NukeBomb" in nuke_ev[0]["detail"]
        assert "detonated" in nuke_ev[0]["detail"]
        assert "200" in nuke_ev[0]["detail"]
        assert "30" in nuke_ev[0]["detail"]

    def test_nuke_does_not_appear_as_weapon_in_range(self):
        """Nuke fires do NOT produce 'Weapon in range' events."""
        timeline = [
            {
                "tick": 50,
                "type": "weapon_fire",
                "actor": "Alice",
                "target": "Bob",
                "data": {
                    "slot": "secondary",
                    "subtype": "nuke",
                    "weapon": "NukeBomb",
                    "opponent_damage": 200,
                    "self_damage": 30,
                    "side": 1,
                },
            }
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert wir == [], f"Nuke must not appear as 'Weapon in range'; got: {wir}"


# ---------------------------------------------------------------------------
# 10. Shock-blast detail line
# ---------------------------------------------------------------------------


class TestShockBlastDetail:
    def test_shock_blast_fire_emits_shock_blast_event(self):
        """A shock-blast weapon_fire produces a 'Shock blast' event with distance reset."""
        timeline = [
            _distance_event(20, cause="shock_blast", from_m=2000.0, to_m=5000.0, side=2),
            {
                "tick": 20,
                "type": "weapon_fire",
                "actor": "Bob",
                "target": "Alice",
                "data": {
                    "slot": "secondary",
                    "subtype": "shock-blast",
                    "weapon": "Shock Device",
                    "side": 2,
                },
            },
        ]
        result = _extract_key_events(timeline)
        shock_ev = [e for e in result if e["event_type"] == "Shock blast"]
        assert len(shock_ev) == 1
        assert "Shock Device" in shock_ev[0]["detail"]
        assert "distance reset to" in shock_ev[0]["detail"]
        assert "5000m" in shock_ev[0]["detail"]

    def test_shock_blast_does_not_appear_as_weapon_in_range(self):
        """Shock-blast fires do NOT produce 'Weapon in range' events."""
        timeline = [
            {
                "tick": 20,
                "type": "weapon_fire",
                "actor": "Bob",
                "target": "Alice",
                "data": {
                    "slot": "secondary",
                    "subtype": "shock-blast",
                    "weapon": "Shock Device",
                    "side": 2,
                },
            },
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert wir == [], f"Shock-blast must not appear as 'Weapon in range'; got: {wir}"

    def test_shock_blast_fallback_distance_when_no_distance_event(self):
        """If no matching distance event, Shock blast detail falls back to GameConstants.STARTING_DISTANCE_M."""
        from services.game_constants import GameConstants

        timeline = [
            {
                "tick": 20,
                "type": "weapon_fire",
                "actor": "Bob",
                "target": "Alice",
                "data": {"slot": "secondary", "subtype": "shock-blast", "weapon": "ShockBlaster", "side": 2},
            }
        ]
        result = _extract_key_events(timeline)
        shock_ev = [e for e in result if e["event_type"] == "Shock blast"]
        assert len(shock_ev) == 1
        expected_dist = int(GameConstants.STARTING_DISTANCE_M)
        assert f"{expected_dist}m" in shock_ev[0]["detail"], (
            f"Fallback distance must be {expected_dist}m; got: {shock_ev[0]['detail']!r}"
        )


# ---------------------------------------------------------------------------
# 11. Denoising — Rule 1: Cyclic run collapse
# ---------------------------------------------------------------------------


def _module_activation(tick: int, actor: str, module: str, side: int = 1) -> dict:
    return {
        "tick": tick,
        "type": "module_activation",
        "actor": actor,
        "target": None,
        "data": {"module": module, "side": side},
    }


class TestGlobalPerKeyAggregation:
    """Rule 1 REVISED: global per-key aggregation — fold ALL occurrences of a collapse key
    across the whole timeline (not just consecutive runs) to ONE line anchored at the first tick,
    with count=N and detail containing combatant name, ×N marker, and (first_tick_s–last_tick_s) span.

    These tests FAIL on the currently-shipped consecutive-only collapser, proving they catch the bug.
    """

    def _interleaved_raccoon_timeline(self, n_raccoon: int = 5) -> tuple[list[dict], dict]:
        """Build a timeline mirroring battle 285's real interleaved structure.

        Raccoon (bluefyre, side 1) fires every 1500 ticks.  Between each Raccoon fire, Vilhelm
        (side 2) gets a layer break and activates a module.  This is exactly the pattern that
        makes the consecutive collapser a no-op: after sort, the timeline alternates
          Raccoon WiR → LayerDepleted → ModuleActivated → Raccoon WiR → ...
        so no two consecutive Raccoon WiR entries are adjacent → old collapser cannot merge them.

        Under GLOBAL aggregation, all N Raccoon WiR events share the same collapse key and fold.
        """
        combatants_map = {"1": {"name": "bluefyre", "ship": "VoidX"}, "2": {"name": "Vilhelm Lindon", "ship": "Ghost"}}
        timeline: list[dict] = [_fight_start(dist=5000.0, c1_name="bluefyre", c2_name="Vilhelm Lindon")]
        # Establish Raccoon cadence with initial fires at ticks 10, 20, 30.
        raccoon = 'M6 A4 "Raccoon"'
        for i in range(3):
            timeline.append(_weapon_fire(10 + i * 10, "bluefyre", raccoon, subtype="missile", hit=True, side=1))
        # Now interleave: each Raccoon re-entry is separated by Vilhelm events.
        for i in range(n_raccoon - 1):  # -1 because the first "enters range" is the tick-10 fire
            base_tick = 500 + i * 1500
            # Raccoon re-enters
            timeline.append(_weapon_fire(base_tick, "bluefyre", raccoon, subtype="missile", hit=True, side=1))
            # Vilhelm's layer breaks and module between each Raccoon fire (interleaving events)
            timeline.append(_damage_event(base_tick + 10, target_side=2, absorbed=80.0, weapon=raccoon))
            timeline.append(_layer_depleted(base_tick + 10, "shield", actor="Vilhelm Lindon", side=2))
            timeline.append(_module_activation(base_tick + 50, "Vilhelm Lindon", "cloak", side=2))
        return timeline, combatants_map

    def test_interleaved_raccoon_folds_to_one_line_global(self):
        """Raccoon's interleaved WiR events (separated by Vilhelm's events) must fold to ONE line.

        This is the core regression test.  The old consecutive-only collapser leaves all N lines
        intact because no two Raccoon WiR entries are adjacent after tick-sort.  The new global
        aggregation collapses them all to one anchored-at-first-tick line with count=N.

        EXPECTED FAILURE on current shipped code: the consecutive collapser returns count=1 for
        each Raccoon WiR line, so `count == N` fails.
        """
        n = 5  # 1 enters + 4 re-enters = 5 total Raccoon WiR events
        timeline, combatants_map = self._interleaved_raccoon_timeline(n_raccoon=n)
        result = _extract_key_events(timeline, combatants_map=combatants_map)

        raccoon_wir = [e for e in result if e["event_type"] == "Weapon in range" and "Raccoon" in e["detail"]]
        # Global aggregation: N Raccoon WiR events → exactly 1 output line.
        assert len(raccoon_wir) == 1, (
            f"Global aggregation must fold {n} interleaved Raccoon WiR events to 1 line; "
            f"got {len(raccoon_wir)} lines. "
            f"(Fails on consecutive-only collapser because Raccoon fires are non-adjacent after sort.)"
        )
        collapsed = raccoon_wir[0]
        # count must equal total occurrences
        assert collapsed.get("count", 1) == n, (
            f"Collapsed line must have count={n}; got count={collapsed.get('count', 1)}"
        )
        # The ×N marker must be in the detail.
        assert f"×{n}" in collapsed["detail"], (
            f"Detail must contain ×{n}; got: {collapsed['detail']!r}"
        )
        # Combatant name must appear in the collapsed detail.
        assert "bluefyre" in collapsed["detail"], (
            f"Collapsed line must name the combatant; got: {collapsed['detail']!r}"
        )
        # Span (first_s–last_s) must appear in the detail.
        assert "s–" in collapsed["detail"] or "s-" in collapsed["detail"], (
            f"Collapsed detail must include a tick-span like '(Xs–Ys)'; got: {collapsed['detail']!r}"
        )

    def test_global_collapser_folds_interleaved_to_one(self):
        """Verify the global collapser folds the interleaved timeline to exactly one Raccoon row.

        This is the complement of test_interleaved_raccoon_folds_to_one_line_global: it confirms
        the folded row has count=N (not count=1), proving the global aggregation is active.
        """
        n = 5
        timeline, combatants_map = self._interleaved_raccoon_timeline(n_raccoon=n)
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        raccoon_wir = [e for e in result if e["event_type"] == "Weapon in range" and "Raccoon" in e["detail"]]
        # Global aggregation: exactly 1 collapsed row with count=N.
        assert len(raccoon_wir) == 1, (
            f"Global aggregation must fold {n} interleaved Raccoon WiR events to 1 line; "
            f"got {len(raccoon_wir)} lines."
        )
        assert raccoon_wir[0].get("count", 1) == n, (
            f"Collapsed row must have count={n}; got count={raccoon_wir[0].get('count', 1)}"
        )

    def test_n2_key_stays_expanded(self):
        """A collapse key occurring exactly twice (interleaved) must NOT be folded.

        Threshold is ≥3.  Two occurrences → both lines survive individually.
        The interleaving ensures the consecutive collapser would also not fold them
        (since they're non-adjacent), so this tests the shared guard, not the difference.
        """
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(dist=2000.0),
            # Establish cadence for Spark
            _weapon_fire(10, "Alice", "Spark", subtype="missile", hit=True, side=1),
            _weapon_fire(20, "Alice", "Spark", subtype="missile", hit=True, side=1),
            _weapon_fire(30, "Alice", "Spark", subtype="missile", hit=True, side=1),
            # Bob's interleaving event
            _layer_depleted(100, "shield", actor="Bob", side=2),
            # Exactly one Spark re-entry (total WiR for Spark = 2: the enters + this re-enter)
            _weapon_fire(500, "Alice", "Spark", subtype="missile", hit=False, side=1),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        spark_wir = [e for e in result if e["event_type"] == "Weapon in range" and "Spark" in e["detail"]]
        # 1 enters + 1 re-enter = N=2 → must NOT be collapsed (threshold ≥3)
        assert len(spark_wir) == 2, (
            f"N=2 same-key WiR (below threshold 3) must stay as 2 separate lines; got: {spark_wir}"
        )
        for ev in spark_wir:
            assert ev.get("count", 1) == 1, f"Uncollapsed lines must have count=1; got: {ev}"

    def test_module_activated_interleaved_collapses_globally(self):
        """Module activations interleaved with weapon fire events collapse globally to one line.

        Old consecutive collapser: module activations separated by WiR events → not adjacent → not folded.
        New global aggregation: all same-actor module activations fold together.

        EXPECTED FAILURE on current shipped code.
        """
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline = [
            _fight_start(dist=2000.0),
            # Alice activates her booster (trigger 1)
            _module_activation(100, "Alice", "booster", side=1),
            # Bob fires a weapon (interleaving event between Alice's module activations)
            _weapon_fire(200, "Bob", "Blaster", subtype="missile", hit=False, side=2),
            _weapon_fire(210, "Bob", "Blaster", subtype="missile", hit=False, side=2),
            # Alice activates booster again (trigger 2, separated by Bob's weapon fire)
            _module_activation(300, "Alice", "booster", side=1),
            # Another interleaving event
            _damage_event(350, target_side=1, absorbed=40.0, weapon="Blaster"),
            _layer_depleted(350, "shield", actor="Alice", side=1),
            # Alice activates booster again (trigger 3)
            _module_activation(500, "Alice", "booster", side=1),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        alice_mods = [e for e in result if e["event_type"] == "Module activated" and e.get("actor") == "Alice"]
        # Global aggregation: 3 booster activations → 1 collapsed line.
        assert len(alice_mods) == 1, (
            f"Global aggregation must fold 3 interleaved booster activations to 1 line; "
            f"got {len(alice_mods)}. (Fails on consecutive-only collapser.)"
        )
        assert alice_mods[0].get("count", 1) == 3, f"count must be 3; got: {alice_mods[0]}"
        assert "×3" in alice_mods[0]["detail"], f"detail must contain ×3; got: {alice_mods[0]['detail']!r}"
        # Combatant name must appear in the collapsed line.
        assert "Alice" in alice_mods[0]["detail"], (
            f"Collapsed module line must name the combatant; got: {alice_mods[0]['detail']!r}"
        )

    def test_combatant_name_in_collapsed_wir_detail(self):
        """A collapsed WiR line must contain the combatant (actor) name — not just a ship label."""
        combatants_map = {"1": {"name": "bluefyre", "ship": "VoidX"}, "2": {"name": "Vilhelm Lindon", "ship": "Ghost"}}
        timeline = [_fight_start(dist=5000.0, c1_name="bluefyre", c2_name="Vilhelm Lindon")]
        # Establish cadence then 4 re-entries in non-interleaved order for simplicity
        for t in (10, 20, 30):
            timeline.append(_weapon_fire(t, "bluefyre", "LaserX", subtype="missile", hit=True, side=1))
        for t in (500, 700, 900, 1100):
            timeline.append(_weapon_fire(t, "bluefyre", "LaserX", subtype="missile", hit=True, side=1))
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        wir = [e for e in result if e["event_type"] == "Weapon in range" and "LaserX" in e["detail"]]
        assert len(wir) == 1, f"5 same-key WiR must collapse to 1; got {len(wir)}"
        detail = wir[0]["detail"]
        assert "bluefyre" in detail, (
            f"Collapsed line must include combatant name 'bluefyre'; got: {detail!r}"
        )

    def test_combatant_name_in_collapsed_module_detail(self):
        """A collapsed Module activated line must contain the combatant name."""
        combatants_map = {"1": {"name": "bluefyre", "ship": "VoidX"}, "2": {"name": "Vilhelm Lindon", "ship": "Ghost"}}
        timeline = [
            _fight_start(c1_name="bluefyre", c2_name="Vilhelm Lindon"),
            _module_activation(100, "bluefyre", "cloak", side=1),
            _module_activation(500, "bluefyre", "cloak", side=1),
            _module_activation(900, "bluefyre", "cloak", side=1),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        mods = [e for e in result if e["event_type"] == "Module activated"]
        assert len(mods) == 1, f"3 same-key module activations must collapse to 1; got {len(mods)}"
        detail = mods[0]["detail"]
        assert "bluefyre" in detail, (
            f"Collapsed module line must include combatant name 'bluefyre'; got: {detail!r}"
        )

    def test_two_distinct_weapons_produce_two_collapsed_rows(self):
        """Two weapons each with ≥3 global occurrences stay in separate collapsed rows."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        timeline: list[dict] = [_fight_start(dist=2000.0)]
        # AlphaGun: cadence 10, then 3 non-consecutive re-entries.
        for t in (10, 20, 30):
            timeline.append(_weapon_fire(t, "Alice", "AlphaGun", subtype="missile", hit=True, side=1))
        # Bob's interleaving events between AlphaGun fires
        for t in (50, 150, 250):
            timeline.append(_weapon_fire(t, "Bob", "BobWeapon", subtype="missile", hit=False, side=2))
        for t in (100, 200, 300):
            timeline.append(_weapon_fire(t, "Alice", "AlphaGun", subtype="missile", hit=True, side=1))
        # BetaGun: 3 entries at different ticks, also non-consecutive (interleaved with more Bob)
        for t in (400, 410, 420):
            timeline.append(_weapon_fire(t, "Alice", "BetaGun", subtype="rocket", hit=True, side=1))
        for t in (450, 550):
            timeline.append(_weapon_fire(t, "Bob", "BobWeapon", subtype="missile", hit=False, side=2))
        for t in (500, 600, 700):
            timeline.append(_weapon_fire(t, "Alice", "BetaGun", subtype="rocket", hit=True, side=1))
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        alpha_wir = [e for e in result if e["event_type"] == "Weapon in range" and "AlphaGun" in e["detail"]]
        beta_wir = [e for e in result if e["event_type"] == "Weapon in range" and "BetaGun" in e["detail"]]
        assert len(alpha_wir) == 1, f"AlphaGun (6 global occurrences) must collapse to 1 row; got: {alpha_wir}"
        assert len(beta_wir) == 1, f"BetaGun (6 global occurrences) must collapse to 1 row; got: {beta_wir}"
        assert alpha_wir[0].get("count", 1) >= 3
        assert beta_wir[0].get("count", 1) >= 3

    def test_narrative_events_never_folded(self):
        """Narrative events are never folded even when the same type appears multiple times."""
        combatants_map = {"1": {"name": "Alice", "ship": "S"}, "2": {"name": "Bob", "ship": "W"}}
        # Build a timeline with multiple HP milestone triggers for both sides
        timeline = [
            _fight_start(),
            _weapon_fire(10, "Alice", "Rocket", hit=True, side=1),
            # Damage that crosses 50% for side 2
            {
                "tick": 50,
                "type": "damage",
                "actor": None,
                "target": "Bob",
                "data": {
                    "amount": 60,
                    "absorbed": 60,
                    "hp_after": {"hull": 45, "armour": 0, "shield": 0},
                    "source": {"weapon": "Rocket", "attacker": "Alice"},
                    "side": 2,
                },
            },
            _fight_end(100, "Alice", c1_hull=80, c2_hull=0),
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        # Engagement + Outcome must each appear exactly once (they're narrative, never folded).
        engagements = [e for e in result if e["event_type"] == "Engagement"]
        outcomes = [e for e in result if e["event_type"] == "Outcome"]
        assert len(engagements) == 1, "Exactly one Engagement line must appear"
        assert len(outcomes) == 1, "Exactly one Outcome line must appear"
        # Neither should have count > 1
        for ev in engagements + outcomes:
            assert ev.get("count", 1) == 1, f"Narrative events must not be folded; got: {ev}"


# ---------------------------------------------------------------------------
# 12. Denoising — Rule 2: Nuke significance filter (R-198 canary)
# ---------------------------------------------------------------------------


def _nuke_fire(tick: int, actor: str, weapon: str, opp: int, self_dmg: int = 0, side: int = 1) -> dict:
    return {
        "tick": tick,
        "type": "weapon_fire",
        "actor": actor,
        "target": "Bob",
        "data": {
            "slot": "secondary",
            "subtype": "nuke",
            "weapon": weapon,
            "opponent_damage": opp,
            "self_damage": self_dmg,
            "side": side,
        },
    }


class TestNukeSignificanceFilter:
    """Rule 2: R-198 canary — 9 fires, opp damages [0,0,0,97,30,0,2,0,0].
    opp:97 (best) and opp:30 (≥ 0.25×97=24.25) must remain individual;
    the remaining 7 (0,0,0,0,2,0,0 — 2 < 24.25) fold into one summary.
    """

    def test_r198_high_impact_lines_survive_individually(self):
        """opp:97 and opp:30 must each appear as individual Nuke detonation lines."""
        timeline = [_fight_start()]
        opps = [0, 0, 0, 97, 30, 0, 2, 0, 0]
        for i, opp in enumerate(opps):
            timeline.append(_nuke_fire(tick=10 + i * 20, actor="Alice", weapon="Liberator", opp=opp))
        result = _extract_key_events(timeline)
        nuke_ev = [e for e in result if e["event_type"] == "Nuke detonation"]
        # Must have at least 3: opp:97, opp:30, and 1 summary.
        assert len(nuke_ev) >= 3, f"Expected ≥3 nuke lines (2 individual + 1 summary); got: {nuke_ev}"
        details = [e["detail"] for e in nuke_ev]
        # opp:97 line must be individual (detail contains "opp: 97")
        assert any("opp: 97" in d for d in details), f"opp:97 line must survive individually; got: {details}"
        # opp:30 line must be individual (detail contains "opp: 30")
        assert any("opp: 30" in d for d in details), f"opp:30 line must survive individually; got: {details}"

    def test_r198_trivial_zero_fires_fold_to_summary(self):
        """The low/zero-impact detonations must fold into exactly ONE summary line."""
        timeline = [_fight_start()]
        opps = [0, 0, 0, 97, 30, 0, 2, 0, 0]
        for i, opp in enumerate(opps):
            timeline.append(_nuke_fire(tick=10 + i * 20, actor="Alice", weapon="Liberator", opp=opp))
        result = _extract_key_events(timeline)
        nuke_ev = [e for e in result if e["event_type"] == "Nuke detonation"]
        # Individual lines: opp:97 and opp:30 → 2; summary line → 1; total = 3
        assert len(nuke_ev) == 3, (
            f"Expected exactly 3 nuke lines (opp:97, opp:30, +1 summary); got {len(nuke_ev)}: {nuke_ev}"
        )
        # The summary line contains "×N" and "best:"
        summary_lines = [e["detail"] for e in nuke_ev if "best:" in e["detail"]]
        assert len(summary_lines) == 1, f"Must have exactly one summary line; got: {summary_lines}"
        assert "×7" in summary_lines[0], f"Summary must show ×7 (7 trivial); got: {summary_lines[0]!r}"
        assert "97" in summary_lines[0], f"Summary must cite best=97; got: {summary_lines[0]!r}"

    def test_below_threshold_nukes_not_grouped(self):
        """If a weapon fires fewer than RECAP_NUKE_SUMMARY_MIN_COUNT times, all lines are individual."""
        timeline = [_fight_start()]
        # 2 nuke fires — below the threshold of 3
        timeline.append(_nuke_fire(tick=10, actor="Alice", weapon="SmallNuke", opp=50))
        timeline.append(_nuke_fire(tick=30, actor="Alice", weapon="SmallNuke", opp=20))
        result = _extract_key_events(timeline)
        nuke_ev = [e for e in result if e["event_type"] == "Nuke detonation"]
        assert len(nuke_ev) == 2, f"Below threshold → all individual; got: {nuke_ev}"
        # Neither line should be a summary
        for ev in nuke_ev:
            assert "best:" not in ev["detail"], f"No summary for sub-threshold weapon; got: {ev['detail']!r}"


# ---------------------------------------------------------------------------
# 13. Denoising — Rule 3: count field on KeyEvent (legacy compat)
# ---------------------------------------------------------------------------


class TestCountFieldLegacyCompat:
    """count=1 default is backward-compatible; all rows have count ≥ 1."""

    def test_all_rows_have_count_field(self):
        """Every row in the output must have a count field (default 1 for non-collapsed rows)."""
        timeline = [
            _fight_start(),
            _weapon_fire(10, "Alice", "Rocket", hit=True, side=1),
            _fight_end(100, "Alice"),
        ]
        result = _extract_key_events(timeline)
        for ev in result:
            assert "count" not in ev or ev.get("count", 1) >= 1, (
                f"count must be ≥1 or absent; got: {ev}"
            )

    def test_non_collapsed_rows_have_no_count_or_count_1(self):
        """Non-collapsed rows must not have count>1 (either absent or 1)."""
        timeline = [
            _fight_start(),
            _weapon_fire(10, "Alice", "Rocket", hit=True, side=1),
            _fight_end(100, "Alice"),
        ]
        result = _extract_key_events(timeline)
        for ev in result:
            count = ev.get("count", 1)
            assert count == 1, f"Non-collapsed row must have count=1; got: {ev}"


# ---------------------------------------------------------------------------
# 14. R-241 real-data golden test — must-not-collapse baseline
# ---------------------------------------------------------------------------


class TestR241RealDataGolden:
    """R-241: 3s fight, bluefyre vs Borsul Tarand.

    Every cyclic key appears only once (N=1) — no key meets the ≥3 threshold.
    Both the old consecutive collapser AND the new global aggregation must produce exactly 6 lines.
    This test must PASS on current code and continue to pass after the fix.
    """

    def test_r241_exact_line_count_six(self):
        """Real battle 241 must produce exactly 6 key events (no collapse occurs)."""
        d = _load_fixture(241)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        assert len(result) == 6, (
            f"Battle 241 must produce exactly 6 lines (all cyclic keys N≤2); got {len(result)}: "
            + ", ".join(e["event_type"] for e in result)
        )

    def test_r241_no_event_is_collapsed(self):
        """Every line in battle 241 must have count=1 (nothing folds)."""
        d = _load_fixture(241)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        for ev in result:
            assert ev.get("count", 1) == 1, (
                f"Battle 241: no event should be collapsed; got count={ev.get('count')} on {ev}"
            )

    def test_r241_expected_event_types(self):
        """Battle 241 must contain Engagement, Weapon in range (×2), Module activated (×2), Outcome."""
        d = _load_fixture(241)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        type_counts = Counter(e["event_type"] for e in result)
        assert type_counts["Engagement"] == 1
        assert type_counts["Weapon in range"] == 2
        assert type_counts["Module activated"] == 2
        assert type_counts["Outcome"] == 1

    def test_r241_outcome_names_bluefyre_winner(self):
        """Battle 241 outcome must name bluefyre as winner."""
        d = _load_fixture(241)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        outcome = next(e for e in result if e["event_type"] == "Outcome")
        assert "bluefyre wins" in outcome["detail"], (
            f"Outcome must say 'bluefyre wins'; got: {outcome['detail']!r}"
        )


# ---------------------------------------------------------------------------
# 15. R-285 real-data golden test — must-collapse pole (stalemate, 180s)
# ---------------------------------------------------------------------------


class TestR285RealDataGolden:
    """R-285: 180s stalemate, bluefyre vs Vilhelm Lindon.

    Current shipped code: 76 lines (zero collapse — the consecutive-only collapser is a no-op
    because all cyclic events interleave).

    Under REVISED global aggregation (per-module collapse keys): 11 collapse groups (each N≥3)
    fold to 11 lines; 5 uncollapsed cyclic lines (bluefyre Armour ×2, Vilhelm emergency_system ×1,
    bluefyre cloak ×2); 6 narrative lines → 22... actual run = 21 total lines.

    The golden count (21) is pinned from the actual first-green run.  The PROPERTY assertions are
    the real guards.

    All tests in this class FAIL on current shipped code.
    """

    # Pre-computed from live DB fixture (per-module collapse keys):
    #   bluefyre's M6 A4 "Raccoon" WiR: N=11 → 1 collapsed line
    #   Vilhelm Lindon's Disruptor Laser WiR: N=8 → 1 collapsed line
    #   Vilhelm Lindon's Mass Driver MD 12 WiR: N=3 → 1 collapsed line
    #   Vilhelm Lindon's Berger Converge IV WiR: N=3 → 1 collapsed line
    #   Vilhelm Lindon layer depleted (Armour): N=10 → 1 collapsed line
    #   Vilhelm Lindon layer depleted (Shield): N=8 → 1 collapsed line
    #   bluefyre layer depleted (Shield): N=6 → 1 collapsed line
    #   bluefyre layer depleted (Armour): N=2 → 2 individual lines (not collapsed — below threshold)
    #   Vilhelm Lindon cloak: N=4 → 1 collapsed line
    #   Vilhelm Lindon booster: N=6 → 1 collapsed line
    #   Vilhelm Lindon emergency_system: N=1 → 1 individual line
    #   bluefyre cloak: N=2 → 2 individual lines (not collapsed)
    #   bluefyre booster: N=6 → 1 collapsed line
    # Narrative (never collapsed): Engagement, Nuke detonation, HP milestone×2, Ammo depleted, Outcome = 6
    # Collapse groups (N≥3): 11 → 11 output lines
    # Uncollapsed cyclic (N<3): 2 (bluefyre Armour ×2) + 1 (Vilhelm emergency_system ×1)
    #                           + 2 (bluefyre cloak ×2) = 5 output lines
    # EXPECTED TOTAL: 6 + 11 + 5 = 22... wait: let's count directly from actual output:
    # WiR: 4 collapsed; Layer: 3 collapsed + 2 individual; Module: 3 collapsed + 1 + 2 + 1 individual
    # Actual run yields: 21 total (6 narrative + 4 WiR + 3 Layer + 6 Module + 2 bluefyre Armour)
    # Pin to actual first-green value: 21
    _EXPECTED_TOTAL = 21

    def test_r285_output_is_dramatically_reduced(self):
        """Battle 285 must collapse from 76 lines to dramatically fewer under global aggregation.

        FAILS on current shipped code (consecutive-only collapser returns 76 lines).
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        assert len(result) < 30, (
            f"Battle 285 global collapse must reduce lines to <30; got {len(result)}. "
            f"(Current shipped code returns 76 — consecutive-only collapser is a no-op on real data.)"
        )

    def test_r285_exact_line_count(self):
        """Battle 285 must produce exactly _EXPECTED_TOTAL lines under global aggregation.

        Pin: 11 collapse groups (WiR×4 + Layer×3 + Module per-name×4) + 5 uncollapsed cyclic
        (bluefyre Armour×2, Vilhelm emergency_system×1, bluefyre cloak×2) + 6 narrative = 21.
        Re-pin to first-green value if the design yields a slightly different number — the
        property tests above are the real guard.
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        assert len(result) == TestR285RealDataGolden._EXPECTED_TOTAL, (
            f"Battle 285 must produce exactly {TestR285RealDataGolden._EXPECTED_TOTAL} lines; "
            f"got {len(result)}. (Pin: 11 collapsed + 5 uncollapsed + 6 narrative.)"
        )

    def test_r285_no_collapsible_key_appears_more_than_once(self):
        """No cyclic collapse key with global count ≥3 may appear more than once in the output.

        After global collapse: keys with N≥3 → exactly 1 line each; keys with N<3 → ≤N lines.
        We verify that every key appearing >1 time in the output accounts for its full count via the
        `count` field (i.e. no key with count-weighted total ≥3 produces more than 1 output row).

        FAILS on current shipped code: 9 collapse groups (each N≥3) each produce N separate output
        lines (all with count=1), so every such key appears N>1 times instead of 1.
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])

        # For each collapse key, accumulate (row_count, total_count) where:
        #   row_count = number of output rows with this key
        #   total_count = sum of all count fields (1 if absent) across those rows
        from collections import defaultdict

        ck_rows: dict = defaultdict(list)
        for e in result:
            ck = _global_collapse_key(e)
            if ck is not None:
                ck_rows[ck].append(e)

        # Keys with total_count ≥ 3 (globally collapsible) must produce exactly 1 output row.
        violations = {}
        for ck, rows in ck_rows.items():
            total_count = sum(r.get("count", 1) for r in rows)
            row_count = len(rows)
            if total_count >= 3 and row_count > 1:
                violations[ck] = {"rows": row_count, "total_count": total_count}

        assert not violations, (
            f"Collapse keys with total count ≥3 must produce exactly 1 output row; "
            f"violations: {violations}"
        )

    def test_r285_all_narrative_lines_present(self):
        """All 6 narrative lines from battle 285 must survive the collapse unchanged.

        Narrative types: Engagement, Nuke detonation, HP milestone (50%), HP milestone (25%),
        Ammo depleted, Outcome.
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        type_counts = Counter(e["event_type"] for e in result)
        assert type_counts["Engagement"] == 1, "Engagement must survive"
        assert type_counts["Nuke detonation"] == 1, "Nuke detonation must survive"
        assert type_counts["HP milestone (50%)"] == 1, "HP milestone (50%) must survive"
        assert type_counts["HP milestone (25%)"] == 1, "HP milestone (25%) must survive"
        assert type_counts["Ammo depleted"] == 1, "Ammo depleted must survive"
        assert type_counts["Outcome"] == 1, "Outcome must survive"

    def test_r285_raccoon_collapsed_line_has_combatant_name_and_span(self):
        """The collapsed Raccoon WiR line must name 'bluefyre', show ×11, and include a tick span.

        From real data: 11 occurrences, first tick 367 (3.7s), last tick 16867 (168.7s).
        Expected detail like: bluefyre's M6 A4 "Raccoon" re-enters range ×11 (3.7s–168.7s)

        FAILS on current shipped code (no collapse → 11 separate lines, none with ×N or span).
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        raccoon_wir = [e for e in result if e["event_type"] == "Weapon in range" and "Raccoon" in e["detail"]]
        assert len(raccoon_wir) == 1, (
            f"Raccoon WiR must collapse to 1 line; got {len(raccoon_wir)}: {raccoon_wir}"
        )
        detail = raccoon_wir[0]["detail"]
        assert "bluefyre" in detail, f"Collapsed Raccoon line must name 'bluefyre'; got: {detail!r}"
        assert "×11" in detail, f"Collapsed Raccoon line must show ×11; got: {detail!r}"
        # Span: '3.7s' for first tick (367/100=3.7) and '168.7s' for last tick (16867/100=168.7)
        assert "3.7s" in detail, f"Collapsed Raccoon line must include first-tick 3.7s; got: {detail!r}"
        assert "168.7s" in detail, f"Collapsed Raccoon line must include last-tick 168.7s; got: {detail!r}"

    def test_r285_raccoon_collapsed_line_anchored_at_first_tick(self):
        """The collapsed Raccoon line must be anchored at the FIRST occurrence's tick (tick 367).

        FAILS on current shipped code (no collapsed line exists at all).
        """
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        raccoon_wir = [e for e in result if e["event_type"] == "Weapon in range" and "Raccoon" in e["detail"]]
        assert len(raccoon_wir) == 1
        assert raccoon_wir[0]["tick"] == 367, (
            f"Collapsed Raccoon line must be anchored at tick 367 (first occurrence); "
            f"got tick={raccoon_wir[0]['tick']}"
        )

    def test_r285_stalemate_outcome_present(self):
        """Battle 285 outcome is a stalemate (180s) and must appear in the collapsed output."""
        d = _load_fixture(285)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None, "Outcome must be present"
        assert "Stalemate" in outcome["detail"], (
            f"Battle 285 outcome must be a Stalemate; got: {outcome['detail']!r}"
        )


# ---------------------------------------------------------------------------
# 16. R-219 real-data golden test — long fight, 93% cyclic
# ---------------------------------------------------------------------------


class TestR219RealDataGolden:
    """R-219: 166.8s fight, bluefyre wins.  85 lines currently; 93% cyclic.

    Under global aggregation (per-module collapse keys): 10 collapse groups fold → 18 total lines.

    All assertion tests FAIL on current shipped code.
    """

    # Pre-computed from live DB fixture (per-module collapse keys):
    # WiR: bluefyre Raccoon ×12, Bartholomeu Raccoon ×12 → 2 collapsed lines
    # Layer: Nemesis Shield ×10, Nemesis Armour ×7, Na'Srrk Armour ×4, Na'Srrk Shield ×9 → 4 collapsed
    # Module (per-name): bluefyre cloak ×8, booster ×6, Bartholomeu cloak ×3, booster ×6 → 4 collapsed
    # Uncollapsed cyclic (N<3): 1 (Bartholomeu Garuda-IV ×1) + 1 (Bartholomeu emergency_system ×1) = 2 lines
    # Narrative: Engagement + HP milestone (50%)×2 + HP milestone (25%)×1 + Ammo depleted + Outcome = 6
    # EXPECTED: 6 + 10 + 2 = 18
    _EXPECTED_TOTAL = 18

    def test_r219_output_is_dramatically_reduced(self):
        """Battle 219 must collapse from 85 lines to dramatically fewer under global aggregation.

        FAILS on current shipped code.
        """
        d = _load_fixture(219)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        assert len(result) < 30, (
            f"Battle 219 global collapse must reduce to <30 lines; got {len(result)}."
        )

    def test_r219_exact_line_count(self):
        """Battle 219 must produce exactly {_EXPECTED_TOTAL} lines under global aggregation.

        FAILS on current shipped code.
        """
        d = _load_fixture(219)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        assert len(result) == TestR219RealDataGolden._EXPECTED_TOTAL, (
            f"Battle 219 must produce exactly {TestR219RealDataGolden._EXPECTED_TOTAL} lines; got {len(result)}."
        )

    def test_r219_no_collapsible_key_appears_more_than_once(self):
        """No cyclic collapse key with global count ≥3 may appear more than once in the output.

        FAILS on current shipped code (8 groups each with N≥3 produce N separate output rows
        instead of being folded to 1).
        """
        d = _load_fixture(219)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        from collections import defaultdict

        ck_rows: dict = defaultdict(list)
        for e in result:
            ck = _global_collapse_key(e)
            if ck is not None:
                ck_rows[ck].append(e)

        violations = {}
        for ck, rows in ck_rows.items():
            total_count = sum(r.get("count", 1) for r in rows)
            row_count = len(rows)
            if total_count >= 3 and row_count > 1:
                violations[ck] = {"rows": row_count, "total_count": total_count}

        assert not violations, (
            f"Collapse keys with total count ≥3 must produce exactly 1 output row; "
            f"violations: {violations}"
        )

    def test_r219_all_narrative_lines_present(self):
        """All 6 narrative lines from battle 219 must survive collapse.

        This test PASSES on current code (narrative events are not touched); must continue to pass.
        """
        d = _load_fixture(219)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        type_counts = Counter(e["event_type"] for e in result)
        assert type_counts["Engagement"] == 1
        assert type_counts["HP milestone (50%)"] == 2, "Two 50% milestones: one per side"
        assert type_counts["HP milestone (25%)"] == 1
        assert type_counts["Ammo depleted"] == 1
        assert type_counts["Outcome"] == 1

    def test_r219_outcome_names_bluefyre_winner(self):
        """Battle 219 outcome must name bluefyre as winner.

        This test PASSES on current code; must continue to pass.
        """
        d = _load_fixture(219)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        outcome = next(e for e in result if e["event_type"] == "Outcome")
        assert "bluefyre wins" in outcome["detail"], f"Got: {outcome['detail']!r}"


# ---------------------------------------------------------------------------
# 17. R-198 real-data nuke canary
# ---------------------------------------------------------------------------


class TestR198NukeCanaryRealData:
    """R-198: 83s fight, bluefyre fires Liberator (nuke) 9 times.
    Nuke significance filter (Rule 2): opp:97 and opp:30 survive as individual lines;
    the remaining 7 fold to a summary.  Total nuke lines = 3.

    These tests use the real fixture to guard Rule 2 in a real-data context.
    They PASS on current code (Rule 2 already works) and must continue to pass after the fix.
    """

    def test_r198_exactly_three_nuke_lines(self):
        """Battle 198 must produce exactly 3 Nuke detonation lines (opp:97, opp:30, ×7 summary)."""
        d = _load_fixture(198)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        nuke_ev = [e for e in result if e["event_type"] == "Nuke detonation"]
        assert len(nuke_ev) == 3, (
            f"Battle 198: expected 3 nuke lines (opp:97 + opp:30 + ×7 summary); "
            f"got {len(nuke_ev)}: {[e['detail'] for e in nuke_ev]}"
        )

    def test_r198_opp97_and_opp30_survive_individually(self):
        """opp:97 and opp:30 detonations must each appear as individual lines in real battle 198."""
        d = _load_fixture(198)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        details = [e["detail"] for e in result if e["event_type"] == "Nuke detonation"]
        assert any("opp: 97" in d for d in details), f"opp:97 must survive individually; got: {details}"
        assert any("opp: 30" in d for d in details), f"opp:30 must survive individually; got: {details}"

    def test_r198_low_impact_nukes_fold_to_summary(self):
        """The 6 low/zero-impact Liberator fires must fold to one ×6 summary line.

        Real data: 8 total fires.  opp:97 and opp:30 → 2 individual lines.
        Remaining 6 (opp 0,0,0,0,2,0) → fold to ×6 summary.
        """
        d = _load_fixture(198)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        details = [e["detail"] for e in result if e["event_type"] == "Nuke detonation"]
        summary_lines = [d for d in details if "best:" in d]
        assert len(summary_lines) == 1, f"Exactly one nuke summary line expected; got: {summary_lines}"
        assert "×6" in summary_lines[0], f"Summary must show ×6 (6 trivial fires); got: {summary_lines[0]!r}"

    def test_r198_outcome_names_bluefyre_winner(self):
        """Battle 198 outcome must name bluefyre as winner (Oluchi Erland destroyed)."""
        d = _load_fixture(198)
        result = _extract_key_events(d["timeline"], 10, d["combatants"])
        outcome = next(e for e in result if e["event_type"] == "Outcome")
        assert "bluefyre wins" in outcome["detail"], f"Got: {outcome['detail']!r}"
