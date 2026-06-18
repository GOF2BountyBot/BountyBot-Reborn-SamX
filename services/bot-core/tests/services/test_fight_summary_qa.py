"""QA adversarial tests for commit 728a330 (accuracy cluster/nuke fix).

Probes coverage gaps identified during review:
- cluster on side 2 (C2 fires cluster) — slot attribution
- ionizing-missile counted as 1 fired / 1 hit iff hit (else branch)
- pure-nuke combatant: summary accuracy=0.0, API _parse_combatant returns None
- cluster with fired=0 (degenerate, defensive-default path) → 0 shots added
- cluster hits=0 (complete miss) → correct shot count, 0 accuracy
- shock-blast that carries hit=True is still excluded (subtype wins over hit bool)
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Shared bblogger guard — must run before any project import
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

import pytest
from services.combat_log_service import CombatLogService
from services.combat_models import CombatEvent, CombatEventType, ShipLoadout
from services.combat_resolver import _build_fight_summary, _CombatantState, _init_combatant

# ---------------------------------------------------------------------------
# Helpers — mirrors test_fight_summary.py to stay self-contained
# ---------------------------------------------------------------------------


def _loadout(ship_name: str = "Ship", base_armour: int = 1000) -> ShipLoadout:
    return ShipLoadout(ship_name=ship_name, base_armour=base_armour, modules=[], weapons=[])


def _make_states(name1: str = "C1", name2: str = "C2") -> tuple[_CombatantState, _CombatantState]:
    c1 = _init_combatant(_loadout(ship_name=name1), is_player=False)
    c2 = _init_combatant(_loadout(ship_name=name2), is_player=False)
    return c1, c2


def _hp(hull: int, armour: int = 0, shield: int = 0) -> dict:
    return {"shield": shield, "armour": armour, "hull": hull}


def _fight_start_event(c1_name: str, c2_name: str) -> CombatEvent:
    return CombatEvent(
        tick=0,
        type=CombatEventType.fight_start,
        actor=None,
        target=None,
        data={
            "combatants": [
                {"name": c1_name, "ship": c1_name, "hp": _hp(100)},
                {"name": c2_name, "ship": c2_name, "hp": _hp(100)},
            ],
            "initial_distance": 5000.0,
        },
    )


def _fight_end_event(tick: int, winner: str | None, reason: str, dur: int, c1_hp: dict, c2_hp: dict) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.fight_end,
        actor=None,
        target=None,
        data={"winner": winner, "reason": reason, "duration_ticks": dur, "final_hp": {"c1": c1_hp, "c2": c2_hp}},
    )


def _weapon_fire(actor: str, target: str, subtype: str, *, tick: int = 1, **data) -> CombatEvent:
    """Generic weapon_fire event with explicit actor (no side key → name fallback)."""
    return CombatEvent(
        tick=tick,
        type=CombatEventType.weapon_fire,
        actor=actor,
        target=target,
        data={"slot": "secondary", "subtype": subtype, "weapon": "Weapon", **data},
    )


def _weapon_fire_sided(actor: str, target: str, subtype: str, side: int, *, tick: int = 1, **data) -> CombatEvent:
    """Weapon_fire with explicit side key (preferred attribution path)."""
    return CombatEvent(
        tick=tick,
        type=CombatEventType.weapon_fire,
        actor=actor,
        target=target,
        data={"slot": "secondary", "subtype": subtype, "weapon": "Weapon", "side": side, **data},
    )


# ---------------------------------------------------------------------------
# Gap 1: Cluster on side 2 — attribution must NOT land on slot "1"
# ---------------------------------------------------------------------------


class TestClusterSide2Attribution:
    def test_cluster_fired_by_c2_attributes_to_slot_2(self):
        """C2 fires a 3/4 cluster → slot 2 gets shots_fired=4, slot 1 unchanged."""
        c1, c2 = _make_states("C1", "C2")
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C2", "C1", "cluster-missile", tick=1, fired=4, hits=3),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 0, "C1 must not get C2's cluster shots"
        assert s["combatants"]["2"]["shots_fired"] == 4
        assert s["combatants"]["2"]["shots_hit"] == 3
        assert s["combatants"]["2"]["accuracy"] == pytest.approx(0.75)

    def test_cluster_sided_key_c2_attributes_to_slot_2(self):
        """Same as above but uses the explicit side=2 key (real-resolver path)."""
        c1, c2 = _make_states("C1", "C2")
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire_sided("C2", "C1", "cluster-missile", side=2, tick=1, fired=6, hits=2),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 0
        assert s["combatants"]["2"]["shots_fired"] == 6
        assert s["combatants"]["2"]["shots_hit"] == 2
        assert s["combatants"]["2"]["accuracy"] == pytest.approx(2 / 6)


# ---------------------------------------------------------------------------
# Gap 2: Ionizing-missile must be counted (it has hit/miss semantics)
# ---------------------------------------------------------------------------


class TestIonizingMissileCounted:
    def test_ionizing_missile_hit_increments_shots_and_hits(self):
        """ionizing-missile with hit=True → +1 fired, +1 hit (fires but deals 0 dmg)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C1", "C2", "ionizing-missile", tick=1, hit=True),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 1
        assert s["combatants"]["1"]["shots_hit"] == 1
        assert s["combatants"]["1"]["accuracy"] == pytest.approx(1.0)

    def test_ionizing_missile_miss_increments_only_fired(self):
        """ionizing-missile with hit=False → +1 fired, 0 hits."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C1", "C2", "ionizing-missile", tick=1, hit=False),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 1
        assert s["combatants"]["1"]["shots_hit"] == 0
        assert s["combatants"]["1"]["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# Gap 3: Pure-nuke combatant — summary accuracy=0.0, API returns None
# ---------------------------------------------------------------------------


class TestPureNukeAccuracyConsistency:
    def test_pure_nuke_summary_accuracy_is_zero(self):
        """A combatant that fires only nukes → shots_fired=0, accuracy=0.0 in raw summary."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C1", "C2", "nuke", tick=1, opponent_damage=200, self_damage=5),
            _weapon_fire("C1", "C2", "nuke", tick=2, opponent_damage=150, self_damage=3),
            _fight_end_event(3, None, "time_cap", 3, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 3, None)
        cb = s["combatants"]["1"]
        assert cb["shots_fired"] == 0
        assert cb["shots_hit"] == 0
        assert cb["accuracy"] == 0.0  # raw summary: 0 when no shots

    def test_pure_nuke_api_parse_combatant_returns_none_accuracy(self):
        """_parse_combatant re-derives accuracy; 0 shots_fired → None (not 0.0)."""
        raw_cb = {
            "name": "Nuke Fighter",
            "ship": "Death Star",
            "shots_fired": 0,
            "shots_hit": 0,
            "accuracy": 0.0,  # what summary puts in
            "damage_dealt": 500,
            "damage_taken": 10,
            "start_hp": _hp(100),
            "final_hp": _hp(50),
            "secondary_fired": {"nuke": 2},
            "module_activations": {},
        }
        parsed = CombatLogService._parse_combatant(raw_cb)
        # The key claim: _parse_combatant returns None when shots_fired==0,
        # not 0.0 (distinguishes "no aimed shots" from "aimed but all missed").
        assert parsed["accuracy"] is None, f"Expected None accuracy for pure-nuke combatant, got {parsed['accuracy']!r}"


# ---------------------------------------------------------------------------
# Gap 4: Cluster with hits=0 (complete miss) — shots counted but 0 hits
# ---------------------------------------------------------------------------


class TestClusterCompleteMiss:
    def test_cluster_all_miss_fires_n_shots_zero_hits(self):
        """A 4/0 cluster (all miss) → 4 shots fired, 0 hits, accuracy=0.0."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C1", "C2", "cluster-missile", tick=1, fired=4, hits=0),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 4
        assert s["combatants"]["1"]["shots_hit"] == 0
        assert s["combatants"]["1"]["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# Gap 5: Cluster with missing fired/hits keys (defensive .get("fired", 0))
# ---------------------------------------------------------------------------


class TestClusterMissingKeys:
    def test_cluster_missing_fired_hits_adds_zero(self):
        """If a cluster event omits fired/hits (shouldn't happen in live code),
        the .get defaults of 0 mean no shots are added — no crash, no phantom count."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            # Craft cluster event WITHOUT fired/hits keys
            CombatEvent(
                tick=1,
                type=CombatEventType.weapon_fire,
                actor="C1",
                target="C2",
                data={"slot": "secondary", "subtype": "cluster-missile", "weapon": "Broken"},
            ),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        # No crash; 0 shots added (defensive default)
        assert s["combatants"]["1"]["shots_fired"] == 0
        assert s["combatants"]["1"]["shots_hit"] == 0
        assert s["combatants"]["1"]["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# Gap 6: shock-blast carries hit=True but must still be excluded
# ---------------------------------------------------------------------------


class TestShockBlastHitTrueStillExcluded:
    def test_shock_blast_with_hit_true_still_excluded(self):
        """Shock-blast always emits hit=True in live code. Subtype check must
        take precedence over the hit bool — exclusion is on subtype, not on
        absence of a hit field."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire("C1", "C2", "shock-blast", tick=1, hit=True, accuracy=1.0),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["shots_fired"] == 0
        assert s["combatants"]["1"]["shots_hit"] == 0
        assert s["combatants"]["1"]["accuracy"] == 0.0
