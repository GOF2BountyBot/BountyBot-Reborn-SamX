"""Thread-5 combat chained-activation tests (BALANCE_JOURNAL §E / decision log).

Covers the LOCKED Thread-5 design:
- Trigger A: EmergencySystem activates → Booster activates (if off cooldown + not active); lost on cd.
- Trigger B: EmergencySystem ends (invuln >0 → 0) → Cloak activates (if off cd + not active); lost on cd.
- Phase-5 cloak guard: cloak does NOT activate while invuln_remaining_ms > 0.
- Same-tick ES resolution = ES ✓ / Booster ✓ / Cloak ✗ (falls out of phase order + injections).
- Already-active cloak/booster is never refreshed nor cut short.
- Regen continues during invuln.
- Distinct telemetry markers (trigger="emergency_activate"/"emergency_end") emitted + counted.
- Parser / summary render the new markers legibly.

Real-object-first: a real TickResolver drives full-fight integration tests; the trigger-helper
unit tests use real _CombatantState objects from _init_combatant (no mocks).
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
from src.services.combat_models import (
    CombatEventType,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from src.services.combat_resolver import (
    _BOOSTER_MODULE_TYPE,
    _CLOAK_MODULE_TYPE,
    _EMERGENCY_SYSTEM_MODULE_TYPE,
    _CombatantState,
    _eval_emergency_system,
    _eval_hp_threshold_modules,
    _extract_key_events,
    _init_combatant,
    _try_activate_chained_module,
)
from src.services.game_constants import GameConstants

TICK_MS = GameConstants.TICK_MS
INVULN_MS = GameConstants.EMERGENCY_SYSTEM_INVULN_S * 1000
CLOAK_THRESHOLDS = list(GameConstants.CLOAK_HP_THRESHOLDS_PCT)
BOOSTER_THRESHOLDS = list(GameConstants.BOOSTER_HP_THRESHOLDS_PCT)


# ---------------------------------------------------------------------------
# Module fixtures
# ---------------------------------------------------------------------------
def _es_mod(name: str = "Emergency System") -> ModuleStats:
    return ModuleStats(name=name, module_type=_EMERGENCY_SYSTEM_MODULE_TYPE)


def _cloak_mod(name: str = "Sight Suppressor", effect_duration_ms: int = 20_000, loading_speed_ms: int = 6_500):
    return ModuleStats(
        name=name,
        module_type=_CLOAK_MODULE_TYPE,
        effect_duration_ms=effect_duration_ms,
        loading_speed_ms=loading_speed_ms,
    )


def _booster_mod(
    name: str = "Cyclotron Boost",
    effect_pct: float = 80.0,
    effect_duration_ms: int = 4_400,
    loading_speed_ms: int = 10_000,
):
    return ModuleStats(
        name=name,
        module_type=_BOOSTER_MODULE_TYPE,
        effect_pct=effect_pct,
        effect_duration_ms=effect_duration_ms,
        loading_speed_ms=loading_speed_ms,
    )


def _loadout(*, base_armour: int = 100, modules=None, weapons=None) -> ShipLoadout:
    return ShipLoadout(
        ship_name="C1",
        base_armour=base_armour,
        modules=modules or [],
        weapons=weapons or [],
    )


def _state(*, modules) -> _CombatantState:
    """Real combatant with the given modules; cloak/booster start ready (cd=0, effect=0)."""
    return _init_combatant(_loadout(base_armour=100, modules=modules), is_player=False)


def _acts(events, module: str | None = None, trigger: str | None = None) -> list:
    out = [e for e in events if e.type == CombatEventType.module_activation]
    if module is not None:
        out = [e for e in out if e.data.get("module") == module]
    if trigger is not None:
        out = [e for e in out if e.data.get("trigger") == trigger]
    return out


# ---------------------------------------------------------------------------
# Trigger A — ES activates → Booster activates
# ---------------------------------------------------------------------------
class TestTriggerA_EmergencyActivatesBooster:
    def test_es_fires_booster_when_off_cooldown(self):
        s = _state(modules=[_es_mod(), _booster_mod()])
        s.current_hull = -5  # lethal: hull <= 0 → ES fires
        events: list = []
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        # ES fired and clamped hull
        assert s.current_hull == 1
        assert s.es_runtime.invuln_remaining_ms == INVULN_MS
        # Booster chain-activated with the distinct marker
        boost = _acts(events, module="booster", trigger="emergency_activate")
        assert len(boost) == 1
        assert s.booster_runtime.effect_remaining_ms == s.booster_runtime.stats.effect_duration_ms
        assert s.booster_runtime.activation_count == 1
        # The booster marker carries NO trigger_hp_pct (distinguishable from HP-threshold path)
        assert "trigger_hp_pct" not in boost[0].data

    def test_es_does_not_fire_booster_when_on_cooldown(self):
        """G2: chain is one-shot at the trigger instant — booster on cooldown → activation LOST."""
        s = _state(modules=[_es_mod(), _booster_mod()])
        s.booster_runtime.cooldown_remaining_ms = 3_000  # on cooldown
        s.current_hull = -5
        events: list = []
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        assert s.current_hull == 1  # ES still fires
        assert _acts(events, module="emergency_system")  # ES emitted
        assert _acts(events, module="booster") == []  # booster chain lost
        assert s.booster_runtime.effect_remaining_ms == 0

    def test_es_does_not_refresh_already_active_booster(self):
        """Already-active booster is NEVER refreshed by Trigger A."""
        s = _state(modules=[_es_mod(), _booster_mod()])
        s.booster_runtime.effect_remaining_ms = 1_000  # already active
        s.current_hull = -5
        events: list = []
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        assert s.booster_runtime.effect_remaining_ms == 1_000  # untouched, not refreshed
        assert _acts(events, module="booster") == []

    def test_no_booster_equipped_is_noop(self):
        s = _state(modules=[_es_mod()])
        s.current_hull = -5
        events: list = []
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        assert _acts(events, module="emergency_system")
        assert _acts(events, module="booster") == []


# ---------------------------------------------------------------------------
# Trigger B — ES ends → Cloak activates  (helper unit; full-fight covered below)
# ---------------------------------------------------------------------------
class TestTriggerB_EmergencyEndActivatesCloak:
    def test_chain_helper_fires_cloak_when_off_cooldown(self):
        s = _state(modules=[_cloak_mod()])
        events: list = []
        fired = _try_activate_chained_module(s, "cloak", "emergency_end", tick=0, events=events)
        assert fired is True
        acts = _acts(events, module="cloak", trigger="emergency_end")
        assert len(acts) == 1
        assert "trigger_hp_pct" not in acts[0].data
        assert s.cloak_runtime.effect_remaining_ms == s.cloak_runtime.stats.effect_duration_ms

    def test_chain_helper_lost_when_cloak_on_cooldown(self):
        s = _state(modules=[_cloak_mod()])
        s.cloak_runtime.cooldown_remaining_ms = 2_000
        events: list = []
        fired = _try_activate_chained_module(s, "cloak", "emergency_end", tick=0, events=events)
        assert fired is False
        assert _acts(events, module="cloak") == []

    def test_chain_helper_does_not_refresh_active_cloak(self):
        s = _state(modules=[_cloak_mod()])
        s.cloak_runtime.effect_remaining_ms = 5_000  # already active
        events: list = []
        fired = _try_activate_chained_module(s, "cloak", "emergency_end", tick=0, events=events)
        assert fired is False
        assert s.cloak_runtime.effect_remaining_ms == 5_000  # not cut short / refreshed

    def test_full_fight_es_end_triggers_cloak(self):
        """Integration: drive a real fight; the cloak fires at the ES invuln >0 → 0 transition
        with the emergency_end marker, and NOT during the invuln window."""
        events = _run_es_fight(with_cloak=True, with_booster=False)
        cloak_acts = _acts(events, module="cloak", trigger="emergency_end")
        assert len(cloak_acts) >= 1, "cloak should chain-activate at ES end"
        # Find ES activate tick and the cloak (ES-end) tick.
        es_act = _acts(events, module="emergency_system")
        assert es_act, "ES must have fired in this fight"
        es_tick = es_act[0].tick
        cloak_tick = cloak_acts[0].tick
        # Cloak fires AFTER ES (post-invuln), exactly INVULN_MS later (invuln >0 → 0 transition).
        assert cloak_tick > es_tick
        assert cloak_tick - es_tick == INVULN_MS // TICK_MS


# ---------------------------------------------------------------------------
# Phase-5 cloak guard — no cloak while invuln > 0
# ---------------------------------------------------------------------------
class TestCloakInvulnGuard:
    def test_cloak_threshold_suppressed_during_invuln(self):
        """A cloak HP-threshold crossing while invuln is active does NOT activate the cloak."""
        s = _state(modules=[_es_mod(), _cloak_mod()])
        s.es_runtime.invuln_remaining_ms = INVULN_MS  # invuln active
        # Set up a 66% downward crossing.
        s.prev_hp_pct = 0.70
        s.current_hull = 65
        events: list = []
        _eval_hp_threshold_modules(
            s, tick=0, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
        )
        assert _acts(events, module="cloak") == []  # suppressed by invuln guard
        assert s.cloak_runtime.effect_remaining_ms == 0

    def test_booster_threshold_still_fires_during_invuln(self):
        """The invuln guard is cloak-only — a booster threshold crossing still fires during invuln."""
        s = _state(modules=[_es_mod(), _booster_mod()])
        s.es_runtime.invuln_remaining_ms = INVULN_MS
        s.prev_hp_pct = 1.0
        s.current_hull = 79  # cross 80%
        events: list = []
        _eval_hp_threshold_modules(
            s, tick=0, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
        )
        assert len(_acts(events, module="booster")) == 1

    def test_cloak_fires_after_invuln_clears(self):
        """Once invuln is 0, the cloak threshold path is no longer suppressed."""
        s = _state(modules=[_es_mod(), _cloak_mod()])
        s.es_runtime.invuln_remaining_ms = 0  # invuln cleared
        s.prev_hp_pct = 0.70
        s.current_hull = 65
        events: list = []
        _eval_hp_threshold_modules(
            s, tick=0, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
        )
        assert len(_acts(events, module="cloak")) == 1


# ---------------------------------------------------------------------------
# Same-tick ES resolution: ES ✓ / Booster ✓ / Cloak ✗
# ---------------------------------------------------------------------------
class TestSameTickEsResolution:
    def test_es_tick_resolves_es_booster_not_cloak(self):
        """One tick crosses cloak+booster thresholds AND drops hull <= 0.

        Engine phase order: Phase 4a (ES + Trigger-A booster) runs BEFORE Phase 4b clamp and
        Phase 5 (HP-threshold). With hull clamped to 1 and invuln active:
        - Booster: already active via Trigger A → its Phase-5 threshold no-ops (not refreshed).
        - Cloak: Phase-5 invuln guard suppresses it.
        Net: ES ✓ / Booster ✓ / Cloak ✗ — no bespoke arbitration.
        """
        s = _state(modules=[_es_mod(), _cloak_mod(), _booster_mod()])
        s.prev_hp_pct = 1.0  # full HP last tick
        s.current_hull = -10  # lethal this tick (also implies thresholds crossed)
        events: list = []
        # Phase 4a
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        # Phase 4b clamp (engine does this between 4a and 5)
        s.current_hull = max(0, s.current_hull)  # already 1 from ES
        # Phase 5
        _eval_hp_threshold_modules(
            s, tick=0, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
        )
        assert len(_acts(events, module="emergency_system")) == 1
        assert len(_acts(events, module="booster")) == 1  # via Trigger A only
        assert _acts(events, module="booster")[0].data.get("trigger") == "emergency_activate"
        assert _acts(events, module="cloak") == []  # suppressed by invuln guard


# ---------------------------------------------------------------------------
# Telemetry / parser / summary rendering
# ---------------------------------------------------------------------------
class TestTelemetryAndRendering:
    def test_chained_activations_counted_in_summary(self):
        """Chained booster+cloak activations are counted in module_activations stats."""
        summary, events = _run_es_fight_full()
        # Defender is slot "1"; booster chained off ES activate, cloak chained off ES end.
        ma = summary["combatants"]["1"]["module_activations"]
        # At minimum: the chained activations are present and counted.
        booster_chain = _acts(events, module="booster", trigger="emergency_activate")
        cloak_chain = _acts(events, module="cloak", trigger="emergency_end")
        assert booster_chain, "booster should chain-activate off ES"
        assert cloak_chain, "cloak should chain-activate off ES end"
        assert ma.get("booster", 0) >= len(booster_chain)
        assert ma.get("cloak", 0) >= len(cloak_chain)

    def test_key_events_render_chain_markers(self):
        """_extract_key_events renders distinct 'why' text for the chained markers."""
        timeline = [
            {
                "tick": 100,
                "type": "module_activation",
                "actor": "C1",
                "target": None,
                "data": {"module": "booster", "trigger": "emergency_activate", "side": 1},
            },
            {
                "tick": 200,
                "type": "module_activation",
                "actor": "C1",
                "target": None,
                "data": {"module": "cloak", "trigger": "emergency_end", "side": 1},
            },
            {
                "tick": 300,
                "type": "module_activation",
                "actor": "C1",
                "target": None,
                "data": {"module": "cloak", "trigger_hp_pct": 66, "side": 1},
            },
        ]
        ke = _extract_key_events(timeline, tick_ms=TICK_MS)
        details = " | ".join(e["detail"] for e in ke)
        assert "activated booster (emergency system activated)" in details
        assert "activated cloak (emergency system ended)" in details
        assert "activated cloak (at 66% HP)" in details


# ---------------------------------------------------------------------------
# Real-fight harness — used by Trigger-B integration + summary tests
# ---------------------------------------------------------------------------
import random

from src.services.combat_resolver import TickResolver


def _glass_cannon_attacker() -> ShipLoadout:
    """A real attacker that reliably drops the defender's hull to <= 0 (triggers ES)."""
    gun = WeaponStats(name="Cannon", dps=400.0, damage_per_shot=400, loading_speed_ms=TICK_MS, range_m=100_000.0)
    return ShipLoadout(ship_name="Attacker", base_armour=100_000, modules=[], weapons=[gun])


def _es_defender(*, with_cloak: bool, with_booster: bool) -> ShipLoadout:
    mods: list[ModuleStats] = [_es_mod()]
    if with_cloak:
        mods.append(_cloak_mod())
    if with_booster:
        mods.append(_booster_mod())
    return ShipLoadout(ship_name="Defender", base_armour=300, modules=mods, weapons=[])


def _resolve_fight(*, with_cloak: bool, with_booster: bool):
    """Run a real deterministic fight; defender (slot 1) has ES (+optional cloak/booster)."""
    resolver = TickResolver()
    return resolver.resolve(
        _es_defender(with_cloak=with_cloak, with_booster=with_booster),
        _glass_cannon_attacker(),
        rng=random.Random(1234),
    )


def _run_es_fight(*, with_cloak: bool, with_booster: bool) -> list:
    """Returns the raw CombatEvent list so chain markers can be asserted."""
    return _resolve_fight(with_cloak=with_cloak, with_booster=with_booster).combat_log


def _run_es_fight_full():
    """Returns (summary_dict, events) with BOTH chained activations present."""
    result = _resolve_fight(with_cloak=True, with_booster=True)
    return result.metadata["summary"], result.combat_log
