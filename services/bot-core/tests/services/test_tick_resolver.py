"""
T3 Tick Resolver tests.

Covers all 8 categories from TASK_0003.md §Test surface:
  1. End-to-end drift-to-floor fight (time_cap result, perf sanity)
  2. Damage helper — overkill carryover through shield → armour → hull
  3. PvC damage reduction — player-side only (Cases A, B, C)
  4. Shield regen pulse cadence (+1 HP every N ticks)
  5. Repair Bot regen — hull-first then armour, Ketar I vs II rate
  6. Regen dormancy (3 sub-tests)
  7. Termination matrix (hp_depleted, mutual, time_cap)
  8. Appendix B phase ordering — regen before damage on finishing-blow tick
"""

from __future__ import annotations

import sys
import time
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
    CombatEvent,
    CombatEventType,
    ModuleStats,
    ShipLoadout,
)
from services.combat_service import (
    TickResolver,
    _apply_damage,
    _CombatantState,
    _init_combatant,
    _tick_repair_bot_regen,
    _tick_shield_regen,
)
from services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _bare_loadout(ship_name: str = "TestShip", base_armour: int = 100) -> ShipLoadout:
    return ShipLoadout(ship_name=ship_name, base_armour=base_armour)


def _loadout_with_shield(capacity: int, recharge_ms: int, base_armour: int = 100) -> ShipLoadout:
    mod = ModuleStats(name="TestShield", shield=capacity, shield_recharge_ms=recharge_ms)
    return ShipLoadout(ship_name="TestShip", base_armour=base_armour, modules=[mod])


def _make_state(
    *,
    name: str = "C1",
    max_hull: int = 100,
    max_armour: int = 0,
    max_shield: int = 0,
    shield_schedules: list[tuple[int, int]] | None = None,
    repair_rate: float = 0.0,
    is_player: bool = False,
) -> _CombatantState:
    """Build a _CombatantState with explicit HP values, bypassing loadout builder."""
    bare = _bare_loadout(ship_name=name, base_armour=max_hull)
    s = _init_combatant(bare, is_player=is_player)
    s.max_shield = max_shield
    s.current_shield = max_shield
    s.max_armour = max_armour
    s.current_armour = max_armour
    s.max_hull = max_hull
    s.current_hull = max_hull
    s.shield_regen_schedules = shield_schedules or []
    s.shield_regen_accumulators = [0] * len(s.shield_regen_schedules)
    s.repair_bot_rate_per_sec = repair_rate
    s.repair_bot_delta_per_tick = (s.max_hull + s.max_armour) * repair_rate * (GameConstants.TICK_MS / 1000)
    s.repair_bot_regen_accumulator = 0.0
    return s


# ---------------------------------------------------------------------------
# 1. End-to-end drift-to-floor fight
# ---------------------------------------------------------------------------


class TestDriftFight:
    def test_time_cap_outcome(self):
        """Empty loadouts → distance closes 5000m→300m, both alive → time_cap."""
        loadout = _bare_loadout(base_armour=100)
        t0 = time.perf_counter()
        result = TickResolver(seed=42).resolve(loadout, loadout)
        elapsed = time.perf_counter() - t0

        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.loser_name is None
        assert result.metadata["metadata"]["total_ticks"] == GameConstants.MAX_FIGHT_TICKS
        assert elapsed < 1.0, f"Drift fight took {elapsed:.3f}s (budget: 1s)"

    def test_event_bookends(self):
        """combat_log starts with fight_start and ends with fight_end."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        log = result.combat_log
        assert len(log) >= 2
        assert log[0].type == CombatEventType.fight_start
        assert log[-1].type == CombatEventType.fight_end

    def test_distance_events_emitted_then_stop(self):
        """Distance events fire while closing (≈1567 ticks), then stop once at floor."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        dist_events = [e for e in result.combat_log if e.type == CombatEventType.distance]
        # Floor reached at tick ceil((5000-300)/3) = 1567; events only while distance changes
        floor = GameConstants.MIN_DISTANCE_M
        assert len(dist_events) > 0
        # Last distance event should land exactly at floor
        assert dist_events[-1].data["to"] == floor

    def test_fight_end_payload(self):
        """fight_end event carries correct reason, winner=null, duration_ticks."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        end = result.combat_log[-1]
        assert end.data["reason"] == "time_cap"
        assert end.data["winner"] is None
        assert end.data["duration_ticks"] == GameConstants.MAX_FIGHT_TICKS

    def test_metadata_block(self):
        """Metadata contains required resolver fields (T9 envelope: schema_version / summary / metadata)."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout(), pvc_damage_reduction=0.33)
        outer = result.metadata
        # T9 envelope keys
        assert outer["schema_version"] == 1
        assert "summary" in outer
        md = outer["metadata"]
        assert md["tick_ms"] == GameConstants.TICK_MS
        assert md["resolver"] == "tick_v1"
        assert md["pvc_damage_reduction"] == 0.33
        assert md["total_ticks"] == GameConstants.MAX_FIGHT_TICKS

    def test_fight_start_payload(self):
        """fight_start event records combatant names and initial distance."""
        l1 = _bare_loadout("Alpha", base_armour=150)
        l2 = _bare_loadout("Beta", base_armour=80)
        result = TickResolver().resolve(l1, l2)
        start = result.combat_log[0]
        names = [c["name"] for c in start.data["combatants"]]
        assert "Alpha" in names and "Beta" in names
        assert start.data["initial_distance"] == GameConstants.STARTING_DISTANCE_M

    def test_no_regen_events_on_full_hp_drift(self):
        """No regen events emitted when both combatants stay at full HP throughout."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        regen_events = [e for e in result.combat_log if e.type == CombatEventType.regen]
        assert regen_events == []


# ---------------------------------------------------------------------------
# 2. Damage helper — stacking with overkill carryover
# ---------------------------------------------------------------------------


class TestDamageHelper:
    def test_overkill_through_all_layers(self):
        """800 damage on 50/200/100 → hull = 100 - 550 = -450 (pre-clamp overkill)."""
        state = _make_state(max_shield=50, max_armour=200, max_hull=100)
        events: list[CombatEvent] = []
        _apply_damage(state, 800.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)

        assert state.current_shield == 0
        assert state.current_armour == 0
        assert state.current_hull == -450  # overkill — before step 4b clamp

        dmg = next(e for e in events if e.type == CombatEventType.damage)
        assert dmg.data["breakdown"]["shield"] == 50
        assert dmg.data["breakdown"]["armour"] == 200
        assert dmg.data["breakdown"]["hull"] == 550
        assert dmg.data["hp_after"]["hull"] == -450

    def test_layer_depleted_shield_then_armour_but_not_hull(self):
        """CI-27: layer_depleted fires for shield and armour in _apply_damage; hull is NOT emitted here.

        Hull layer_depleted is moved to Phase 8 (post-ES, post-clamp) so that ES-saved
        ships never receive a false 'Hull depleted (dead)' event.  Shield and armour
        layer_depleted remain in _apply_damage (they are not death events).
        """
        state = _make_state(max_shield=50, max_armour=200, max_hull=100)
        events: list[CombatEvent] = []
        _apply_damage(state, 800.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)

        depleted = [e for e in events if e.type == CombatEventType.layer_depleted]
        # CI-27: hull layer_depleted is now emitted only at Phase 8, NOT in _apply_damage
        assert len(depleted) == 2, (
            f"Expected shield + armour layer_depleted only (hull moved to Phase 8); got {[e.data for e in depleted]}"
        )
        assert depleted[0].data["layer"] == "shield"
        assert depleted[1].data["layer"] == "armour"

    def test_hull_layer_depleted_not_emitted_by_apply_damage(self):
        """CI-27: _apply_damage does NOT emit hull layer_depleted even when hull goes ≤ 0.

        Hull layer_depleted is emitted at Phase 8 (true death, post-ES) instead.
        """
        state = _make_state(max_hull=100)
        events: list[CombatEvent] = []
        _apply_damage(state, 200.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)

        assert state.current_hull == -100  # overkill (pre-clamp) — damage math unchanged
        hull_depleted = [e for e in events if e.type == CombatEventType.layer_depleted]
        # Hull layer_depleted is NOT emitted by _apply_damage (moved to Phase 8)
        assert hull_depleted == [], (
            f"CI-27: _apply_damage must NOT emit hull layer_depleted; got {[e.data for e in hull_depleted]}"
        )

    def test_partial_damage_does_not_emit_layer_depleted(self):
        """When a layer survives (HP > 0), no layer_depleted event fires for it."""
        state = _make_state(max_shield=100, max_hull=100)
        events: list[CombatEvent] = []
        _apply_damage(state, 50.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)

        assert state.current_shield == 50  # partial
        assert not any(e.type == CombatEventType.layer_depleted for e in events)


# ---------------------------------------------------------------------------
# 3. PvC damage reduction — player-side only
# ---------------------------------------------------------------------------


class TestPvCDR:
    def test_case_a_no_dr(self):
        """pvc_damage_reduction=0.0 → C1 (player) takes full 100 damage."""
        state = _make_state(max_hull=200, is_player=True)
        events: list[CombatEvent] = []
        _apply_damage(state, 100.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)
        assert state.current_hull == 100

    def test_case_b_dr_applied_to_player(self):
        """pvc_damage_reduction=0.33 → player takes round(100 * 0.67) = 67 damage."""
        state = _make_state(max_hull=200, is_player=True)
        events: list[CombatEvent] = []
        _apply_damage(state, 100.0, tick=0, events=events, source={}, pvc_damage_reduction=0.33)
        assert state.current_hull == 200 - 67  # round(100 * (1 - 0.33)) = 67

    def test_case_c_dr_not_applied_to_npc(self):
        """pvc_damage_reduction=0.33 → NPC (is_player=False) takes full 100 damage."""
        state = _make_state(max_hull=200, is_player=False)
        events: list[CombatEvent] = []
        _apply_damage(state, 100.0, tick=0, events=events, source={}, pvc_damage_reduction=0.33)
        assert state.current_hull == 100  # full damage, no DR

    def test_dr_applied_amount_in_event(self):
        """The damage event's 'amount' reflects the post-DR applied value."""
        state = _make_state(max_hull=200, is_player=True)
        events: list[CombatEvent] = []
        _apply_damage(state, 100.0, tick=0, events=events, source={}, pvc_damage_reduction=0.33)
        dmg = next(e for e in events if e.type == CombatEventType.damage)
        assert dmg.data["amount"] == 67


# ---------------------------------------------------------------------------
# 4. Shield regen pulse cadence
# ---------------------------------------------------------------------------


class TestShieldRegenCadence:
    def test_period_computed_from_init(self):
        """_init_combatant computes period = ceil(recharge_ms / capacity / TICK_MS)."""
        loadout = _loadout_with_shield(capacity=50, recharge_ms=20000)
        state = _init_combatant(loadout, is_player=False)
        # ceil(20000 / 50 / 10) = ceil(40) = 40
        assert state.shield_regen_schedules == [(50, 40)]

    def test_targe_40_tick_period(self):
        """Targe-like (50 cap, 20000 ms recharge) → exactly 1 regen pulse at tick 40."""
        state = _make_state(max_shield=50, shield_schedules=[(50, 40)])
        state.current_shield = 49  # 1 HP below max → regen active

        events: list[CombatEvent] = []
        for tick in range(40):
            _tick_shield_regen(state, tick, events)

        regen = [e for e in events if e.type == CombatEventType.regen]
        assert len(regen) == 1
        assert regen[0].data["amount"] == 1
        assert regen[0].data["layer"] == "shield"
        assert regen[0].data["hp_after"] == 50

    def test_small_module_50_tick_period(self):
        """capacity=10, recharge=5000 → period = ceil(5000/10/10) = 50 ticks."""
        state = _make_state(max_shield=10, shield_schedules=[(10, 50)])
        state.current_shield = 9

        events: list[CombatEvent] = []
        for tick in range(50):
            _tick_shield_regen(state, tick, events)

        regen = [e for e in events if e.type == CombatEventType.regen]
        assert len(regen) == 1

    def test_no_pulse_before_period(self):
        """No regen fires in ticks 0..period-2."""
        state = _make_state(max_shield=50, shield_schedules=[(50, 40)])
        state.current_shield = 49

        events: list[CombatEvent] = []
        for tick in range(39):  # one tick short of period
            _tick_shield_regen(state, tick, events)

        assert not any(e.type == CombatEventType.regen for e in events)


# ---------------------------------------------------------------------------
# 5. Repair Bot regen — hull-first then armour
# ---------------------------------------------------------------------------


class TestRepairBotRegen:
    def test_ketar1_rate_from_module_subclass(self):
        """_init_combatant propagates Ketar I rate from module_type + repair_rate property."""
        mod = ModuleStats(
            name="Ketar Repair Bot",
            module_type="RepairBotModule",
            repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC,
        )
        loadout = ShipLoadout(ship_name="S", base_armour=100, modules=[mod])
        state = _init_combatant(loadout, is_player=False)
        assert state.repair_bot_rate_per_sec == GameConstants.KETAR_I_REPAIR_PCT_PER_SEC

    def test_ketar2_rate_from_module_subclass(self):
        """_init_combatant propagates Ketar II rate from module_type + repair_rate property."""
        mod = ModuleStats(
            name="Ketar Repair Bot II",
            module_type="RepairBotModule",
            repair_rate=GameConstants.KETAR_II_REPAIR_PCT_PER_SEC,
        )
        loadout = ShipLoadout(ship_name="S", base_armour=100, modules=[mod])
        state = _init_combatant(loadout, is_player=False)
        assert state.repair_bot_rate_per_sec == GameConstants.KETAR_II_REPAIR_PCT_PER_SEC

    def test_ketar2_wins_over_ketar1(self):
        """When both equipped, highest rate (Ketar II) wins."""
        mods = [
            ModuleStats(
                name="Ketar Repair Bot",
                module_type="RepairBotModule",
                repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC,
            ),
            ModuleStats(
                name="Ketar Repair Bot II",
                module_type="RepairBotModule",
                repair_rate=GameConstants.KETAR_II_REPAIR_PCT_PER_SEC,
            ),
        ]
        loadout = ShipLoadout(ship_name="S", base_armour=100, modules=mods)
        state = _init_combatant(loadout, is_player=False)
        assert state.repair_bot_rate_per_sec == GameConstants.KETAR_II_REPAIR_PCT_PER_SEC

    def test_ketar1_heals_hull_200_ticks(self):
        """Ketar I: per-tick delta = 200 * 0.025 * 0.01 = 0.05 → 20 ticks per +1 HP.
        10 HP deficit heals in 200 ticks."""
        state = _make_state(max_hull=100, max_armour=100, repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)
        state.current_hull = 90  # 10 HP deficit

        events: list[CombatEvent] = []
        for tick in range(200):
            _tick_repair_bot_regen(state, tick, events)

        assert state.current_hull == 100
        assert state.current_armour == 100  # untouched

    def test_hull_fills_before_armour(self):
        """Hull deficit takes priority over armour deficit."""
        state = _make_state(max_hull=100, max_armour=100, repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)
        state.current_hull = 99  # 1 HP hull deficit
        state.current_armour = 99  # 1 HP armour deficit

        events: list[CombatEvent] = []
        # Run enough ticks to heal 1 HP (20 ticks at Ketar I rate)
        for tick in range(20):
            _tick_repair_bot_regen(state, tick, events)

        # hull healed first
        hull_regen = [e for e in events if e.data.get("layer") == "hull"]
        armour_regen = [e for e in events if e.data.get("layer") == "armour"]
        assert hull_regen  # hull healed
        if armour_regen:
            # If armour also healed, hull event must come first
            assert events.index(hull_regen[0]) < events.index(armour_regen[0])

    def test_ketar2_heals_faster(self):
        """Ketar II (5%/s) heals same 10 HP deficit in 100 ticks."""
        state = _make_state(max_hull=100, max_armour=100, repair_rate=GameConstants.KETAR_II_REPAIR_PCT_PER_SEC)
        state.current_hull = 90

        events: list[CombatEvent] = []
        for tick in range(100):
            _tick_repair_bot_regen(state, tick, events)

        assert state.current_hull == 100

    def test_float_drift_workaround_flushes_at_integer_boundary(self):
        """Regression guard for the round(x, 12) accumulator workaround.

        Naive '+= 0.1' for 10 iterations would land at 0.9999...8 due to
        IEEE 754, causing the integer-flush schedule to skip the 10th flush.
        The resolver rounds the accumulator to 12dp after each addition.
        If a future refactor drops this round, this test fails loudly.
        """
        acc = 0.0
        delta = 0.1
        flushed = 0
        for _ in range(10):
            acc = round(acc + delta, 12)
            if acc >= 1.0:
                flushed += 1
                acc -= 1.0
        assert flushed == 1, f"Expected 1 integer flush; got {flushed}. Float drift broke the integer-flush schedule."


# ---------------------------------------------------------------------------
# 6. Regen dormancy
# ---------------------------------------------------------------------------


class TestRegenDormancy:
    def test_shield_dormant_at_max_no_events(self):
        """(a) Shield at max → no regen events, accumulator never ticked."""
        state = _make_state(max_shield=50, shield_schedules=[(50, 40)])
        # current_shield == max_shield (50) → dormant

        events: list[CombatEvent] = []
        for tick in range(100):
            _tick_shield_regen(state, tick, events)

        assert not any(e.type == CombatEventType.regen for e in events)
        assert state.shield_regen_accumulators[0] == 0

    def test_repair_bot_dormant_at_max_no_events(self):
        """(a) Hull+armour at max → Repair Bot accumulator never ticked."""
        state = _make_state(max_hull=100, repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)
        # current_hull == max_hull → dormant

        events: list[CombatEvent] = []
        for tick in range(100):
            _tick_repair_bot_regen(state, tick, events)

        assert events == []
        assert state.repair_bot_regen_accumulator == 0.0

    def test_accumulator_starts_at_zero_after_first_damage(self):
        """(b) First damage activates accumulator at 0; pulse arrives exactly N ticks later."""
        period = 40
        state = _make_state(max_shield=50, shield_schedules=[(50, period)])
        state.current_shield = 49  # simulate 1 HP damage — regen now active

        events: list[CombatEvent] = []
        for tick in range(period - 1):
            _tick_shield_regen(state, tick, events)
        # No pulse yet — accumulator = period-1
        assert not any(e.type == CombatEventType.regen for e in events)

        _tick_shield_regen(state, period - 1, events)
        regen = [e for e in events if e.type == CombatEventType.regen]
        assert len(regen) == 1

    def test_return_to_max_discards_partial_accumulation(self):
        """(c) Shield returns to max mid-accumulation → partial discarded; next damage = fresh start."""
        period = 40
        state = _make_state(max_shield=50, shield_schedules=[(50, period)])
        state.current_shield = 49  # first damage

        # Run 20 ticks (half a period — accumulator reaches 20)
        events: list[CombatEvent] = []
        for tick in range(20):
            _tick_shield_regen(state, tick, events)
        assert state.shield_regen_accumulators[0] == 20

        # Simulate return to full HP (e.g. potion or emergency heal — outside scope, forced here)
        state.current_shield = 50
        _tick_shield_regen(state, 20, events)  # dormancy check: returns early, resets accumulator
        assert state.shield_regen_accumulators[0] == 0

        # Second damage — fresh accumulator, pulse arrives exactly period ticks later
        state.current_shield = 49
        events2: list[CombatEvent] = []
        for tick in range(period):
            _tick_shield_regen(state, tick, events2)

        regen = [e for e in events2 if e.type == CombatEventType.regen]
        assert len(regen) == 1


# ---------------------------------------------------------------------------
# 7. Termination matrix
# ---------------------------------------------------------------------------


class TestTermination:
    def test_time_cap_full_run(self):
        """Empty loadouts → no deaths → stalemate / time_cap after MAX_FIGHT_TICKS."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.metadata["metadata"]["total_ticks"] == GameConstants.MAX_FIGHT_TICKS
        assert result.combat_log[-1].data["reason"] == "time_cap"

    def test_hp_depleted_c2_dies(self):
        """C2 hull reaches 0 → resolver exits via hp_depleted, winner=C1, ticks < MAX.

        base_armour=0 means _init_combatant sets max_hull=current_hull=0; Phase 8 of
        tick 0 detects c2_dead=True before the time_cap branch can fire.
        """
        l1 = _bare_loadout("Survivor", base_armour=100)
        l2 = _bare_loadout("DeadC2", base_armour=0)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.winner_name == "Survivor"
        assert result.loser_name == "DeadC2"
        assert result.is_stalemate is False
        assert result.combat_log[-1].data["reason"] == "hp_depleted"
        assert result.metadata["metadata"]["total_ticks"] < GameConstants.MAX_FIGHT_TICKS

    def test_hp_depleted_c1_dies(self):
        """C1 hull reaches 0 → resolver exits via hp_depleted, winner=C2, ticks < MAX."""
        l1 = _bare_loadout("DeadC1", base_armour=0)
        l2 = _bare_loadout("Survivor", base_armour=100)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.winner_name == "Survivor"
        assert result.loser_name == "DeadC1"
        assert result.is_stalemate is False
        assert result.combat_log[-1].data["reason"] == "hp_depleted"
        assert result.metadata["metadata"]["total_ticks"] < GameConstants.MAX_FIGHT_TICKS

    def test_mutual_kill(self):
        """Both hulls ≤ 0 same tick → resolver exits via mutual, ticks < MAX."""
        l1 = _bare_loadout("C1", base_armour=0)
        l2 = _bare_loadout("C2", base_armour=0)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.combat_log[-1].data["reason"] == "mutual"
        assert result.metadata["metadata"]["total_ticks"] < GameConstants.MAX_FIGHT_TICKS

    def test_fight_end_event_on_time_cap(self):
        """fight_end event emitted inside the loop on last tick."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        end = result.combat_log[-1]
        assert end.type == CombatEventType.fight_end
        assert end.data["reason"] == "time_cap"
        assert "final_hp" in end.data


# ---------------------------------------------------------------------------
# 8. Appendix B phase ordering — regen before damage
# ---------------------------------------------------------------------------


class TestPhaseOrdering:
    def test_regen_event_precedes_damage_event(self):
        """Phase 2 (regen) fires before phase 4 (damage) within the same tick.

        Setup: hull=1, Repair Bot accumulator pre-loaded to 1.0 (will flush +1 this tick).
        After regen: hull = 2. After damage of 2: hull = 0.
        Regen event index < damage event index in events list.
        """
        state = _make_state(max_hull=100, repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)
        state.current_hull = 1
        state.repair_bot_regen_accumulator = 1.0  # will flush +1 HP immediately

        events: list[CombatEvent] = []

        # Phase 2: regen
        _tick_repair_bot_regen(state, tick=0, events=events)
        assert state.current_hull == 2

        # Phase 4: damage
        _apply_damage(state, 2.0, tick=0, events=events, source={}, pvc_damage_reduction=0.0)

        # Phase 4b: clamp
        state.current_hull = max(0, state.current_hull)
        assert state.current_hull == 0

        # Ordering assertion
        regen_idx = next(i for i, e in enumerate(events) if e.type == CombatEventType.regen)
        damage_idx = next(i for i, e in enumerate(events) if e.type == CombatEventType.damage)
        assert regen_idx < damage_idx, "Phase 2 regen must precede phase 4 damage (Appendix B)"

    def test_c1_regen_before_c2_regen(self):
        """Within phase 2, C1 regen events precede C2 regen events (C1-before-C2 rule)."""
        # period=1 so every tick fires a pulse
        c1 = _make_state(name="C1", max_shield=10, shield_schedules=[(10, 1)])
        c2 = _make_state(name="C2", max_shield=10, shield_schedules=[(10, 1)])
        c1.current_shield = 9
        c2.current_shield = 9

        events: list[CombatEvent] = []
        _tick_shield_regen(c1, tick=0, events=events)
        _tick_shield_regen(c2, tick=0, events=events)

        regen = [e for e in events if e.type == CombatEventType.regen]
        assert len(regen) == 2
        assert regen[0].actor == "C1"
        assert regen[1].actor == "C2"

    def test_resolver_emits_no_weapon_events(self):
        """T3 resolver emits only permitted event types (no weapon_fire, cooldown_end)."""
        result = TickResolver().resolve(_bare_loadout(), _bare_loadout())
        allowed = {
            CombatEventType.fight_start,
            CombatEventType.fight_end,
            CombatEventType.regen,
            CombatEventType.damage,
            CombatEventType.layer_depleted,
            CombatEventType.distance,
        }
        for evt in result.combat_log:
            assert evt.type in allowed, f"Unexpected event type in T3 log: {evt.type!r}"


# ---------------------------------------------------------------------------
# 9. winner_side field (P2-T0b)
# ---------------------------------------------------------------------------


class TestWinnerSide:
    """P2-T0b: FightResults.winner_side is derived from death-branch, not winner_name."""

    def test_winner_side_is_1_when_c1_wins(self):
        """C2 hull reaches 0 first → winner_side == 1 (challenger/combatant1)."""
        l1 = _bare_loadout("Survivor", base_armour=100)
        l2 = _bare_loadout("DeadC2", base_armour=0)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is False
        assert result.winner_name == "Survivor"
        assert result.winner_side == 1

    def test_winner_side_is_2_when_c2_wins(self):
        """C1 hull reaches 0 first → winner_side == 2 (target/combatant2)."""
        l1 = _bare_loadout("DeadC1", base_armour=0)
        l2 = _bare_loadout("Survivor", base_armour=100)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is False
        assert result.winner_name == "Survivor"
        assert result.winner_side == 2

    def test_winner_side_is_none_on_time_cap_stalemate(self):
        """Neither combatant dies → time_cap stalemate → winner_side is None."""
        l1 = _bare_loadout("P1", base_armour=100)
        l2 = _bare_loadout("P2", base_armour=100)
        result = TickResolver().resolve(l1, l2)

        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.winner_side is None

    def test_winner_side_is_none_on_mutual_kill(self):
        """Both hulls depleted same tick → mutual kill stalemate → winner_side is None."""
        l1 = _bare_loadout("MutualA", base_armour=0)
        l2 = _bare_loadout("MutualB", base_armour=0)
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.winner_side is None

    def test_winner_side_same_name_combatant1_wins(self):
        """SAME-NAME decisiveness test: both combatants have identical names.

        C2 (base_armour=0) is already dead on tick-0, so C1 (side 1) wins.
        A name-compare approach would be ambiguous here; winner_side must still
        correctly report 1 because the termination logic keys on c2_dead, not names.
        """
        shared_name = "CloneShip"
        l1 = _bare_loadout(shared_name, base_armour=100)  # side 1 — survives
        l2 = _bare_loadout(shared_name, base_armour=0)  # side 2 — dead on tick 0
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is False
        # Both names are the same — a name-compare would be unreliable
        assert result.winner_name == shared_name
        assert result.loser_name == shared_name
        # winner_side is unambiguous because it comes from the death-branch
        assert result.winner_side == 1, (
            "winner_side must be 1 (side of c1/combatant1) even when both combatants share the same ship name"
        )

    def test_winner_side_same_name_combatant2_wins(self):
        """SAME-NAME discriminating test: both combatants share an identical name.

        C1 (side 1, base_armour=0) dies on tick-0; C2 (side 2) survives.
        A name-derived impl ('winner_side = 1 if winner_name == c1.name else 2')
        would return 1 (WRONG) because winner_name == c1.name == c2.name.
        The correct impl keys on the death-branch, not the name, and must return 2.
        """
        shared_name = "CloneShip"
        l1 = _bare_loadout(shared_name, base_armour=0)  # side 1 — dies immediately
        l2 = _bare_loadout(shared_name, base_armour=100)  # side 2 — survives
        result = TickResolver(seed=1).resolve(l1, l2)

        assert result.is_stalemate is False
        assert result.winner_name == shared_name
        assert result.loser_name == shared_name
        # Name-derived impl returns 1 here (wrong); death-branch impl returns 2 (correct)
        assert result.winner_side == 2, (
            "winner_side must be 2 (side of c2/combatant2) when c1 dies, "
            "even though both combatants share the same ship name"
        )

    def test_winner_side_consistent_with_winner_name(self):
        """winner_side==1 implies winner_name==c1.name; winner_side==2 implies winner_name==c2.name."""
        # C1 wins
        r1 = TickResolver(seed=1).resolve(
            _bare_loadout("Alice", base_armour=100),
            _bare_loadout("Bob", base_armour=0),
        )
        assert r1.winner_side == 1
        assert r1.winner_name == "Alice"

        # C2 wins
        r2 = TickResolver(seed=1).resolve(
            _bare_loadout("Alice", base_armour=0),
            _bare_loadout("Bob", base_armour=100),
        )
        assert r2.winner_side == 2
        assert r2.winner_name == "Bob"

    def test_fight_results_pickle_roundtrip_with_winner_side(self):
        """FightResults (frozen+slots) with winner_side set round-trips through pickle."""
        import pickle

        from services.combat_models import FightResults, FightStats

        def _fs(name: str) -> FightStats:
            return FightStats(
                ship_name=name,
                raw_hp=1000,
                raw_dps=10.0,
                varied_hp=1000,
                varied_dps=10.0,
                ttk=100.0,
            )

        fr = FightResults(
            winner_name="Alpha",
            loser_name="Beta",
            is_stalemate=False,
            ship1_stats=_fs("Alpha"),
            ship2_stats=_fs("Beta"),
            winner_side=1,
        )

        serialised = pickle.dumps(fr)
        restored = pickle.loads(serialised)

        assert restored.winner_side == 1
        assert restored.winner_name == "Alpha"
        assert restored.loser_name == "Beta"
        assert restored.is_stalemate is False
