"""New coverage for _extract_key_events — DESIGN_COMBAT_LOG_RECAP behaviors.

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
"""

from __future__ import annotations

import sys
import types
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
        """Weapon fires, shock-blast resets dist > range, weapon fires again → 're-enters range'."""
        # Missile fires at tick 10 (dist ~2000 = range_est)
        # Shock-blast at tick 20 resets dist to 5000 (> range_est)
        # Closure at tick 100 brings dist back to 1500 < range_est
        # Missile fires again at tick 110 → re-enter
        timeline = [
            _fight_start(dist=2000.0),
            _weapon_fire(10, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
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
            _distance_event(100, cause="closure", from_m=5000.0, to_m=1500.0, side=2),
            _weapon_fire(110, "Alice", "Tomahawk", subtype="missile", hit=True, side=1),
        ]
        result = _extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 2, f"Expected R1 + R2 (2 range-in events), got: {wir}"
        r1 = next(e for e in wir if "re-enters" not in e["detail"])
        r2 = next(e for e in wir if "re-enters" in e["detail"])
        assert r1["tick"] == 10
        assert r2["tick"] == 110
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
    def test_stalemate_outcome_includes_why_line(self):
        """Stalemate outcome includes 'couldn't out-damage' why-line with aggressor stats."""
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
        # Alice dealt more damage (200 vs 100) → is the aggressor
        assert "Alice" in detail
        assert "couldn't out-damage" in detail
        # Why-line includes shot stats and damage
        assert "50/80" in detail
        assert "200" in detail

    def test_stalemate_lower_damage_side_is_defender(self):
        """The lower-damage side is called out as the defender (their regen won)."""
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
                "data": {"winner": None, "reason": "time_cap", "duration_ticks": 18000},
            }
        ]
        result = _extract_key_events(timeline, combatants_map=combatants_map)
        outcome = next((e for e in result if e["event_type"] == "Outcome"), None)
        assert outcome is not None
        detail = outcome["detail"]
        # Bob's regen prevailed; Bob's dealt damage appears after the semicolon
        assert "Bob" in detail
        assert "regen" in detail


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
