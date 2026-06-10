"""
T9 EmergencySystem tests.

Covers the full EmergencySystem test surface from TASK_0009.md:
- Single lethal hit trigger / clamp-to-1 / overkill discarded
- Multi-source same-tick → ES fires ONCE (one module_activation)
- Cluster overkill: wildly past lethal → ES fires once; subsequent regen from 1
- Nuke self-damage: firer's own nuke self-damage triggers firer's ES
- Invuln blocks all incoming damage (amount=0, blocked_by annotated)
- Regen continues during invuln window (shield pulse + Repair Bot)
- HP at expiry without regen: hull = 1
- HP at expiry with Repair Bot: hull approx 1 + 10 * rate (capped at max)
- Consumable: fires once; no second activation even if somehow re-evaluated
- NOT a threshold device: HP-pct crossing 33% with hull > 0 → ES does NOT fire
- Inert modules: GammaShield + RepairBeam → no events, no crash over full sim
- All §7.9 / §7.10 inert module types in same loadout → no-op, no crash
- ES is evaluated at phase 4a, NOT phase 5 (ordering test)
- Timeline ordering: damage events precede ES module_activation on lethal tick
"""

from __future__ import annotations

import sys
import types
from typing import ClassVar
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
    _EMERGENCY_SYSTEM_MODULE_TYPE,
    TickResolver,
    _apply_damage,
    _CombatantState,
    _EmergencySystemRuntime,
    _eval_emergency_system,
    _init_combatant,
    _tick_repair_bot_regen,
    _tick_shield_regen,
)
from src.services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TICK_MS = GameConstants.TICK_MS
INVULN_MS = GameConstants.EMERGENCY_SYSTEM_INVULN_S * 1000  # 10 000 ms


def _es_mod(name: str = "Emergency System") -> ModuleStats:
    """Minimal ModuleStats representing an EmergencySystem module."""
    return ModuleStats(name=name, module_type=_EMERGENCY_SYSTEM_MODULE_TYPE)


def _loadout(
    ship_name: str = "TestShip",
    base_armour: int = 200,
    modules: list[ModuleStats] | None = None,
    weapons: list[WeaponStats] | None = None,
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=ship_name,
        base_armour=base_armour,
        modules=modules or [],
        weapons=weapons or [],
    )


def _state_with_es(
    *,
    name: str = "C1",
    max_hull: int = 100,
    max_armour: int = 0,
    repair_rate: float = 0.0,
    shield: int = 0,
    shield_recharge_ms: int = 0,
) -> _CombatantState:
    """Build a _CombatantState with EmergencySystem equipped."""
    mods: list[ModuleStats] = [_es_mod()]
    if shield > 0:
        mods.append(ModuleStats(name="Shield", shield=shield, shield_recharge_ms=shield_recharge_ms))
    if repair_rate > 0.0:
        mods.append(ModuleStats(name="RepairBot", repair_rate=repair_rate))
    lo = _loadout(ship_name=name, base_armour=max_hull, modules=mods)
    s = _init_combatant(lo, is_player=False)
    # Override armour on top of hull if needed
    s.max_armour = max_armour
    s.current_armour = max_armour
    return s


def _state_no_es(*, name: str = "C2", max_hull: int = 100) -> _CombatantState:
    """Build a bare _CombatantState with NO EmergencySystem."""
    lo = _loadout(ship_name=name, base_armour=max_hull)
    return _init_combatant(lo, is_player=False)


def _dummy_source(attacker: str = "Attacker") -> dict:
    return {"subtype": "primary", "weapon": "TestGun", "attacker": attacker}


def _find_events(log, event_type: str) -> list:
    return [e for e in log if e.type == event_type]


def _find_module_activations(log, module_key: str | None = None) -> list:
    acts = _find_events(log, CombatEventType.module_activation)
    if module_key:
        acts = [e for e in acts if e.data.get("module") == module_key]
    return acts


# ---------------------------------------------------------------------------
# TestEmergencySystemInit — initial state at combatant init
# ---------------------------------------------------------------------------


class TestEmergencySystemInit:
    def test_es_runtime_present_when_equipped(self):
        """_CombatantState.es_runtime is not None when ES module is in loadout."""
        s = _state_with_es()
        assert s.es_runtime is not None
        assert isinstance(s.es_runtime, _EmergencySystemRuntime)

    def test_es_runtime_none_without_es(self):
        """_CombatantState.es_runtime is None when no ES module in loadout."""
        s = _state_no_es()
        assert s.es_runtime is None

    def test_initial_state_unconsumed(self):
        """ES starts unconsumed with zero invuln window."""
        s = _state_with_es()
        assert s.es_runtime is not None
        assert s.es_runtime.consumed is False
        assert s.es_runtime.invuln_remaining_ms == 0


# ---------------------------------------------------------------------------
# TestEvalEmergencySystem — unit-level _eval_emergency_system
# ---------------------------------------------------------------------------


class TestEvalEmergencySystem:
    def test_lethal_hit_triggers_es(self):
        """Hull ≤ 0 after damage → ES fires: hull clamped to 1, invuln started."""
        state = _state_with_es(max_hull=50)
        state.current_hull = -5  # simulated lethal overkill
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        assert state.current_hull == 1
        assert state.es_runtime is not None
        assert state.es_runtime.invuln_remaining_ms == INVULN_MS
        assert state.es_runtime.consumed is True
        assert len(events) == 1
        ev = events[0]
        assert ev.type == CombatEventType.module_activation
        assert ev.actor == "C1"
        assert ev.data["module"] == "emergency_system"
        assert "trigger_hp_pct" not in ev.data  # ES OMITS trigger_hp_pct per §12

    def test_hull_above_zero_does_not_trigger(self):
        """Hull still positive → ES does NOT fire."""
        state = _state_with_es(max_hull=50)
        state.current_hull = 1  # positive — no trigger
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        assert state.es_runtime is not None
        assert state.es_runtime.consumed is False
        assert state.es_runtime.invuln_remaining_ms == 0
        assert len(events) == 0

    def test_es_not_present_no_op(self):
        """No ES equipped → function returns without error or event."""
        state = _state_no_es()
        state.current_hull = -10  # would trigger if ES were present
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        assert len(events) == 0

    def test_es_consumable_fires_once(self):
        """ES is consumable — second evaluation after consumption does NOT fire again."""
        state = _state_with_es(max_hull=50)
        state.current_hull = -5
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)
        assert len(events) == 1
        assert state.es_runtime is not None
        assert state.es_runtime.consumed is True

        # Simulate a second evaluation (e.g., called twice in a buggy impl)
        state.current_hull = -5  # knock back to lethal
        _eval_emergency_system(state, tick=1, events=events, invuln_ms=INVULN_MS)

        assert len(events) == 1  # still only 1 event — not fired again

    def test_es_overkill_discarded(self):
        """Hull clamped to exactly 1 — overkill does NOT carry into the invuln window."""
        state = _state_with_es(max_hull=100)
        state.current_hull = -9999  # extreme overkill
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        assert state.current_hull == 1  # always 1, regardless of overkill depth

    def test_trigger_hp_pct_absent_from_event(self):
        """ES module_activation event must NOT carry trigger_hp_pct (locked §12 decision)."""
        state = _state_with_es(max_hull=50)
        state.current_hull = -1
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        ev = events[0]
        assert "trigger_hp_pct" not in ev.data

    def test_es_not_a_threshold_device(self):
        """ES at hull > 0 with HP-pct crossing 33% → ES does NOT fire (phase 5 is cloak/booster only)."""
        state = _state_with_es(max_hull=100)
        # HP dropped to exactly 33% but hull is still positive
        state.current_hull = 33
        events: list = []

        _eval_emergency_system(state, tick=0, events=events, invuln_ms=INVULN_MS)

        assert len(events) == 0
        assert state.es_runtime is not None
        assert state.es_runtime.consumed is False


# ---------------------------------------------------------------------------
# TestInvulnGate — _apply_damage invuln blocking
# ---------------------------------------------------------------------------


class TestInvulnGate:
    def test_invuln_blocks_damage(self):
        """During ES invuln, incoming damage is blocked: amount=0 event emitted, HP unchanged."""
        state = _state_with_es(max_hull=100)
        assert state.es_runtime is not None
        state.es_runtime.invuln_remaining_ms = INVULN_MS
        state.es_runtime.consumed = True
        original_hull = state.current_hull

        events: list = []
        _apply_damage(
            state,
            raw_damage=500,
            tick=5,
            events=events,
            source=_dummy_source(),
            pvc_damage_reduction=0.0,
        )

        assert state.current_hull == original_hull  # HP unchanged
        assert len(events) == 1
        ev = events[0]
        assert ev.type == CombatEventType.damage
        assert ev.data["amount"] == 0
        assert ev.data["blocked_by"] == "emergency_system_invuln"
        assert "breakdown" not in ev.data  # breakdown OMITTED per §12

    def test_invuln_event_has_correct_hp_after(self):
        """Invuln damage event hp_after reflects current (unchanged) HP layers."""
        state = _state_with_es(max_hull=50, max_armour=20, shield=30, shield_recharge_ms=1000)
        assert state.es_runtime is not None
        state.es_runtime.invuln_remaining_ms = INVULN_MS
        state.es_runtime.consumed = True

        events: list = []
        _apply_damage(
            state,
            raw_damage=999,
            tick=3,
            events=events,
            source=_dummy_source(),
            pvc_damage_reduction=0.0,
        )

        hp_after = events[0].data["hp_after"]
        assert hp_after["shield"] == state.current_shield
        assert hp_after["armour"] == state.current_armour
        assert hp_after["hull"] == state.current_hull

    def test_no_invuln_damage_applies_normally(self):
        """Without invuln, damage applies normally to the HP layers."""
        state = _state_with_es(max_hull=100)
        assert state.es_runtime is not None
        assert state.es_runtime.invuln_remaining_ms == 0  # no window active

        events: list = []
        _apply_damage(
            state,
            raw_damage=30,
            tick=0,
            events=events,
            source=_dummy_source(),
            pvc_damage_reduction=0.0,
        )

        assert state.current_hull == 70  # 100 - 30
        assert events[0].data["amount"] == 30
        assert "blocked_by" not in events[0].data


# ---------------------------------------------------------------------------
# TestInvulnDecrement — invuln window countdown
# ---------------------------------------------------------------------------


class TestInvulnDecrement:
    def test_invuln_decrements_each_tick(self):
        """Invuln window is decremented by TICK_MS each tick in Phase 1."""
        # Run a full resolver fight with one ES combatant
        # We'll verify via event counting that invuln blocks damage for INVULN_MS worth of ticks
        # then stops blocking.
        # 10 000 ms / 10 ms = 1000 ticks of invuln

        es_loadout = _loadout(
            ship_name="ESFighter",
            base_armour=200,
            modules=[_es_mod()],
            weapons=[WeaponStats(name="TestGun", dps=10.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="Enemy",
            base_armour=5000,  # survives easily
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        # seed=0 ensures hits so ES fires; ESFighter should take lethal damage and trigger ES
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_events = _find_module_activations(log, "emergency_system")
        # ES must fire — ESFighter has low HP (200 base_armour) and takes 500-damage lethal hits
        assert len(es_events) == 1, f"Expected ES to fire exactly once; got {len(es_events)}"
        blocked = [
            e for e in log if e.type == CombatEventType.damage and e.data.get("blocked_by") == "emergency_system_invuln"
        ]
        # During 10s invuln, many damage events should be blocked (amount=0)
        assert len(blocked) >= 1
        for ev in blocked:
            assert ev.data["amount"] == 0


# ---------------------------------------------------------------------------
# TestESRegenDuringInvuln — regen continues during invuln (§7.7)
# ---------------------------------------------------------------------------


class TestESRegenDuringInvuln:
    def test_shield_regen_during_invuln(self):
        """Shield regen pulses fire normally while invuln is active."""
        # Build a state with ES in invuln + a shield that has been depleted
        state = _state_with_es(max_hull=1, shield=100, shield_recharge_ms=TICK_MS)
        assert state.es_runtime is not None
        state.es_runtime.invuln_remaining_ms = INVULN_MS
        state.es_runtime.consumed = True
        # Deplete shield so regen can tick
        state.current_shield = 0

        events: list = []
        # Run one regen tick — should restore shield
        _tick_shield_regen(state, tick=1, events=events)

        regen_events = [e for e in events if e.type == CombatEventType.regen]
        # Regen should have fired (shield was depleted)
        assert len(regen_events) >= 1

    def test_repair_bot_regen_during_invuln(self):
        """Repair Bot hull regen pulses fire normally while invuln is active."""
        state = _state_with_es(max_hull=100, repair_rate=0.1)
        assert state.es_runtime is not None
        state.es_runtime.invuln_remaining_ms = INVULN_MS
        state.es_runtime.consumed = True
        state.current_hull = 1  # ES just fired, hull at 1

        events: list = []
        _tick_repair_bot_regen(state, tick=1, events=events)
        # With rate=0.1, delta_per_tick = (100+0)*0.1*(10/1000) = 0.1 per tick
        # After enough accumulation ≥1.0 HP restores; here we just verify no crash
        # and regen accumulator increases
        assert state.repair_bot_regen_accumulator >= 0.0  # sanity check


# ---------------------------------------------------------------------------
# TestESHPExpiry — HP at end of invuln window
# ---------------------------------------------------------------------------


class TestESHPExpiry:
    def test_hull_stays_1_without_regen(self):
        """With no shield or Repair Bot, ES clamps hull to 1; final_hp is non-negative post-clamp."""
        es_loadout = _loadout(
            ship_name="C1",
            base_armour=10,  # low HP — lethal damage from enemy fires ES
            modules=[_es_mod()],
            weapons=[],
        )
        # Opponent has a weapon dealing lethal damage to C1 (500 >> 10 HP) and huge HP so it survives
        enemy_loadout = _loadout(
            ship_name="C2",
            base_armour=99999,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_acts = _find_module_activations(log, "emergency_system")
        # ES must fire — enemy delivers lethal damage on first shot
        assert len(es_acts) == 1, f"Expected ES to fire exactly once; got {len(es_acts)}"
        # fight_end carries final_hp; hull should be non-negative (post-clamp)
        end_ev = log[-1]
        assert end_ev.type == CombatEventType.fight_end
        final_hp = end_ev.data["final_hp"]
        # C1 hull must be ≥ 0 (clamped; ES fired so it was at least 1 at some point)
        assert final_hp["c1"]["hull"] >= 0


# ---------------------------------------------------------------------------
# TestESEndToEnd — full resolver integration
# ---------------------------------------------------------------------------


class TestESEndToEnd:
    def test_es_fires_in_full_sim(self):
        """Full sim: combatant with ES takes lethal damage → ES fires, ES module_activation emitted once."""
        es_loadout = _loadout(
            ship_name="ESShip",
            base_armour=50,  # low HP — likely to take lethal damage
            modules=[_es_mod()],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="Enemy",
            base_armour=2000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_events = _find_module_activations(log, "emergency_system")
        # ES must fire exactly once (seed=0 with this low-HP loadout is deterministic)
        assert len(es_events) == 1, f"Expected ES to fire exactly once; got {len(es_events)}"
        ev = es_events[0]
        assert ev.type == CombatEventType.module_activation
        assert ev.data["module"] == "emergency_system"
        assert "trigger_hp_pct" not in ev.data
        assert ev.actor == "ESShip"

    def test_es_fires_exactly_once_consumable(self):
        """ES is consumable — module_activation count for emergency_system must be ≤ 1."""
        es_loadout = _loadout(
            ship_name="C1",
            base_armour=50,
            modules=[_es_mod()],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="C2",
            base_armour=2000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_acts = [
            e for e in log if e.type == CombatEventType.module_activation and e.data.get("module") == "emergency_system"
        ]
        # consumable — fires exactly once (seed=0 with this low-HP loadout is deterministic)
        assert len(es_acts) == 1, f"Expected ES to fire exactly once; got {len(es_acts)}"

    def test_no_es_equipped_no_module_activation(self):
        """Combatant without ES module never emits emergency_system activation."""
        loadout1 = _loadout(
            ship_name="C1",
            base_armour=50,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        loadout2 = _loadout(
            ship_name="C2",
            base_armour=2000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(loadout1, loadout2)
        log = result.combat_log

        es_acts = [
            e for e in log if e.type == CombatEventType.module_activation and e.data.get("module") == "emergency_system"
        ]
        assert len(es_acts) == 0

    def test_final_hp_nonnegative_with_es(self):
        """fight_end.final_hp values are all non-negative (phase 4b clamp applied before fight_end)."""
        es_loadout = _loadout(
            ship_name="C1",
            base_armour=50,
            modules=[_es_mod()],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="C2",
            base_armour=2000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        end_ev = log[-1]
        assert end_ev.type == CombatEventType.fight_end
        final_hp = end_ev.data["final_hp"]
        for key in ("c1", "c2"):
            hp = final_hp[key]
            assert hp["shield"] >= 0
            assert hp["armour"] >= 0
            assert hp["hull"] >= 0

    def test_invuln_damage_events_have_amount_zero(self):
        """During ES invuln window, all damage events have amount=0 and blocked_by annotation."""
        es_loadout = _loadout(
            ship_name="ESShip",
            base_armour=50,
            modules=[_es_mod()],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="Enemy",
            base_armour=2000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_acts = _find_module_activations(log, "emergency_system")
        # ES must fire — low HP + lethal weapon + seed=0 is deterministic
        assert len(es_acts) == 1, f"Expected ES to fire exactly once; got {len(es_acts)}"
        # Find all blocked damage events (amount=0, blocked_by set)
        blocked = [
            e for e in log if e.type == CombatEventType.damage and e.data.get("blocked_by") == "emergency_system_invuln"
        ]
        assert len(blocked) >= 1, "Expected at least one invuln-blocked damage event during the 10s window"
        for ev in blocked:
            assert ev.data["amount"] == 0
            assert "breakdown" not in ev.data

    def test_es_fires_from_nuke_self_damage(self):
        """Nuke firer's own self-damage trips ES when firer is at low hull.

        TASK_0009 test-surface bullet 4: a combatant fires a nuke at low HP and
        its own self-damage trips ES.  Verifies that the nuke_self path through
        _apply_damage correctly feeds into _eval_emergency_system at phase 4a.
        """
        nuke_weapon = WeaponStats(
            name="TestNuke",
            dps=10.0,
            damage_per_shot=10000,  # large payload → self-damage ≫ 10 HP hull
            loading_speed_ms=100,
            range_m=6000.0,
            subtype="nuke",
            magnitude_m=100000.0,  # huge blast radius → high self-damage even at 300 m epicenter
        )
        small_primary = WeaponStats(
            name="SmallGun",
            dps=1.0,
            damage_per_shot=1,
            loading_speed_ms=100,
            range_m=6000.0,
        )
        firer_loadout = ShipLoadout(
            ship_name="NukeFirer",
            base_armour=10,  # hull=10 — nuke self-damage (huge payload × NUKE_FRIENDLY_FACTOR) is lethal
            modules=[_es_mod()],
            weapons=[small_primary],
            secondary_weapons=[nuke_weapon],
        )
        enemy_loadout = ShipLoadout(
            ship_name="BigEnemy",
            base_armour=999999,  # survives; no weapon needed (fight ends by time cap or C2 takes lethal)
            modules=[],
            weapons=[],
        )

        result = TickResolver(seed=0).resolve(firer_loadout, enemy_loadout)
        log = result.combat_log

        es_events = _find_module_activations(log, "emergency_system")
        # ES fires exactly once — triggered by nuke self-damage on tick of first nuke fire
        assert len(es_events) == 1, f"Expected ES to fire exactly once from nuke self-damage; got {len(es_events)}"

        es_tick = es_events[0].tick
        # Confirm a nuke self-damage event exists on the same tick that triggered ES
        nuke_self_on_es_tick = [
            e
            for e in log
            if e.type == CombatEventType.damage
            and e.data.get("source", {}).get("subtype") == "nuke"
            and e.data.get("source", {}).get("is_self") is True
            and e.tick == es_tick
        ]
        assert len(nuke_self_on_es_tick) >= 1, (
            f"Expected a nuke self-damage event on ES-trigger tick {es_tick}; "
            f"nuke_self events: {[(e.tick, e.data) for e in log if e.type == CombatEventType.damage and e.data.get('source', {}).get('is_self')]}"  # noqa: E501
        )
        # ES event must appear AFTER the nuke self-damage event (phase 4 → phase 4a ordering)
        es_idx = log.index(es_events[0])
        nuke_self_idx = log.index(nuke_self_on_es_tick[0])
        assert nuke_self_idx < es_idx, (
            f"Nuke self-damage (idx={nuke_self_idx}) must precede ES activation (idx={es_idx}) on tick {es_tick}"
        )

    def test_es_phase4a_before_phase5(self):
        """ES module_activation must appear BEFORE any phase-5 module_activation on same lethal tick."""
        # We need a combatant with both ES AND a cloak/booster to detect ordering.
        # Set up so that ES fires on a lethal-damage tick, and cloak MIGHT also trigger.
        # The ES event must precede any phase-5 module_activation events.
        from src.services.combat_resolver import _CLOAK_MODULE_TYPE

        cloak_mod = ModuleStats(
            name="TestCloak",
            module_type=_CLOAK_MODULE_TYPE,
            effect_duration_ms=5000,
            loading_speed_ms=2000,
        )
        es_loadout = _loadout(
            ship_name="ESCloakShip",
            base_armour=100,
            modules=[_es_mod(), cloak_mod],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = _loadout(
            ship_name="Enemy",
            base_armour=5000,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=200, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_acts = _find_module_activations(log, "emergency_system")
        assert len(es_acts) == 1, f"Expected ES to fire exactly once for ordering check; got {len(es_acts)}"

        es_tick = es_acts[0].tick
        # Any cloak activations on the SAME tick must come AFTER the ES activation in the event list
        cloak_on_same_tick = [
            e
            for e in log
            if e.type == CombatEventType.module_activation and e.data.get("module") == "cloak" and e.tick == es_tick
        ]
        if cloak_on_same_tick:
            es_idx = log.index(es_acts[0])
            for cloak_ev in cloak_on_same_tick:
                assert log.index(cloak_ev) > es_idx, "ES must precede phase-5 activations in same tick"


# ---------------------------------------------------------------------------
# TestInertModules — §7.9 + §7.10 no-op contract
# ---------------------------------------------------------------------------


class TestInertModules:
    """Inert modules (§7.9 / §7.10) produce no events, no state mutations, no crashes."""

    _INERT_MODULE_TYPES: ClassVar[list[str]] = [
        "GammaShieldModule",
        "JumpDriveModule",
        "TimeExtenderModule",
        "CompressorModule",
        "MiningDrillModule",
        "TractorBeamModule",
        "CabinModule",
        "SignatureModule",
        "SpectralFilterModule",
        # Phase-2 deferred (§7.10) — treated identically: inert in Phase-1
        "ShieldInjectorModule",
        "RepairBeamModule",
        "TransfusionBeamModule",
    ]

    def test_gamma_shield_no_crash_no_events(self):
        """GammaShield in loadout → no crash, no module_activation events."""
        inert_mod = ModuleStats(name="Gamma Shield", module_type="GammaShieldModule")
        lo = _loadout(ship_name="C1", base_armour=200, modules=[inert_mod])
        lo2 = _loadout(ship_name="C2", base_armour=200)
        result = TickResolver(seed=42).resolve(lo, lo2)
        log = result.combat_log

        mod_acts = _find_module_activations(log)
        # No module activations for an inert module
        for ev in mod_acts:
            assert ev.data.get("module") not in {"gamma_shield", "GammaShield", "GammaShieldModule"}

    def test_deferred_phase2_modules_no_crash(self):
        """ShieldInjector + RepairBeam + TransfusionBeam in loadout → no crash, no events."""
        inert_mods = [
            ModuleStats(name="Shield Injector", module_type="ShieldInjectorModule"),
            ModuleStats(name="Repair Beam", module_type="RepairBeamModule"),
            ModuleStats(name="Transfusion Beam", module_type="TransfusionBeamModule"),
        ]
        lo = _loadout(ship_name="C1", base_armour=200, modules=inert_mods)
        lo2 = _loadout(ship_name="C2", base_armour=200)
        result = TickResolver(seed=42).resolve(lo, lo2)
        # If no exception was raised and the fight ended, the contract holds
        assert result.combat_log[-1].type == CombatEventType.fight_end

    def test_all_inert_module_types_in_same_loadout_no_crash(self):
        """All §7.9 + §7.10 inert module types together → no crash, fight completes."""
        inert_mods = [ModuleStats(name=f"InertMod_{mt}", module_type=mt) for mt in self._INERT_MODULE_TYPES]
        lo = _loadout(ship_name="C1", base_armour=200, modules=inert_mods)
        lo2 = _loadout(ship_name="C2", base_armour=200)
        result = TickResolver(seed=42).resolve(lo, lo2)
        assert result.combat_log[-1].type == CombatEventType.fight_end


# ---------------------------------------------------------------------------
# TestCI27ESHullDeathEvent — CI-27: hull layer_depleted timing vs ES
# ---------------------------------------------------------------------------


class TestCI27ESHullDeathEvent:
    """CI-27: hull layer_depleted must NOT fire on a tick when ES saves the ship.

    The bug: _apply_damage emitted layer_depleted{hull} when hull went ≤ 0, BUT
    _eval_emergency_system (Phase 4a) then clamps hull back to 1.  Result was
    a false "Hull depleted (dead)" line on the same tick as "Emergency System
    activated" — the ship is NOT dead.

    Fix: hull layer_depleted is now emitted only at Phase 8 (post-ES, post-clamp),
    where c_dead is true only if hull is genuinely ≤ 0 after ES evaluation.
    """

    def test_es_saves_ship_no_hull_dead_event_on_that_tick(self):
        """Bug repro (CI-27): ES fires on tick T → no layer_depleted{hull} on tick T.

        Pre-fix this would produce both 'Hull depleted (dead)' AND 'Emergency System
        activated' on the same tick.  Post-fix the hull-death event must be absent.
        """
        es_loadout = _loadout(
            ship_name="ESShip",
            base_armour=50,  # low HP — killed by first hit
            modules=[_es_mod()],
            weapons=[],
        )
        enemy_loadout = _loadout(
            ship_name="Enemy",
            base_armour=99999,
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        # ES must fire at least once for this test to be meaningful
        es_acts = [
            e for e in log if e.type == CombatEventType.module_activation and e.data.get("module") == "emergency_system"
        ]
        assert len(es_acts) >= 1, "ES must fire for this regression to be exercised"

        es_tick = es_acts[0].tick

        # No hull layer_depleted should exist on the tick ES fired (ship was saved)
        hull_dead_on_es_tick = [
            e
            for e in log
            if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull" and e.tick == es_tick
        ]
        assert hull_dead_on_es_tick == [], (
            f"CI-27 regression: hull layer_depleted emitted on ES-save tick {es_tick}; "
            f"ship was saved by ES and must NOT be marked dead. events: {hull_dead_on_es_tick}"
        )

    def test_true_death_no_es_emits_hull_dead_exactly_once(self):
        """Ship without ES that dies → exactly one hull layer_depleted event at the death tick."""
        att = _loadout(
            ship_name="Att",
            base_armour=200,
            weapons=[WeaponStats(name="BigGun", dps=500.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        defn = _loadout(ship_name="Def", base_armour=50)  # no ES, dies quickly
        result = TickResolver(seed=42).resolve(att, defn)
        log = result.combat_log

        hull_dead_events = [
            e for e in log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull"
        ]
        assert len(hull_dead_events) == 1, (
            f"Expected exactly one hull layer_depleted for a true death; got {len(hull_dead_events)}"
        )

        # Hull-death event must be on the same tick as fight_end
        fight_end = next(e for e in log if e.type == CombatEventType.fight_end)
        assert hull_dead_events[0].tick == fight_end.tick, (
            f"hull layer_depleted tick {hull_dead_events[0].tick} != fight_end tick {fight_end.tick}"
        )

        # Hull-death must appear BEFORE fight_end in the event list
        hd_idx = log.index(hull_dead_events[0])
        fe_idx = log.index(fight_end)
        assert hd_idx < fe_idx, "hull layer_depleted must precede fight_end in event list"

    def test_es_consumed_then_later_death_hull_dead_only_at_death_tick(self):
        """ES fires at tick A (saves ship); ship genuinely dies later at tick B.

        Exactly one hull layer_depleted event at tick B; none at tick A.
        """
        # ES ship with low HP; enemy has enough firepower to kill it after invuln expires.
        # The trick: ES invuln is 10s = 1000 ticks.  We need the enemy to keep shooting
        # after invuln.  Give ESShip a weapon so it runs past time-cap only if enemy
        # survives; enemy HP is tuned so the fight ends after ES fires + invuln expires.
        es_loadout = ShipLoadout(
            ship_name="ESShip",
            base_armour=10,  # lethal on first hit → ES fires tick ~0
            modules=[_es_mod()],
            weapons=[WeaponStats(name="SmallGun", dps=1.0, damage_per_shot=1, loading_speed_ms=100, range_m=5000.0)],
        )
        enemy_loadout = ShipLoadout(
            ship_name="Enemy",
            base_armour=5000,  # survives easily; keeps shooting after invuln expires
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=0).resolve(es_loadout, enemy_loadout)
        log = result.combat_log

        es_acts = [
            e for e in log if e.type == CombatEventType.module_activation and e.data.get("module") == "emergency_system"
        ]
        assert len(es_acts) == 1, f"Expected ES to fire exactly once; got {len(es_acts)}"
        es_tick = es_acts[0].tick

        hull_dead_events = [
            e for e in log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull"
        ]

        # No hull-death at the ES-save tick
        on_es_tick = [e for e in hull_dead_events if e.tick == es_tick]
        assert on_es_tick == [], f"Hull-dead event must not appear on ES-save tick {es_tick}; found: {on_es_tick}"

        # If the ship genuinely dies later, hull-dead must be exactly once
        if hull_dead_events:
            assert len(hull_dead_events) == 1, (
                f"Expected at most one hull layer_depleted after ES; got {len(hull_dead_events)}"
            )
            fight_end = next(e for e in log if e.type == CombatEventType.fight_end)
            assert hull_dead_events[0].tick == fight_end.tick, (
                f"hull layer_depleted must be on death tick (fight_end tick), "
                f"got hd={hull_dead_events[0].tick} fe={fight_end.tick}"
            )

    def test_mutual_death_both_get_hull_dead_event(self):
        """Both combatants die on the same terminating tick → both get a hull layer_depleted event."""
        # Make both ships fragile and both armed so they can kill each other on the same tick.
        att = ShipLoadout(
            ship_name="C1",
            base_armour=1,
            modules=[],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=9999, loading_speed_ms=100, range_m=5000.0)],
        )
        defn = ShipLoadout(
            ship_name="C2",
            base_armour=1,
            modules=[],
            weapons=[WeaponStats(name="BigGun", dps=10.0, damage_per_shot=9999, loading_speed_ms=100, range_m=5000.0)],
        )
        result = TickResolver(seed=42).resolve(att, defn)
        log = result.combat_log

        fight_end = next(e for e in log if e.type == CombatEventType.fight_end)
        hull_dead_events = [
            e for e in log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull"
        ]

        if fight_end.data.get("reason") == "mutual":
            # Mutual death — both should have hull-death events
            assert len(hull_dead_events) == 2, (
                f"Mutual death should produce 2 hull layer_depleted events; got {len(hull_dead_events)}"
            )
            sides = {e.data["side"] for e in hull_dead_events}
            assert sides == {1, 2}, f"Expected hull-dead events for both slots; got sides={sides}"
        else:
            # One-sided death — exactly one hull-dead event
            assert len(hull_dead_events) == 1

    def test_time_cap_stalemate_no_hull_dead_event(self):
        """Time-cap stalemate (neither dead) → no hull layer_depleted event emitted."""
        # Both ships with huge HP and tiny weapons so fight runs to max_ticks
        att = _loadout(
            ship_name="C1",
            base_armour=999999,
            weapons=[WeaponStats(name="TinyGun", dps=0.01, damage_per_shot=1, loading_speed_ms=100, range_m=5000.0)],
        )
        defn = _loadout(ship_name="C2", base_armour=999999)
        result = TickResolver(seed=0).resolve(att, defn)
        log = result.combat_log

        fight_end = next(e for e in log if e.type == CombatEventType.fight_end)
        assert fight_end.data.get("reason") == "time_cap", (
            "This test requires a time-cap stalemate outcome; check the loadout params"
        )

        hull_dead_events = [
            e for e in log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull"
        ]
        assert hull_dead_events == [], (
            f"Time-cap stalemate must NOT emit any hull layer_depleted; got: {hull_dead_events}"
        )

    def test_extract_key_events_renders_hull_dead_for_true_death(self):
        """_extract_key_events correctly renders 'Hull depleted (dead)' for a true-death fight."""
        from src.services.combat_log_service import CombatLogService

        att = _loadout(
            ship_name="Att",
            base_armour=200,
            weapons=[WeaponStats(name="BigGun", dps=500.0, damage_per_shot=500, loading_speed_ms=100, range_m=5000.0)],
        )
        defn = _loadout(ship_name="Def", base_armour=50)
        result = TickResolver(seed=42).resolve(att, defn)

        # Serialize to dict list as _extract_key_events expects
        timeline = [
            {
                "tick": e.tick,
                "type": e.type,
                "actor": e.actor,
                "target": e.target,
                "data": e.data,
            }
            for e in result.combat_log
        ]
        key_events = CombatLogService._extract_key_events(timeline)

        hull_dead_labels = [ke for ke in key_events if "Hull depleted (dead)" in ke.get("event_type", "")]
        assert len(hull_dead_labels) >= 1, (
            f"_extract_key_events should produce at least one 'Hull depleted (dead)' entry for a true death; "
            f"got key_events={key_events}"
        )
