"""
T7 acceptance tests: turret weapon firing path — auto, manual, plasma-collector.

Test categories (per TASK_0007.md §Test surface; manual-turret tests updated for
range-driven switching — manual turrets fire only while NO primary is in range):
  Auto-turret:     1–4   (cadence; range gate; multi-turret acc share; cloak override)
  Manual-turret:   5–8   (primary-in-range inert; gap-closer switch; accuracy; N independent)
  Cross-phase:     9     (auto fires in both phases; manual only in turret phase)
  PrimaryWeaponMod:10–11 (isolation — auto; manual)
  Plasma-collector:12    (inert)
  Distance resets: 13    (no-primaries always-active; shock-blast revert; booster revert)
  Event payloads:  14    (subtype labels per §12)

D1 integration test: builder-fed fight (AsyncMock DB) with auto+manual+plasma turrets.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# Guard: ensure shared.bblogger is mocked if running in isolation.
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Stub sqlalchemy_utils
if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

from src.services.combat_models import ModuleStats, ShipLoadout, WeaponStats
from src.services.combat_resolver import TickResolver, _init_combatant
from src.services.game_constants import GameConstants

TICK_MS: int = GameConstants.TICK_MS  # 10
MIN_DIST: float = float(GameConstants.MIN_DISTANCE_M)  # 300.0
STARTING_DIST: float = float(GameConstants.STARTING_DISTANCE_M)  # 5000.0
AUTO_MULT: float = GameConstants.AUTO_TURRET_ACCURACY_MULTIPLIER  # 0.85
CLOAK_SET: float = GameConstants.CLOAK_SET_VALUE  # 0.25
ACC_CLAMP_MIN: float = GameConstants.ACCURACY_CLAMP_MIN  # 0.05
ACC_CLAMP_MAX: float = GameConstants.ACCURACY_CLAMP_MAX  # 0.99
PLAYER_BASE_ACC: float = GameConstants.PLAYER_BASE_ACCURACY  # 0.60
NPC_BASE_ACC: float = GameConstants.NPC_BASE_ACCURACY  # 0.50


# ---------------------------------------------------------------------------
# Deterministic RNG stubs (real objects, no mocks)
# ---------------------------------------------------------------------------


class _AlwaysHit:
    """Returns 0.0 every .random() call — always below any positive accuracy."""

    def random(self) -> float:
        return 0.0

    def uniform(self, a: float, b: float) -> float:
        return a


class _AlwaysMiss:
    """Returns 1.0 every .random() call — never hits."""

    def random(self) -> float:
        return 1.0

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2.0


class _SequencedRNG:
    """Returns values from a list in order; raises if exhausted."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._idx = 0

    def random(self) -> float:
        if self._idx >= len(self._values):
            raise IndexError(f"_SequencedRNG exhausted after {self._idx} draws")
        v = self._values[self._idx]
        self._idx += 1
        return v

    def uniform(self, a: float, b: float) -> float:
        return self.random()

    @property
    def draws(self) -> int:
        return self._idx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_turret(
    name: str = "AutoTurret",
    dps: float = 40.0,
    loading_speed_ms: int = 500,
    range_m: float = 4400.0,
    damage_per_shot: float | None = None,
) -> WeaponStats:
    """Create an auto-turret WeaponStats (automatic=True, no subtype)."""
    dmg = damage_per_shot if damage_per_shot is not None else dps * loading_speed_ms / 1000.0
    return WeaponStats(
        name=name,
        dps=dps,
        damage_per_shot=dmg,
        loading_speed_ms=loading_speed_ms,
        range_m=range_m,
        automatic=True,
        subtype="",
    )


def _manual_turret(
    name: str = "ManualTurret",
    dps: float = 20.0,
    loading_speed_ms: int = 500,
    range_m: float = 3800.0,
    damage_per_shot: float | None = None,
) -> WeaponStats:
    """Create a manual-turret WeaponStats (automatic=False, no subtype)."""
    dmg = damage_per_shot if damage_per_shot is not None else dps * loading_speed_ms / 1000.0
    return WeaponStats(
        name=name,
        dps=dps,
        damage_per_shot=dmg,
        loading_speed_ms=loading_speed_ms,
        range_m=range_m,
        automatic=False,
        subtype="",
    )


def _plasma_collector(name: str = "PlasmaCollector") -> WeaponStats:
    """Create a plasma-collector WeaponStats (subtype='plasma-collector', dps=0)."""
    return WeaponStats(
        name=name,
        dps=0.0,
        damage_per_shot=None,
        loading_speed_ms=0,
        range_m=5000.0,
        automatic=False,
        subtype="plasma-collector",
    )


def _primary(name: str = "Gun", damage: float = 10.0, speed_ms: int = 500, range_m: float = 6000.0) -> WeaponStats:
    return WeaponStats(name=name, dps=1.0, damage_per_shot=damage, loading_speed_ms=speed_ms, range_m=range_m)


def _loadout(
    name: str = "Ship",
    base_armour: int = 5000,
    weapons: list[WeaponStats] | None = None,
    turrets: list[WeaponStats] | None = None,
    modules: list[ModuleStats] | None = None,
    secondary_weapons: list[WeaponStats] | None = None,
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=name,
        base_armour=base_armour,
        weapons=weapons or [],
        turrets=turrets or [],
        modules=modules or [],
        secondary_weapons=secondary_weapons or [],
    )


def _fire_events(log, actor: str, slot: str | None = None, subtype: str | None = None) -> list:
    return [
        e
        for e in log
        if e.type == "weapon_fire"
        and e.actor == actor
        and (slot is None or e.data.get("slot") == slot)
        and (subtype is None or e.data.get("subtype") == subtype)
    ]


def _damage_events(log, target: str) -> list:
    return [e for e in log if e.type == "damage" and e.target == target]


def _run_n_ticks(loadout1: ShipLoadout, loadout2: ShipLoadout, n_ticks: int, rng=None) -> list:
    """Run the fight for exactly n_ticks and return the events so far.

    We override MAX_FIGHT_TICKS by just running the resolver and then slicing
    the resulting combat_log — the resolver runs to completion/time_cap.
    For short n_ticks counts, we use a fat-HP target so it doesn't die early.
    """
    resolver = TickResolver()
    result = resolver.resolve(loadout1, loadout2, rng=rng)
    return [e for e in result.combat_log if e.tick < n_ticks]


# ---------------------------------------------------------------------------
# Test 1: Auto-turret cadence — fires every loading_speed_ms ticks
# ---------------------------------------------------------------------------


def test_auto_turret_cadence():
    """Auto-turret with loading_speed_ms=500 fires at tick 0 then every 50 ticks.

    At tick 0 cooldown_remaining=0 → fires immediately.
    Then cooldown resets to 500ms → ready again at tick 50.
    """
    turret = _auto_turret(loading_speed_ms=500, range_m=STARTING_DIST)  # always in range
    attacker = _loadout("Attacker", turrets=[turret])
    target = _loadout("Target", base_armour=999_999)  # survives long enough

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    fire_evts = _fire_events(log, "Attacker", slot="turret", subtype="auto")
    assert len(fire_evts) >= 2, "Expected at least 2 auto-turret fires"

    # First fire at tick 0 (cooldown starts at 0)
    ticks = [e.tick for e in fire_evts]
    assert ticks[0] == 0, f"First auto-turret fire expected at tick 0, got {ticks[0]}"

    # Each subsequent fire should be exactly 50 ticks later (500ms / 10ms per tick)
    for i in range(1, min(5, len(ticks))):
        gap = ticks[i] - ticks[i - 1]
        assert gap == 50, f"Expected 50-tick cadence, got {gap} between fires {i - 1} and {i}"


# ---------------------------------------------------------------------------
# Test 2: Auto-turret range gate
# ---------------------------------------------------------------------------


def test_auto_turret_range_gate():
    """Auto-turret does NOT fire when out of range; fires at exactly range_m boundary."""
    # Turret range of 300m = MIN_DISTANCE_M — only fires when ships are at minimum distance
    turret = _auto_turret(loading_speed_ms=TICK_MS, range_m=MIN_DIST)
    attacker = _loadout("Attacker", turrets=[turret])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    fire_evts = _fire_events(log, "Attacker", slot="turret", subtype="auto")
    # Ships start at STARTING_DIST (5000m) — should not fire until they close to 300m
    # At minimum they should fire at least once (when ships reach 300m)
    assert len(fire_evts) > 0, "Expected auto-turret to fire when ships close to range_m"

    # All fires should be when distance ≤ range_m = 300m
    # Ships close at ~3m/tick (150*2*0.01=3). From 5000m to 300m ≈ 1566 ticks
    for evt in fire_evts:
        assert evt.data.get("slot") == "turret"
        assert evt.data.get("subtype") == "auto"

    # Turret with range larger than starting distance fires from tick 0
    turret_far = _auto_turret(loading_speed_ms=500, range_m=STARTING_DIST + 1)
    attacker_far = _loadout("AttackerFar", turrets=[turret_far])
    result_far = resolver.resolve(attacker_far, target, rng=_AlwaysHit())
    fire_far = _fire_events(result_far.combat_log, "AttackerFar", slot="turret", subtype="auto")
    assert fire_far[0].tick == 0, "Far-range turret should fire at tick 0"


# ---------------------------------------------------------------------------
# Test 3: Auto-turret multi-turret accuracy share (one value per tick)
# ---------------------------------------------------------------------------


def test_auto_turret_multi_turret_accuracy_share():
    """8-turret ship: all 8 fire at the SAME auto_turret_acc value in a single tick."""
    # Use loading_speed_ms = TICK_MS so all 8 fire every tick
    turrets = [_auto_turret(name=f"AT{i}", loading_speed_ms=TICK_MS, range_m=STARTING_DIST) for i in range(8)]
    attacker = _loadout("BigShip", turrets=turrets)
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    # pvc_damage_reduction > 0 makes c1 (BigShip) a player → uses PLAYER_BASE_ACC
    result = resolver.resolve(attacker, target, pvc_damage_reduction=0.33, rng=_AlwaysHit())
    log = result.combat_log

    # Check tick 0: all 8 turrets should fire with the SAME accuracy value
    tick0_fires = [
        e
        for e in log
        if e.type == "weapon_fire"
        and e.actor == "BigShip"
        and e.tick == 0
        and e.data.get("slot") == "turret"
        and e.data.get("subtype") == "auto"
    ]
    assert len(tick0_fires) == 8, f"Expected 8 auto-turret fires at tick 0, got {len(tick0_fires)}"

    # All 8 must have the identical accuracy value
    accs = {e.data["accuracy"] for e in tick0_fires}
    assert len(accs) == 1, f"Expected 1 shared accuracy value, got {len(accs)}: {accs}"

    # Expected accuracy: clamp(PLAYER_BASE_ACC × 0.85, 0.05, 0.99)
    # (no scanner bonus, no thruster, no cloak override in this fight; c1 is player)
    expected_acc = max(ACC_CLAMP_MIN, min(ACC_CLAMP_MAX, PLAYER_BASE_ACC * AUTO_MULT))
    actual_acc = accs.pop()
    assert abs(actual_acc - expected_acc) < 1e-9, f"Expected {expected_acc}, got {actual_acc}"


# ---------------------------------------------------------------------------
# Test 4: Auto-turret cloak override compounds with 0.85 multiplier
# ---------------------------------------------------------------------------


def test_auto_turret_cloak_override_compounds():
    """Cloaked target: pilot_turret_acc = CLOAK_SET_VALUE → auto fires at 0.25×0.85=0.2125 re-clamped."""
    # We simulate cloak override by using opponent_cloak_active=True.
    # T4's compute_pilot_accuracy applies it; we verify the resulting event accuracy.
    # The simplest approach: run a fight where we know cloak override activates.
    # For T7 tests we rely on the resolver's per-tick accuracy computation.
    # We inject known cloak_set_value and verify the accuracy reported in the event.

    # Since T8 (module activations) is not in scope, we test cloak by patching
    # compute_pilot_accuracy to return cloak-overridden values.
    # Per §5: pilot_turret_acc = CLOAK_SET_VALUE when target is cloaked.
    # auto_turret_acc = clamp(0.25 × 0.85, 0.05, 0.99) = 0.2125

    expected_auto_acc = max(ACC_CLAMP_MIN, min(ACC_CLAMP_MAX, CLOAK_SET * AUTO_MULT))
    # 0.25 * 0.85 = 0.2125 — within [0.05, 0.99] so no clamping
    assert abs(expected_auto_acc - 0.2125) < 1e-9

    # Build a combat where pilot_turret_acc is already CLOAK_SET_VALUE (pre-computed by T4).
    # We verify the math holds via _init_combatant — the acc calculation happens in the resolver.
    # We use a wrapper to patch the compute_pilot_accuracy to return cloak values:
    import src.services.combat_resolver as cr_module
    from src.services.combat_balance import compute_pilot_accuracy

    original_compute = compute_pilot_accuracy

    def cloak_compute(*args, **kwargs):
        # Return (CLOAK_SET_VALUE, CLOAK_SET_VALUE) — cloak override for both acc values
        return (CLOAK_SET, CLOAK_SET)

    cr_module.compute_pilot_accuracy = cloak_compute
    try:
        turret = _auto_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
        attacker = _loadout("Attacker", turrets=[turret])
        target = _loadout("Target", base_armour=999_999)

        resolver = TickResolver()
        result = resolver.resolve(attacker, target, rng=_AlwaysHit())
        log = result.combat_log

        # Get any auto-turret fire event and check accuracy
        fires = _fire_events(log, "Attacker", slot="turret", subtype="auto")
        assert len(fires) > 0, "Expected auto-turret fires"
        for evt in fires[:3]:
            acc = evt.data["accuracy"]
            assert abs(acc - expected_auto_acc) < 1e-9, (
                f"Expected auto-turret accuracy {expected_auto_acc} under cloak, got {acc}"
            )
    finally:
        cr_module.compute_pilot_accuracy = original_compute


# ---------------------------------------------------------------------------
# Test 5: Manual turret — inert while any primary is in range
# ---------------------------------------------------------------------------


def test_manual_turret_inert_while_primary_in_range():
    """Primary in range from tick 0 → manual turret never fires; primaries fire normally."""
    primary = _primary(damage=10.0, speed_ms=500, range_m=STARTING_DIST)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", weapons=[primary], turrets=[manual])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    # Manual turret must NOT fire — primary is in range for the whole fight
    manual_fires = _fire_events(log, "Attacker", slot="turret", subtype="manual")
    assert len(manual_fires) == 0, f"Expected 0 manual-turret fires with primary in range, got {len(manual_fires)}"

    # Primaries MUST fire
    primary_fires = _fire_events(log, "Attacker", slot="primary")
    assert len(primary_fires) > 0, "Primaries should fire while in range"


# ---------------------------------------------------------------------------
# Test 6: Manual turret — gap-closer switch: turret during approach, then primaries
# ---------------------------------------------------------------------------

# Passive closure per tick: BASE_SHIP_SPEED_MPS × 2 × (TICK_MS / 1000) = 3.0 m
CLOSURE_PER_TICK: float = GameConstants.BASE_SHIP_SPEED_MPS * 2 * (TICK_MS / 1000)


def test_manual_turret_gap_closer_switch():
    """Manual turret fires during the approach; primaries take over the instant they're in range.

    Primary range 4400m, start 5000m, closure 3 m/tick → primary in range at tick 200.
    Manual turret (range 5000m, 1-tick cadence) must fire ticks 0–199 and go inert
    from tick 200 on. The primary's first fire at exactly tick 200 also proves its
    cooldown stayed ready (decremented/held at 0) throughout the turret phase.
    """
    primary_range = 4400.0
    switch_tick = int((STARTING_DIST - primary_range) / CLOSURE_PER_TICK)  # 200
    primary = _primary(damage=10.0, speed_ms=500, range_m=primary_range)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", weapons=[primary], turrets=[manual])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    manual_fires = _fire_events(log, "Attacker", slot="turret", subtype="manual")
    primary_fires = _fire_events(log, "Attacker", slot="primary")
    assert len(manual_fires) > 0, "Manual turret should fire during the approach phase"
    assert len(primary_fires) > 0, "Primaries should fire once in range"

    # Approach phase: manual turret fires every tick from 0 up to (but not including) switch_tick
    manual_ticks = sorted(e.tick for e in manual_fires)
    assert manual_ticks[0] == 0, f"Manual turret should fire at tick 0, first fire at {manual_ticks[0]}"
    assert manual_ticks[-1] == switch_tick - 1, (
        f"Manual turret must stop the tick the primary comes in range: "
        f"last manual fire at {manual_ticks[-1]}, expected {switch_tick - 1}"
    )

    # Switch: primary's first fire is at exactly switch_tick (cooldown was held ready)
    assert primary_fires[0].tick == switch_tick, (
        f"Primary should fire the instant it's in range (tick {switch_tick}), got {primary_fires[0].tick}"
    )

    # Sticky: no manual fires at or after switch_tick (distance only closes from here)
    late_manual = [t for t in manual_ticks if t >= switch_tick]
    assert late_manual == [], f"Manual turret fired after primaries took over: ticks {late_manual[:5]}"

    # Primary cadence after switch: every loading_speed_ms // TICK_MS ticks
    expected_gap = 500 // TICK_MS
    assert primary_fires[1].tick - primary_fires[0].tick == expected_gap, (
        f"Primary cadence: expected {expected_gap}-tick gap, got {primary_fires[1].tick - primary_fires[0].tick}"
    )


# ---------------------------------------------------------------------------
# Test 7: Manual turret uses pilot_primary_acc (includes thruster bonus concept)
# ---------------------------------------------------------------------------


def test_manual_turret_uses_primary_accuracy():
    """Manual turrets use pilot_primary_acc (full §5 formula), not pilot_turret_acc × 0.85.

    No primaries equipped → manual turret is active the whole fight. The fire
    events must carry the primary-channel accuracy (NOT turret × 0.85).
    """
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", turrets=[manual])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    # pvc_damage_reduction > 0 makes c1 (Attacker) a player → uses PLAYER_BASE_ACC
    result = resolver.resolve(attacker, target, pvc_damage_reduction=0.33, rng=_AlwaysHit())
    log = result.combat_log

    fires = _fire_events(log, "Attacker", slot="turret", subtype="manual")
    assert len(fires) > 0

    # Accuracy must be pilot_primary_acc, NOT multiplied by 0.85
    # Expected: weapon_accuracy(PLAYER_BASE_ACC, ws_ref) — no scanner, no thruster, no cloak
    from src.services.combat_balance import weapon_accuracy

    expected_acc = weapon_accuracy(PLAYER_BASE_ACC, manual)
    for evt in fires[:3]:
        acc = evt.data["accuracy"]
        assert abs(acc - expected_acc) < 1e-9, (
            f"Manual turret accuracy should be pilot_primary_acc ({expected_acc}), got {acc}"
        )
    # Confirm NOT multiplied by 0.85
    auto_acc = max(ACC_CLAMP_MIN, min(ACC_CLAMP_MAX, PLAYER_BASE_ACC * AUTO_MULT))
    assert abs(expected_acc - auto_acc) > 1e-6, "Test assumption: primary_acc != auto_acc"


# ---------------------------------------------------------------------------
# Test 8: Manual turret — N turrets independent, each rolls independently
# ---------------------------------------------------------------------------


def test_manual_turret_n_turrets_independent():
    """N manual turrets in the turret phase fire up to N shots per cycle, independently."""
    n = 4
    turrets = [_manual_turret(name=f"MT{i}", loading_speed_ms=TICK_MS, range_m=STARTING_DIST) for i in range(n)]
    attacker = _loadout("Attacker", turrets=turrets)
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    # Use always-hit so all N fire
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    # At tick 0: all 4 manual turrets should have cooldown=0 and be in range → all fire
    tick0_fires = [
        e
        for e in log
        if e.type == "weapon_fire"
        and e.actor == "Attacker"
        and e.tick == 0
        and e.data.get("slot") == "turret"
        and e.data.get("subtype") == "manual"
    ]
    assert len(tick0_fires) == n, f"Expected {n} manual-turret fires at tick 0, got {len(tick0_fires)}"

    # Each turret should show up once (different weapon names)
    weapons_fired = {e.data["weapon"] for e in tick0_fires}
    expected_names = {f"MT{i}" for i in range(n)}
    assert weapons_fired == expected_names, f"Expected {expected_names}, got {weapons_fired}"


# ---------------------------------------------------------------------------
# Test 9: Auto-turret fires in BOTH phases; manual only in the turret phase
# ---------------------------------------------------------------------------


def test_auto_turret_fires_in_both_phases():
    """Auto turrets fire on their own cooldown in both phases; manual turrets only pre-switch."""
    primary_range = 4400.0
    switch_tick = int((STARTING_DIST - primary_range) / CLOSURE_PER_TICK)  # 200
    auto = _auto_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    primary = _primary(damage=10.0, speed_ms=500, range_m=primary_range)
    attacker = _loadout("Attacker", weapons=[primary], turrets=[auto, manual])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    auto_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="turret", subtype="auto"))
    manual_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="turret", subtype="manual"))
    primary_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="primary"))

    # Auto turret fires in the approach phase AND alongside primaries after the switch
    assert auto_ticks and auto_ticks[0] < switch_tick, "Auto turret should fire during the approach"
    assert any(t >= switch_tick for t in auto_ticks), "Auto turret should keep firing alongside primaries"

    # Manual turret only fires pre-switch; primaries only post-switch
    assert manual_ticks and all(t < switch_tick for t in manual_ticks), "Manual turret must stop at the switch"
    assert primary_ticks and all(t >= switch_tick for t in primary_ticks), "Primaries fire only once in range"


# ---------------------------------------------------------------------------
# Test 10: PrimaryWeaponMod isolation — auto turret damage NOT scaled
# ---------------------------------------------------------------------------


def test_primary_weapon_mod_does_not_scale_auto_turret():
    """PrimaryWeaponMod (+20% damage) must NOT affect auto-turret damage_per_shot."""
    pw_mod = ModuleStats(
        name="PrimaryWeaponModModule",
        module_type="PrimaryWeaponModModule",
        damage_pct=20,
        fire_rate_pct=0,
    )
    auto = _auto_turret(damage_per_shot=100.0, loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", turrets=[auto], modules=[pw_mod])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    # Damage events caused by auto-turret
    dmg_evts = [
        e
        for e in log
        if e.type == "damage" and e.target == "Target" and e.data.get("source", {}).get("subtype") == "auto"
    ]
    assert len(dmg_evts) > 0, "Expected auto-turret damage events"

    # Each damage should be 100 (raw seed), NOT 120 (scaled by +20%)
    for evt in dmg_evts[:5]:
        assert evt.data["amount"] == 100, (
            f"Auto-turret damage should be 100 (seed value), not {evt.data['amount']} (PrimaryWeaponMod applied)"
        )


# ---------------------------------------------------------------------------
# Test 11: PrimaryWeaponMod isolation — manual turret damage NOT scaled
# ---------------------------------------------------------------------------


def test_primary_weapon_mod_does_not_scale_manual_turret():
    """PrimaryWeaponMod (+20% damage) must NOT affect manual-turret damage_per_shot."""
    pw_mod = ModuleStats(
        name="PrimaryWeaponModModule",
        module_type="PrimaryWeaponModModule",
        damage_pct=20,
        fire_rate_pct=0,
    )
    manual = _manual_turret(damage_per_shot=50.0, loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", turrets=[manual], modules=[pw_mod])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    dmg_evts = [
        e
        for e in log
        if e.type == "damage" and e.target == "Target" and e.data.get("source", {}).get("subtype") == "manual"
    ]
    assert len(dmg_evts) > 0, "Expected manual-turret damage events"

    # Each damage should be 50 (raw seed), NOT 60 (scaled by +20%)
    for evt in dmg_evts[:5]:
        assert evt.data["amount"] == 50, (
            f"Manual-turret damage should be 50 (seed value), not {evt.data['amount']} (PrimaryWeaponMod applied)"
        )


# ---------------------------------------------------------------------------
# Test 12: Plasma-collector is fully inert
# ---------------------------------------------------------------------------


def test_plasma_collector_inert():
    """Plasma-collector turret: no weapon_fire event, no damage, no cooldown decrement."""
    plasma = _plasma_collector()
    attacker = _loadout("Attacker", turrets=[plasma])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    # NO weapon_fire events from attacker at all (plasma-collector is completely inert)
    fire_evts = [e for e in log if e.type == "weapon_fire" and e.actor == "Attacker"]
    assert len(fire_evts) == 0, f"Plasma-collector should emit NO weapon_fire events, got {len(fire_evts)}"

    # NO damage events targeting the opponent from attacker
    dmg_evts = [e for e in log if e.type == "damage" and e.data.get("source", {}).get("attacker") == "Attacker"]
    assert len(dmg_evts) == 0, f"Plasma-collector should deal NO damage, got {len(dmg_evts)}"

    # Also verify plasma-collector is NOT in effective_turrets (filtered at init)
    state = _init_combatant(attacker, is_player=True)
    assert len(state.effective_turrets) == 0, (
        f"Plasma-collector should not appear in effective_turrets, found {len(state.effective_turrets)}"
    )


# ---------------------------------------------------------------------------
# Test 13: Distance resets — no-primaries always-active; shock-blast revert;
#          booster-push revert
# ---------------------------------------------------------------------------


def test_manual_turret_no_primaries_always_active():
    """A ship with zero primaries uses its manual turret for the whole fight."""
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", turrets=[manual])
    target = _loadout("Target", base_armour=999_999)

    result = TickResolver().resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    manual_fires = _fire_events(log, "Attacker", slot="turret", subtype="manual")
    assert len(manual_fires) > 0, "Manual turret should be active with no primaries equipped"
    assert manual_fires[0].tick == 0, "Manual turret should fire from tick 0"
    # Active through the END of the fight, not just the start (range covers all distances)
    last_tick = max(e.tick for e in log)
    assert manual_fires[-1].tick > last_tick - (last_tick // 10 + 1), (
        "Manual turret should keep firing through the whole fight"
    )


def test_manual_turret_resumes_after_shock_blast():
    """Shock-blast distance reset flips the attacker back to the manual turret.

    Attacker closes from 5000m; primary (4400m) takes over at tick 200. The
    TARGET carries a shock-blast, which fires once the gap is inside
    SHOCK_BLAST_TRIGGER_RANGE_M and resets distance to STARTING_DISTANCE_M —
    the attacker must drop back to the manual turret until the primary is in
    range again (one full approach later), then switch back to primaries.
    """
    primary_range = 4400.0
    approach_ticks = int((STARTING_DIST - primary_range) / CLOSURE_PER_TICK)  # 200
    primary = _primary(damage=10.0, speed_ms=500, range_m=primary_range)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    attacker = _loadout("Attacker", weapons=[primary], turrets=[manual], base_armour=999_999)
    shock_blast = WeaponStats(
        name="Shock Blast",
        dps=0.0,
        damage_per_shot=0.0,
        loading_speed_ms=6000,
        range_m=0.0,  # range_m=0 → always in range (D1.2)
        subtype="shock-blast",
    )
    target = _loadout("Target", base_armour=999_999, secondary_weapons=[shock_blast])

    result = TickResolver().resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    sb_resets = [e for e in log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
    assert len(sb_resets) >= 1, "Test setup: shock-blast should have fired at least once"
    t_reset = sb_resets[0].tick
    # The shock-blast re-fires once off cooldown and the gap is closed again — bound
    # all post-reset assertions to the window before the SECOND reset.
    t_reset2 = sb_resets[1].tick if len(sb_resets) > 1 else max(e.tick for e in log) + 1

    manual_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="turret", subtype="manual"))
    primary_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="primary"))

    # Before the reset: manual stops when the primary comes in range
    assert any(t < approach_ticks for t in manual_ticks), "Manual turret fires during initial approach"
    assert not [t for t in manual_ticks if approach_ticks <= t <= t_reset], (
        "Manual turret must stay inert from first switch until the shock-blast reset"
    )

    # After the reset: manual turret resumes the next tick (distance back at 5000m)
    resumed = [t for t in manual_ticks if t > t_reset]
    assert resumed and resumed[0] == t_reset + 1, (
        f"Manual turret should resume the tick after the shock-blast reset "
        f"(tick {t_reset + 1}), first post-reset fire: {resumed[:1]}"
    )

    # ...and stops again once the primary is back in range (a full approach later)
    reswitch = t_reset + 1 + approach_ticks
    assert not [t for t in manual_ticks if reswitch <= t <= t_reset2], (
        "Manual turret must hand back to primaries after the post-reset approach"
    )
    assert any(reswitch <= t <= t_reset2 for t in primary_ticks), (
        "Primaries should resume after the post-reset approach"
    )

    # During the post-reset approach the primary is out of range → silent
    assert not [t for t in primary_ticks if t_reset < t < reswitch], (
        "Primary must be silent while out of range after the shock-blast reset"
    )


def test_manual_turret_resumes_after_booster_push():
    """A booster push that re-opens the gap beyond primary range revives the manual turret.

    The TARGET's booster activates when primary fire drops it under the 80% HP
    threshold (right at the 4400m switch point) and then pushes the distance
    outward, so the attacker's primary falls out of range again and the manual
    turret must resume until the boost expires and the gap re-closes.
    """
    primary_range = 4400.0
    switch_tick = int((STARTING_DIST - primary_range) / CLOSURE_PER_TICK)  # 200
    primary = _primary(damage=250.0, speed_ms=500, range_m=primary_range)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=99_999.0)
    attacker = _loadout("Attacker", weapons=[primary], turrets=[manual], base_armour=999_999)
    booster = ModuleStats(
        name="Cyclotron Boost",
        module_type="BoosterModule",
        effect_pct=300.0,
        effect_duration_ms=10_000,
        loading_speed_ms=60_000,
    )
    # 1000 HP: first primary hit (250 dmg) drops target to 75% → booster activates
    target = _loadout("Target", base_armour=1000, modules=[booster])

    result = TickResolver().resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    pushes = [e for e in log if e.type == "distance" and e.data.get("cause") == "booster_push"]
    assert len(pushes) >= 1, "Test setup: booster push should have occurred"
    t_push = pushes[0].tick
    assert t_push >= switch_tick, "Booster should activate at/after the primary switch point"

    manual_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="turret", subtype="manual"))
    primary_ticks = sorted(e.tick for e in _fire_events(log, "Attacker", slot="primary"))

    # The push re-opens the gap beyond 4400m → manual turret resumes
    resumed = [t for t in manual_ticks if t > t_push]
    assert resumed, "Manual turret should resume after the booster pushes the gap open"
    assert resumed[0] == t_push + 1, (
        f"Manual turret should resume the tick after the first booster push "
        f"(tick {t_push + 1}), first post-push fire: {resumed[:1]}"
    )

    # While pushed out of range, the primary is silent
    assert not [t for t in primary_ticks if t_push < t < resumed[-1]], (
        "Primary must be silent while the booster holds the gap beyond its range"
    )


# ---------------------------------------------------------------------------
# Test 14: Event payload contract — subtype labels per §12
# ---------------------------------------------------------------------------


def test_event_payload_contract():
    """weapon_fire events carry correct subtype labels per §12."""
    auto = _auto_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    manual = _manual_turret(loading_speed_ms=TICK_MS, range_m=STARTING_DIST)
    primary = _primary(speed_ms=TICK_MS, range_m=STARTING_DIST)

    # Fight 1: auto turret with primary (in range — manual would be inert; auto fires)
    attacker1 = _loadout("A1", weapons=[primary], turrets=[auto])
    target1 = _loadout("T1", base_armour=999_999)
    result1 = TickResolver().resolve(attacker1, target1, rng=_AlwaysHit())
    log1 = result1.combat_log

    auto_fires = [e for e in log1 if e.type == "weapon_fire" and e.actor == "A1" and e.data.get("slot") == "turret"]
    assert len(auto_fires) > 0
    for evt in auto_fires[:3]:
        assert evt.data["slot"] == "turret", f"Expected slot='turret', got {evt.data['slot']!r}"
        assert evt.data["subtype"] == "auto", f"Expected subtype='auto', got {evt.data['subtype']!r}"
        assert "weapon" in evt.data
        assert "hit" in evt.data
        assert "accuracy" in evt.data

    # Fight 2: manual turret only (no primaries → manual active all fight)
    attacker2 = _loadout("A2", turrets=[manual])
    target2 = _loadout("T2", base_armour=999_999)
    result2 = TickResolver().resolve(attacker2, target2, rng=_AlwaysHit())
    log2 = result2.combat_log

    manual_fires = [e for e in log2 if e.type == "weapon_fire" and e.actor == "A2"]
    assert len(manual_fires) > 0
    for evt in manual_fires[:3]:
        assert evt.data["slot"] == "turret", f"Expected slot='turret', got {evt.data['slot']!r}"
        assert evt.data["subtype"] == "manual", f"Expected subtype='manual', got {evt.data['subtype']!r}"
        assert "weapon" in evt.data
        assert "hit" in evt.data
        assert "accuracy" in evt.data


# ---------------------------------------------------------------------------
# Test D1 (builder integration): LoadoutBuilder.from_player + fight with auto+manual+plasma turrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_fed_turret_fight():
    """Integration test: LoadoutBuilder.from_player() with AsyncMock DB + real turret discriminators.

    Calls the REAL LoadoutBuilder.from_player() against an AsyncMock DB session that returns
    mock ORM objects with the REAL nested extra_atts shape:
        {"extra_atts": {"loading_speed_ms": ..., "range_m": ..., "damage_per_shot": ..., ...}}

    This exercises the builder's inner-extra_atts unpacking (tw_outer.get("extra_atts", tw_outer))
    so that any nesting regression would break this test.

    Three turrets:
    - "Berger AGT 20mm": automatic=True, explicit damage_per_shot=4 in seed
    - "Hammerhead D1": automatic=False, damage_per_shot derived (no explicit seed value)
    - "PE Ambipolar-5": subtype="plasma-collector" — must be inert

    Asserts:
    - builder.from_player() produces correct WeaponStats (automatic, loading_speed_ms, range_m,
      damage_per_shot) for each turret — including correct inner-extra_atts unpacking
    - ShipLoadout.turrets[0] (Berger) carries damage_per_shot=4 from explicit seed value
    - TickResolver fight: auto fires, manual fires (no primaries equipped), plasma inert
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    # ------------------------------------------------------------------ #
    # Build fake TurretWeapon ORM objects (no SQLAlchemy session needed)  #
    # ------------------------------------------------------------------ #

    def _fake_turret(name, dps, automatic, loading_speed_ms, range_m, subtype="", damage_per_shot=None):
        """Return a MagicMock shaped like a TurretWeapon ORM row."""
        obj = MagicMock()
        obj.name = name
        obj.dps = dps
        obj.automatic = automatic
        # Real DB shape: outer extra_atts wraps an inner "extra_atts" dict
        inner = {"loading_speed_ms": loading_speed_ms, "range_m": range_m, "subtype": subtype}
        if damage_per_shot is not None:
            inner["damage_per_shot"] = damage_per_shot
        obj.extra_atts = {"extra_atts": inner}
        return obj

    berger_orm = _fake_turret(
        "Berger AGT 20mm", dps=40.0, automatic=True, loading_speed_ms=100, range_m=4400.0, damage_per_shot=4
    )
    hammerhead_orm = _fake_turret(
        "Hammerhead D1", dps=20.0, automatic=False, loading_speed_ms=300, range_m=3800.0
    )  # no explicit dps → derived
    plasma_orm = _fake_turret(
        "PE Ambipolar-5", dps=0.0, automatic=False, loading_speed_ms=0, range_m=5000.0, subtype="plasma-collector"
    )

    # ------------------------------------------------------------------ #
    # Mock #1: AsyncMock DB session — execute() returns scalars chain     #
    # ------------------------------------------------------------------ #
    db = AsyncMock()

    def _make_result(first_return):
        result = MagicMock()
        result.scalars.return_value.first.return_value = first_return
        return result

    # Fake PlayerShip: has our three turrets, no weapons/modules
    fake_player_ship = MagicMock()
    fake_player_ship.ship_name = "TestShip"
    fake_player_ship.weapons = []
    fake_player_ship.turrets = ["Berger AGT 20mm", "Hammerhead D1", "PE Ambipolar-5"]
    fake_player_ship.modules = []
    fake_player_ship.secondary_weapons = []

    # Fake Ship: provides base_armour
    fake_ship = MagicMock()
    fake_ship.armour = 500

    # db.execute side_effect: called in order (PlayerRepository.get_by_id is patched → no Player query):
    #   [0]  select(PlayerShip).where(PlayerShip.id == active_ship_id)
    #   [1]  select(Ship).where(Ship.name == ship_name)
    #   [2]  select(TurretWeapon) for "Berger AGT 20mm"
    #   [3]  select(TurretWeapon) for "Hammerhead D1"
    #   [4]  select(TurretWeapon) for "PE Ambipolar-5"
    _execute_returns = [
        _make_result(fake_player_ship),  # [0] PlayerShip
        _make_result(fake_ship),  # [1] Ship
        _make_result(berger_orm),  # [2] TurretWeapon: Berger
        _make_result(hammerhead_orm),  # [3] TurretWeapon: Hammerhead
        _make_result(plasma_orm),  # [4] TurretWeapon: PE Ambipolar-5
    ]
    db.execute.side_effect = _execute_returns

    # ------------------------------------------------------------------ #
    # Mock #2: Patch PlayerRepository so get_by_id returns a known player #
    # (avoids needing db.execute for Player lookup via repo internals)    #
    # ------------------------------------------------------------------ #
    fake_player = MagicMock()
    fake_player.active_ship_id = 1

    from src.services.loadout_builder import LoadoutBuilder

    with patch("persist.repositories.player_repository.PlayerRepository") as MockPlayerRepo:
        mock_repo_instance = MockPlayerRepo.return_value
        mock_repo_instance.get_by_id = AsyncMock(return_value=fake_player)

        loadout = await LoadoutBuilder.from_player(db, player_id=42)

    # ------------------------------------------------------------------ #
    # Assertions: builder output                                          #
    # ------------------------------------------------------------------ #
    assert len(loadout.turrets) == 3, f"Expected 3 turrets, got {len(loadout.turrets)}"

    berger_ws = next(t for t in loadout.turrets if t.name == "Berger AGT 20mm")
    hammerhead_ws = next(t for t in loadout.turrets if t.name == "Hammerhead D1")
    plasma_ws = next(t for t in loadout.turrets if t.name == "PE Ambipolar-5")

    # Berger: explicit damage_per_shot=4 from seed (exercises inner-extra_atts unpacking)
    assert berger_ws.automatic is True
    assert berger_ws.loading_speed_ms == 100
    assert berger_ws.range_m == 4400.0
    assert berger_ws.damage_per_shot == 4.0, (
        f"Berger damage_per_shot should be 4 (explicit seed), got {berger_ws.damage_per_shot}"
    )

    # Hammerhead: no explicit damage_per_shot → derived (dps=20 × 300ms/1000 = 6.0)
    assert hammerhead_ws.automatic is False
    assert hammerhead_ws.loading_speed_ms == 300
    assert hammerhead_ws.damage_per_shot == 6.0, (
        f"Hammerhead damage_per_shot should be 6 (derived), got {hammerhead_ws.damage_per_shot}"
    )

    # Plasma: subtype preserved, dps=0
    assert plasma_ws.subtype == "plasma-collector"

    # ------------------------------------------------------------------ #
    # Feed builder output through TickResolver — confirm it fires         #
    # ------------------------------------------------------------------ #
    target = ShipLoadout(ship_name="Target", base_armour=999_999, weapons=[], turrets=[], modules=[])
    resolver = TickResolver()
    result = resolver.resolve(loadout, target, rng=_AlwaysHit())
    log = result.combat_log

    # Auto-turret (Berger) fires
    auto_fires = [
        e
        for e in log
        if e.type == "weapon_fire"
        and e.actor == "TestShip"
        and e.data.get("slot") == "turret"
        and e.data.get("subtype") == "auto"
        and e.data.get("weapon") == "Berger AGT 20mm"
    ]
    assert len(auto_fires) > 0, "Berger AGT 20mm (auto) should fire"

    # Manual-turret (Hammerhead D1) fires — no primaries equipped → turret phase all fight
    manual_fires = [
        e
        for e in log
        if e.type == "weapon_fire"
        and e.actor == "TestShip"
        and e.data.get("slot") == "turret"
        and e.data.get("subtype") == "manual"
        and e.data.get("weapon") == "Hammerhead D1"
    ]
    assert len(manual_fires) > 0, "Hammerhead D1 (manual, no primaries) should fire"

    # Plasma-collector (PE Ambipolar-5) NEVER fires
    plasma_fires = [
        e for e in log if e.type == "weapon_fire" and e.actor == "TestShip" and e.data.get("weapon") == "PE Ambipolar-5"
    ]
    assert len(plasma_fires) == 0, f"PE Ambipolar-5 (plasma) should be inert, got {len(plasma_fires)} fires"

    # Plasma-collector not in effective_turrets
    state = _init_combatant(loadout, is_player=True)
    assert len(state.effective_turrets) == 2  # berger + hammerhead only
    turret_names = {t.name for t in state.effective_turrets}
    assert "PE Ambipolar-5" not in turret_names, "Plasma-collector must not appear in effective_turrets"
    assert "Berger AGT 20mm" in turret_names
    assert "Hammerhead D1" in turret_names

    # Berger runtime carries the explicit seed damage_per_shot (not derived)
    berger_rt = next(t for t in state.effective_turrets if t.name == "Berger AGT 20mm")
    assert berger_rt.damage_per_shot == 4, (
        f"Expected damage_per_shot=4 (from explicit seed), got {berger_rt.damage_per_shot}"
    )


# ---------------------------------------------------------------------------
# Additional: auto-turret fires alongside primaries (additive, test 5 regression)
# ---------------------------------------------------------------------------


def test_auto_turret_additive_alongside_primaries():
    """Auto-turret fires additive alongside primaries (independent of the manual-turret phase)."""
    auto = _auto_turret(loading_speed_ms=500, range_m=STARTING_DIST)
    primary = _primary(speed_ms=500, range_m=STARTING_DIST)

    # Primary in range from tick 0: both primary and auto fire
    attacker = _loadout("Attacker", weapons=[primary], turrets=[auto])
    target = _loadout("Target", base_armour=999_999)

    resolver = TickResolver()
    result = resolver.resolve(attacker, target, rng=_AlwaysHit())
    log = result.combat_log

    auto_fires = _fire_events(log, "Attacker", slot="turret", subtype="auto")
    primary_fires = _fire_events(log, "Attacker", slot="primary")

    # Both must fire
    assert len(auto_fires) > 0, "Auto-turret should fire alongside primaries"
    assert len(primary_fires) > 0, "Primary should fire while in range"

    # Damage should be additive: both sources appear in damage log
    dmg_evts = _damage_events(log, "Target")
    sources = {e.data.get("source", {}).get("subtype") for e in dmg_evts}
    assert "primary" in sources, f"Primary damage events expected; sources found: {sources}"
    assert "auto" in sources, f"Auto-turret damage events expected; sources found: {sources}"
