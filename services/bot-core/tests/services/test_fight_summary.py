"""
T9 Fight-summary builder tests.

Covers the per-combatant summary builder (section 12 data.summary) and FightResults envelope.

Locked decisions verified:
  Q5: module key names are lowercase_snake_case (cloak / booster / emergency_system)
  Q6: module_activations + secondary_fired are SPARSE - only fired keys; outer dict always present
  ES omits trigger_hp_pct (section 12)
  Precision note: damage_dealt sourced from damage events (data.source.attacker), NOT weapon_fire
  Precision note: damage actor=None; attacker in data.source.attacker
  Precision note: fight_end.final_hp uses c1/c2 keys -> mapped to "1"/"2" in summary
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

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
import pytest
from src.services.combat_models import (
    CombatEvent,
    CombatEventType,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from src.services.combat_resolver import (
    _BOOSTER_MODULE_TYPE,
    _CLOAK_MODULE_TYPE,
    _EMERGENCY_SYSTEM_MODULE_TYPE,
    TickResolver,
    _build_fight_summary,
    _CombatantState,
    _init_combatant,
)
from src.services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TICK_MS = GameConstants.TICK_MS


def _loadout(
    ship_name: str = "TestShip",
    base_armour: int = 1000,
    modules: list[ModuleStats] | None = None,
    weapons: list[WeaponStats] | None = None,
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=ship_name,
        base_armour=base_armour,
        modules=modules or [],
        weapons=weapons or [],
    )


def _gun(dps: float = 50.0, dmg: int = 50, speed_ms: int = 200, range_m: float = 5000.0) -> WeaponStats:
    return WeaponStats(name="TestGun", dps=dps, damage_per_shot=dmg, loading_speed_ms=speed_ms, range_m=range_m)


def _es_mod() -> ModuleStats:
    return ModuleStats(name="Emergency System", module_type=_EMERGENCY_SYSTEM_MODULE_TYPE)


def _cloak_mod(duration_ms: int = 10_000, cooldown_ms: int = 2_000) -> ModuleStats:
    return ModuleStats(
        name="TestCloak",
        module_type=_CLOAK_MODULE_TYPE,
        effect_duration_ms=duration_ms,
        loading_speed_ms=cooldown_ms,
    )


def _booster_mod(duration_ms: int = 4_400, cooldown_ms: int = 10_000, effect_pct: float = 80.0) -> ModuleStats:
    return ModuleStats(
        name="TestBooster",
        module_type=_BOOSTER_MODULE_TYPE,
        effect_duration_ms=duration_ms,
        loading_speed_ms=cooldown_ms,
        effect_pct=effect_pct,
    )


def _find_events(log: list, event_type: str) -> list:
    return [e for e in log if e.type == event_type]


def _summary_of(result) -> dict:
    """Extract the summary dict from FightResults.metadata."""
    return result.metadata["summary"]


def _hp(hull: int, armour: int = 0, shield: int = 0) -> dict:
    """Compact HP dict helper — keeps long fight_end_event calls within 120 chars."""
    return {"shield": shield, "armour": armour, "hull": hull}


# ---------------------------------------------------------------------------
# Helper: build a minimal state pair for _build_fight_summary unit tests
# ---------------------------------------------------------------------------


def _make_states(
    name1: str = "C1",
    name2: str = "C2",
    hull1: int = 100,
    hull2: int = 100,
) -> tuple[_CombatantState, _CombatantState]:
    c1 = _init_combatant(_loadout(ship_name=name1, base_armour=hull1), is_player=False)
    c2 = _init_combatant(_loadout(ship_name=name2, base_armour=hull2), is_player=False)
    return c1, c2


def _fight_start_event(c1_name: str, c2_name: str, hull1: int = 100, hull2: int = 100) -> CombatEvent:
    return CombatEvent(
        tick=0,
        type=CombatEventType.fight_start,
        actor=None,
        target=None,
        data={
            "combatants": [
                {"name": c1_name, "ship": c1_name, "hp": _hp(hull1)},
                {"name": c2_name, "ship": c2_name, "hp": _hp(hull2)},
            ],
            "initial_distance": 5000.0,
        },
    )


def _fight_end_event(
    tick: int,
    winner: str | None,
    reason: str,
    dur: int,
    c1_hp: dict,
    c2_hp: dict,
) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.fight_end,
        actor=None,
        target=None,
        data={
            "winner": winner,
            "reason": reason,
            "duration_ticks": dur,
            "final_hp": {"c1": c1_hp, "c2": c2_hp},
        },
    )


def _weapon_fire_event(actor: str, target: str, tick: int = 1, hit: bool = True) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.weapon_fire,
        actor=actor,
        target=target,
        data={"slot": "primary", "subtype": "primary", "weapon": "TestGun", "hit": hit, "accuracy": 1.0},
    )


def _damage_event(target: str, attacker: str, amount: int, tick: int = 1) -> CombatEvent:
    """Helper: damage event where absorbed == amount (no overkill scenario).

    T10: the summary builder reads 'absorbed' (not 'amount') for damage_dealt/taken.
    In this helper, absorbed == amount (no overkill); use explicit CombatEvent for
    overkill scenarios.
    """
    return CombatEvent(
        tick=tick,
        type=CombatEventType.damage,
        actor=None,
        target=target,
        data={
            "amount": amount,
            "absorbed": amount,  # T10: absorbed == amount when no overkill
            "breakdown": {"shield": 0, "armour": 0, "hull": amount},
            "hp_after": _hp(100 - amount),
            "source": {"subtype": "primary", "weapon": "TestGun", "attacker": attacker},
        },
    )


def _module_activation_event(actor: str, module_key: str, tick: int = 2, trigger_pct: int | None = 66) -> CombatEvent:
    data: dict = {"module": module_key}
    if trigger_pct is not None:
        data["trigger_hp_pct"] = trigger_pct
    return CombatEvent(tick=tick, type=CombatEventType.module_activation, actor=actor, target=None, data=data)


# ---------------------------------------------------------------------------
# TestSummaryStructure — top-level shape matches section 12 JSONC
# ---------------------------------------------------------------------------


class TestSummaryStructure:
    def test_summary_keys_present(self):
        """Summary dict has all required top-level keys."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(10, "C1", "hp_depleted", 10, _hp(100), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 10, "C1")
        for key in ("outcome", "reason", "duration_ticks", "winner", "combatants"):
            assert key in s, f"Missing top-level key: {key}"

    def test_combatant_keys_present(self):
        """Each combatant block under 'combatants' has all required per-combatant keys."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(10, "C1", "hp_depleted", 10, _hp(100), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 10, "C1")
        expected_keys = (
            "name",
            "ship",
            "start_hp",
            "final_hp",
            "damage_dealt",
            "damage_taken",
            "shots_fired",
            "shots_hit",
            "accuracy",
            "module_activations",
            "secondary_fired",
        )
        for ck in ("1", "2"):
            cb = s["combatants"][ck]
            for key in expected_keys:
                assert key in cb, f"Combatant {ck} missing key: {key}"

    def test_outcome_and_reason_passthrough(self):
        """outcome and reason are taken from the caller arguments."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(10, None, "time_cap", 10, _hp(50), _hp(60)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)
        assert s["outcome"] == "stalemate"
        assert s["reason"] == "time_cap"
        assert s["winner"] is None

    def test_duration_ticks_passthrough(self):
        """duration_ticks equals the argument passed."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(42, "C1", "hp_depleted", 42, _hp(80), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 42, "C1")
        assert s["duration_ticks"] == 42

    def test_c1_c2_keys_map_to_1_and_2(self):
        """fight_end.final_hp c1 -> summary combatants['1'], c2 -> summary combatants['2']."""
        c1, c2 = _make_states()
        c1_final = _hp(75)
        c2_final = _hp(0)
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(10, "C1", "hp_depleted", 10, c1_final, c2_final),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 10, "C1")
        assert s["combatants"]["1"]["final_hp"] == c1_final
        assert s["combatants"]["2"]["final_hp"] == c2_final


# ---------------------------------------------------------------------------
# TestAccuracyCounting — shots_fired / shots_hit / accuracy
# ---------------------------------------------------------------------------


class TestAccuracyCounting:
    def test_accuracy_zero_shots(self):
        """accuracy is 0.0 when shots_fired is 0."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(1, None, "time_cap", 1, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 1, None)
        assert s["combatants"]["1"]["shots_fired"] == 0
        assert s["combatants"]["1"]["accuracy"] == 0.0

    def test_accuracy_all_hits(self):
        """10 shots, 10 hits -> accuracy == 1.0."""
        c1, c2 = _make_states()
        events = [_fight_start_event("C1", "C2")]
        for i in range(10):
            events.append(_weapon_fire_event("C1", "C2", tick=i + 1, hit=True))
        events.append(_fight_end_event(11, None, "time_cap", 11, _hp(100), _hp(100)))
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 11, None)
        assert s["combatants"]["1"]["shots_fired"] == 10
        assert s["combatants"]["1"]["shots_hit"] == 10
        assert s["combatants"]["1"]["accuracy"] == pytest.approx(1.0)

    def test_accuracy_partial(self):
        """10 shots, 7 hits -> accuracy == 0.70."""
        c1, c2 = _make_states()
        events = [_fight_start_event("C1", "C2")]
        for i in range(7):
            events.append(_weapon_fire_event("C1", "C2", tick=i + 1, hit=True))
        for i in range(3):
            events.append(_weapon_fire_event("C1", "C2", tick=i + 8, hit=False))
        events.append(_fight_end_event(20, None, "time_cap", 20, _hp(100), _hp(100)))
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 20, None)
        assert s["combatants"]["1"]["shots_fired"] == 10
        assert s["combatants"]["1"]["shots_hit"] == 7
        assert s["combatants"]["1"]["accuracy"] == pytest.approx(0.70)

    def test_shots_not_double_counted_across_combatants(self):
        """C1's shots do NOT increment C2's shots_fired and vice versa."""
        c1, c2 = _make_states()
        events = [_fight_start_event("C1", "C2")]
        events.append(_weapon_fire_event("C1", "C2", tick=1, hit=True))
        events.append(_weapon_fire_event("C2", "C1", tick=2, hit=False))
        events.append(_fight_end_event(3, None, "time_cap", 3, _hp(100), _hp(100)))
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 3, None)
        assert s["combatants"]["1"]["shots_fired"] == 1
        assert s["combatants"]["2"]["shots_fired"] == 1
        assert s["combatants"]["1"]["shots_hit"] == 1
        assert s["combatants"]["2"]["shots_hit"] == 0


# ---------------------------------------------------------------------------
# TestDamageDealAndTaken — damage_dealt / damage_taken sourced from damage events
# ---------------------------------------------------------------------------


class TestDamageDealtAndTaken:
    def test_damage_dealt_from_damage_events_not_weapon_fire(self):
        """damage_dealt is sourced from damage events, NOT weapon_fire total_damage."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire_event("C1", "C2", tick=1, hit=True),
            # Only 30 HP absorbed even though weapon may have fired more
            _damage_event("C2", attacker="C1", amount=30, tick=1),
            _fight_end_event(2, "C1", "hp_depleted", 2, _hp(100), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 2, "C1")
        assert s["combatants"]["1"]["damage_dealt"] == 30

    def test_damage_taken_equals_damage_dealt_opponent(self):
        """C2.damage_taken == C1.damage_dealt when C1 is the only attacker."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _damage_event("C2", attacker="C1", amount=40, tick=1),
            _damage_event("C2", attacker="C1", amount=25, tick=2),
            _fight_end_event(3, None, "time_cap", 3, _hp(100), _hp(35)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 3, None)
        assert s["combatants"]["1"]["damage_dealt"] == 65
        assert s["combatants"]["2"]["damage_taken"] == 65

    def test_invuln_events_excluded_from_damage_dealt(self):
        """Invuln-blocked damage events (amount=0) do NOT contribute to damage_dealt."""
        c1, c2 = _make_states()
        invuln_event = CombatEvent(
            tick=2,
            type=CombatEventType.damage,
            actor=None,
            target="C2",
            data={
                "amount": 0,
                "absorbed": 0,  # T10: explicitly 0 for invuln-blocked events
                "hp_after": _hp(100),
                "source": {"subtype": "primary", "weapon": "TestGun", "attacker": "C1"},
                "blocked_by": "emergency_system_invuln",
            },
        )
        events = [
            _fight_start_event("C1", "C2"),
            _damage_event("C2", attacker="C1", amount=20, tick=1),
            invuln_event,
            _fight_end_event(3, None, "time_cap", 3, _hp(100), _hp(80)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 3, None)
        # Only 20 should count — the invuln event (amount=0) is excluded
        assert s["combatants"]["1"]["damage_dealt"] == 20

    def test_damage_dealt_cluster_post_clamp(self):
        """Cluster missile: damage_dealt reflects post-clamp absorbed, NOT swung total.

        Scenario: cluster fires 3 sub-munitions, first kills (absorbs remaining hull 30),
        last two overkill. Only 30 HP actually absorbed per damage events.

        T10: uses absorbed field; raw amount for sub-munitions 2+3 could be >0 (raw overkill),
        but absorbed=0 for overkill hits.
        """
        c1, c2 = _make_states()
        # Simulate cluster scenario: target hull=30, cluster fires 3 sub-munitions
        # First sub-munition absorbs 30; second + third: raw=30 each but absorbed=0 (overkill)
        overkill_ev = CombatEvent(
            tick=1,
            type=CombatEventType.damage,
            actor=None,
            target="C2",
            data={
                "amount": 30,  # raw overkill — kept for log display
                "absorbed": 0,  # T10: no HP actually removed (target already dead)
                "breakdown": {"shield": 0, "armour": 0, "hull": 30},
                "hp_after": _hp(-30),
                "source": {"subtype": "cluster-missile", "weapon": "ClusterBomb", "attacker": "C1"},
            },
        )
        events = [
            _fight_start_event("C1", "C2", hull1=200, hull2=30),
            _damage_event("C2", attacker="C1", amount=30, tick=1),  # first sub: absorbs 30
            overkill_ev,  # second sub: raw=30 but absorbed=0
            overkill_ev,  # third sub: raw=30 but absorbed=0
            _fight_end_event(2, "C1", "hp_depleted", 2, _hp(200), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 2, "C1")
        assert s["combatants"]["1"]["damage_dealt"] == 30


# ---------------------------------------------------------------------------
# TestModuleActivationsSummary — module_activations sparse dict (Q6)
# ---------------------------------------------------------------------------


class TestModuleActivationsSummary:
    def test_no_activations_returns_empty_dict(self):
        """No module activations -> module_activations is {} (outer always present, keys sparse)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(1, None, "time_cap", 1, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 1, None)
        assert s["combatants"]["1"]["module_activations"] == {}
        assert s["combatants"]["2"]["module_activations"] == {}

    def test_cloak_counted_lowercase_snake(self):
        """Cloak activation -> key 'cloak' (lowercase_snake_case per Q5)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _module_activation_event("C1", "cloak", tick=2, trigger_pct=66),
            _module_activation_event("C1", "cloak", tick=5, trigger_pct=33),
            _fight_end_event(10, None, "time_cap", 10, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)
        ma = s["combatants"]["1"]["module_activations"]
        assert ma == {"cloak": 2}

    def test_booster_counted_lowercase_snake(self):
        """Booster activation -> key 'booster' (lowercase_snake_case per Q5)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _module_activation_event("C2", "booster", tick=3, trigger_pct=80),
            _module_activation_event("C2", "booster", tick=6, trigger_pct=60),
            _module_activation_event("C2", "booster", tick=9, trigger_pct=40),
            _fight_end_event(10, None, "time_cap", 10, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)
        ma = s["combatants"]["2"]["module_activations"]
        assert ma == {"booster": 3}

    def test_emergency_system_counted_lowercase_snake(self):
        """ES activation -> key 'emergency_system' (lowercase_snake_case per Q5)."""
        c1, c2 = _make_states()
        # ES event omits trigger_hp_pct
        es_event = CombatEvent(
            tick=4,
            type=CombatEventType.module_activation,
            actor="C1",
            target=None,
            data={"module": "emergency_system"},
        )
        events = [
            _fight_start_event("C1", "C2"),
            es_event,
            _fight_end_event(10, None, "time_cap", 10, _hp(1), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)
        ma = s["combatants"]["1"]["module_activations"]
        assert ma == {"emergency_system": 1}

    def test_mixed_cloak_booster_es_counted_correctly(self):
        """Cloak x2, booster x3, ES x1 on C1 -> correct sparse dict."""
        c1, c2 = _make_states()
        events = [_fight_start_event("C1", "C2")]
        events += [_module_activation_event("C1", "cloak", tick=i + 1, trigger_pct=66) for i in range(2)]
        events += [_module_activation_event("C1", "booster", tick=i + 3, trigger_pct=80) for i in range(3)]
        es_ev = CombatEvent(
            tick=8,
            type=CombatEventType.module_activation,
            actor="C1",
            target=None,
            data={"module": "emergency_system"},
        )
        events.append(es_ev)
        events.append(_fight_end_event(20, None, "time_cap", 20, _hp(1), _hp(100)))
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 20, None)
        ma = s["combatants"]["1"]["module_activations"]
        assert ma == {"cloak": 2, "booster": 3, "emergency_system": 1}

    def test_passive_modules_not_counted(self):
        """Passive module events are NOT in module_activations (section 13: passives excluded)."""
        c1, c2 = _make_states()
        # Passive modules (repair_bot, thruster, primary_weapon_mod) must not appear
        passive_event = CombatEvent(
            tick=1,
            type=CombatEventType.module_activation,
            actor="C1",
            target=None,
            data={"module": "repair_bot"},  # passives must not be counted
        )
        events = [
            _fight_start_event("C1", "C2"),
            passive_event,
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        ma = s["combatants"]["1"]["module_activations"]
        # repair_bot is NOT in the discrete-activation set (cloak/booster/emergency_system)
        assert "repair_bot" not in ma

    def test_sparse_absent_keys_omitted(self):
        """Keys that never fired must be omitted from module_activations (SPARSE Q6)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _module_activation_event("C1", "cloak", tick=2, trigger_pct=66),
            _fight_end_event(10, None, "time_cap", 10, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)
        ma = s["combatants"]["1"]["module_activations"]
        # booster and emergency_system not fired -> must be absent from the dict
        assert "booster" not in ma
        assert "emergency_system" not in ma
        assert ma == {"cloak": 1}


# ---------------------------------------------------------------------------
# TestSecondaryFiredSummary — secondary_fired sparse dict (Q6)
# ---------------------------------------------------------------------------


class TestSecondaryFiredSummary:
    def _sec_event(self, actor: str, subtype: str, tick: int = 1) -> CombatEvent:
        return CombatEvent(
            tick=tick,
            type=CombatEventType.weapon_fire,
            actor=actor,
            target="C2",
            data={
                "slot": "secondary",
                "subtype": subtype,
                "weapon": f"{subtype}-weapon",
                "hit": True,
                "accuracy": 1.0,
            },
        )

    def test_no_secondaries_empty_dict(self):
        """No secondaries fired -> secondary_fired is {} (outer always present, sparse Q6)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(1, None, "time_cap", 1, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 1, None)
        assert s["combatants"]["1"]["secondary_fired"] == {}
        assert s["combatants"]["2"]["secondary_fired"] == {}

    def test_rocket_counted(self):
        """Rocket weapon_fire counted under 'rocket' key."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            self._sec_event("C1", "rocket", tick=1),
            self._sec_event("C1", "rocket", tick=2),
            _fight_end_event(3, None, "time_cap", 3, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 3, None)
        sf = s["combatants"]["1"]["secondary_fired"]
        assert sf == {"rocket": 2}

    def test_cluster_missile_key(self):
        """cluster-missile secondary counted under 'cluster-missile' key (hyphenated per spec)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            self._sec_event("C1", "cluster-missile", tick=1),
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        sf = s["combatants"]["1"]["secondary_fired"]
        assert "cluster-missile" in sf
        assert sf["cluster-missile"] == 1

    def test_shock_blast_key(self):
        """shock-blast secondary counted under 'shock-blast' key (hyphenated per spec)."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            self._sec_event("C1", "shock-blast", tick=1),
            self._sec_event("C1", "shock-blast", tick=2),
            self._sec_event("C1", "shock-blast", tick=3),
            _fight_end_event(4, None, "time_cap", 4, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 4, None)
        sf = s["combatants"]["1"]["secondary_fired"]
        assert sf == {"shock-blast": 3}

    def test_mixed_secondaries_per_subtype(self):
        """Multiple secondary subtypes counted separately per subtype."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            self._sec_event("C1", "rocket", tick=1),
            self._sec_event("C1", "missile", tick=2),
            self._sec_event("C1", "missile", tick=3),
            self._sec_event("C1", "nuke", tick=4),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        sf = s["combatants"]["1"]["secondary_fired"]
        assert sf["rocket"] == 1
        assert sf["missile"] == 2
        assert sf["nuke"] == 1

    def test_primary_slot_not_in_secondary_fired(self):
        """Primary weapon_fire events do NOT count in secondary_fired."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire_event("C1", "C2", tick=1, hit=True),  # primary
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["secondary_fired"] == {}


# ---------------------------------------------------------------------------
# TestStartAndFinalHP — start_hp / final_hp sourced from fight events
# ---------------------------------------------------------------------------


class TestStartAndFinalHP:
    def test_start_hp_from_fight_start_event(self):
        """start_hp is sourced from the fight_start event payload."""
        c1, c2 = _make_states(hull1=150, hull2=80)
        events = [
            _fight_start_event("C1", "C2", hull1=150, hull2=80),
            _fight_end_event(5, None, "time_cap", 5, _hp(150), _hp(80)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        assert s["combatants"]["1"]["start_hp"]["hull"] == 150
        assert s["combatants"]["2"]["start_hp"]["hull"] == 80

    def test_final_hp_from_fight_end_event_post_clamp(self):
        """final_hp is sourced from fight_end (post-clamp values; must be non-negative)."""
        c1, c2 = _make_states()
        c1_final = _hp(42)
        c2_final = _hp(0)  # clamped to 0
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(15, "C1", "hp_depleted", 15, c1_final, c2_final),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 15, "C1")
        assert s["combatants"]["1"]["final_hp"] == c1_final
        assert s["combatants"]["2"]["final_hp"] == c2_final
        # Values must be non-negative (post-clamp)
        assert s["combatants"]["2"]["final_hp"]["hull"] == 0


# ---------------------------------------------------------------------------
# TestFightResultsEnvelope — FightResults.metadata shape (section 12)
# ---------------------------------------------------------------------------


class TestFightResultsEnvelope:
    def test_metadata_schema_version(self):
        """FightResults.metadata['schema_version'] == 1 (section 12)."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo)
        assert result.metadata["schema_version"] == 1

    def test_metadata_has_summary_key(self):
        """FightResults.metadata contains 'summary' key."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo)
        assert "summary" in result.metadata

    def test_metadata_inner_keys(self):
        """FightResults.metadata['metadata'] contains tick_ms, total_ticks, resolver, pvc_damage_reduction."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo, pvc_damage_reduction=0.33)
        md = result.metadata["metadata"]
        assert md["tick_ms"] == GameConstants.TICK_MS
        assert md["resolver"] == "tick_v1"
        assert md["pvc_damage_reduction"] == 0.33
        assert "total_ticks" in md

    def test_combat_log_is_timeline_list(self):
        """FightResults.combat_log is the full in-memory event list (CombatEvent objects)."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo)
        log = result.combat_log
        assert isinstance(log, list)
        assert len(log) >= 2  # at least fight_start + fight_end
        assert log[0].type == CombatEventType.fight_start
        assert log[-1].type == CombatEventType.fight_end

    def test_total_ticks_in_metadata_inner(self):
        """total_ticks is in the nested 'metadata' dict (T9 envelope change)."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo)
        assert result.metadata["metadata"]["total_ticks"] == GameConstants.MAX_FIGHT_TICKS

    def test_pvc_damage_reduction_passthrough(self):
        """pvc_damage_reduction in metadata.metadata equals the value passed to resolver."""
        lo = _loadout(base_armour=100)
        result = TickResolver(seed=42).resolve(lo, lo, pvc_damage_reduction=0.0)
        assert result.metadata["metadata"]["pvc_damage_reduction"] == 0.0


# ---------------------------------------------------------------------------
# TestSummaryEndToEnd — full resolver + summary integration
# ---------------------------------------------------------------------------


class TestSummaryEndToEnd:
    def test_summary_in_full_fight(self):
        """summary dict is populated correctly for a full fight with a winner."""
        attacker = _loadout(
            ship_name="C1",
            base_armour=200,
            weapons=[_gun(dps=100.0, dmg=100, speed_ms=100, range_m=5000.0)],
        )
        defender = _loadout(ship_name="C2", base_armour=50)
        result = TickResolver(seed=0).resolve(attacker, defender)
        s = _summary_of(result)

        assert s["outcome"] in ("win", "stalemate")
        assert s["reason"] in ("hp_depleted", "time_cap", "mutual")
        assert "combatants" in s
        for ck in ("1", "2"):
            cb = s["combatants"][ck]
            assert "shots_fired" in cb
            assert "accuracy" in cb
            assert "damage_dealt" in cb
            assert "module_activations" in cb
            assert isinstance(cb["module_activations"], dict)
            assert "secondary_fired" in cb
            assert isinstance(cb["secondary_fired"], dict)

    def test_damage_dealt_plus_taken_symmetric(self):
        """In a two-combatant fight, C1.damage_dealt == C2.damage_taken (no other sources)."""
        lo1 = _loadout(
            ship_name="C1",
            base_armour=300,
            weapons=[_gun(dps=50.0, dmg=50, speed_ms=200, range_m=5000.0)],
        )
        lo2 = _loadout(ship_name="C2", base_armour=300)
        result = TickResolver(seed=0).resolve(lo1, lo2)
        s = _summary_of(result)
        # C1 fires, C2 has no weapons; so C1.damage_dealt should == C2.damage_taken
        assert s["combatants"]["1"]["damage_dealt"] == s["combatants"]["2"]["damage_taken"]
        # C2 has no weapons — C2.damage_dealt should be 0
        assert s["combatants"]["2"]["damage_dealt"] == 0
        assert s["combatants"]["1"]["damage_taken"] == 0


# ---------------------------------------------------------------------------
# TestSecondaryRoundsByWeapon — P2-T3: secondary_rounds_by_weapon sparse dict
# ---------------------------------------------------------------------------


def _sec_event_with_side(actor: str, subtype: str, weapon: str, side: int, tick: int = 1) -> CombatEvent:
    """Secondary weapon_fire event with data['side'] set for correct slot attribution."""
    return CombatEvent(
        tick=tick,
        type=CombatEventType.weapon_fire,
        actor=actor,
        target="other",
        data={
            "slot": "secondary",
            "subtype": subtype,
            "weapon": weapon,
            "hit": True,
            "accuracy": 1.0,
            "side": side,  # attacker's slot — mirrors what the resolver emits (CI-20)
        },
    )


class TestSecondaryRoundsByWeapon:
    # ------------------------------------------------------------------
    # Additive check: pre-existing combatant keys unchanged with no secondaries
    # ------------------------------------------------------------------

    _PRE_EXISTING_KEYS = (
        "name",
        "ship",
        "start_hp",
        "final_hp",
        "damage_dealt",
        "damage_taken",
        "shots_fired",
        "shots_hit",
        "accuracy",
        "module_activations",
        "secondary_fired",
    )

    def test_pre_existing_keys_byte_identical_no_secondaries(self):
        """All pre-existing combatant keys are present and byte-identical when no secondaries fire.

        The only change to the block is a new key; existing values must be unaltered.
        """
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire_event("C1", "C2", tick=1, hit=True),
            _damage_event("C2", attacker="C1", amount=20, tick=1),
            _fight_end_event(10, "C1", "hp_depleted", 10, _hp(100), _hp(0)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 10, "C1")
        for ck in ("1", "2"):
            cb = s["combatants"][ck]
            for key in self._PRE_EXISTING_KEYS:
                assert key in cb, f"Combatant {ck} missing pre-existing key: {key}"
        # Spot-check values are unchanged (not just present)
        assert s["combatants"]["1"]["shots_fired"] == 1
        assert s["combatants"]["1"]["shots_hit"] == 1
        assert s["combatants"]["1"]["accuracy"] == pytest.approx(1.0)
        assert s["combatants"]["1"]["damage_dealt"] == 20
        assert s["combatants"]["2"]["secondary_fired"] == {}

    def test_new_field_present_in_combatant_block(self):
        """secondary_rounds_by_weapon key is present in every combatant block."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(1, None, "time_cap", 1, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 1, None)
        for ck in ("1", "2"):
            assert "secondary_rounds_by_weapon" in s["combatants"][ck]

    def test_empty_when_no_secondaries(self):
        """secondary_rounds_by_weapon is {} for both sides when no secondary fires occur."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _fight_end_event(1, None, "time_cap", 1, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 1, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {}
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == {}

    def test_primary_events_not_tallied(self):
        """Primary weapon_fire events (slot='primary') do NOT appear in secondary_rounds_by_weapon."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _weapon_fire_event("C1", "C2", tick=1, hit=True),  # primary slot
            _fight_end_event(2, None, "time_cap", 2, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 2, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {}

    # ------------------------------------------------------------------
    # Correctness: side-keyed counts match hand-tally of secondary weapon_fire events
    # ------------------------------------------------------------------

    def test_single_weapon_single_fire(self):
        """One secondary fire on side 1: secondary_rounds_by_weapon[weapon_name] == 1."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _sec_event_with_side("C1", "rocket", "Viper Rocket", side=1, tick=1),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"Viper Rocket": 1}
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == {}

    def test_multiple_fires_same_weapon(self):
        """Three fires of the same weapon on side 1: count == 3."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _sec_event_with_side("C1", "rocket", "Viper Rocket", side=1, tick=1),
            _sec_event_with_side("C1", "rocket", "Viper Rocket", side=1, tick=2),
            _sec_event_with_side("C1", "rocket", "Viper Rocket", side=1, tick=3),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"Viper Rocket": 3}

    def test_two_weapons_two_sides(self):
        """C1 fires RocketA x2; C2 fires MissileB x1. Both sides tallied independently."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _sec_event_with_side("C1", "rocket", "RocketA", side=1, tick=1),
            _sec_event_with_side("C1", "rocket", "RocketA", side=1, tick=2),
            _sec_event_with_side("C2", "missile", "MissileB", side=2, tick=3),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"RocketA": 2}
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == {"MissileB": 1}

    def test_mixed_weapons_per_side(self):
        """C1 fires RocketA x2 and MissileC x1; counts split by weapon name."""
        c1, c2 = _make_states()
        events = [
            _fight_start_event("C1", "C2"),
            _sec_event_with_side("C1", "rocket", "RocketA", side=1, tick=1),
            _sec_event_with_side("C1", "missile", "MissileC", side=1, tick=2),
            _sec_event_with_side("C1", "rocket", "RocketA", side=1, tick=3),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"RocketA": 2, "MissileC": 1}

    # ------------------------------------------------------------------
    # Criterion match: secondary_rounds_by_weapon mirrors _consume_secondary_ammo scan
    # ------------------------------------------------------------------

    def test_matches_consume_ammo_scan_criterion(self):
        """secondary_rounds_by_weapon per side matches a manual scan using _consume_secondary_ammo
        criterion (weapon_fire events with slot=='secondary', counted by data['weapon']).

        This verifies P2-T5 can collapse the full-timeline scan onto the summary field
        and produce identical ammo decrements.
        """
        c1, c2 = _make_states("Pilot1", "Pilot2")
        sec_events = [
            _sec_event_with_side("Pilot1", "rocket", "Viper Rocket", side=1, tick=1),
            _sec_event_with_side("Pilot1", "rocket", "Viper Rocket", side=1, tick=2),
            _sec_event_with_side("Pilot1", "missile", "Patala Missile", side=1, tick=3),
            _sec_event_with_side("Pilot2", "rocket", "Viper Rocket", side=2, tick=4),
        ]
        events = [
            _fight_start_event("Pilot1", "Pilot2"),
            *sec_events,
            _fight_end_event(10, None, "time_cap", 10, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 10, None)

        # Hand-tally using same criterion as _consume_secondary_ammo:
        # filter weapon_fire, slot=="secondary", count by data["weapon"] per side
        hand_tally: dict[str, dict[str, int]] = {"1": {}, "2": {}}
        for ev in sec_events:
            if ev.data.get("slot") == "secondary":
                wname = ev.data.get("weapon", "")
                slot_str = str(ev.data["side"])
                if wname and slot_str in hand_tally:
                    hand_tally[slot_str][wname] = hand_tally[slot_str].get(wname, 0) + 1

        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == hand_tally["1"]
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == hand_tally["2"]

    # ------------------------------------------------------------------
    # Same-name correctness: side attribution via data["side"] not actor name
    # ------------------------------------------------------------------

    def test_same_name_ships_split_by_side_not_name(self):
        """When both combatants share an identical ship name, secondary_rounds_by_weapon
        must split counts correctly by side (data['side']), NOT collapse by actor name.

        C1 fires 'Viper Rocket' x2 (side=1); C2 fires 'Viper Rocket' x1 (side=2).
        Both ships are named 'SameName'. Without side-keying they would collapse.
        """
        c1, c2 = _make_states("SameName", "SameName")  # identical ship names
        events = [
            _fight_start_event("SameName", "SameName"),
            _sec_event_with_side("SameName", "rocket", "Viper Rocket", side=1, tick=1),
            _sec_event_with_side("SameName", "rocket", "Viper Rocket", side=1, tick=2),
            _sec_event_with_side("SameName", "rocket", "Viper Rocket", side=2, tick=3),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        # Side 1 fired 2; side 2 fired 1 — must NOT be merged by name
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"Viper Rocket": 2}
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == {"Viper Rocket": 1}

    def test_same_name_no_name_collision_in_secondary_fired(self):
        """Same-name fight: secondary_fired is also side-keyed; verify both fields agree
        on the same-name attribution invariant.
        """
        c1, c2 = _make_states("Clone", "Clone")
        events = [
            _fight_start_event("Clone", "Clone"),
            _sec_event_with_side("Clone", "missile", "Patala Missile", side=1, tick=1),
            _sec_event_with_side("Clone", "missile", "Patala Missile", side=2, tick=2),
            _sec_event_with_side("Clone", "missile", "Patala Missile", side=2, tick=3),
            _fight_end_event(5, None, "time_cap", 5, _hp(100), _hp(100)),
        ]
        s = _build_fight_summary(events, c1, c2, "stalemate", "time_cap", 5, None)
        # secondary_fired: keyed by subtype
        assert s["combatants"]["1"]["secondary_fired"] == {"missile": 1}
        assert s["combatants"]["2"]["secondary_fired"] == {"missile": 2}
        # secondary_rounds_by_weapon: keyed by weapon name — must match same split
        assert s["combatants"]["1"]["secondary_rounds_by_weapon"] == {"Patala Missile": 1}
        assert s["combatants"]["2"]["secondary_rounds_by_weapon"] == {"Patala Missile": 2}

    # ------------------------------------------------------------------
    # TickResolver integration: real fight with secondaries
    # ------------------------------------------------------------------

    def test_resolver_fight_with_secondaries_has_field(self):
        """A TickResolver fight with a secondary-equipped ship produces secondary_rounds_by_weapon
        in the summary, and the total count matches weapon_fire events in the combat log.
        """
        from src.services.combat_models import WeaponStats

        rocket = WeaponStats(
            name="Viper Rocket",
            dps=10.0,
            damage_per_shot=100,
            loading_speed_ms=500,
            range_m=5000.0,
            subtype="rocket",
        )
        lo1 = ShipLoadout(
            ship_name="Attacker",
            base_armour=500,
            weapons=[],
            secondary_weapons=[rocket],
        )
        lo2 = ShipLoadout(ship_name="Defender", base_armour=500, weapons=[], secondary_weapons=[])
        result = TickResolver(seed=42).resolve(lo1, lo2)
        s = result.metadata["summary"]

        # Gather weapon_fire secondary events from combat_log for side 1
        timeline_count: dict[str, int] = {}
        for ev in result.combat_log:
            if ev.type == "weapon_fire" and ev.data.get("slot") == "secondary":
                side_str = str(ev.data.get("side", ""))
                if side_str == "1":
                    wname = ev.data.get("weapon", "")
                    if wname:
                        timeline_count[wname] = timeline_count.get(wname, 0) + 1

        field = s["combatants"]["1"]["secondary_rounds_by_weapon"]
        assert field == timeline_count, (
            f"secondary_rounds_by_weapon {field!r} != hand-tally from combat_log {timeline_count!r}"
        )
