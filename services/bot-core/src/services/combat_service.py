"""
Combat Service for BountyBot.

Implements the tick-based Phase-1 combat resolver (TickResolver) plus
legacy-compatible stat collection helpers (get_dps, get_armour, get_shield).

T10: fight_ships is now async and routes exclusively through TickResolver.
SimpleTTKResolver and variance helpers are retired.
"""

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shared import bblogger

from services.combat_balance import (
    ScannerTier,
    booster_debuff_pp,
    compute_pilot_accuracy,
    resolve_scanner_tier,
    thruster_ramp,
    weapon_accuracy,
)
from services.combat_models import (
    CombatEvent,
    CombatEventType,
    CombatMeta,
    CombatStats,
    FightResults,
    FightStats,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from services.game_constants import GameConstants

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger(__name__)

# ---------------------------------------------------------------------------
# TickResolver — tick-based combat simulation (T3 skeleton)
# ---------------------------------------------------------------------------

# Ketar Repair Bot module name constants (used for rate detection in _init_combatant)
_KETAR_II_NAME = "Ketar Repair Bot II"
_KETAR_I_NAME = "Ketar Repair Bot I"

# STI discriminator for the PrimaryWeaponMod module class (§10)
_PRIMARY_WEAPON_MOD_TYPE = "PrimaryWeaponModModule"


@dataclass(slots=True)
class _PrimaryWeaponRuntime:
    """Baked per-primary-weapon stats for one combatant in the tick loop.

    NOT frozen — cooldown_remaining_ms is mutated every tick. Effective stats
    are baked once at combatant init (§7.8 / §1 implementation note); the tick
    loop never recomputes them.

    RNG draw order within a tick (§ determinism):
        C1 primaries in loadout insertion order, then C2 primaries in loadout
        insertion order. This matches the (c1, c2) iteration in Phase 3.
    """

    name: str
    effective_damage_per_shot: int  # round(damage_per_shot × (1 + damage_pct/100)); no floor (§7.8)
    effective_loading_speed_ms: int  # snapped to nearest TICK_MS (§7.8); ≥ TICK_MS
    range_m: float  # binary gate; PrimaryWeaponMod does NOT modify range (§7.8)
    is_pure_emp: bool  # damage_per_shot == 0; fires normally, applies 0 HP delta (§4)
    weapon_stats_ref: WeaponStats  # back-ref for weapon_accuracy() passthrough
    cooldown_remaining_ms: int = 0  # §1: fully ready at tick 0; mutated by Phase 1 each tick


@dataclass(slots=True)
class _SecondaryWeaponRuntime:
    """Baked per-secondary-weapon stats for one combatant in the tick loop.

    Mirror of _PrimaryWeaponRuntime for secondary weapons (D1, T6).
    PrimaryWeaponMod does NOT apply to secondaries (§7.8).
    Secondaries read raw seed damage/loading_speed_ms directly.

    RNG draw order (§ determinism):
        C1 secondaries in loadout insertion order, then C2 secondaries.
        Shock-blast takes NO RNG draw; nuke draws rng.uniform() for epicenter;
        all others draw rng.random() for hit roll.
    """

    name: str
    subtype: str  # "rocket"|"missile"|"cluster-missile"|"nuke"|"shock-blast"|"emp-bomb"|...
    damage_per_shot: int  # raw seed damage (§7.8: secondaries read raw seed, NOT effective_*)
    loading_speed_ms: int  # raw seed loading_speed_ms (reset on fire — hit OR miss)
    range_m: float  # binary gate (§2 / §6.2 D1.2); 0.0 = infinite range
    burst_count: int  # cluster-missile: sub-munition count; 0 for non-cluster
    emp_damage: int  # EMP damage (baked for log fidelity; phase-2+); 0 if none
    magnitude_m: float  # nuke: seed magnitude_m (scaled by NUKE_MAGNITUDE_SCALE at resolve time)
    steerable: bool  # data-only flag in Phase-1; no behaviour branch
    weapon_stats_ref: WeaponStats  # back-ref for weapon_accuracy() passthrough
    cooldown_remaining_ms: int = 0  # §1: fully ready at tick 0; mutated by Phase 1 each tick
    # CI-16: remaining rounds (None = infinite); decremented ONCE after the if/elif chain fires
    remaining_ammo: int | None = None  # None = infinite (back-compat); 0 = depleted (gate blocks fire)


@dataclass(slots=True)
class _TurretWeaponRuntime:
    """Baked per-turret-weapon stats for one combatant in the tick loop (T7).

    Plasma-collector turrets are NOT placed in this list — they are skipped
    entirely at init time and never appear in the tick loop (§7.9).

    PrimaryWeaponMod does NOT apply to turrets (§7.8 explicit exclusion).
    Turrets read raw seed damage_per_shot and loading_speed_ms directly.

    Discriminators (read from WeaponStats typed fields — no extra_atts blob):
        automatic=True  → auto-turret: always fires on its own cooldown alongside primaries.
        automatic=False → manual-turret: fires only when loadout.manual_turret_mode=True,
                          and suppresses primaries when that mode is active.

    RNG draw order (§ determinism):
        Auto turrets: C1 auto-turrets (insertion order), then C2 auto-turrets.
        Manual turrets: C1 manual-turrets (insertion order), then C2 manual-turrets.
        Both turret phases follow the corresponding primary / secondary phases within
        the same Phase 3 tick step.
    """

    name: str
    automatic: bool  # True = auto-turret, False = manual-turret (never plasma-collector)
    damage_per_shot: int  # derived: round(dps × loading_speed_ms / 1000); raw if seed provides it
    loading_speed_ms: int  # raw seed loading_speed_ms
    range_m: float  # binary fire gate: current_distance ≤ range_m (§6.1)
    weapon_stats_ref: WeaponStats  # back-ref for weapon_accuracy() passthrough
    cooldown_remaining_ms: int = 0  # §1: fully ready at tick 0; mutated by Phase 1 each tick


# STI discriminator constants for T8/T9 module detection
_CLOAK_MODULE_TYPE = "CloakModule"
_BOOSTER_MODULE_TYPE = "BoosterModule"
_THRUSTER_MODULE_TYPE = "ThrusterModule"
_EMERGENCY_SYSTEM_MODULE_TYPE = "EmergencySystemModule"

# Built-in U'tool module name (§10 supersession)
_UTOOL_BUILTIN_NAME = "U'tool"

# U'tool virtual stats when used as built-in (§10 / §7.2 wiki values)
_UTOOL_EFFECT_DURATION_MS = 10_000
_UTOOL_LOADING_SPEED_MS = 2_000


@dataclass(slots=True)
class _CloakRuntime:
    """Per-combatant runtime state for the cloak module (§7.2 / §8).

    Tracks activation count, effect/cooldown timers, and consumed thresholds.
    Initial state: cooldown=0, effect=0, activation_count=0 (§1 / §8).
    """

    stats: ModuleStats  # effective cloak module stats (equipped or U'tool virtual)
    cooldown_remaining_ms: int = 0
    effect_remaining_ms: int = 0
    activation_count: int = 0
    consumed_thresholds: list = field(default_factory=list)  # list[int] of consumed threshold pct values


@dataclass(slots=True)
class _BoosterRuntime:
    """Per-combatant runtime state for the booster module (§7.3 / §8).

    Tracks activation count, effect/cooldown timers, and consumed thresholds.
    Initial state: cooldown=0, effect=0, activation_count=0 (§1 / §8).
    """

    stats: ModuleStats  # effective booster module stats
    cooldown_remaining_ms: int = 0
    effect_remaining_ms: int = 0
    activation_count: int = 0
    consumed_thresholds: list = field(default_factory=list)  # list[int] of consumed threshold pct values


@dataclass(slots=True)
class _EmergencySystemRuntime:
    """Per-combatant runtime state for the EmergencySystem module (§7.7 / T9).

    Tracks consumption and remaining invulnerability window.
    Initial state: consumed=False, invuln_remaining_ms=0 (§1).
    """

    consumed: bool = False
    invuln_remaining_ms: int = 0


@dataclass
class _CombatantState:
    """Per-side mutable runtime state for TickResolver. Not frozen — mutated every tick."""

    name: str  # ship_name byte-for-byte — used for stats/damage attribution (DO NOT change)
    loadout: ShipLoadout
    is_player: bool
    # HP layers (integer storage; overkill may go transiently negative before step 4b clamp)
    max_shield: int
    current_shield: int
    max_armour: int
    current_armour: int
    max_hull: int
    current_hull: int
    # Shield regen: one entry per ShieldModule (capacity, period_ticks)
    shield_regen_schedules: list[tuple[int, int]]
    shield_regen_accumulators: list[int]
    # Repair Bot regen accumulator (float; flushed to integer HP when ≥ 1.0)
    repair_bot_regen_accumulator: float
    repair_bot_rate_per_sec: float
    repair_bot_delta_per_tick: float  # pre-baked: (max_hull+max_armour)*rate*(tick_ms/1000)
    # Cooldowns in ms remaining; decremented by TICK_MS each tick; floored at 0
    weapon_cooldowns: dict[str, int]
    module_cooldowns: dict[str, int]
    # Carry-forward state for T7
    manual_turret_mode: bool
    emergency_system_consumed: bool  # legacy field — superseded by es_runtime; kept for compat
    # CI-20: slot (1|2) + pilot/criminal display label for thread/embed naming.
    # display_name defaults to ship_name so preflight/sim callers are unchanged.
    slot: int = 1
    display_name: str = ""  # populated by _init_combatant (defaults to ship_name)
    # CI-21: tracks which layers have fired layer_depleted since last meaningful recovery.
    depleted_layers: set = field(default_factory=set)
    # T4: scanner tier precomputed at combatant init; pilot accuracy recomputed every tick
    scanner_tier: ScannerTier = field(
        default_factory=lambda: ScannerTier(tier="A", accuracy_bonus_pp=0.0, missile_tracking_active=False)
    )
    pilot_primary_acc: float = 0.0
    pilot_turret_acc: float = 0.0
    # T5: baked per-primary-weapon runtime list (effective stats + mutable cooldown)
    # Primaries only — turrets/secondaries are NOT in this list (§7.8 PrimaryWeaponMod scope)
    effective_primaries: list[_PrimaryWeaponRuntime] = field(default_factory=list)
    # T6: baked per-secondary-weapon runtime list (raw seed stats + mutable cooldown)
    # PrimaryWeaponMod does NOT apply to secondaries (§7.8).
    effective_secondaries: list[_SecondaryWeaponRuntime] = field(default_factory=list)
    # T7: baked per-turret-weapon runtime list (non-plasma only; PrimaryWeaponMod excluded §7.8)
    # Plasma-collectors are skipped at init; only auto + manual turrets appear here.
    effective_turrets: list[_TurretWeaponRuntime] = field(default_factory=list)
    # T8: activation-rule module runtime states (None = module not equipped/available)
    cloak_runtime: _CloakRuntime | None = None
    booster_runtime: _BoosterRuntime | None = None
    # T8: thruster stats (passive, no runtime state needed beyond the ModuleStats reference)
    thruster_stats: ModuleStats | None = None
    # T8: per-combatant HP-percent tracking (post-damage, used for threshold crossing detection)
    prev_hp_pct: float = 1.0  # starts at 100% (§8: Phase-1 always starts at 100%)
    # T9: EmergencySystem runtime state (None = ES not equipped)
    es_runtime: _EmergencySystemRuntime | None = None


def _init_combatant(
    loadout: ShipLoadout,
    *,
    is_player: bool,
    slot: int = 1,
    display_name: str = "",
) -> _CombatantState:
    """Build combatant runtime state from a ShipLoadout.

    Called once before the tick loop begins (§1 implementation note).
    All weapons enter at cooldown_remaining = 0; all HP layers start at max;
    all regen accumulators are dormant (layers at max).

    CI-20: ``slot`` (1|2) and ``display_name`` (pilot/criminal label) are threaded
    through as new fields. ``name`` remains = ship_name byte-for-byte.
    ``display_name`` defaults to ``loadout.ship_name`` if not provided.
    """
    tick_ms = GameConstants.TICK_MS

    # HP layers — raw sum, no multipliers (tick resolver uses per-layer raw HP per §3)
    max_shield = sum(m.shield for m in loadout.modules if m.shield > 0)
    max_armour = sum(m.armour for m in loadout.modules if m.armour > 0)
    max_hull = loadout.base_armour  # §3: hull = ship.armour column, NOT a module type

    # Shield regen schedule — one entry per module that provides shield + recharge timing
    shield_schedules: list[tuple[int, int]] = []
    for mod in loadout.modules:
        if mod.shield > 0 and mod.shield_recharge_ms > 0:
            period = math.ceil(mod.shield_recharge_ms / mod.shield / tick_ms)
            shield_schedules.append((mod.shield, period))
    shield_accumulators = [0] * len(shield_schedules)

    # Repair Bot rate — pick highest Ketar rate equipped; ignore stale seed HPps values (§3)
    repair_rate = 0.0
    for mod in loadout.modules:
        # Check II before I to avoid substring collision ("Ketar Repair Bot I" ⊂ "Ketar Repair Bot II")
        if _KETAR_II_NAME in mod.name:
            repair_rate = max(repair_rate, GameConstants.KETAR_II_REPAIR_PCT_PER_SEC)
        elif _KETAR_I_NAME in mod.name:
            repair_rate = max(repair_rate, GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)

    # All cooldowns start at 0 — weapons fully ready at tick 0 (§1)
    # Primary weapon cooldowns are tracked in effective_primaries (T5).
    # Turret cooldowns are tracked in effective_turrets (T7); weapon_cooldowns is now empty.
    # Module cooldowns are tracked here (no per-module runtime object yet).
    weapon_cooldowns: dict[str, int] = {}
    module_cooldowns = {m.name: 0 for m in loadout.modules}

    # Precompute scanner tier once — stateless, same loadout always returns same result (§7.1)
    scanner_tier = resolve_scanner_tier(
        loadout,
        tier_b_bonus_pp=float(GameConstants.SCANNER_TIER_B_BONUS_PP),
        tier_c_bonus_pp=float(GameConstants.SCANNER_TIER_C_BONUS_PP),
    )

    # ------------------------------------------------------------------
    # T5: PrimaryWeaponMod pre-pass — bake effective stats once at init (§7.8)
    # Applies to primary weapons ONLY; turrets and secondaries are unaffected.
    # ------------------------------------------------------------------
    pw_mods = [m for m in loadout.modules if m.module_type == _PRIMARY_WEAPON_MOD_TYPE]
    if len(pw_mods) > 1:
        # Unique-equip invariant violation — first wins; log once outside tick loop (§10)
        flogger.warning(
            f"Combatant '{loadout.ship_name}': multiple PrimaryWeaponMods equipped "
            f"({[m.name for m in pw_mods]}). Using first: '{pw_mods[0].name}'. "
            "Upstream loadout-builder invariant violated."
        )
    pw_mod = pw_mods[0] if pw_mods else None
    damage_pct_val: int = pw_mod.damage_pct if pw_mod is not None else 0
    fire_rate_pct_val: int = pw_mod.fire_rate_pct if pw_mod is not None else 0

    effective_primaries: list[_PrimaryWeaponRuntime] = []
    for ws in loadout.weapons:
        base_dmg = ws.damage_per_shot if ws.damage_per_shot is not None else 0.0
        base_speed = float(ws.loading_speed_ms) if ws.loading_speed_ms > 0 else float(tick_ms)
        # Formula (§7.8 / Appendix B):
        #   effective_damage  = round(base × (1 + damage_pct/100))      — integer, no floor
        #   effective_speed   = round((base / (1 + fire_rate_pct/100)) / TICK_MS) × TICK_MS
        eff_damage: int = round(base_dmg * (1.0 + damage_pct_val / 100.0))
        eff_speed: int = round((base_speed / (1.0 + fire_rate_pct_val / 100.0)) / tick_ms) * tick_ms
        # Ensure cooldown is at least one tick (0ms would fire every tick; unintended for Phase-1 seeds)
        eff_speed = max(tick_ms, eff_speed)
        effective_primaries.append(
            _PrimaryWeaponRuntime(
                name=ws.name,
                effective_damage_per_shot=eff_damage,
                effective_loading_speed_ms=eff_speed,
                range_m=ws.range_m,
                is_pure_emp=(base_dmg == 0.0),
                weapon_stats_ref=ws,
                cooldown_remaining_ms=0,  # §1: fully ready at tick 0
            )
        )

    # ------------------------------------------------------------------
    # T6: Secondary weapon runtime list — baked once at init (§1)
    # PrimaryWeaponMod does NOT apply (§7.8); raw seed values used directly.
    # ------------------------------------------------------------------
    effective_secondaries: list[_SecondaryWeaponRuntime] = []
    for sw in loadout.secondary_weapons:
        raw_dmg = sw.damage_per_shot if sw.damage_per_shot is not None else 0.0
        raw_speed = sw.loading_speed_ms if sw.loading_speed_ms > 0 else tick_ms
        effective_secondaries.append(
            _SecondaryWeaponRuntime(
                name=sw.name,
                subtype=sw.subtype,
                damage_per_shot=round(raw_dmg),
                loading_speed_ms=raw_speed,
                range_m=sw.range_m,
                burst_count=sw.burst_count,
                emp_damage=sw.emp_damage,
                magnitude_m=sw.magnitude_m,
                steerable=sw.steerable,
                weapon_stats_ref=sw,
                cooldown_remaining_ms=0,  # §1: fully ready at tick 0
                # CI-16: bake remaining_ammo from WeaponStats.ammo (None = infinite)
                remaining_ammo=sw.ammo,
            )
        )

    # ------------------------------------------------------------------
    # T7: Turret weapon runtime list — baked once at init (§1)
    # Plasma-collectors (subtype=="plasma-collector") are SKIPPED entirely (§7.9).
    # PrimaryWeaponMod does NOT apply (§7.8 explicit exclusion).
    # damage_per_shot derived from dps × loading_speed_ms / 1000 when not explicitly provided.
    # ------------------------------------------------------------------
    effective_turrets: list[_TurretWeaponRuntime] = []
    for tw in loadout.turrets:
        # Skip plasma-collectors — fully inert; no cooldown, no fire, no event (§7.9)
        if tw.subtype == "plasma-collector":
            continue
        tw_speed = tw.loading_speed_ms if tw.loading_speed_ms > 0 else tick_ms
        # Derive damage_per_shot: use explicit field if set; otherwise dps × loading_speed_ms/1000
        if tw.damage_per_shot is not None and tw.damage_per_shot > 0:
            tw_damage = round(tw.damage_per_shot)
        else:
            # Fallback: derive from dps and cadence (damage = dps × cycle_seconds)
            tw_damage = round(tw.dps * tw_speed / 1000.0)
        effective_turrets.append(
            _TurretWeaponRuntime(
                name=tw.name,
                automatic=tw.automatic,
                damage_per_shot=tw_damage,
                loading_speed_ms=tw_speed,
                range_m=tw.range_m,
                weapon_stats_ref=tw,
                cooldown_remaining_ms=0,  # §1: fully ready at tick 0
            )
        )

    # ------------------------------------------------------------------
    # T8: Cloak runtime state (§7.2 / §10)
    # Supersession: equipped cloak wins; else U'tool built-in (Scimitar/Specter); else None.
    # ------------------------------------------------------------------
    cloak_runtime: _CloakRuntime | None = None
    _cloak_equipped = next((m for m in loadout.modules if m.module_type == _CLOAK_MODULE_TYPE), None)
    if _cloak_equipped is not None:
        cloak_runtime = _CloakRuntime(stats=_cloak_equipped)
    elif _UTOOL_BUILTIN_NAME in (loadout.builtin_modules or []):
        # Synthesize virtual U'tool with wiki stats
        _utool_virtual = ModuleStats(
            name=_UTOOL_BUILTIN_NAME,
            module_type=_CLOAK_MODULE_TYPE,
            effect_duration_ms=_UTOOL_EFFECT_DURATION_MS,
            loading_speed_ms=_UTOOL_LOADING_SPEED_MS,
        )
        cloak_runtime = _CloakRuntime(stats=_utool_virtual)

    # ------------------------------------------------------------------
    # T8: Booster runtime state (§7.3)
    # ------------------------------------------------------------------
    booster_runtime: _BoosterRuntime | None = None
    _booster_equipped = next((m for m in loadout.modules if m.module_type == _BOOSTER_MODULE_TYPE), None)
    if _booster_equipped is not None:
        booster_runtime = _BoosterRuntime(stats=_booster_equipped)

    # ------------------------------------------------------------------
    # T8: Thruster stats (§7.4) — passive; no runtime state beyond ModuleStats ref
    # ------------------------------------------------------------------
    _thruster_equipped = next((m for m in loadout.modules if m.module_type == _THRUSTER_MODULE_TYPE), None)

    # ------------------------------------------------------------------
    # T9: EmergencySystem runtime state (§7.7)
    # Inert modules (§7.9 / §7.10) are NOT initialised — they produce no runtime state,
    # no cooldown, no event emission. The ES is the only consumable in Phase-1.
    # Multiple ES instances (malformed loadout): first wins; loader must not crash (§10).
    # ------------------------------------------------------------------
    _es_equipped = next((m for m in loadout.modules if m.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE), None)
    _es_runtime: _EmergencySystemRuntime | None = _EmergencySystemRuntime() if _es_equipped is not None else None

    return _CombatantState(
        name=loadout.ship_name,
        loadout=loadout,
        is_player=is_player,
        slot=slot,
        display_name=display_name if display_name else loadout.ship_name,
        max_shield=max_shield,
        current_shield=max_shield,
        max_armour=max_armour,
        current_armour=max_armour,
        max_hull=max_hull,
        current_hull=max_hull,
        shield_regen_schedules=shield_schedules,
        shield_regen_accumulators=shield_accumulators,
        repair_bot_regen_accumulator=0.0,
        repair_bot_rate_per_sec=repair_rate,
        repair_bot_delta_per_tick=(max_hull + max_armour) * repair_rate * (tick_ms / 1000),
        weapon_cooldowns=weapon_cooldowns,
        module_cooldowns=module_cooldowns,
        manual_turret_mode=loadout.manual_turret_mode,
        emergency_system_consumed=False,
        scanner_tier=scanner_tier,
        pilot_primary_acc=0.0,  # recomputed at start of tick 0
        pilot_turret_acc=0.0,
        effective_primaries=effective_primaries,
        effective_secondaries=effective_secondaries,
        effective_turrets=effective_turrets,
        cloak_runtime=cloak_runtime,
        booster_runtime=booster_runtime,
        thruster_stats=_thruster_equipped,
        prev_hp_pct=1.0,  # §8: combat starts at 100% HP
        es_runtime=_es_runtime,
    )


def _tick_shield_regen(state: _CombatantState, tick: int, events: list[CombatEvent]) -> None:
    """Apply per-tick shield regen pulses. No-op when shield is at max (dormant)."""
    if not state.shield_regen_schedules:
        return
    if state.current_shield >= state.max_shield:
        # Dormant — discard any partial accumulation from a prior damaged window (§3)
        state.shield_regen_accumulators = [0] * len(state.shield_regen_schedules)
        return

    for i, (_cap, period_ticks) in enumerate(state.shield_regen_schedules):
        state.shield_regen_accumulators[i] += 1
        if state.shield_regen_accumulators[i] >= period_ticks:
            state.shield_regen_accumulators[i] = 0
            if state.current_shield < state.max_shield:
                state.current_shield += 1
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.regen,
                        actor=state.name,
                        target=None,
                        data={"layer": "shield", "amount": 1, "hp_after": state.current_shield, "side": state.slot},
                    )
                )

    # Discard partial accumulation when shield returns to max
    if state.current_shield >= state.max_shield:
        state.shield_regen_accumulators = [0] * len(state.shield_regen_schedules)

    # CI-21: clear depleted_layers latch for shield when meaningful recovery achieved
    if state.max_shield > 0 and "shield" in state.depleted_layers and (
        state.current_shield >= math.ceil(state.max_shield * GameConstants.COMBAT_LAYER_REEMIT_FRACTION)
    ):
        state.depleted_layers.discard("shield")


def _tick_repair_bot_regen(state: _CombatantState, tick: int, events: list[CombatEvent]) -> None:
    """Apply Repair Bot regen for one tick (Appendix B step 2).

    Fills hull first, then armour. Dormant when both layers are at max.
    Float accumulator; integer-flushed per §3. Partial discarded on return to max.
    """
    if state.repair_bot_rate_per_sec == 0.0:
        return
    if state.current_hull >= state.max_hull and state.current_armour >= state.max_armour:
        return  # dormant — both layers at max

    delta = state.repair_bot_delta_per_tick
    # Round to 12 decimal places to prevent IEEE 754 drift (e.g. 10 × 0.1 = 0.9999…)
    state.repair_bot_regen_accumulator = round(state.repair_bot_regen_accumulator + delta, 12)

    if state.repair_bot_regen_accumulator >= 1.0:
        flush = int(state.repair_bot_regen_accumulator)
        state.repair_bot_regen_accumulator -= flush

        # Hull first (§3 fill order)
        hull_deficit = state.max_hull - state.current_hull
        if hull_deficit > 0 and flush > 0:
            hull_add = min(flush, hull_deficit)
            state.current_hull += hull_add
            flush -= hull_add
            events.append(
                CombatEvent(
                    tick=tick,
                    type=CombatEventType.regen,
                    actor=state.name,
                    target=None,
                    data={"layer": "hull", "amount": hull_add, "hp_after": state.current_hull, "side": state.slot},
                )
            )

        # Armour next
        if flush > 0:
            armour_deficit = state.max_armour - state.current_armour
            if armour_deficit > 0:
                armour_add = min(flush, armour_deficit)
                state.current_armour += armour_add
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.regen,
                        actor=state.name,
                        target=None,
                        data={
                            "layer": "armour", "amount": armour_add,
                            "hp_after": state.current_armour, "side": state.slot,
                        },
                    )
                )

    # Discard partial when both layers are back at max
    if state.current_hull >= state.max_hull and state.current_armour >= state.max_armour:
        state.repair_bot_regen_accumulator = 0.0

    # CI-21: clear depleted_layers latch for hull/armour when meaningful recovery achieved
    _reemit_frac = GameConstants.COMBAT_LAYER_REEMIT_FRACTION
    if state.max_hull > 0 and "hull" in state.depleted_layers and (
        state.current_hull >= math.ceil(state.max_hull * _reemit_frac)
    ):
        state.depleted_layers.discard("hull")
    if state.max_armour > 0 and "armour" in state.depleted_layers and (
        state.current_armour >= math.ceil(state.max_armour * _reemit_frac)
    ):
        state.depleted_layers.discard("armour")


def _compute_hp_pct(state: _CombatantState) -> float:
    """Compute HP-percent for threshold detection (§8 locked formula).

    hp_percent = (current_shield + current_armour + current_hull)
               / (max_shield + max_armour + max_hull)

    Ships without shield have max_shield=0; formula degrades naturally.
    Returns 1.0 if total max is zero (degenerate loadout — no threshold ever crosses).
    """
    total_max = state.max_shield + state.max_armour + state.max_hull
    if total_max <= 0:
        return 1.0
    total_current = state.current_shield + state.current_armour + state.current_hull
    return total_current / total_max


def _tick_module_effects(state: _CombatantState, tick: int, events: list[CombatEvent], tick_ms: int) -> None:
    """Phase 1 (alongside cooldown decrement): tick down effect and cooldown timers for T8 modules.

    - Cloak and Booster: effect_remaining_ms decrements; on expiry cooldown starts; cooldown decrements.
    - Thruster: passive, no timer.
    - Emits cooldown_end when cooldown transitions >0 → 0 (consistent with weapon cooldown semantics).

    Cooldown timing (§7.2): when effect expires, cooldown is set to loading_speed_ms and does NOT
    decrement on that same tick — it starts decrementing on the NEXT tick. This mirrors the weapon
    cooldown path where Phase-1 decrement runs BEFORE Phase-3 fire sets the cooldown, so the
    effective cooldown is always exactly loading_speed_ms ticks long.
    """
    for mod_rt in (state.cloak_runtime, state.booster_runtime):
        if mod_rt is None:
            continue
        # Effect tick-down; track whether cooldown was set this tick to avoid an immediate decrement.
        cooldown_just_set = False
        if mod_rt.effect_remaining_ms > 0:
            prior_effect = mod_rt.effect_remaining_ms
            mod_rt.effect_remaining_ms = max(0, prior_effect - tick_ms)
            # Cooldown starts at effect EXPIRY (§7.2 / §7.3)
            if mod_rt.effect_remaining_ms <= 0 and prior_effect > 0:
                mod_rt.cooldown_remaining_ms = mod_rt.stats.loading_speed_ms
                cooldown_just_set = True
        # Cooldown tick-down — skip on the tick cooldown was just set so the effective
        # window is exactly loading_speed_ms (not loading_speed_ms - tick_ms).
        if mod_rt.cooldown_remaining_ms > 0 and not cooldown_just_set:
            prior_cd = mod_rt.cooldown_remaining_ms
            mod_rt.cooldown_remaining_ms = max(0, prior_cd - tick_ms)
            if prior_cd > 0 and mod_rt.cooldown_remaining_ms == 0:
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.cooldown_end,
                        actor=state.name,
                        target=None,
                        data={"system": mod_rt.stats.name, "side": state.slot},
                    )
                )


def _eval_hp_threshold_modules(
    state: _CombatantState,
    tick: int,
    events: list[CombatEvent],
    cloak_thresholds: list[int],
    booster_thresholds: list[int],
) -> None:
    """Phase 5: evaluate HP-threshold module activations for one combatant (§8).

    Crossing detection: previous-tick HP-pct was above threshold; post-damage HP-pct is at or below.
    Threshold consumed regardless of whether device activates (universal trigger rule §8).
    Booster-user can still fire (§7.3) — no phase-3 suppression needed here.
    """
    current_pct = _compute_hp_pct(state)
    prev_pct = state.prev_hp_pct

    # --- Cloak (§7.2) ---
    if state.cloak_runtime is not None:
        cr = state.cloak_runtime
        for threshold in cloak_thresholds:
            threshold_frac = threshold / 100.0
            # Crossing: was above threshold last tick, now at or below (§8 definition)
            if prev_pct > threshold_frac >= current_pct and threshold not in cr.consumed_thresholds:
                cr.consumed_thresholds.append(threshold)
                # Check activation eligibility (§7.2)
                eligible = cr.cooldown_remaining_ms <= 0 and cr.activation_count < 2 and cr.effect_remaining_ms == 0
                if eligible:
                    cr.effect_remaining_ms = cr.stats.effect_duration_ms
                    cr.activation_count += 1
                    events.append(
                        CombatEvent(
                            tick=tick,
                            type=CombatEventType.module_activation,
                            actor=state.name,
                            target=None,
                            data={"module": "cloak", "trigger_hp_pct": threshold, "side": state.slot},
                        )
                    )
                # else: threshold consumed but not activated (cooling or active or count cap)
                # Universal rule: threshold never retried (§8)

    # --- Booster (§7.3) ---
    if state.booster_runtime is not None:
        br = state.booster_runtime
        for threshold in booster_thresholds:
            threshold_frac = threshold / 100.0
            if prev_pct > threshold_frac >= current_pct and threshold not in br.consumed_thresholds:
                br.consumed_thresholds.append(threshold)
                eligible = br.cooldown_remaining_ms <= 0 and br.activation_count < 4 and br.effect_remaining_ms == 0
                if eligible:
                    br.effect_remaining_ms = br.stats.effect_duration_ms
                    br.activation_count += 1
                    events.append(
                        CombatEvent(
                            tick=tick,
                            type=CombatEventType.module_activation,
                            actor=state.name,
                            target=None,
                            data={"module": "booster", "trigger_hp_pct": threshold, "side": state.slot},
                        )
                    )

    # Update prev_hp_pct for next tick's crossing detection
    state.prev_hp_pct = current_pct


def _eval_emergency_system(
    state: _CombatantState,
    tick: int,
    events: list[CombatEvent],
    invuln_ms: int,
) -> None:
    """Phase 4a: EmergencySystem evaluation for one combatant (§7.7 / T9).

    Called AFTER all damage events for the tick have been applied (including overkill that pushed
    hull transiently negative), and BEFORE the phase 4b HP clamp (hull clamped to 0 for display).

    Trigger: ES is equipped, unconsumed, AND hull ≤ 0 after this tick's damage application.
    Effect: hull clamped to 1, invuln window started (invuln_remaining_ms = invuln_ms).
    ES is marked consumed (once per fight — §7.7).

    Multiple ES instances from a malformed loadout: _init_combatant grabs the FIRST matching
    module into es_runtime, so there is only ever one runtime object. The consumed flag prevents
    a second activation even if somehow called twice.

    Emits: module_activation event with data={module: "emergency_system"}.
    trigger_hp_pct is intentionally OMITTED for ES (§12 explicit — cloak/booster carry it; ES does not).

    NOT an HP-threshold device (§8). This function must NOT be called from Phase 5.
    """
    if state.es_runtime is None:
        return  # no ES equipped
    if state.es_runtime.consumed:
        return  # already fired once this fight (consumable — §7.7)
    if state.current_hull > 0:
        return  # hull still positive — not triggered

    # Hull ≤ 0 AND ES available: fire ES
    state.current_hull = 1  # clamp to 1; overkill discarded (§7.7)
    state.es_runtime.invuln_remaining_ms = invuln_ms
    state.es_runtime.consumed = True
    events.append(
        CombatEvent(
            tick=tick,
            type=CombatEventType.module_activation,
            actor=state.name,
            target=None,
            data={"module": "emergency_system", "side": state.slot},
            # trigger_hp_pct intentionally OMITTED per §12 / locked decision Q5
        )
    )


def _apply_damage(
    state: _CombatantState,
    raw_damage: float,
    tick: int,
    events: list[CombatEvent],
    *,
    source: dict,
    pvc_damage_reduction: float,
) -> None:
    """Apply one damage event to a combatant (Appendix B step 4).

    DR is the first scaler (§3). Walks shield → armour → hull with overkill carryover.
    HP may go transiently negative (clamped at step 4b). Emits damage + layer_depleted events.

    T9 — EmergencySystem invuln gate (§7.7 / §12):
    If the target's ES invuln window is active (invuln_remaining_ms > 0), incoming damage is
    blocked entirely. We still emit a damage event with amount=0, breakdown omitted, hp_after
    unchanged, and blocked_by="emergency_system_invuln" — this keeps shot/hit accounting correct
    for the summary builder (every weapon_fire hit gets a corresponding damage row).

    T10 (Deliverable 0) — absorbed HP:
    The event data includes an ``absorbed`` field = HP ACTUALLY REMOVED from this combatant
    (post-clamp at 0, overkill excluded). Computed as (hp_before - hp_after) clamped to ≥ 0.
    ``amount`` keeps the raw DR-scaled value for combat-log display; only ``absorbed`` feeds
    the summary builder's damage_dealt / damage_taken counters.
    """
    # T9: EmergencySystem invuln gate — block all incoming damage during the window (§7.7)
    if state.es_runtime is not None and state.es_runtime.invuln_remaining_ms > 0:
        events.append(
            CombatEvent(
                tick=tick,
                type=CombatEventType.damage,
                actor=None,
                target=state.name,
                data={
                    "amount": 0,
                    "absorbed": 0,  # T10: no HP removed during invuln
                    # breakdown OMITTED per §12 damage-row spec for invuln events
                    "hp_after": {
                        "shield": state.current_shield,
                        "armour": state.current_armour,
                        "hull": state.current_hull,
                    },
                    "source": source,
                    "blocked_by": "emergency_system_invuln",
                    "side": state.slot,  # CI-20: target slot for unambiguous display
                },
            )
        )
        return  # no HP mutation; no layer_depleted possible

    # Step (i): PvC DR — first modifier, before stacking (§3 / Appendix B step 4i)
    if state.is_player and pvc_damage_reduction > 0.0:
        applied: int = round(raw_damage * (1.0 - pvc_damage_reduction))
    else:
        applied = round(raw_damage)

    # Snapshot HP before damage application (for absorbed calculation)
    hp_before = state.current_shield + state.current_armour + max(0, state.current_hull)

    remaining = applied
    shield_taken = 0
    armour_taken = 0
    hull_taken = 0

    shield_was_positive = state.current_shield > 0
    armour_was_positive = state.current_armour > 0

    # Step (ii): shield → armour → hull with overkill carryover (§3)
    if remaining > 0 and state.current_shield > 0:
        take = min(state.current_shield, remaining)
        shield_taken = take
        state.current_shield -= take
        remaining -= take

    if remaining > 0 and state.current_armour > 0:
        take = min(state.current_armour, remaining)
        armour_taken = take
        state.current_armour -= take
        remaining -= take

    if remaining > 0:
        hull_taken = remaining
        state.current_hull -= remaining  # intentionally negative — overkill allowed until step 4b

    # T10: Compute absorbed HP — HP actually removed (overkill excluded).
    # hp_after is post-damage but pre-clamp; clamp hull to 0 for the diff so overkill is excluded.
    hp_after_clamped = state.current_shield + state.current_armour + max(0, state.current_hull)
    absorbed = max(0, hp_before - hp_after_clamped)

    events.append(
        CombatEvent(
            tick=tick,
            type=CombatEventType.damage,
            actor=None,
            target=state.name,
            data={
                "amount": applied,
                "absorbed": absorbed,  # T10: HP actually removed, overkill excluded
                "breakdown": {"shield": shield_taken, "armour": armour_taken, "hull": hull_taken},
                "hp_after": {
                    "shield": state.current_shield,
                    "armour": state.current_armour,
                    "hull": state.current_hull,
                },
                "source": source,
                "side": state.slot,  # CI-20: target slot for HP-milestone synthesis (CI-22)
            },
        )
    )

    # layer_depleted — shield first, then armour (hull depletion → termination at step 8)
    # CI-21: emit only if NOT already in depleted_layers (emission-side latch).
    # Hull is terminal (emits once on death) — not latched; it can only deplete once per fight.
    if shield_was_positive and state.current_shield <= 0 and "shield" not in state.depleted_layers:
        state.depleted_layers.add("shield")
        events.append(
            CombatEvent(
                tick=tick,
                type=CombatEventType.layer_depleted,
                actor=state.name,
                target=None,
                data={"layer": "shield", "side": state.slot},
            )
        )
    if armour_was_positive and state.current_armour <= 0 and "armour" not in state.depleted_layers:
        state.depleted_layers.add("armour")
        events.append(
            CombatEvent(
                tick=tick,
                type=CombatEventType.layer_depleted,
                actor=state.name,
                target=None,
                data={"layer": "armour", "side": state.slot},
            )
        )
    # CI-27: hull depletion is NOT emitted here — moved to Phase 8 (post-ES/post-clamp)
    # so that ES-saved ships never receive a false "hull depleted (dead)" event.


def _rocket_accuracy(current_distance: float, range_m: float, min_distance: float) -> float:
    """Linear rocket accuracy curve (§6.2 / Appendix B).

    accuracy = 0.05 + 0.55 × ((range_m − current_distance) / (range_m − min_distance))
             → clamp [0.05, 0.60]

    Args:
        current_distance: Fire-time distance in meters.
        range_m: Weapon range gate from seed.
        min_distance: GameConstants.MIN_DISTANCE_M.

    Returns:
        Accuracy float in [0.05, 0.60].
    """
    denom = range_m - min_distance
    if denom <= 0:
        return 0.05  # degenerate: range_m == min_distance → floor
    raw = 0.05 + 0.55 * ((range_m - current_distance) / denom)
    return max(0.05, min(0.60, raw))


def _nuke_dmg(distance: float, damage: int, effective_magnitude: float) -> float:
    """Nuke falloff formula (§6.2 / Appendix B).

    dmg(d) = damage × (1 − min(1, d / effective_magnitude))²

    Returns un-rounded float; callers pass to _apply_damage which rounds.
    """
    if effective_magnitude <= 0:
        return 0.0
    fraction = min(1.0, distance / effective_magnitude)
    return damage * (1.0 - fraction) ** 2


def _shock_blast_apply(attacker: _CombatantState, current_distance: float) -> float:
    """Apply shock-blast Phase 6 effect: returns new distance (STARTING_DISTANCE_M).

    Shock-blast resets current_distance to STARTING_DISTANCE_M (D6 / Appendix B §6).
    This function is PURE with respect to combatant state — it ONLY computes the
    new distance value. It does NOT mutate attacker, the target, module_cooldowns,
    weapon_cooldowns, or any other field on any _CombatantState.

    The caller (TickResolver Phase 6) is responsible for:
      - updating the resolver-local current_distance variable
      - emitting the distance event to the combat log

    Args:
        attacker: Shock-blast owner (used for name in event; state NOT mutated).
        current_distance: Current resolver-local distance before reset.

    Returns:
        New distance after shock-blast reset (always STARTING_DISTANCE_M).
    """
    _ = attacker  # name used by caller for event actor; no state mutation
    _ = current_distance  # captured by caller for 'from' field; not used here
    return float(GameConstants.STARTING_DISTANCE_M)


def _build_fight_summary(
    events: list[CombatEvent],
    c1: _CombatantState,
    c2: _CombatantState,
    outcome: str,
    reason: str,
    duration_ticks: int,
    winner_name: str | None,
) -> dict:
    """Build the Tier-0 summary dict (§12 data.summary) by scanning the in-memory event list.

    All scans read CombatEvent OBJECT ATTRIBUTES (ev.type, ev.actor, ev.data[...]),
    NOT dict keys — the timeline holds dataclass instances, not dicts (§12 precision note).

    Per-combatant fields derived from the event scan:
      shots_fired       — count of weapon_fire events actor==combatant
      shots_hit         — count of weapon_fire events actor==combatant AND data["hit"]==True
      accuracy          — shots_hit/shots_fired; 0.0 if no shots
      module_activations — {module_key: count} SPARSE (cloak/booster/emergency_system only)
      secondary_fired   — {subtype: count} SPARSE (secondaries only)
      damage_dealt      — sum of damage event amounts where data["source"]["attacker"]==combatant
      damage_taken      — sum of damage event amounts where target==combatant

    fight_start event provides start_hp; fight_end event provides final_hp (post-clamp).
    c1/c2 keys in fight_end.final_hp map to summary combatants "1"/"2" respectively (precision note).
    """
    # Extract start_hp and final_hp from fight_start / fight_end events
    start_hp: dict[str, dict] = {}  # {"1": {shield, armour, hull}, "2": {...}}
    final_hp: dict[str, dict] = {}

    for ev in events:
        if ev.type == CombatEventType.fight_start:
            combatants_data = ev.data.get("combatants", [])
            for i, cb in enumerate(combatants_data):
                key = str(i + 1)
                start_hp[key] = dict(cb.get("hp", {}))
        elif ev.type == CombatEventType.fight_end:
            # fight_end.final_hp uses c1/c2 keys; map to "1"/"2" per precision note
            raw_final = ev.data.get("final_hp", {})
            final_hp["1"] = dict(raw_final.get("c1", {}))
            final_hp["2"] = dict(raw_final.get("c2", {}))

    # Per-combatant accumulators — CI-24: keyed on SLOT (1/2) not ship name so same-ship
    # fights accumulate stats per-side correctly. Ship name is still stored for display.
    # Build a name→slot map for attacker/target lookups (still ship_name in events).
    _name_to_slot: dict[str, int] = {}
    # When both ships share a name, the map would collide; we handle that by using
    # data["side"] directly when available, falling back to name-match.
    # For the accumulators themselves we always key on slot ("1"/"2").
    c1_slot = "1"
    c2_slot = "2"
    _name_to_slot[c1.name] = 1
    if c2.name != c1.name:
        _name_to_slot[c2.name] = 2

    shots_fired: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    shots_hit: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    module_activations: dict[str, dict[str, int]] = {c1_slot: {}, c2_slot: {}}
    secondary_fired: dict[str, dict[str, int]] = {c1_slot: {}, c2_slot: {}}
    damage_dealt: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    damage_taken: dict[str, int] = {c1_slot: 0, c2_slot: 0}

    def _slot_from_event(ev: CombatEvent) -> str | None:
        """Resolve actor's slot from event. Prefers data['side']; falls back to name→slot."""
        side = ev.data.get("side") if ev.data else None
        if side is not None:
            return str(side)
        actor = ev.actor
        if actor is None:
            return None
        slot = _name_to_slot.get(actor)
        return str(slot) if slot is not None else None

    # Discrete activation modules tracked in summary (§12 / §13)
    _ACTIVATION_MODULES = frozenset({"cloak", "booster", "emergency_system"})

    for ev in events:
        if ev.type == CombatEventType.weapon_fire:
            ev_slot = _slot_from_event(ev)
            if ev_slot in shots_fired:
                shots_fired[ev_slot] += 1
                if ev.data.get("hit") is True:
                    shots_hit[ev_slot] += 1
            # secondary_fired — count by subtype for secondaries (data["slot"] == "secondary")
            if ev.data.get("slot") == "secondary" and ev_slot in secondary_fired:
                sub = ev.data.get("subtype", "")
                if sub:
                    secondary_fired[ev_slot][sub] = secondary_fired[ev_slot].get(sub, 0) + 1

        elif ev.type == CombatEventType.module_activation:
            ev_slot = _slot_from_event(ev)
            if ev_slot in module_activations:
                mod_key = ev.data.get("module", "")
                if mod_key in _ACTIVATION_MODULES:
                    module_activations[ev_slot][mod_key] = module_activations[ev_slot].get(mod_key, 0) + 1

        elif ev.type == CombatEventType.damage:
            # damage_dealt attribution: actor is None on damage events; attacker lives in data.source.attacker
            # (precision note §12 — do NOT match ev.actor for damage events)
            # T10 (Deliverable 0): use absorbed (HP actually removed, overkill excluded) not raw amount.
            # ES-invuln events have absorbed=0 (and amount=0); both excluded from damage_dealt/taken.
            absorbed_hp = ev.data.get("absorbed", 0)
            if absorbed_hp > 0:
                source = ev.data.get("source", {})
                attacker_name = source.get("attacker")
                # Resolve attacker slot from data["side"] (target) then name fallback for attacker
                # damage events have data["side"] = target's slot; attacker slot = the other one
                target_side = ev.data.get("side")
                if target_side is not None:
                    att_slot = c2_slot if str(target_side) == c1_slot else c1_slot
                elif attacker_name is not None:
                    _raw = _name_to_slot.get(attacker_name)
                    att_slot = str(_raw) if _raw is not None else None
                else:
                    att_slot = None
                # target slot from data["side"]
                tgt_slot = str(target_side) if target_side is not None else None
                if tgt_slot is None and ev.target is not None:
                    _raw_t = _name_to_slot.get(ev.target)
                    tgt_slot = str(_raw_t) if _raw_t is not None else None
                if att_slot in damage_dealt:
                    damage_dealt[att_slot] += absorbed_hp
                if tgt_slot in damage_taken:
                    damage_taken[tgt_slot] += absorbed_hp

    def _combatant_block(cx: _CombatantState, slot_key: str) -> dict:
        """Build per-combatant summary block (CI-24: keyed on slot, not name)."""
        fired = shots_fired[slot_key]
        hit = shots_hit[slot_key]
        acc = (hit / fired) if fired > 0 else 0.0
        return {
            "name": cx.display_name,   # CI-20: pilot/criminal label (defaults to ship_name)
            "ship": cx.loadout.ship_name,
            "start_hp": start_hp.get(slot_key, {"shield": 0, "armour": 0, "hull": 0}),
            "final_hp": final_hp.get(slot_key, {"shield": 0, "armour": 0, "hull": 0}),
            "damage_dealt": damage_dealt[slot_key],
            "damage_taken": damage_taken[slot_key],
            "shots_fired": fired,
            "shots_hit": hit,
            "accuracy": acc,
            "module_activations": dict(module_activations[slot_key]),  # sparse — only keys that fired ≥1
            "secondary_fired": dict(secondary_fired[slot_key]),  # sparse — only subtypes that fired ≥1
        }

    return {
        "outcome": outcome,
        "reason": reason,
        "duration_ticks": duration_ticks,
        "winner": winner_name,
        "combatants": {
            "1": _combatant_block(c1, c1_slot),
            "2": _combatant_block(c2, c2_slot),
        },
    }


class TickResolver:
    """Tick-based combat resolver implementing Appendix B phase order.

    Takes two ShipLoadout inputs and simulates a fight tick-by-tick (10 ms ticks,
    max 18,000 ticks = 3 simulated minutes). Returns FightResults with a full
    CombatEvent timeline in combat_log.

    T3 milestone: resolver runs end-to-end as a drift-to-floor fight (no weapons,
    distance closes 5000m → 300m, terminates at time_cap). Weapon firing (T5),
    modules (T8/T9), and persistence (T10) are clearly-named no-op stubs.

    Does NOT implement the CombatResolver Protocol (different signature); coexists
    with SimpleTTKResolver until T10 retires the legacy resolver.

    PvC convention: pvc_damage_reduction > 0.0 → C1 is the player-side combatant.
    The resolver does NOT introspect loadouts for an is_player flag — the caller
    sets the convention via pvc_damage_reduction (§12, Q7 lock).
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)  # injectable for T4+ deterministic RNG; unused in T3

    def resolve(
        self,
        loadout1: ShipLoadout,
        loadout2: ShipLoadout,
        *,
        pvc_damage_reduction: float = 0.0,
        guild_config=None,
        rng: random.Random | None = None,
        combatant1_label: str = "",
        combatant2_label: str = "",
    ) -> FightResults:
        """Run a full tick-based fight between two ShipLoadouts.

        Args:
            loadout1: C1 — challenger (player in PvC when pvc_damage_reduction > 0).
            loadout2: C2 — opponent (NPC in PvC; player2 in PvP).
            pvc_damage_reduction: Keith T. Maxwell DR (§3). 0.33 for PvC, 0.0 for PvP.
            guild_config: Reserved for per-guild constant overrides (T10+).
            rng: Optional seeded RNG for deterministic testing. When provided, takes
                 precedence over any seed passed to the constructor. Pass exactly one;
                 passing both is allowed but rng= wins. None (default) falls back to
                 the constructor's self._rng (seeded via TickResolver(seed=...)).
            combatant1_label: CI-20 display label for C1 (pilot/player name). Defaults
                              to ship_name when empty — preflight/sim paths unchanged.
            combatant2_label: CI-20 display label for C2 (criminal/opponent name). Same default.

        Returns:
            FightResults with combat_log timeline and metadata block.
        """
        # --- Pre-loop bake: read constants once, not per-tick ---
        tick_ms = GameConstants.TICK_MS
        max_ticks = GameConstants.MAX_FIGHT_TICKS
        min_dist = float(GameConstants.MIN_DISTANCE_M)
        distance_delta = GameConstants.BASE_SHIP_SPEED_MPS * 2 * (tick_ms / 1000)
        # Accuracy constants (T4)
        _player_base_acc = GameConstants.PLAYER_BASE_ACCURACY
        _npc_base_acc = GameConstants.NPC_BASE_ACCURACY
        _cloak_set = GameConstants.CLOAK_SET_VALUE
        _acc_clamp_min = GameConstants.ACCURACY_CLAMP_MIN
        _acc_clamp_max = GameConstants.ACCURACY_CLAMP_MAX
        # RNG seam (T4 — not consumed until T5; inject via rng= kwarg for deterministic tests)
        _rng = rng if rng is not None else self._rng
        # T7: auto-turret accuracy multiplier (baked once — constant per fight)
        _auto_turret_multiplier = GameConstants.AUTO_TURRET_ACCURACY_MULTIPLIER
        # T8: HP-threshold activation constants (baked once per fight)
        _cloak_thresholds: list[int] = list(GameConstants.CLOAK_HP_THRESHOLDS_PCT)
        _booster_thresholds: list[int] = list(GameConstants.BOOSTER_HP_THRESHOLDS_PCT)
        _k_boost = float(GameConstants.BOOSTER_ACCURACY_DEBUFF_FACTOR)
        _k_thrust = float(GameConstants.THRUSTER_ACCURACY_BONUS_FACTOR)
        _thruster_window = float(GameConstants.THRUSTER_WINDOW_M)
        _base_speed_mps = float(GameConstants.BASE_SHIP_SPEED_MPS)

        # --- Combatant init (§1: separate from tick loop) ---
        c1 = _init_combatant(
            loadout1, is_player=(pvc_damage_reduction > 0.0), slot=1, display_name=combatant1_label
        )
        c2 = _init_combatant(loadout2, is_player=False, slot=2, display_name=combatant2_label)

        current_distance = float(GameConstants.STARTING_DISTANCE_M)
        events: list[CombatEvent] = []

        # fight_start event (tick 0 pre-loop)
        events.append(
            CombatEvent(
                tick=0,
                type=CombatEventType.fight_start,
                actor=None,
                target=None,
                data={
                    "combatants": [
                        {
                            "name": c1.name,           # ship_name byte-for-byte
                            "display_name": c1.display_name,  # CI-20: pilot/player label
                            "ship": c1.loadout.ship_name,
                            "slot": c1.slot,
                            "hp": {"shield": c1.current_shield, "armour": c1.current_armour, "hull": c1.current_hull},
                        },
                        {
                            "name": c2.name,
                            "display_name": c2.display_name,
                            "ship": c2.loadout.ship_name,
                            "slot": c2.slot,
                            "hp": {"shield": c2.current_shield, "armour": c2.current_armour, "hull": c2.current_hull},
                        },
                    ],
                    "initial_distance": current_distance,
                },
            )
        )

        outcome = "stalemate"
        reason = "time_cap"
        winner_name: str | None = None
        loser_name: str | None = None
        ticks_elapsed = max_ticks

        for tick in range(max_ticks):
            # ------------------------------------------------------------------
            # T4/T8: Per-tick accuracy recomputation — before Phase 1.
            # T8 wires: own_thruster_bonus_pp (passive ramp), opponent_booster_debuff_pp
            # (opponent booster active), opponent_cloak_active (own cloak effect active
            # → opponent's accuracy replaced). Each combatant's accuracy is affected by
            # its OWN thruster and the OPPONENT'S booster/cloak.
            # ------------------------------------------------------------------
            for _state, _opponent in ((c1, c2), (c2, c1)):
                _acc_base = _player_base_acc if _state.is_player else _npc_base_acc
                # Own thruster bonus (passive ramp, primaries only — turret excluded in compute_pilot_accuracy)
                _thr_bonus_pp = 0.0
                if _state.thruster_stats is not None and _state.thruster_stats.effect_pct > 0:
                    _ramp = thruster_ramp(
                        current_distance,
                        thruster_window_m=_thruster_window,
                        min_distance_m=min_dist,
                    )
                    _thr_bonus_pp = _state.thruster_stats.effect_pct * _k_thrust * _ramp
                # Opponent booster debuff
                _boost_debuff_pp = 0.0
                if _opponent.booster_runtime is not None and _opponent.booster_runtime.effect_remaining_ms > 0:
                    _boost_debuff_pp = booster_debuff_pp(_opponent.booster_runtime.stats.effect_pct, k_boost=_k_boost)
                # Opponent cloak active (own cloak replaces our accuracy)
                _opp_cloak_active = (
                    _opponent.cloak_runtime is not None and _opponent.cloak_runtime.effect_remaining_ms > 0
                )
                _state.pilot_primary_acc, _state.pilot_turret_acc = compute_pilot_accuracy(
                    combatant_base=_acc_base,
                    own_scanner_bonus_pp=_state.scanner_tier.accuracy_bonus_pp,
                    own_thruster_bonus_pp=_thr_bonus_pp,
                    opponent_booster_debuff_pp=_boost_debuff_pp,
                    opponent_cloak_active=_opp_cloak_active,
                    cloak_set_value=_cloak_set,
                    clamp_min=_acc_clamp_min,
                    clamp_max=_acc_clamp_max,
                )

            # ------------------------------------------------------------------
            # Phase 1: Decrement cooldowns (C1 then C2; floor at 0)
            # Primary weapon cooldowns tracked in effective_primaries; emit cooldown_end
            # on the tick a cooldown crosses >0 → 0 (no emission for weapons that start at 0).
            # ------------------------------------------------------------------
            for _cs in (c1, c2):
                for _pw in _cs.effective_primaries:
                    _prior = _pw.cooldown_remaining_ms
                    _pw.cooldown_remaining_ms = max(0, _prior - tick_ms)
                    if _prior > 0 and _pw.cooldown_remaining_ms == 0:
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.cooldown_end,
                                actor=_cs.name,
                                target=None,
                                data={"system": _pw.name, "side": _cs.slot},
                            )
                        )
                # T6: secondary weapon cooldowns (mirror of primary path above)
                for _sw in _cs.effective_secondaries:
                    _sw_prior = _sw.cooldown_remaining_ms
                    _sw.cooldown_remaining_ms = max(0, _sw_prior - tick_ms)
                    if _sw_prior > 0 and _sw.cooldown_remaining_ms == 0:
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.cooldown_end,
                                actor=_cs.name,
                                target=None,
                                data={"system": _sw.name, "side": _cs.slot},
                            )
                        )
                # T7: turret cooldowns (non-plasma only — plasma-collectors not in effective_turrets)
                # Primary cooldown STILL decrements under manual_turret_mode=True (§6.3 note)
                for _tw in _cs.effective_turrets:
                    _tw_prior = _tw.cooldown_remaining_ms
                    _tw.cooldown_remaining_ms = max(0, _tw_prior - tick_ms)
                    if _tw_prior > 0 and _tw.cooldown_remaining_ms == 0:
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.cooldown_end,
                                actor=_cs.name,
                                target=None,
                                data={"system": _tw.name, "side": _cs.slot},
                            )
                        )
                # weapon_cooldowns is now empty (turrets moved to effective_turrets in T7).
                # Kept as a field on _CombatantState for forward-compat; no-op loop below.
                for _w in _cs.weapon_cooldowns:
                    _cs.weapon_cooldowns[_w] = max(0, _cs.weapon_cooldowns[_w] - tick_ms)
                for _m in _cs.module_cooldowns:
                    _cs.module_cooldowns[_m] = max(0, _cs.module_cooldowns[_m] - tick_ms)
                # T8: tick down cloak/booster effect and cooldown timers (§7.2 / §7.3)
                _tick_module_effects(_cs, tick, events, tick_ms)
                # T9: tick down EmergencySystem invuln window (§7.7)
                # Colocated with cooldown decrements per TASK_0009 §3 (phase 1 choice).
                if _cs.es_runtime is not None and _cs.es_runtime.invuln_remaining_ms > 0:
                    _cs.es_runtime.invuln_remaining_ms = max(0, _cs.es_runtime.invuln_remaining_ms - tick_ms)

            # ------------------------------------------------------------------
            # Phase 2: Apply regen pulses (C1 then C2; shield + repair bot parallel)
            # ------------------------------------------------------------------
            _tick_shield_regen(c1, tick, events)
            _tick_repair_bot_regen(c1, tick, events)
            _tick_shield_regen(c2, tick, events)
            _tick_repair_bot_regen(c2, tick, events)

            # ------------------------------------------------------------------
            # Phase 3: Evaluate weapon firings — primary weapons (T5)
            # Hits are RECORDED here, not applied. Fire/apply separation is what
            # makes mutual-fire-on-the-lethal-tick correct (Appendix B).
            # RNG draw order: C1 primaries (insertion order), then C2 primaries.
            # ------------------------------------------------------------------
            # pending: (attacker_state, target_state, weapon_runtime) — hits only
            _pending: list[tuple[_CombatantState, _CombatantState, _PrimaryWeaponRuntime]] = []

            for _attacker, _target in ((c1, c2), (c2, c1)):
                # T7: Primary suppression under manual_turret_mode=True (§6.3).
                # Fire-eval is skipped; cooldown STILL decrements (Phase 1 above).
                if _attacker.manual_turret_mode:
                    pass  # suppressed — skip inner loop below
                else:
                    for _pw in _attacker.effective_primaries:
                        # Gate 1: cooldown ready (§6.1 / D3)
                        if _pw.cooldown_remaining_ms > 0:
                            continue
                        # Gate 2: in range — binary gate (§2 / §6.1); weapon stays ready while out of range
                        if current_distance > _pw.range_m:
                            continue
                        # Accuracy — T4 passthrough; clamp already applied by compute_pilot_accuracy
                        _acc = weapon_accuracy(_attacker.pilot_primary_acc, _pw.weapon_stats_ref)
                        # RNG draw — canonical order documented on _PrimaryWeaponRuntime
                        _hit = _rng.random() < _acc
                        # weapon_fire event — §12 primary payload shape (Q9 lock)
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "primary",
                                    "subtype": "primary",
                                    "weapon": _pw.name,
                                    "hit": _hit,
                                    "accuracy": _acc,
                                    "side": _attacker.slot,  # CI-20: attacker slot
                                },
                            )
                        )
                        # Queue hit for phase 4 — misses emit weapon_fire(hit=false) only (Q10 lock)
                        if _hit:
                            _pending.append((_attacker, _target, _pw))
                        # Reset cooldown — happens on both hit AND miss (§6.1 D4)
                        _pw.cooldown_remaining_ms = _pw.effective_loading_speed_ms

            # ------------------------------------------------------------------
            # Phase 3 (T6): Evaluate secondary weapon firings.
            # RNG draw order: C1 secondaries (insertion order) then C2 secondaries.
            # Hits/pending entries are recorded here; damage applied at Phase 4.
            # Shock-blast fires here (weapon_fire emitted); distance reset at Phase 6.
            # ------------------------------------------------------------------
            # Secondary pending queue entries:
            #   ("primary_hit", attacker, target, sw, raw_damage_float)   — rocket/missile/EMP
            #   ("cluster", attacker, target, sw, hit_mask: list[bool])   — cluster
            #   ("nuke", attacker, sw, opponent_raw, self_raw)             — nuke (attacker == firer)
            #   ("shock_blast", attacker, sw, prev_distance)              — no damage, for Phase 6
            _sec_pending: list[tuple] = []

            for _attacker, _target in ((c1, c2), (c2, c1)):
                for _sw in _attacker.effective_secondaries:
                    # CI-16 ammo gate (FIRST — before cooldown/range): skip if depleted
                    if _sw.remaining_ammo is not None and _sw.remaining_ammo <= 0:
                        continue
                    # D1.1: cooldown gate
                    if _sw.cooldown_remaining_ms > 0:
                        continue
                    # D1.2: range gate — shock-blast has range_m=0 → always in range
                    _sw_range = _sw.range_m
                    if _sw_range > 0 and current_distance > _sw_range:
                        continue
                    # D1.3: subtype dispatch
                    _sub = _sw.subtype

                    if _sub == "rocket":
                        # D2: linear accuracy curve
                        _acc_r = _rocket_accuracy(current_distance, _sw.range_m, min_dist)
                        _hit_r = _rng.random() < _acc_r
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "rocket",
                                    "weapon": _sw.name,
                                    "hit": _hit_r,
                                    "accuracy": _acc_r,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        if _hit_r:
                            _sec_pending.append(("primary_hit", _attacker, _target, _sw, float(_sw.damage_per_shot)))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    elif _sub == "missile":
                        # D3: scanner-tier branch
                        _tracking = _attacker.scanner_tier.missile_tracking_active
                        if _tracking:
                            _acc_m = weapon_accuracy(_attacker.pilot_primary_acc, _sw.weapon_stats_ref)
                            _branch = "tier_bc"
                        else:
                            _acc_m = _rocket_accuracy(current_distance, _sw.range_m, min_dist)
                            _branch = "tier_a"
                        _hit_m = _rng.random() < _acc_m
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "missile",
                                    "weapon": _sw.name,
                                    "hit": _hit_m,
                                    "accuracy": _acc_m,
                                    "branch": _branch,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        if _hit_m:
                            _sec_pending.append(("primary_hit", _attacker, _target, _sw, float(_sw.damage_per_shot)))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    elif _sub == "cluster-missile":
                        # D4: accuracy snapshot captured ONCE at fire time; all N sub-munitions roll
                        _tracking_c = _attacker.scanner_tier.missile_tracking_active
                        if _tracking_c:
                            _acc_c = weapon_accuracy(_attacker.pilot_primary_acc, _sw.weapon_stats_ref)
                            _cbranch = "tier_bc"
                        else:
                            _acc_c = _rocket_accuracy(current_distance, _sw.range_m, min_dist)
                            _cbranch = "tier_a"
                        _n = _sw.burst_count if _sw.burst_count > 0 else 1
                        _hits_mask: list[bool] = [_rng.random() < _acc_c for _ in range(_n)]
                        _k = sum(_hits_mask)
                        # ONE condensed weapon_fire event per cluster fire (§6.2 D4)
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "cluster-missile",
                                    "weapon": _sw.name,
                                    "fired": _n,
                                    "hits": _k,
                                    "damage_per_hit": _sw.damage_per_shot,
                                    "total_damage": _k * _sw.damage_per_shot,
                                    "branch": _cbranch,
                                    "accuracy": _acc_c,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        _sec_pending.append(("cluster", _attacker, _target, _sw, _hits_mask))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    elif _sub == "nuke":
                        # D5: no accuracy roll; epicenter sampled via injected RNG
                        _epicenter = _rng.uniform(min_dist, float(GameConstants.STARTING_DISTANCE_M))
                        _d_firer = _epicenter  # firer at position 0
                        _d_opp = abs(_epicenter - current_distance)
                        _eff_mag = _sw.magnitude_m * GameConstants.NUKE_MAGNITUDE_SCALE
                        _opp_raw = _nuke_dmg(_d_opp, _sw.damage_per_shot, _eff_mag)
                        _self_raw = (
                            _nuke_dmg(_d_firer, _sw.damage_per_shot, _eff_mag) * GameConstants.NUKE_FRIENDLY_FACTOR
                        )
                        _opp_dmg_int = round(_opp_raw)
                        _self_dmg_int = round(_self_raw)
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "nuke",
                                    "weapon": _sw.name,
                                    "epicenter": _epicenter,
                                    "d_firer": _d_firer,
                                    "d_opponent": _d_opp,
                                    "opponent_damage": _opp_dmg_int,
                                    "self_damage": _self_dmg_int,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        # Queue: opponent damage, then self-damage (phase 4 canonical order)
                        _sec_pending.append(("nuke_opponent", _attacker, _target, _sw, _opp_raw))
                        _sec_pending.append(("nuke_self", _attacker, _sw, _self_raw))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    elif _sub == "shock-blast":
                        # D6: 100% guaranteed distance reset — no RNG draw, no damage
                        _prev_dist = current_distance
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "shock-blast",
                                    "weapon": _sw.name,
                                    "hit": True,
                                    "accuracy": 1.0,
                                    "damage": 0,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        # Queue for Phase 6 distance reset (Appendix B step 6)
                        _sec_pending.append(("shock_blast", _attacker, _sw, _prev_dist))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    elif _sub == "ionizing-missile":
                        # D1.3: fire-but-noop; fires, rolls accuracy, applies 0 HP delta
                        _tracking_ion = _attacker.scanner_tier.missile_tracking_active
                        if _tracking_ion:
                            _acc_ion = weapon_accuracy(_attacker.pilot_primary_acc, _sw.weapon_stats_ref)
                            _branch_ion = "tier_bc"
                        else:
                            _acc_ion = _rocket_accuracy(current_distance, _sw.range_m, min_dist)
                            _branch_ion = "tier_a"
                        _hit_ion = _rng.random() < _acc_ion
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.weapon_fire,
                                actor=_attacker.name,
                                target=_target.name,
                                data={
                                    "slot": "secondary",
                                    "subtype": "ionizing-missile",
                                    "weapon": _sw.name,
                                    "hit": _hit_ion,
                                    "accuracy": _acc_ion,
                                    "branch": _branch_ion,
                                    "side": _attacker.slot,  # CI-20
                                },
                            )
                        )
                        if _hit_ion:
                            # fire-but-noop: routes through helper with raw_damage=0
                            _sec_pending.append(("primary_hit", _attacker, _target, _sw, 0.0))
                        _sw.cooldown_remaining_ms = _sw.loading_speed_ms

                    # else: deferred subtypes (emp-bomb, mine, sentry-gun) — noop; cooldown continues

                    # CI-16: single post-dispatch ammo decrement (covers all 7 fire branches).
                    # Gated on cooldown_remaining_ms > 0 — every firing branch sets it to loading_speed_ms
                    # (always > 0 because raw_speed >= tick_ms); the deferred else noop does NOT set it,
                    # so cooldown_remaining_ms remains 0 and this block is naturally excluded.
                    if _sw.remaining_ammo is not None and _sw.cooldown_remaining_ms > 0:
                        _sw.remaining_ammo -= 1
                        if _sw.remaining_ammo == 0:
                            events.append(
                                CombatEvent(
                                    tick=tick,
                                    type=CombatEventType.secondary_depleted,
                                    actor=_attacker.name,
                                    target=None,
                                    data={"weapon": _sw.name, "subtype": _sw.subtype, "side": _attacker.slot},
                                )
                            )

            # ------------------------------------------------------------------
            # Phase 3 (T7): Evaluate turret weapon firings.
            # Auto-turrets always fire (alongside primaries, regardless of manual_turret_mode).
            # Manual-turrets fire only when manual_turret_mode=True; inert otherwise.
            # RNG draw order: C1 auto-turrets, C2 auto-turrets, C1 manual-turrets, C2 manual-turrets.
            # One auto-turret accuracy per combatant per tick (§6.3 correctness statement).
            # Plasma-collectors are NOT in effective_turrets — already filtered at init.
            # ------------------------------------------------------------------
            # Turret pending: (attacker_state, target_state, turret_runtime, damage_per_shot) — hits only
            _turret_pending: list[tuple[_CombatantState, _CombatantState, _TurretWeaponRuntime, int]] = []

            # Pre-bake auto-turret accuracy once per combatant per tick (§6.3 / Appendix A)
            # ONE value per combatant per tick — correctness statement (§6.3), not just perf.
            _c1_auto_acc = max(_acc_clamp_min, min(_acc_clamp_max, c1.pilot_turret_acc * _auto_turret_multiplier))
            _c2_auto_acc = max(_acc_clamp_min, min(_acc_clamp_max, c2.pilot_turret_acc * _auto_turret_multiplier))

            # Auto-turrets: C1 then C2 (§ determinism)
            for _attacker, _target, _auto_acc in ((c1, c2, _c1_auto_acc), (c2, c1, _c2_auto_acc)):
                for _tw in _attacker.effective_turrets:
                    if not _tw.automatic:
                        continue  # manual-turret — handled in next loop
                    # Gate 1: cooldown ready
                    if _tw.cooldown_remaining_ms > 0:
                        continue
                    # Gate 2: range gate (§6.1)
                    if current_distance > _tw.range_m:
                        continue
                    # Accuracy: pre-baked auto_turret_acc (ONE value per combatant per tick)
                    _tw_acc = _auto_acc
                    _tw_hit = _rng.random() < _tw_acc
                    events.append(
                        CombatEvent(
                            tick=tick,
                            type=CombatEventType.weapon_fire,
                            actor=_attacker.name,
                            target=_target.name,
                            data={
                                "slot": "turret",
                                "subtype": "auto",
                                "weapon": _tw.name,
                                "hit": _tw_hit,
                                "accuracy": _tw_acc,
                                "side": _attacker.slot,  # CI-20
                            },
                        )
                    )
                    if _tw_hit:
                        _turret_pending.append((_attacker, _target, _tw, _tw.damage_per_shot))
                    # Cooldown resets on fire — hit OR miss (§6.3 mirror of §6.1 D4)
                    _tw.cooldown_remaining_ms = _tw.loading_speed_ms

            # Manual-turrets: C1 then C2 — only when manual_turret_mode=True (§6.3)
            for _attacker, _target in ((c1, c2), (c2, c1)):
                if not _attacker.manual_turret_mode:
                    continue  # primary-mode: manual turrets inert
                for _tw in _attacker.effective_turrets:
                    if _tw.automatic:
                        continue  # auto-turret — already handled above
                    # Gate 1: cooldown ready
                    if _tw.cooldown_remaining_ms > 0:
                        continue
                    # Gate 2: range gate (§6.1 — manual turret treated as primary)
                    if current_distance > _tw.range_m:
                        continue
                    # Accuracy: pilot_primary_acc (full §5 with thruster; NOT 0.85 multiplied) (§6.3)
                    _mt_acc = weapon_accuracy(_attacker.pilot_primary_acc, _tw.weapon_stats_ref)
                    _mt_hit = _rng.random() < _mt_acc
                    events.append(
                        CombatEvent(
                            tick=tick,
                            type=CombatEventType.weapon_fire,
                            actor=_attacker.name,
                            target=_target.name,
                            data={
                                "slot": "turret",
                                "subtype": "manual",
                                "weapon": _tw.name,
                                "hit": _mt_hit,
                                "accuracy": _mt_acc,
                                "side": _attacker.slot,  # CI-20
                            },
                        )
                    )
                    if _mt_hit:
                        _turret_pending.append((_attacker, _target, _tw, _tw.damage_per_shot))
                    # Cooldown resets on fire — hit OR miss
                    _tw.cooldown_remaining_ms = _tw.loading_speed_ms

            # ------------------------------------------------------------------
            # Phase 4: Apply damage — drain pending queue (C1 hits first, then C2)
            # The pending list preserves (c1,c2) then (c2,c1) ordering from Phase 3.
            # Pure-EMP primaries land with raw_damage=0; helper records the 0-delta event (§4/D5).
            # ------------------------------------------------------------------
            for _attacker, _target, _pw in _pending:
                _apply_damage(
                    _target,
                    raw_damage=float(_pw.effective_damage_per_shot),
                    tick=tick,
                    events=events,
                    source={"subtype": "primary", "weapon": _pw.name, "attacker": _attacker.name},
                    pvc_damage_reduction=pvc_damage_reduction,
                )

            # ------------------------------------------------------------------
            # Phase 4 (T6): Apply secondary pending entries.
            # Entries are in phase-3 recording order (C1 then C2).
            # Shock-blast entries skipped here — handled in Phase 6.
            # ------------------------------------------------------------------
            _shock_blast_entries: list[tuple] = []
            for _entry in _sec_pending:
                _kind = _entry[0]
                if _kind == "primary_hit":
                    _, _att, _tgt, _sw_e, _raw = _entry
                    _apply_damage(
                        _tgt,
                        raw_damage=_raw,
                        tick=tick,
                        events=events,
                        source={"subtype": _sw_e.subtype, "weapon": _sw_e.name, "attacker": _att.name},
                        pvc_damage_reduction=pvc_damage_reduction,
                    )
                elif _kind == "cluster":
                    _, _att, _tgt, _sw_e, _mask = _entry
                    for _hit_sub in _mask:
                        if _hit_sub:
                            _apply_damage(
                                _tgt,
                                raw_damage=float(_sw_e.damage_per_shot),
                                tick=tick,
                                events=events,
                                source={"subtype": "cluster-missile", "weapon": _sw_e.name, "attacker": _att.name},
                                pvc_damage_reduction=pvc_damage_reduction,
                            )
                elif _kind == "nuke_opponent":
                    _, _att, _tgt, _sw_e, _raw = _entry
                    _apply_damage(
                        _tgt,
                        raw_damage=_raw,
                        tick=tick,
                        events=events,
                        source={"subtype": "nuke", "weapon": _sw_e.name, "attacker": _att.name},
                        pvc_damage_reduction=pvc_damage_reduction,
                    )
                elif _kind == "nuke_self":
                    _, _firer, _sw_e, _raw = _entry
                    # Self-damage: firer IS the target; PvC DR applies via T3's helper (§3/D5)
                    _apply_damage(
                        _firer,
                        raw_damage=_raw,
                        tick=tick,
                        events=events,
                        source={"subtype": "nuke", "weapon": _sw_e.name, "attacker": _firer.name, "is_self": True},
                        pvc_damage_reduction=pvc_damage_reduction,
                    )
                elif _kind == "shock_blast":
                    _shock_blast_entries.append(_entry)
                # else: unrecognised — defensive skip

            # ------------------------------------------------------------------
            # Phase 4 (T7): Apply turret pending entries.
            # Entries are in phase-3 recording order (C1 auto then C2 auto then C1 manual then C2 manual).
            # PrimaryWeaponMod does NOT apply to turret damage (§7.8).
            # ------------------------------------------------------------------
            for _t_att, _t_tgt, _t_tw, _t_dmg in _turret_pending:
                _apply_damage(
                    _t_tgt,
                    raw_damage=float(_t_dmg),
                    tick=tick,
                    events=events,
                    source={
                        "subtype": "auto" if _t_tw.automatic else "manual",
                        "weapon": _t_tw.name,
                        "attacker": _t_att.name,
                    },
                    pvc_damage_reduction=pvc_damage_reduction,
                )

            # ------------------------------------------------------------------
            # Phase 4a: EmergencySystem evaluation (§7.7 / T9)
            # AFTER all damage events for this tick have been applied (hull may be transiently negative).
            # BEFORE phase 4b display clamp. C1 evaluated first, then C2 (Appendix B ordering).
            # ES is NOT an HP-threshold device (§8) — it lives here, not in Phase 5.
            # ------------------------------------------------------------------
            _eval_emergency_system(c1, tick, events, GameConstants.EMERGENCY_SYSTEM_INVULN_S * 1000)
            _eval_emergency_system(c2, tick, events, GameConstants.EMERGENCY_SYSTEM_INVULN_S * 1000)

            # ------------------------------------------------------------------
            # Phase 4b: HP clamp (C1 then C2; any layer below 0 → 0)
            # ------------------------------------------------------------------
            c1.current_shield = max(0, c1.current_shield)
            c1.current_armour = max(0, c1.current_armour)
            c1.current_hull = max(0, c1.current_hull)
            c2.current_shield = max(0, c2.current_shield)
            c2.current_armour = max(0, c2.current_armour)
            c2.current_hull = max(0, c2.current_hull)

            # ------------------------------------------------------------------
            # Phase 5: HP-threshold checks — T8: HP-threshold module activations (cloak/booster)
            # Evaluated per combatant using post-damage HP (Appendix B step 5).
            # Thruster is passive — no threshold check needed.
            # ------------------------------------------------------------------
            _eval_hp_threshold_modules(c1, tick, events, _cloak_thresholds, _booster_thresholds)
            _eval_hp_threshold_modules(c2, tick, events, _cloak_thresholds, _booster_thresholds)

            # ------------------------------------------------------------------
            # Phase 6: Update distance — passive closure (§2); shock-blast reset (T6); Appendix B
            # Booster push REPLACES passive closure during the boost window (§2 / §7.3).
            # Shock-blast entries from Phase 3 are applied here (Appendix B step 6).
            # Priority: shock-blast > booster push > passive closure.
            # ------------------------------------------------------------------
            _shock_blast_fired = bool(_shock_blast_entries)
            if _shock_blast_fired:
                # Apply shock-blasts in phase-3 recording order.
                # Multiple shock-blasts same tick: each one resets to STARTING_DISTANCE_M.
                # The `from` field in each distance event reflects the actual pre-reset distance
                # AT PHASE 6 APPLICATION TIME (not the phase-3 capture), so the second shock-blast
                # correctly reports `from: STARTING_DISTANCE_M` (already reset by the first).
                for _sb_entry in _shock_blast_entries:
                    _, _sb_att, _sb_sw, _sb_prev = _sb_entry  # _sb_prev unused — use live distance
                    _sb_from = current_distance  # actual pre-reset distance at phase 6 apply time
                    _sb_new_dist = _shock_blast_apply(_sb_att, current_distance)
                    current_distance = _sb_new_dist
                    events.append(
                        CombatEvent(
                            tick=tick,
                            type=CombatEventType.distance,
                            actor=_sb_att.name,
                            target=None,
                            data={"from": _sb_from, "to": _sb_new_dist, "cause": "shock_blast", "side": _sb_att.slot},
                        )
                    )
            else:
                # T8: Check both combatants for active booster push (§2 / §7.3).
                # Booster REPLACES passive closure for the booster's owner;
                # if both fire boosters simultaneously, both pushes apply.
                # A push is: current_distance INCREASES (away from opponent).
                _booster_active_any = False
                for _bcs in (c1, c2):
                    if _bcs.booster_runtime is not None and _bcs.booster_runtime.effect_remaining_ms > 0:
                        _bcs_push = _base_speed_mps * (_bcs.booster_runtime.stats.effect_pct / 100.0) * (tick_ms / 1000)
                        _old_d_b = current_distance
                        current_distance += _bcs_push
                        _booster_active_any = True
                        if current_distance != _old_d_b:
                            events.append(
                                CombatEvent(
                                    tick=tick,
                                    type=CombatEventType.distance,
                                    actor=_bcs.name,
                                    target=None,
                                    data={
                                        "from": _old_d_b,
                                        "to": current_distance,
                                        "cause": "booster_push",
                                        "side": _bcs.slot,
                                    },
                                )
                            )
                if not _booster_active_any:
                    old_dist = current_distance
                    current_distance = max(min_dist, current_distance - distance_delta)
                    if current_distance != old_dist:
                        events.append(
                            CombatEvent(
                                tick=tick,
                                type=CombatEventType.distance,
                                actor=None,
                                target=None,
                                data={"from": old_dist, "to": current_distance, "cause": "closure"},
                            )
                        )

            # ------------------------------------------------------------------
            # Phase 7: Events emitted inline above (already in processing order)
            # ------------------------------------------------------------------

            # ------------------------------------------------------------------
            # Phase 8: Termination check (§9)
            # ------------------------------------------------------------------
            c1_dead = c1.current_hull <= 0
            c2_dead = c2.current_hull <= 0
            is_last_tick = tick == max_ticks - 1

            if c1_dead and c2_dead:
                outcome, reason = "stalemate", "mutual"
                winner_name, loser_name = None, None
                ticks_elapsed = tick + 1
            elif c2_dead:
                outcome, reason = "win", "hp_depleted"
                winner_name, loser_name = c1.name, c2.name
                ticks_elapsed = tick + 1
            elif c1_dead:
                outcome, reason = "win", "hp_depleted"
                winner_name, loser_name = c2.name, c1.name
                ticks_elapsed = tick + 1
            elif is_last_tick:
                outcome, reason = "stalemate", "time_cap"
                winner_name, loser_name = None, None
                ticks_elapsed = max_ticks
            else:
                continue  # fight continues — skip fight_end emission

            # CI-27: hull layer_depleted — emitted HERE (post-ES, post-clamp) so that
            # ES-saved ships (hull clamped to 1, not dead) never receive a false "dead" event.
            # Only emitted for combatants whose hull is truly ≤ 0 at termination.
            # time_cap stalemate (neither dead) → no hull-death events.
            if c2_dead:
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.layer_depleted,
                        actor=c2.name,
                        target=None,
                        data={"layer": "hull", "side": c2.slot},
                    )
                )
            if c1_dead:
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.layer_depleted,
                        actor=c1.name,
                        target=None,
                        data={"layer": "hull", "side": c1.slot},
                    )
                )

            events.append(
                CombatEvent(
                    tick=tick,
                    type=CombatEventType.fight_end,
                    actor=None,
                    target=None,
                    data={
                        "winner": winner_name,
                        "reason": reason,
                        "duration_ticks": ticks_elapsed,
                        "final_hp": {
                            "c1": {"shield": c1.current_shield, "armour": c1.current_armour, "hull": c1.current_hull},
                            "c2": {"shield": c2.current_shield, "armour": c2.current_armour, "hull": c2.current_hull},
                        },
                    },
                )
            )
            break

        is_stalemate = outcome == "stalemate"

        # T9: Build Tier-0 summary by scanning the in-memory event list (§12 / §13).
        # Events are CombatEvent dataclass objects (not dicts); summary scans read attributes.
        # Serialization to dicts happens at persist time (T10).
        summary = _build_fight_summary(
            events=events,
            c1=c1,
            c2=c2,
            outcome=outcome,
            reason=reason,
            duration_ticks=ticks_elapsed,
            winner_name=winner_name,
        )

        # FightStats wire-compat (§12 "Legacy FightStats wire-compat") — derived from summary.
        # raw_hp: sum of effective start HP across all layers.
        # varied_hp = raw_hp (no variance in tick resolver).
        # raw_dps = damage_dealt / duration_s; varied_dps = raw_dps.
        # ttk = duration_s for the LOSER, None for the winner.
        duration_s = (ticks_elapsed * tick_ms) / 1000.0
        total_hp1 = c1.max_shield + c1.max_armour + c1.max_hull
        total_hp2 = c2.max_shield + c2.max_armour + c2.max_hull

        # Extract damage_dealt from summary combatant blocks
        cb_summary = summary.get("combatants", {})
        c1_dealt = cb_summary.get("1", {}).get("damage_dealt", 0)
        c2_dealt = cb_summary.get("2", {}).get("damage_dealt", 0)

        c1_raw_dps = c1_dealt / duration_s if duration_s > 0 else 0.0
        c2_raw_dps = c2_dealt / duration_s if duration_s > 0 else 0.0

        # ttk: duration_s for the combatant that LOST; None for the winner (they survived).
        # On stalemate: both survived (or mutual kill) → both get None.
        c1_ttk: float | None = None
        c2_ttk: float | None = None
        if not is_stalemate:
            if winner_name == c1.name:
                c2_ttk = duration_s  # c2 died
            elif winner_name == c2.name:
                c1_ttk = duration_s  # c1 died

        ship1_stats = FightStats(
            ship_name=c1.name,
            raw_hp=total_hp1,
            raw_dps=c1_raw_dps,
            varied_hp=total_hp1,
            varied_dps=c1_raw_dps,
            ttk=c1_ttk,
        )
        ship2_stats = FightStats(
            ship_name=c2.name,
            raw_hp=total_hp2,
            raw_dps=c2_raw_dps,
            varied_hp=total_hp2,
            varied_dps=c2_raw_dps,
            ttk=c2_ttk,
        )

        return FightResults(
            winner_name=winner_name,
            loser_name=loser_name,
            is_stalemate=is_stalemate,
            ship1_stats=ship1_stats,
            ship2_stats=ship2_stats,
            combat_log=events,  # type: ignore[arg-type]  — stores CombatEvent, annotation is list[dict]
            metadata={
                "schema_version": 1,
                "summary": summary,
                "metadata": {
                    "tick_ms": tick_ms,
                    "total_ticks": ticks_elapsed,
                    "resolver": "tick_v1",
                    "pvc_damage_reduction": pvc_damage_reduction,
                },
            },
        )


# ---------------------------------------------------------------------------
# CombatService — stat collection + fight orchestration (T10: async, TickResolver)
# ---------------------------------------------------------------------------


class CombatService:
    """Service for ship combat stat computation and fight resolution.

    T10: fight_ships now routes exclusively through TickResolver (async).
    SimpleTTKResolver is retired. Per-fight DB persistence and Player stat
    increments happen inside fight_ships when log_result=True.

    Usage:
        service = CombatService()
        result = await service.fight_ships(
            loadout1, loadout2,
            context="duel", log_result=True, pvc_damage_reduction=0.0,
            session=db,
            combatant1_user_id=player1.user_id,
            combatant2_user_id=None,
            guild_id=guild_id,
        )
    """

    def __init__(self) -> None:
        """Initialize CombatService with the tick-based resolver."""
        self._tick_resolver = TickResolver()
        flogger.debug("CombatService initialized with TickResolver")

    # ------------------------------------------------------------------
    # Stat Collection — Legacy-compatible formulas
    # ------------------------------------------------------------------

    @staticmethod
    def get_dps(loadout: ShipLoadout) -> float:
        """Calculate total effective DPS for a ship loadout.

        Formula (from legacy shipBase.py:456-467):
            totalDPS = (sum(weapon.dps) + sum(turret.dps) + sum(module.dps))
                       * product(module.dps_multiplier)

        Module DPS multipliers stack multiplicatively (e.g. two x1.2
        modules = x1.44 total).

        Args:
            loadout: Ship loadout with weapons, turrets, and modules.

        Returns:
            Total effective DPS as float.
        """
        flogger.debug(f"Calculating DPS for {loadout.ship_name}")
        total = 0.0
        multiplier = 1.0

        for weapon in loadout.weapons:
            total += weapon.dps
            flogger.trace(f"DPS calc: added weapon {weapon.name} dps={weapon.dps}, cumulative_total={total}")

        for turret in loadout.turrets:
            total += turret.dps
            flogger.trace(f"DPS calc: added turret {turret.name} dps={turret.dps}, cumulative_total={total}")

        for module in loadout.modules:
            total += module.dps
            multiplier *= module.dps_multiplier
            flogger.trace(
                f"DPS calc: added module {module.name} dps={module.dps}, dps_mult={module.dps_multiplier}, "
                f"cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_dps = total * multiplier
        flogger.debug(
            f"DPS calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_dps={final_dps:.1f}"
        )
        return final_dps

    @staticmethod
    def get_armour(loadout: ShipLoadout) -> int:
        """Calculate total effective armour for a ship loadout.

        Formula (from legacy shipBase.py:491-512):
            totalArmour = int(
                (baseArmour + sum(module.armour) + sum(upgrade.armour))
                * product(module.armour_multiplier)
                * product(upgrade.armour_multiplier)
            )

        Args:
            loadout: Ship loadout with base armour, modules, and upgrades.

        Returns:
            Total effective armour as int (truncated).
        """
        flogger.debug(f"Calculating armour for {loadout.ship_name}")
        total = loadout.base_armour
        multiplier = 1.0
        flogger.trace(f"Armour calc: base_armour={total}")

        for module in loadout.modules:
            total += module.armour
            multiplier *= module.armour_multiplier
            flogger.trace(
                f"Armour calc: added module {module.name} armour={module.armour}, "
                f"armour_mult={module.armour_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        for upgrade in loadout.upgrades:
            total += upgrade.armour
            multiplier *= upgrade.armour_multiplier
            flogger.trace(
                f"Armour calc: added upgrade {upgrade.name} armour={upgrade.armour}, "
                f"armour_mult={upgrade.armour_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_armour = int(total * multiplier)
        flogger.debug(
            f"Armour calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_armour={final_armour}"
        )
        return final_armour

    @staticmethod
    def get_shield(loadout: ShipLoadout) -> int:
        """Calculate total effective shield for a ship loadout.

        Formula (from legacy shipBase.py:470-488):
            totalShield = int(sum(module.shield) * product(module.shield_multiplier))

        Ships have no intrinsic shield. All shield HP comes from modules.

        Args:
            loadout: Ship loadout with modules.

        Returns:
            Total effective shield as int (truncated).
        """
        flogger.debug(f"Calculating shield for {loadout.ship_name}")
        total = 0
        multiplier = 1.0
        flogger.trace("Shield calc: starting with total=0 (no base shield)")

        for module in loadout.modules:
            total += module.shield
            multiplier *= module.shield_multiplier
            flogger.trace(
                f"Shield calc: added module {module.name} shield={module.shield}, "
                f"shield_mult={module.shield_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_shield = int(total * multiplier)
        flogger.debug(
            f"Shield calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_shield={final_shield}"
        )
        return final_shield

    def collect_stats(self, loadout: ShipLoadout) -> CombatStats:
        """Compute all combat statistics for a ship loadout.

        Combines get_dps, get_armour, and get_shield into a single
        CombatStats object. total_hp = armour + shield.

        Args:
            loadout: Complete ship loadout.

        Returns:
            CombatStats with all computed values.
        """
        flogger.debug(f"Stat collection started for {loadout.ship_name}")
        dps = self.get_dps(loadout)
        armour = self.get_armour(loadout)
        shield = self.get_shield(loadout)
        total_hp = armour + shield

        flogger.debug(
            f"Ship stats: {loadout.ship_name} dps={dps:.1f} armour={armour} shield={shield} total_hp={total_hp}"
        )
        flogger.trace(f"Accuracy: {loadout.base_accuracy}, Evasion: {loadout.base_evasion}")

        return CombatStats(
            ship_name=loadout.ship_name,
            dps=dps,
            armour=armour,
            shield=shield,
            total_hp=total_hp,
            accuracy=loadout.base_accuracy,
            evasion=loadout.base_evasion,
        )

    # ------------------------------------------------------------------
    # Fight Resolution (T10: async, routes through TickResolver)
    # ------------------------------------------------------------------

    async def fight_ships(
        self,
        loadout1: ShipLoadout,
        loadout2: ShipLoadout,
        *,
        context: str | None = None,
        log_result: bool = True,
        pvc_damage_reduction: float = 0.0,
        guild_config=None,
        # DB context required when log_result=True
        session: "AsyncSession | None" = None,
        guild_id: int | None = None,
        combatant1_user_id: int | None = None,
        combatant2_user_id: int | None = None,
        # CI-20: display labels for thread naming / dropdown
        combatant1_label: str = "",
        combatant2_label: str = "",
    ) -> FightResults:
        """Simulate a fight between two ship loadouts via TickResolver.

        Args:
            loadout1: C1 — challenger (player in PvC when pvc_damage_reduction > 0).
            loadout2: C2 — opponent (NPC in PvC; player2 in PvP).
            context: Fight context string ("duel"|"bounty_pvc"|"bounty_bonus").
                     Required when log_result=True.
            log_result: When True, persist the combat_log row and update Player stats.
                        When False (preflight Monte-Carlo), no DB writes are made.
            pvc_damage_reduction: Keith T. Maxwell DR (§3). 0.33 for PvC, 0.0 for PvP.
            guild_config: Per-guild config for constant overrides (reserved).
            session: Async SQLAlchemy session — required when log_result=True.
            guild_id: Guild ID for combat_log.guild_id — required when log_result=True.
            combatant1_user_id: Discord user_id for C1 (None = NPC).
            combatant2_user_id: Discord user_id for C2 (None = NPC).

        Returns:
            FightResults with combat_log timeline, metadata, and combat_log_id.

        Raises:
            ValueError: if log_result=True and context is None.
        """
        if log_result and context is None:
            raise ValueError("fight_ships: context is required when log_result=True")

        flogger.debug(
            f"fight_ships: {loadout1.ship_name} vs {loadout2.ship_name} "
            f"context={context!r} log_result={log_result} pvc_dr={pvc_damage_reduction}"
        )

        # Run the tick resolver (pure, synchronous computation — no DB)
        fight_results = self._tick_resolver.resolve(
            loadout1,
            loadout2,
            pvc_damage_reduction=pvc_damage_reduction,
            guild_config=guild_config,
            combatant1_label=combatant1_label,
            combatant2_label=combatant2_label,
        )

        if not log_result:
            # Preflight / simulation path — no DB writes
            flogger.debug(
                f"fight_ships (log_result=False): winner={fight_results.winner_name} "
                f"stalemate={fight_results.is_stalemate}"
            )
            return fight_results

        # ------------------------------------------------------------------ #
        # log_result=True path: persist + Player stat increments             #
        # ------------------------------------------------------------------ #
        if session is None:
            raise ValueError("fight_ships: session is required when log_result=True")
        if guild_id is None:
            raise ValueError("fight_ships: guild_id is required when log_result=True")

        # Annotate metadata with combatant user_ids so CombatLogService can project them
        fight_results.metadata["combatant_user_ids"] = {
            "c1": combatant1_user_id,
            "c2": combatant2_user_id,
        }

        # Persist combat_log row
        from services.combat_log_service import CombatLogService  # deferred to avoid circular import

        combat_log_svc = CombatLogService()
        meta = CombatMeta(guild_id=guild_id)
        combat_log_id = await combat_log_svc.persist(meta, fight_results, context, session)

        # Rebuild FightResults with combat_log_id populated (frozen dataclass — replace)
        fight_results = FightResults(
            winner_name=fight_results.winner_name,
            loser_name=fight_results.loser_name,
            is_stalemate=fight_results.is_stalemate,
            ship1_stats=fight_results.ship1_stats,
            ship2_stats=fight_results.ship2_stats,
            combat_log_id=combat_log_id,
            combat_log=fight_results.combat_log,
            metadata=fight_results.metadata,
        )

        # Player stat increments (§13) — one per HUMAN combatant
        await self._increment_player_stats(
            session=session,
            fight_results=fight_results,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            guild_id=guild_id,
        )

        # CI-16: secondary ammo write-back — must be AFTER _increment_player_stats,
        # inside log_result=True branch (sim guard returns at ~line 2137 before this)
        await self._consume_secondary_ammo(
            session=session,
            fight_results=fight_results,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            guild_id=guild_id,
        )

        flogger.info(
            f"fight_ships: persisted combat_log_id={combat_log_id} "
            f"winner={fight_results.winner_name} stalemate={fight_results.is_stalemate}"
        )
        return fight_results

    async def _increment_player_stats(
        self,
        *,
        session: "AsyncSession",
        fight_results: FightResults,
        combatant1_user_id: int | None,
        combatant2_user_id: int | None,
        guild_id: int,
    ) -> None:
        """Increment Player combat-stat counters for human combatants (§13).

        total_fights += 1 always.
        total_nukes_fired += count of weapon_fire/nuke events for this combatant.
        total_module_activations += count of module_activation events for this combatant.

        NPC side (user_id is None): skip cleanly, no DB call.
        """
        from persist.repositories.player_repository import PlayerRepository

        player_repo = PlayerRepository()

        summary = fight_results.metadata.get("summary", {})
        combatants_summary = summary.get("combatants", {})

        # Map slot key → user_id
        slot_map: list[tuple[str, int | None]] = [
            ("1", combatant1_user_id),
            ("2", combatant2_user_id),
        ]

        for slot_key, user_id in slot_map:
            if user_id is None:
                continue  # NPC side — skip cleanly

            cb_block = combatants_summary.get(slot_key, {})

            # Count nuke fires for this combatant from the event timeline
            combatant_name = cb_block.get("name", "")
            nukes_fired = 0
            module_activations = 0
            for ev in fight_results.combat_log:
                # combat_log holds CombatEvent dataclass objects (not dicts); read attributes
                if hasattr(ev, "type") and hasattr(ev, "actor"):
                    ev_actor = ev.actor
                    ev_type = ev.type
                    ev_data = ev.data if hasattr(ev, "data") else {}
                else:
                    # Fallback if somehow a dict slipped through
                    ev_actor = ev.get("actor") if isinstance(ev, dict) else None
                    ev_type = ev.get("type") if isinstance(ev, dict) else None
                    ev_data = ev.get("data", {}) if isinstance(ev, dict) else {}

                if ev_actor != combatant_name:
                    continue
                if ev_type == CombatEventType.weapon_fire:
                    if ev_data.get("subtype") == "nuke":
                        nukes_fired += 1
                elif ev_type == CombatEventType.module_activation:
                    module_activations += 1

            try:
                player = await player_repo.get_by_user_and_guild(session, user_id, guild_id)
                if player is None:
                    flogger.warning(
                        f"fight_ships stat increment: player not found user_id={user_id} guild_id={guild_id} — skipping"
                    )
                    continue
                player.total_fights += 1
                player.total_nukes_fired += nukes_fired
                player.total_module_activations += module_activations
                await session.flush()
                flogger.debug(
                    f"Player stats incremented: user_id={user_id} guild_id={guild_id} "
                    f"total_fights={player.total_fights} nukes_fired+={nukes_fired} "
                    f"module_activations+={module_activations}"
                )
            except Exception as exc:
                # Non-fatal — stat increment failure should not abort the fight
                flogger.error(f"Player stat increment failed: user_id={user_id} guild_id={guild_id}: {exc}")

    async def _consume_secondary_ammo(
        self,
        *,
        session: "AsyncSession",
        fight_results: FightResults,
        combatant1_user_id: int | None,
        combatant2_user_id: int | None,
        guild_id: int,
    ) -> None:
        """Write back per-secondary ammo consumption for human combatants (CI-16).

        Scans the combat_log timeline for weapon_fire events (slot=secondary) per human
        combatant, counts rounds fired per weapon name, decrements secondary_ammo on the
        player's active ship, and auto-unequips (removes name + ammo key) if rounds reach 0.

        Criminal side (user_id is None): skip — no cross-fight persistence for NPCs (CI-17 deferred).
        Mirrors the non-fatal try/except style of _increment_player_stats.
        """
        from persist.repositories.player_repository import PlayerRepository
        from persist.repositories.player_ship_repository import PlayerShipRepository

        player_repo = PlayerRepository()
        player_ship_repo = PlayerShipRepository()

        summary = fight_results.metadata.get("summary", {})
        combatants_summary = summary.get("combatants", {})

        slot_map: list[tuple[str, int | None]] = [
            ("1", combatant1_user_id),
            ("2", combatant2_user_id),
        ]

        for slot_key, user_id in slot_map:
            if user_id is None:
                continue  # NPC side — no cross-fight ammo persistence

            cb_block = combatants_summary.get(slot_key, {})
            combatant_name = cb_block.get("name", "")

            # Count rounds fired per weapon name from combat_log timeline
            rounds_fired: dict[str, int] = {}
            for ev in fight_results.combat_log:
                # combat_log holds CombatEvent dataclass objects — use attribute access
                if hasattr(ev, "type") and hasattr(ev, "actor"):
                    ev_actor = ev.actor
                    ev_type = ev.type
                    ev_data = ev.data if hasattr(ev, "data") else {}
                else:
                    ev_actor = ev.get("actor") if isinstance(ev, dict) else None
                    ev_type = ev.get("type") if isinstance(ev, dict) else None
                    ev_data = ev.get("data", {}) if isinstance(ev, dict) else {}

                if ev_actor != combatant_name:
                    continue
                if ev_type == CombatEventType.weapon_fire and ev_data.get("slot") == "secondary":
                    w_name = ev_data.get("weapon", "")
                    if w_name:
                        rounds_fired[w_name] = rounds_fired.get(w_name, 0) + 1

            if not rounds_fired:
                continue  # no secondary fires — nothing to write back

            try:
                player = await player_repo.get_by_user_and_guild(session, user_id, guild_id)
                if player is None:
                    flogger.warning(
                        f"_consume_secondary_ammo: player not found user_id={user_id} guild_id={guild_id} — skipping"
                    )
                    continue

                ship = await player_ship_repo.get_active_ship(session, player.id)
                if ship is None:
                    flogger.warning(
                        f"_consume_secondary_ammo: no active ship for player_id={player.id} — skipping"
                    )
                    continue

                # Read current ammo dict; never mutate in-place (JSON SQLAlchemy gotcha — must reassign)
                ammo: dict[str, int] = dict(ship.secondary_ammo or {})
                sw_names: list[str] = list(ship.secondary_weapons or [])

                for w_name, fired in rounds_fired.items():
                    if w_name not in ammo:
                        flogger.debug(
                            f"_consume_secondary_ammo: weapon {w_name!r} not in ammo dict for ship {ship.id} — skip"
                        )
                        continue
                    new_qty = max(0, ammo[w_name] - fired)
                    ammo[w_name] = new_qty
                    if new_qty == 0:
                        # Auto-unequip: remove name from secondary_weapons and del from ammo
                        sw_names = [n for n in sw_names if n != w_name]
                        del ammo[w_name]
                        flogger.info(
                            f"_consume_secondary_ammo: {w_name!r} depleted — auto-unequipped "
                            f"from ship {ship.id} (player_id={player.id})"
                        )

                # Reassign both JSON columns (never in-place mutation — SQLAlchemy JSON gotcha)
                ship.secondary_ammo = ammo
                ship.secondary_weapons = sw_names
                await session.flush()

                flogger.debug(
                    f"_consume_secondary_ammo: ship {ship.id} ammo updated: {ammo!r}, "
                    f"secondary_weapons={sw_names!r}"
                )

            except Exception as exc:
                # Non-fatal — ammo write-back failure should not abort the fight record
                flogger.error(
                    f"_consume_secondary_ammo failed: user_id={user_id} guild_id={guild_id}: {exc}"
                )
