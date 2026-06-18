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
from services.combat_models import (
    CombatEventType,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from services.combat_resolver import (
    _BOOSTER_MODULE_TYPE,
    _CLOAK_MODULE_TYPE,
    _EMERGENCY_SYSTEM_MODULE_TYPE,
    _REPAIR_BOT_MODULE_TYPE,
    _CombatantState,
    _eval_emergency_system,
    _eval_hp_threshold_modules,
    _extract_key_events,
    _init_combatant,
    _tick_module_effects,
    _tick_repair_bot_regen,
    _tick_shield_regen,
    _try_activate_chained_module,
)
from services.game_constants import GameConstants

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


def _repair_bot_mod(name: str = "Ketar Repair Bot", repair_rate: float = 0.05) -> ModuleStats:
    """Repair Bot module. NOTE: module_type MUST be set — _init_combatant only reads repair_rate
    from modules whose module_type == _REPAIR_BOT_MODULE_TYPE (combat_resolver.py:317-319)."""
    return ModuleStats(name=name, module_type=_REPAIR_BOT_MODULE_TYPE, repair_rate=repair_rate)


def _shield_mod(name: str = "Shield", shield: int = 100, shield_recharge_ms: int = 1_000) -> ModuleStats:
    return ModuleStats(name=name, shield=shield, shield_recharge_ms=shield_recharge_ms)


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

from services.combat_resolver import TickResolver


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


# ===========================================================================
# Adversarial edge-case anchors (BALANCE_JOURNAL Thread-5 §EDGE-CASE/GAP REVIEW
# G1 / G3 / G5 + the same-tick cloak-expiry G2 corollary). Code is signed-off
# correct; these lock in the named edge cases that lacked explicit coverage.
# ===========================================================================


def _state_with_armour(*, modules, base_armour: int = 100, armour: int = 0) -> _CombatantState:
    """Real combatant; optionally inject a flat armour layer via an armour-bearing module."""
    mods = list(modules)
    if armour > 0:
        mods.append(ModuleStats(name="Armour", armour=armour))
    return _init_combatant(_loadout(base_armour=base_armour, modules=mods), is_player=False)


# ---------------------------------------------------------------------------
# G1 — regen continues DURING the ES invuln window (hull/armour + shield both tick UP)
# ---------------------------------------------------------------------------
class TestG1RegenContinuesDuringInvuln:
    """BALANCE_JOURNAL G1 (LOCKED): Phase-2 regen is UNGATED by invuln. With a Repair Bot
    equipped (hull/armour below max) AND shield below max, both regen paths must tick UP across
    invuln ticks — proving Phase-2 regen is not frozen during the ES immunity window.

    Non-vacuity: if either regen were gated on `invuln_remaining_ms > 0`, the asserted upticks
    would never happen and these assertions fail.
    """

    def test_repair_bot_and_shield_regen_tick_up_during_invuln(self):
        # Repair Bot (rate 0.05) + Shield (100 cap, fast recharge). base_armour=hull=100, +50 armour.
        s = _state_with_armour(
            modules=[_es_mod(), _repair_bot_mod(repair_rate=0.05), _shield_mod(shield=100, shield_recharge_ms=TICK_MS)],
            base_armour=100,
            armour=50,
        )
        # Sanity: repair actually engaged (module_type wired) and shield schedule exists.
        assert s.repair_bot_rate_per_sec == 0.05
        assert s.max_shield == 100
        assert s.shield_regen_schedules, "shield regen schedule must be present"

        # Enter the invuln window with deficits on ALL three layers so regen has somewhere to go.
        # Hull near-full (small deficit) so Repair Bot fills hull FIRST then SPILLS into armour,
        # exercising both layers within the loop (repair fills hull→armour in that order).
        s.es_runtime.invuln_remaining_ms = INVULN_MS
        s.es_runtime.consumed = True
        s.current_hull = 98  # deficit 2 (max 100) — fills fast, then spillover hits armour
        s.current_armour = 10  # below max (50)
        s.current_shield = 0  # below max (100)

        hull0, armour0, shield0 = s.current_hull, s.current_armour, s.current_shield

        # Drive enough invuln ticks for both accumulators to flush several times.
        # Repair delta/tick = (100+50)*0.05*(10/1000) = 0.075/tick → ~15 HP over 200 ticks:
        # 2 to hull (clears deficit) then ~13 spill into armour.
        # Shield period = ceil(1000/100/10) = 1 tick → +1 shield/tick.
        events: list = []
        ticks = 200
        for t in range(ticks):
            assert s.es_runtime.invuln_remaining_ms > 0, "must stay inside invuln for the whole loop"
            _tick_repair_bot_regen(s, tick=t, events=events)
            _tick_shield_regen(s, tick=t, events=events)
            # Mirror the resolver's Phase-1 invuln tick-down so we stay honest about the window.
            s.es_runtime.invuln_remaining_ms = max(0, s.es_runtime.invuln_remaining_ms - TICK_MS)

        # Repair Bot fills hull first, then armour — both must have climbed above their start.
        assert s.current_hull > hull0, "hull regen must tick UP during invuln"
        assert s.current_armour > armour0, "armour regen must tick UP during invuln"
        # Shield regen is likewise ungated by invuln.
        assert s.current_shield > shield0, "shield regen must tick UP during invuln"
        # And genuine regen events were emitted (not a silent counter bump).
        regen = [e for e in events if e.type == CombatEventType.regen]
        layers = {e.data.get("layer") for e in regen}
        assert "shield" in layers
        assert {"hull", "armour"} & layers


# ---------------------------------------------------------------------------
# G3 — fight ENDS during invuln: no crash, Trigger B never fires (cloak not chained)
# ---------------------------------------------------------------------------
class TestG3FightEndsDuringInvuln:
    """BALANCE_JOURNAL G3 (accept): if the fight terminates while a combatant is still inside the
    ES invuln window, the loop breaks at Phase-8 BEFORE the next Phase-1 invuln>0→0 transition, so
    Trigger B (ES-end → Cloak) never fires. Resolution must complete cleanly with no cloak chain.

    Construction: a glass-cannon attacker with HUGE hull (never dies) vs a defender that has ES +
    cloak but ZERO survivability backing — once ES is consumed and its 10s invuln lapses the
    defender dies the very next lethal volley. We force the fight to end *inside* the window by
    capping MAX_FIGHT_TICKS below ES-tick + invuln duration so the loop hits the time-cap stalemate
    while invuln is still open on the defender.

    Non-vacuity: if Trigger B fired regardless of loop termination (e.g. an unconditional ES-end
    chain), a cloak emergency_end activation would appear and the assertion fails.
    """

    def test_time_cap_inside_invuln_no_cloak_chain(self):
        import random

        from services.combat_resolver import TickResolver

        from services import combat_resolver as _cr

        # Patch the resolver's own GameConstants reference (``_cr.GameConstants``) so ``resolve``
        # actually reads the capped value, rather than mutating a different import alias.
        _gc = _cr.GameConstants

        attacker = _glass_cannon_attacker()
        defender = ShipLoadout(
            ship_name="Defender",
            base_armour=300,
            modules=[_es_mod(), _cloak_mod()],
            weapons=[],
        )

        original_max = _gc.MAX_FIGHT_TICKS
        try:
            # Run once at full length to learn the ES-activation tick, then cap the fight to land
            # strictly inside [es_tick, es_tick + invuln) so termination happens mid-invuln.
            probe = TickResolver().resolve(defender, attacker, rng=random.Random(1234))
            es_acts = [
                e
                for e in probe.combat_log
                if e.type == CombatEventType.module_activation and e.data.get("module") == "emergency_system"
            ]
            assert es_acts, "ES must fire in the probe fight"
            es_tick = es_acts[0].tick
            invuln_ticks = INVULN_MS // TICK_MS
            # Cap a few ticks past ES so invuln is open but well short of the >0→0 transition.
            _gc.MAX_FIGHT_TICKS = es_tick + (invuln_ticks // 2)
            assert es_tick + invuln_ticks > _gc.MAX_FIGHT_TICKS

            result = TickResolver().resolve(defender, attacker, rng=random.Random(1234))
        finally:
            _gc.MAX_FIGHT_TICKS = original_max

        # Resolution completed without error and produced a terminal outcome.
        assert result.metadata["summary"]["outcome"] in {"win", "stalemate"}
        # ES fired but its invuln never reached the >0→0 edge before the loop broke → no cloak chain.
        cloak_chain = _acts(result.combat_log, module="cloak", trigger="emergency_end")
        assert cloak_chain == [], "Trigger B must NOT fire when the fight ends mid-invuln"
        # Belt: no cloak activation of ANY kind (HP-threshold path is invuln-suppressed too).
        assert _acts(result.combat_log, module="cloak") == []


# ---------------------------------------------------------------------------
# G5 — Trigger-A booster expires MID-invuln and does NOT re-fire (chain consumed, damage blocked)
# ---------------------------------------------------------------------------
class TestG5BoosterExpiresMidInvuln:
    """BALANCE_JOURNAL G5 (accept): booster effect duration (here 4.4s) < invuln (10s). The
    Trigger-A booster activates the instant ES fires, then EXPIRES while invuln is still open.
    It must NOT re-activate: the ES-chain is one-shot (already consumed) and the HP-threshold path
    can't re-fire because incoming damage is blocked during invuln → HP never moves → no crossing.

    Non-vacuity: if booster re-fired from a lingering chain or from a phantom HP crossing during
    invuln, a SECOND booster activation event would appear after expiry and the count assertion
    fails. (Confirmed by checking the activation count stays at exactly 1.)
    """

    def test_booster_expires_during_invuln_no_refire(self):
        # Short booster (4.4s effect) so it expires well inside the 10s invuln window.
        booster = _booster_mod(effect_duration_ms=4_400, loading_speed_ms=10_000)
        s = _state(modules=[_es_mod(), _cloak_mod(), booster])
        # Full HP at the start of "last tick" so a same-tick lethal hit crosses booster thresholds.
        s.prev_hp_pct = 1.0
        s.current_hull = -10  # lethal this tick → ES fires (Trigger A activates booster)

        events: list = []
        # Phase 4a: ES fires + Trigger-A booster.
        _eval_emergency_system(s, tick=0, events=events, invuln_ms=INVULN_MS)
        assert s.booster_runtime.effect_remaining_ms == booster.effect_duration_ms
        assert s.booster_runtime.activation_count == 1
        # Phase 4b clamp + Phase 5 (same tick): booster already active → its threshold no-ops.
        s.current_hull = max(0, s.current_hull)
        _eval_hp_threshold_modules(
            s, tick=0, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
        )
        assert s.booster_runtime.activation_count == 1, "no same-tick double-fire (already active)"

        # Now tick forward through the invuln window. Damage is blocked during invuln, so HP never
        # changes — there is no downward crossing to re-arm the threshold. The booster effect must
        # expire mid-invuln and then sit on cooldown, NOT re-activate.
        booster_expired_tick = None
        for t in range(1, (INVULN_MS // TICK_MS)):
            assert s.es_runtime.invuln_remaining_ms > 0, "still inside invuln for this loop"
            _tick_module_effects(s, tick=t, events=events, tick_ms=TICK_MS)
            # Resolver Phase-1 invuln tick-down (kept >0 by loop bound so Trigger B never fires here).
            s.es_runtime.invuln_remaining_ms = max(0, s.es_runtime.invuln_remaining_ms - TICK_MS)
            # HP-threshold phase re-evaluated each tick (HP frozen → no new crossing).
            _eval_hp_threshold_modules(
                s, tick=t, events=events, cloak_thresholds=CLOAK_THRESHOLDS, booster_thresholds=BOOSTER_THRESHOLDS
            )
            if booster_expired_tick is None and s.booster_runtime.effect_remaining_ms == 0:
                booster_expired_tick = t

        # The booster DID expire inside the invuln window...
        assert booster_expired_tick is not None, "booster should expire mid-invuln (4.4s < 10s)"
        assert booster_expired_tick * TICK_MS < INVULN_MS
        # ...and it is now on cooldown (loading_speed_ms set at expiry), having NEVER re-fired.
        assert s.booster_runtime.cooldown_remaining_ms > 0
        assert s.booster_runtime.activation_count == 1, "booster must NOT re-activate mid-invuln"
        assert len(_acts(events, module="booster")) == 1


# ---------------------------------------------------------------------------
# Same-tick edge — cloak effect expires the EXACT tick invuln hits 0 → Trigger B sees it on
# cooldown → chain activation is correctly LOST (G2 corollary).
# ---------------------------------------------------------------------------
class TestSameTickCloakExpiryInvulnEnd:
    """When cloak `effect_remaining_ms` and ES `invuln_remaining_ms` both reach 0 on the SAME
    Phase-1 tick, the resolver order is: T8 `_tick_module_effects` (sets cloak cooldown =
    loading_speed_ms on expiry) runs BEFORE T9 invuln>0→0 + Trigger B. So Trigger B observes the
    cloak ON COOLDOWN and the ES-end chain activation is LOST (G2). The cloak must NOT chain-fire.

    This reproduces the resolver's Phase-1 ordering using the REAL functions in the REAL order
    (_tick_module_effects, then the invuln tick-down + _try_activate_chained_module), so the
    cooldown-before-Trigger-B causality is exercised, not reimplemented.

    Non-vacuity: if Trigger B ran BEFORE the cloak cooldown were set (order swapped), the cloak
    would see cooldown==0 + effect==0 and chain-fire → an emergency_end cloak activation appears
    and the assertion fails.
    """

    def test_cloak_expiry_coincident_with_invuln_end_loses_chain(self):
        s = _state(modules=[_es_mod(), _cloak_mod()])
        # Arrange BOTH timers to hit 0 on this single tick.
        s.cloak_runtime.effect_remaining_ms = TICK_MS  # expires this tick
        s.es_runtime.invuln_remaining_ms = TICK_MS  # invuln >0 → 0 this tick
        s.es_runtime.consumed = True
        load_ms = s.cloak_runtime.stats.loading_speed_ms
        assert load_ms > 0, "cloak must have a real cooldown so on-cooldown is observable"

        events: list = []
        tick = 7
        # --- Resolver Phase-1 ordering, real functions ---
        # T8: effect expiry sets cloak cooldown = loading_speed_ms BEFORE Trigger B is evaluated.
        _tick_module_effects(s, tick=tick, events=events, tick_ms=TICK_MS)
        assert s.cloak_runtime.effect_remaining_ms == 0, "cloak effect expired this tick"
        assert s.cloak_runtime.cooldown_remaining_ms == load_ms, "cloak put ON COOLDOWN at expiry"
        # T9: invuln >0 → 0 transition fires Trigger B (ES-end → Cloak), but cloak is on cooldown.
        s.es_runtime.invuln_remaining_ms = max(0, s.es_runtime.invuln_remaining_ms - TICK_MS)
        assert s.es_runtime.invuln_remaining_ms == 0
        fired = _try_activate_chained_module(s, "cloak", "emergency_end", tick=tick, events=events)

        # Chain activation LOST (G2): cloak was on cooldown the instant ES ended.
        assert fired is False, "cloak chain must be LOST — it was on cooldown when ES ended"
        assert _acts(events, module="cloak", trigger="emergency_end") == []
        assert s.cloak_runtime.effect_remaining_ms == 0  # not re-activated
        assert s.cloak_runtime.cooldown_remaining_ms == load_ms  # still cooling, untouched
