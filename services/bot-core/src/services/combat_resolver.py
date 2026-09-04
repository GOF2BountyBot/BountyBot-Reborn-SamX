"""Combat resolver — DB-free leaf module for tick-based combat simulation.

Contains all combat-math symbols (constants, dataclasses, helper functions,
TickResolver class) that can be safely imported in a forkserver process-pool
child without pulling in SQLAlchemy, FastAPI, persist, or any service module.

Also contains key-event extraction helpers (_extract_key_events,
_ticks_to_seconds, _TICK_MS) moved from combat_log_service.

P2-T0c: This module is the split point — everything above class CombatService
in the original combat_service.py lives here.  CombatService itself stays in
combat_service.py and imports TickResolver (and other symbols it needs) from
this module.
"""

import itertools
import math
import random
from dataclasses import dataclass, field, replace

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
    CombatTuning,
    FightResults,
    FightStats,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from services.game_constants import GameConstants

flogger = bblogger.get_logger(__name__)

# Repair-bot regen accumulator precision: 12 decimal places is intentionally
# conservative for repeated per-tick float additions (e.g. 0.1) — it suppresses visible
# IEEE-754 drift (10 × 0.1 = 0.9999…) while staying far below any gameplay-significant
# integer-flush threshold.
REPAIR_BOT_ACCUMULATOR_ROUND_DIGITS = 12

_PRIMARY_WEAPON_MOD_TYPE = "PrimaryWeaponModModule"

# STI discriminator for RepairBot modules (used for rate detection in _init_combatant)
_REPAIR_BOT_MODULE_TYPE = "RepairBotModule"


def _damage_or_zero(value: float | None) -> float:
    """Return *value* as a float, defaulting None to 0.0 (a real 0.0 is preserved)."""
    return value if value is not None else 0.0


def _clamp_accuracy(value: float, min_val: float, max_val: float) -> float:
    """Clamp an accuracy value to the configured inclusive [min_val, max_val] bounds."""
    return max(min_val, min(max_val, value))


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
        automatic=False → manual-turret: range-driven gap-closer — fires only while NO
                          primary is in range; inert the moment any primary is in range.

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

# Module keys tracked in the §12 summary module_activations dict (P2-T4).
# Mirrors the local set inside _build_fight_summary; promoted here so callers
# (_increment_player_stats, tests) can reference it without duplicating the list.
# COUPLING NOTE: _increment_player_stats reads module_activations from the summary
# block, which filters by this allowlist. Any new module that emits a
# module_activation event MUST be added here or it will be silently excluded from
# player total_module_activations stats.
_ACTIVATION_MODULES: frozenset[str] = frozenset({"cloak", "booster", "emergency_system"})

# Built-in U'tool module name (§10 supersession)
_UTOOL_BUILTIN_NAME = "U'tool"

# U'tool virtual stats when used as built-in (§10 / §7.2 wiki values)
_UTOOL_EFFECT_DURATION_MS = 10_000
_UTOOL_LOADING_SPEED_MS = 2_000


def deplete_side1_loadout(loadout: ShipLoadout, summary: dict) -> ShipLoadout:
    """Return a NEW ShipLoadout with the player's (side-1) consumables depleted
    according to one fight's summary block.

    Pure, DB-free mirror of ``CombatService._consume_secondary_ammo`` and
    ``_consume_emergency_system``, operating on the frozen :class:`ShipLoadout`
    dataclass (via :func:`dataclasses.replace`) instead of the player's DB ship
    row.  Used by the preflight Monte-Carlo (``run_fight_batch`` carry mode) to
    thread resource state across the sequential 20-sim run, so secondary ammo and
    the one-use EmergencySystem deplete across fights instead of refilling every
    sim.  HP/shields/armour are intentionally NOT carried (they reset per fight).

    Reads ONLY the side-1 summary block — the player is always combatant1/slot
    ``"1"`` in a preflight fight; side-2 (criminal) usage is ignored.  When the
    summary records no relevant consumption the returned loadout is equal in
    effect to the input (a fresh object regardless, never mutating the frozen
    input).

    Secondary weapons: a weapon with ``ammo is None`` is infinite (untouched).  A
    finite-ammo weapon's ammo is decremented by its rounds fired this fight
    (clamped at 0) and DROPPED from ``secondary_weapons`` when it reaches 0 —
    mirroring the DB auto-unequip.

    EmergencySystem: if the ES fired this fight (``module_activations``
    ``"emergency_system"`` >= 1), exactly ONE ``EmergencySystemModule`` is removed
    from ``modules`` (ES fires at most once per fight, §7.7).
    """
    cb_block = summary.get("combatants", {}).get("1", {})

    # --- Secondary ammo depletion (mirror _consume_secondary_ammo) ---
    rounds_fired: dict[str, int] = cb_block.get("secondary_rounds_by_weapon", {}) or {}
    new_secondaries: list[WeaponStats] = []
    for sw in loadout.secondary_weapons:
        if sw.ammo is None:
            new_secondaries.append(sw)  # infinite ammo — never depletes
            continue
        new_ammo = max(0, sw.ammo - rounds_fired.get(sw.name, 0))
        if new_ammo <= 0:
            continue  # depleted — auto-unequip (drop from the sim loadout)
        new_secondaries.append(replace(sw, ammo=new_ammo))

    # --- EmergencySystem consumption (mirror _consume_emergency_system) ---
    es_activations: int = cb_block.get("module_activations", {}).get("emergency_system", 0)
    new_modules: list[ModuleStats] = list(loadout.modules)
    if es_activations >= 1:
        for idx, mod in enumerate(new_modules):
            if mod.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE:
                new_modules.pop(idx)  # consume exactly one (ES fires at most once/fight)
                break

    return replace(loadout, secondary_weapons=new_secondaries, modules=new_modules)


@dataclass(slots=True)
class _CloakRuntime:
    """Per-combatant runtime state for the cloak module (§7.2 / §8).

    Tracks effect/cooldown timers. Initial state: cooldown=0, effect=0 (§1 / §8).

    Thread-5 (combat chained activations, BALANCE_JOURNAL §E / decision log): activation
    limits were REMOVED. There is no per-fight cap and thresholds are re-armable (no
    consumed-threshold tracking). The only gates are `cooldown_remaining_ms <= 0` AND
    `effect_remaining_ms == 0` (plus the no-activate-while-ES-invuln guard in Phase 5).
    `activation_count` is retained for TELEMETRY ONLY — it never gates activation.
    """

    stats: ModuleStats  # effective cloak module stats (equipped or U'tool virtual)
    cooldown_remaining_ms: int = 0
    effect_remaining_ms: int = 0
    activation_count: int = 0  # TELEMETRY ONLY (Thread 5) — never gates activation


@dataclass(slots=True)
class _BoosterRuntime:
    """Per-combatant runtime state for the booster module (§7.3 / §8).

    Tracks effect/cooldown timers. Initial state: cooldown=0, effect=0 (§1 / §8).

    Thread-5 (combat chained activations, BALANCE_JOURNAL §E / decision log): activation
    limits were REMOVED. There is no per-fight cap and thresholds are re-armable (no
    consumed-threshold tracking). The only gates are `cooldown_remaining_ms <= 0` AND
    `effect_remaining_ms == 0`. `activation_count` is retained for TELEMETRY ONLY.
    """

    stats: ModuleStats  # effective booster module stats
    cooldown_remaining_ms: int = 0
    effect_remaining_ms: int = 0
    activation_count: int = 0  # TELEMETRY ONLY (Thread 5) — never gates activation


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
    # D-014: per-side nuke detonation counter — drives yield interference
    # (stack_mult = NUKE_STACK_FALLOFF ** nukes_detonated; resets each fight by construction)
    nukes_detonated: int = 0


def _init_combatant(
    loadout: ShipLoadout,
    *,
    is_player: bool,
    slot: int = 1,
    display_name: str = "",
    tuning: CombatTuning | None = None,
) -> _CombatantState:
    """Build combatant runtime state from a ShipLoadout.

    Called once before the tick loop begins (§1 implementation note).
    All weapons enter at cooldown_remaining = 0 EXCEPT nuke secondaries, which
    start on full cooldown (D-014 arming delay); all HP layers start at max;
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

    # Repair Bot rate — any RepairBotModule contributes; pick the highest equipped (§3).
    # Rate is a property on the module object (set by loadout_builder via HPps→pct mapping).
    repair_rate = 0.0
    for mod in loadout.modules:
        if mod.module_type == _REPAIR_BOT_MODULE_TYPE:
            repair_rate = max(repair_rate, mod.repair_rate)

    # All cooldowns start at 0 — weapons fully ready at tick 0 (§1)
    # Primary weapon cooldowns are tracked in effective_primaries (T5).
    # Turret cooldowns are tracked in effective_turrets (T7); weapon_cooldowns is now empty.
    # Module cooldowns are tracked here (no per-module runtime object yet).
    weapon_cooldowns: dict[str, int] = {}
    module_cooldowns = {m.name: 0 for m in loadout.modules}

    # Precompute scanner tier once — stateless, same loadout always returns same result (§7.1)
    _tier_b = tuning.scanner_tier_b_bonus_pp if tuning is not None else float(GameConstants.SCANNER_TIER_B_BONUS_PP)
    _tier_c = tuning.scanner_tier_c_bonus_pp if tuning is not None else float(GameConstants.SCANNER_TIER_C_BONUS_PP)
    scanner_tier = resolve_scanner_tier(
        loadout,
        tier_b_bonus_pp=_tier_b,
        tier_c_bonus_pp=_tier_c,
    )

    # ------------------------------------------------------------------
    # T5: PrimaryWeaponMod pre-pass — bake effective stats once at init (§7.8)
    # Applies to primary weapons ONLY; turrets and secondaries are unaffected.
    # ------------------------------------------------------------------
    pw_mods = [m for m in loadout.modules if m.module_type == _PRIMARY_WEAPON_MOD_TYPE]
    if len(pw_mods) > 1:
        # Unique-equip invariant violation — first wins; log once outside tick loop (§10)
        pw_mod_names = ", ".join(m.name for m in pw_mods)
        flogger.warning(
            f"Combatant '{loadout.ship_name}': multiple PrimaryWeaponMods equipped "
            f"({pw_mod_names}). Using first: '{pw_mods[0].name}'. "
            "Upstream loadout-builder invariant violated."
        )
    pw_mod = pw_mods[0] if pw_mods else None
    damage_pct_val: int = pw_mod.damage_pct if pw_mod is not None else 0
    fire_rate_pct_val: int = pw_mod.fire_rate_pct if pw_mod is not None else 0

    effective_primaries: list[_PrimaryWeaponRuntime] = []
    for ws in loadout.weapons:
        base_dmg = _damage_or_zero(ws.damage_per_shot)
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
        raw_dmg = _damage_or_zero(sw.damage_per_shot)
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
                # §1: fully ready at tick 0 — EXCEPT nukes (D-014): warheads arm during the
                # fight (start on full cooldown) to kill the free max-range alpha-strike.
                cooldown_remaining_ms=(raw_speed if sw.subtype == "nuke" else 0),
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


def _tick_shield_regen(
    state: _CombatantState,
    tick: int,
    events: list[CombatEvent],
    reemit_frac: float = float(GameConstants.COMBAT_LAYER_REEMIT_FRACTION),
) -> None:
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
    if (
        state.max_shield > 0
        and "shield" in state.depleted_layers
        and (state.current_shield >= math.ceil(state.max_shield * reemit_frac))
    ):
        state.depleted_layers.discard("shield")


def _tick_repair_bot_regen(
    state: _CombatantState,
    tick: int,
    events: list[CombatEvent],
    reemit_frac: float = float(GameConstants.COMBAT_LAYER_REEMIT_FRACTION),
) -> None:
    """Apply Repair Bot regen for one tick (Appendix B step 2).

    Fills hull first, then armour. Dormant when both layers are at max.
    Float accumulator; integer-flushed per §3. Partial discarded on return to max.
    """
    if state.repair_bot_rate_per_sec == 0.0:
        return
    if state.current_hull >= state.max_hull and state.current_armour >= state.max_armour:
        return  # dormant — both layers at max

    delta = state.repair_bot_delta_per_tick
    # Round accumulator to a stable precision to suppress IEEE-754 drift across long
    # tick sequences (e.g. 10 × 0.1 = 0.9999…). See REPAIR_BOT_ACCUMULATOR_ROUND_DIGITS.
    state.repair_bot_regen_accumulator = round(
        state.repair_bot_regen_accumulator + delta, REPAIR_BOT_ACCUMULATOR_ROUND_DIGITS
    )

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
                            "layer": "armour",
                            "amount": armour_add,
                            "hp_after": state.current_armour,
                            "side": state.slot,
                        },
                    )
                )

    # Discard partial when both layers are back at max
    if state.current_hull >= state.max_hull and state.current_armour >= state.max_armour:
        state.repair_bot_regen_accumulator = 0.0

    # CI-21: clear depleted_layers latch for hull/armour when meaningful recovery achieved
    if (
        state.max_hull > 0
        and "hull" in state.depleted_layers
        and (state.current_hull >= math.ceil(state.max_hull * reemit_frac))
    ):
        state.depleted_layers.discard("hull")
    if (
        state.max_armour > 0
        and "armour" in state.depleted_layers
        and (state.current_armour >= math.ceil(state.max_armour * reemit_frac))
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


def _try_activate_chained_module(
    state: _CombatantState,
    module_key: str,
    trigger: str,
    tick: int,
    events: list[CombatEvent],
) -> bool:
    """Activate cloak/booster via the emergency-system chain (Thread 5 — BALANCE_JOURNAL §E).

    ONE-SHOT at the trigger instant (G2): the activation is attempted exactly once, here and now.
    Gates are identical to a normal HP-threshold activation — the module must be off cooldown
    (`cooldown_remaining_ms <= 0`) AND not already active (`effect_remaining_ms == 0`). If either
    gate fails (e.g. the target module is on cooldown, or an already-active instance from a previous
    tick is still running) the chain activation is simply LOST — no deferral, no retry, and an
    already-active module is NEVER refreshed nor cut short (decision #3 / G4).

    Emits a `module_activation` event carrying a distinct ``trigger`` marker
    (``"emergency_activate"`` for the booster via Trigger A, ``"emergency_end"`` for the cloak
    via Trigger B) so it is distinguishable in logs from the normal ``trigger_hp_pct`` crossings,
    while still counting toward ``module_activations`` stats (same ``module`` key).

    Cooldown is NOT set here — it starts at effect EXPIRY in ``_tick_module_effects`` (§7.2 / §7.3),
    mirroring the normal HP-threshold activation path.

    Returns True if the module activated, False otherwise.
    """
    if module_key == "cloak":
        mod_rt = state.cloak_runtime
    elif module_key == "booster":
        mod_rt = state.booster_runtime
    else:  # pragma: no cover — defensive; only cloak/booster chain
        return False
    if mod_rt is None:
        return False
    # Sole gates (Thread 5): off cooldown AND not already active. No cap, no consumed-threshold check.
    if mod_rt.cooldown_remaining_ms > 0 or mod_rt.effect_remaining_ms != 0:
        return False
    mod_rt.effect_remaining_ms = mod_rt.stats.effect_duration_ms
    mod_rt.activation_count += 1  # telemetry only
    events.append(
        CombatEvent(
            tick=tick,
            type=CombatEventType.module_activation,
            actor=state.name,
            target=None,
            data={"module": module_key, "trigger": trigger, "side": state.slot},
        )
    )
    return True


def _eval_hp_threshold_modules(
    state: _CombatantState,
    tick: int,
    events: list[CombatEvent],
    cloak_thresholds: list[int],
    booster_thresholds: list[int],
) -> None:
    """Phase 5: evaluate HP-threshold module activations for one combatant (§8).

    Crossing detection: previous-tick HP-pct was above threshold; post-damage HP-pct is at or below.

    Thread-5 (combat chained activations, BALANCE_JOURNAL §E / decision log):
    - Activation limits REMOVED — no per-fight cap and thresholds are RE-ARMABLE (no
      consumed-threshold tracking). A downward re-cross of a threshold (only possible after HP
      recovers back above it via regen) can re-fire, cooldown permitting. The only gates are
      `cooldown_remaining_ms <= 0` AND `effect_remaining_ms == 0` (not-already-active). The
      `prev_hp_pct` crossing rule keeps this self-regulating (no downward re-cross without first
      recovering above the threshold).
    - Cloak no-activate-while-invuln guard: if the EmergencySystem invuln window is active
      (`es_runtime.invuln_remaining_ms > 0`) the cloak is SKIPPED entirely this phase. This is the
      belt-and-suspenders half of the no-co-activation invariant — cloak is deferred to ES-end via
      Trigger B, so it never co-activates with ES (same-tick ES result: ES✓/Booster✓/Cloak✗).

    Booster-user can still fire (§7.3) — no phase-3 suppression needed here.
    """
    current_pct = _compute_hp_pct(state)
    prev_pct = state.prev_hp_pct

    # --- Cloak (§7.2) ---
    # Thread-5 guard: never activate the cloak while the ES invuln window is open — accuracy
    # reduction is wasted during invuln, and cloak must not co-activate with ES (Trigger B covers
    # the post-ES recovery instead). Skip the whole cloak path while invuln is active.
    _invuln_active = state.es_runtime is not None and state.es_runtime.invuln_remaining_ms > 0
    if state.cloak_runtime is not None and not _invuln_active:
        cr = state.cloak_runtime
        for threshold in cloak_thresholds:
            threshold_frac = threshold / 100.0
            # Crossing: was above threshold last tick, now at or below (§8 definition).
            # Re-armable (Thread 5): no consumed-threshold check; gated only by cooldown + not-active.
            # When not activated (cooling/already active) the threshold stays RE-ARMABLE — it can
            # fire on a later downward re-cross once HP recovers above it and cooldown clears.
            if (
                prev_pct > threshold_frac >= current_pct
                and cr.cooldown_remaining_ms <= 0
                and cr.effect_remaining_ms == 0
            ):
                cr.effect_remaining_ms = cr.stats.effect_duration_ms
                cr.activation_count += 1  # telemetry only — never gates (Thread 5)
                events.append(
                    CombatEvent(
                        tick=tick,
                        type=CombatEventType.module_activation,
                        actor=state.name,
                        target=None,
                        data={"module": "cloak", "trigger_hp_pct": threshold, "side": state.slot},
                    )
                )

    # --- Booster (§7.3) ---
    if state.booster_runtime is not None:
        br = state.booster_runtime
        for threshold in booster_thresholds:
            threshold_frac = threshold / 100.0
            if (
                prev_pct > threshold_frac >= current_pct
                and br.cooldown_remaining_ms <= 0
                and br.effect_remaining_ms == 0
            ):
                br.effect_remaining_ms = br.stats.effect_duration_ms
                br.activation_count += 1  # telemetry only — never gates (Thread 5)
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

    Thread-5 (combat chained activations, BALANCE_JOURNAL §E / decision log) — Trigger A:
    immediately after ES fires, the BOOSTER is chain-activated (if off cooldown AND not already
    active) so mobility helps reposition DURING the immunity window. This is the Phase-4a half of
    the same-tick ES invariant (ES✓/Booster✓): because Phase 4a runs before the Phase-5 HP-threshold
    check, the booster is already active when its own threshold crossing is evaluated this tick, so
    that crossing no-ops (already-active) — no bespoke arbitration. The chain is one-shot (G2): if
    the booster is on cooldown at this instant it is simply lost.
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

    # Trigger A (Thread 5): ES activates → chain-activate the Booster (off-cooldown + not-active only).
    _try_activate_chained_module(state, "booster", "emergency_activate", tick, events)


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


def _nuke_window(
    current_distance: float,
    *,
    threshold_m: float = float(GameConstants.NUKE_RANGE_REGIME_THRESHOLD_M),
    lr_near_frac: float = float(GameConstants.NUKE_LR_NEAR_FRAC),
    cr_short_m: float = float(GameConstants.NUKE_CR_SHORT_M),
    cr_overshoot_m: float = float(GameConstants.NUKE_CR_OVERSHOOT_M),
) -> tuple[float, float]:
    """D-014: two-regime nuke detonation window on the 1-D combat axis (firer at 0).

    Long-range (d > threshold_m):
        [lr_near_frac × d, d] — aimed at the target, never overshoots; short rounds
        can fall deep toward the firer (long-range self-risk scales with the gap).
    Close-range (d ≤ threshold_m):
        [max(0, d − cr_short_m), d + cr_overshoot_m] — artillery bracket with
        overshoot past the target; epicenter can land on either ship.

    Edges meet continuously at the boundary when lr_near_frac × threshold_m
    == threshold_m − cr_short_m (defaults: 0.40×1000 == 1000−600).
    """
    if current_distance > threshold_m:
        return (lr_near_frac * current_distance, current_distance)
    return (
        max(0.0, current_distance - cr_short_m),
        current_distance + cr_overshoot_m,
    )


def _shock_blast_apply(
    attacker: _CombatantState,
    current_distance: float,
    starting_distance_m: float = float(GameConstants.STARTING_DISTANCE_M),
) -> float:
    """Apply shock-blast Phase 6 effect: returns new distance (starting_distance_m).

    Shock-blast resets current_distance to starting_distance_m (D6 / Appendix B §6).
    This function is PURE with respect to combatant state — it ONLY computes the
    new distance value. It does NOT mutate attacker, the target, module_cooldowns,
    weapon_cooldowns, or any other field on any _CombatantState.

    The caller (TickResolver Phase 6) is responsible for:
      - updating the resolver-local current_distance variable
      - emitting the distance event to the combat log

    Args:
        attacker: Shock-blast owner (used for name in event; state NOT mutated).
        current_distance: Current resolver-local distance before reset.
        starting_distance_m: The reset distance (per-guild or global default).

    Returns:
        New distance after shock-blast reset (always starting_distance_m).
    """
    _ = attacker  # name used by caller for event actor; no state mutation
    _ = current_distance  # captured by caller for 'from' field; not used here
    return starting_distance_m


def _build_fight_summary(
    events: list[CombatEvent],
    c1: _CombatantState,
    c2: _CombatantState,
    outcome: str,
    reason: str,
    duration_ticks: int,
    winner_name: str | None,
    winner_side: int | None = None,
) -> dict:
    """Build the Tier-0 summary dict (§12 data.summary) by scanning the in-memory event list.

    All scans read CombatEvent OBJECT ATTRIBUTES (ev.type, ev.actor, ev.data[...]),
    NOT dict keys — the timeline holds dataclass instances, not dicts (§12 precision note).

    Per-combatant fields derived from the event scan:
      shots_fired       — aimed shots: +1 per primary/turret/rocket/missile fire, + the
                          sub-munition count for cluster-missiles; nukes & shock-blasts excluded
      shots_hit         — landed aimed shots: +1 per hit, + landed sub-munitions for clusters
      accuracy          — shots_hit/shots_fired; 0.0 if no shots
      module_activations          — {module_key: count} SPARSE (cloak/booster/emergency_system only)
      killing_blow_subtype        — subtype of last absorbed damage event targeting the loser
                                    (None on stalemate or if no damage events recorded)
      max_nuke_absorbed           — max absorbed HP from a nuke source by this side (0 if none)
      secondary_fired             — {subtype: count} SPARSE (secondaries only)
      secondary_rounds_by_weapon  — {weapon_name: count} SPARSE (secondaries only, by weapon name);
                                    mirrors _consume_secondary_ammo criterion (slot=="secondary").
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
    # When both ships share a name, name-based attribution is ambiguous: the map would
    # collide (a single entry → slot 1). In that case only data["side"] can safely
    # disambiguate, so the name fallbacks below refuse to guess (yield None) rather than
    # mis-attribute every same-name event to slot 1. Real fights always emit data["side"],
    # so this only affects the defensive side-less path.
    # For the accumulators themselves we always key on slot ("1"/"2").
    _names_collide = c1.name == c2.name
    c1_slot = "1"
    c2_slot = "2"
    _name_to_slot[c1.name] = 1
    if not _names_collide:
        _name_to_slot[c2.name] = 2

    shots_fired: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    shots_hit: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    module_activations: dict[str, dict[str, int]] = {c1_slot: {}, c2_slot: {}}
    secondary_fired: dict[str, dict[str, int]] = {c1_slot: {}, c2_slot: {}}
    secondary_rounds_by_weapon: dict[str, dict[str, int]] = {c1_slot: {}, c2_slot: {}}
    damage_dealt: dict[str, int] = {c1_slot: 0, c2_slot: 0}
    damage_taken: dict[str, int] = {c1_slot: 0, c2_slot: 0}

    def _slot_from_event(ev: CombatEvent) -> str | None:
        """Resolve actor's slot from event. Prefers data['side']; falls back to name→slot.

        When both combatants share a ship name the name fallback is ambiguous, so a
        side-less event is left unattributed (returns None) rather than defaulting to slot 1.
        """
        side = ev.data.get("side") if ev.data else None
        if side is not None:
            return str(side)
        if _names_collide:
            return None
        actor = ev.actor
        if actor is None:
            return None
        slot = _name_to_slot.get(actor)
        return str(slot) if slot is not None else None

    # Slice 2 event-hook fields (issue #30)
    # killing_blow_subtype: subtype of the last absorbed-damage event whose target is the loser
    # tracked per target slot — winner's block reads last_damage_subtype[loser_slot]
    last_damage_subtype: dict[str, str | None] = {c1_slot: None, c2_slot: None}
    # max_nuke_absorbed: max nuke absorbed HP by each attacking side
    max_nuke_absorbed: dict[str, int] = {c1_slot: 0, c2_slot: 0}

    # Derive winner/loser slots for killing_blow attribution
    if winner_side == 1:
        _winner_slot: str | None = c1_slot
        _loser_slot: str | None = c2_slot
    elif winner_side == 2:
        _winner_slot = c2_slot
        _loser_slot = c1_slot
    else:
        _winner_slot = None  # stalemate
        _loser_slot = None

    # Discrete activation modules tracked in summary (§12 / §13) — uses module-level constant

    for ev in events:
        if ev.type == CombatEventType.weapon_fire:
            ev_slot = _slot_from_event(ev)
            sub = ev.data.get("subtype", "")
            if ev_slot in shots_fired:
                # Accuracy accounting: cluster-missiles count at sub-munition granularity
                # (each burst round is a shot; each landed sub-munition a hit), so a 3/4 cluster
                # reads 75% rather than a flat miss. Nukes and shock-blasts have no hit/miss
                # semantics (AoE detonation / guaranteed displacement) and are excluded from
                # shot counts entirely. Everything else (primaries, turrets, rockets, missiles,
                # ionizing-missiles) is one aimed shot that hits iff data["hit"] is True.
                if sub == "cluster-missile":
                    shots_fired[ev_slot] += ev.data.get("fired", 0)
                    shots_hit[ev_slot] += ev.data.get("hits", 0)
                elif sub not in ("nuke", "shock-blast"):
                    shots_fired[ev_slot] += 1
                    if ev.data.get("hit") is True:
                        shots_hit[ev_slot] += 1
            # secondary_fired — count by subtype for secondaries (data["slot"] == "secondary")
            if ev.data.get("slot") == "secondary" and ev_slot in secondary_fired:
                if sub:
                    secondary_fired[ev_slot][sub] = secondary_fired[ev_slot].get(sub, 0) + 1
                # secondary_rounds_by_weapon — count by weapon name (same slot criterion);
                # side-keyed so same-name ships accumulate per-side correctly (CI-24).
                # Criterion mirrors _consume_secondary_ammo: slot=="secondary" + weapon name present.
                w_name = ev.data.get("weapon", "")
                if w_name and ev_slot in secondary_rounds_by_weapon:
                    secondary_rounds_by_weapon[ev_slot][w_name] = secondary_rounds_by_weapon[ev_slot].get(w_name, 0) + 1

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
                elif attacker_name is not None and not _names_collide:
                    _raw = _name_to_slot.get(attacker_name)
                    att_slot = str(_raw) if _raw is not None else None
                else:
                    att_slot = None
                # target slot from data["side"]
                tgt_slot = str(target_side) if target_side is not None else None
                if tgt_slot is None and ev.target is not None and not _names_collide:
                    _raw_t = _name_to_slot.get(ev.target)
                    tgt_slot = str(_raw_t) if _raw_t is not None else None
                if att_slot in damage_dealt:
                    damage_dealt[att_slot] += absorbed_hp
                if tgt_slot in damage_taken:
                    damage_taken[tgt_slot] += absorbed_hp
                # Slice 2: track last damage subtype per target slot (killing_blow_subtype)
                if tgt_slot in last_damage_subtype:
                    _sub = source.get("subtype") or "primary"
                    # Turret fire carries subtype "auto" or "manual"; normalise to "turret"
                    # so kills_by_weapon weapon=turret events can score correctly.
                    last_damage_subtype[tgt_slot] = "turret" if _sub in ("auto", "manual") else _sub
                # Slice 2: track max nuke absorbed per attacker slot
                if source.get("subtype") == "nuke" and att_slot in max_nuke_absorbed:
                    max_nuke_absorbed[att_slot] = max(max_nuke_absorbed[att_slot], absorbed_hp)

    def _combatant_block(cx: _CombatantState, slot_key: str) -> dict:
        """Build per-combatant summary block (CI-24: keyed on slot, not name)."""
        fired = shots_fired[slot_key]
        hit = shots_hit[slot_key]
        acc = (hit / fired) if fired > 0 else 0.0
        # killing_blow_subtype: winner's slot gets the subtype of last damage to loser
        if slot_key == _winner_slot and _loser_slot is not None:
            kb_sub: str | None = last_damage_subtype.get(_loser_slot)
        else:
            kb_sub = None
        return {
            "name": cx.display_name,  # CI-20: pilot/criminal label (defaults to ship_name)
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
            "secondary_rounds_by_weapon": dict(secondary_rounds_by_weapon[slot_key]),  # sparse — by weapon name
            "killing_blow_subtype": kb_sub,
            "max_nuke_absorbed": max_nuke_absorbed[slot_key],
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
        tuning: CombatTuning | None = None,
        rng: random.Random | None = None,
        combatant1_label: str = "",
        combatant2_label: str = "",
    ) -> FightResults:
        """Run a full tick-based fight between two ShipLoadouts.

        Args:
            loadout1: C1 — challenger (player in PvC when pvc_damage_reduction > 0).
            loadout2: C2 — opponent (NPC in PvC; player2 in PvP).
            pvc_damage_reduction: Keith T. Maxwell DR (§3). 0.33 for PvC, 0.0 for PvP.
            guild_config: Reserved for per-guild constant overrides (T10+). Superseded by
                          ``tuning`` — pass the pre-built CombatTuning struct instead.
            tuning: Pre-built per-guild constant snapshot (issue #70, unit A1). When None
                    all constants fall back to GameConstants globals (existing behaviour).
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
        # Global-invariant constants (never per-guild):
        tick_ms = GameConstants.TICK_MS
        max_ticks = GameConstants.MAX_FIGHT_TICKS
        _acc_clamp_min = GameConstants.ACCURACY_CLAMP_MIN
        _acc_clamp_max = GameConstants.ACCURACY_CLAMP_MAX
        # T8: HP-threshold activation lists (global-invariant, not per-guild)
        _cloak_thresholds: list[int] = list(GameConstants.CLOAK_HP_THRESHOLDS_PCT)
        _booster_thresholds: list[int] = list(GameConstants.BOOSTER_HP_THRESHOLDS_PCT)
        # Per-guild tunable constants — resolved from tuning struct or GameConstants:
        _t = tuning  # alias for brevity
        min_dist = _t.min_distance_m if _t is not None else float(GameConstants.MIN_DISTANCE_M)
        _base_speed_mps = _t.base_ship_speed_mps if _t is not None else float(GameConstants.BASE_SHIP_SPEED_MPS)
        distance_delta = _base_speed_mps * 2 * (tick_ms / 1000)
        # Accuracy constants (T4)
        _player_base_acc = _t.player_base_accuracy if _t is not None else GameConstants.PLAYER_BASE_ACCURACY
        _npc_base_acc = _t.npc_base_accuracy if _t is not None else GameConstants.NPC_BASE_ACCURACY
        _cloak_set = _t.cloak_set_value if _t is not None else GameConstants.CLOAK_SET_VALUE
        # RNG seam (T4 — not consumed until T5; inject via rng= kwarg for deterministic tests)
        _rng = rng if rng is not None else self._rng
        # T7: auto-turret accuracy multiplier (baked once — constant per fight)
        _auto_turret_multiplier = (
            _t.auto_turret_accuracy_multiplier if _t is not None else GameConstants.AUTO_TURRET_ACCURACY_MULTIPLIER
        )
        # T8: per-guild debuff / bonus scalers
        _k_boost = (
            _t.booster_accuracy_debuff_factor if _t is not None else float(GameConstants.BOOSTER_ACCURACY_DEBUFF_FACTOR)
        )
        _k_thrust = (
            _t.thruster_accuracy_bonus_factor if _t is not None else float(GameConstants.THRUSTER_ACCURACY_BONUS_FACTOR)
        )
        _thruster_window = _t.thruster_window_m if _t is not None else float(GameConstants.THRUSTER_WINDOW_M)
        # Per-guild regen reemit threshold (CI-21)
        _reemit_frac = (
            _t.combat_layer_reemit_fraction if _t is not None else float(GameConstants.COMBAT_LAYER_REEMIT_FRACTION)
        )
        # Starting distance
        _starting_dist = _t.starting_distance_m if _t is not None else float(GameConstants.STARTING_DISTANCE_M)
        # Emergency system invuln (ms)
        _emerg_invuln_ms = (
            _t.emergency_system_invuln_s * 1000 if _t is not None else GameConstants.EMERGENCY_SYSTEM_INVULN_S * 1000
        )
        # Nuke constants
        _nuke_mag_scale = _t.nuke_magnitude_scale if _t is not None else float(GameConstants.NUKE_MAGNITUDE_SCALE)
        _nuke_friendly = _t.nuke_friendly_factor if _t is not None else float(GameConstants.NUKE_FRIENDLY_FACTOR)
        _nuke_threshold_m = (
            _t.nuke_range_regime_threshold_m if _t is not None else float(GameConstants.NUKE_RANGE_REGIME_THRESHOLD_M)
        )
        _nuke_lr_near = _t.nuke_lr_near_frac if _t is not None else float(GameConstants.NUKE_LR_NEAR_FRAC)
        _nuke_cr_short = _t.nuke_cr_short_m if _t is not None else float(GameConstants.NUKE_CR_SHORT_M)
        _nuke_cr_over = _t.nuke_cr_overshoot_m if _t is not None else float(GameConstants.NUKE_CR_OVERSHOOT_M)
        _nuke_stack = _t.nuke_stack_falloff if _t is not None else float(GameConstants.NUKE_STACK_FALLOFF)
        # Shock-blast trigger range
        _shock_trigger_m = (
            _t.shock_blast_trigger_range_m if _t is not None else float(GameConstants.SHOCK_BLAST_TRIGGER_RANGE_M)
        )

        # --- Combatant init (§1: separate from tick loop) ---
        c1 = _init_combatant(
            loadout1, is_player=(pvc_damage_reduction > 0.0), slot=1, display_name=combatant1_label, tuning=tuning
        )
        c2 = _init_combatant(loadout2, is_player=False, slot=2, display_name=combatant2_label, tuning=tuning)

        current_distance = _starting_dist
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
                            "name": c1.name,  # ship_name byte-for-byte
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
        # P2-T0b: side of winner — derived from death-branch, NOT from winner_name.
        # 1 = c1 (challenger) wins; 2 = c2 (target/criminal) wins; None = stalemate/timeout.
        winner_side: int | None = None
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
                # Primary cooldowns STILL decrement while out of range / turret-phase (§6.3 note)
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
                    # Trigger B (Thread 5): the invuln >0 → 0 transition IS "ES ends" → chain-activate
                    # the Cloak (off-cooldown + not-active only) to cover the vulnerable post-ES
                    # recovery. One-shot (G2): if cloak is on cooldown at this instant it is lost.
                    # G3: if the fight already ended this tick, Trigger B simply never reaches here.
                    if _cs.es_runtime.invuln_remaining_ms == 0:
                        _try_activate_chained_module(_cs, "cloak", "emergency_end", tick, events)

            # ------------------------------------------------------------------
            # Phase 2: Apply regen pulses (C1 then C2; shield + repair bot parallel)
            # ------------------------------------------------------------------
            _tick_shield_regen(c1, tick, events, _reemit_frac)
            _tick_repair_bot_regen(c1, tick, events, _reemit_frac)
            _tick_shield_regen(c2, tick, events, _reemit_frac)
            _tick_repair_bot_regen(c2, tick, events, _reemit_frac)

            # ------------------------------------------------------------------
            # Phase 3: Evaluate weapon firings — primary weapons (T5)
            # Hits are RECORDED here, not applied. Fire/apply separation is what
            # makes mutual-fire-on-the-lethal-tick correct (Appendix B).
            # RNG draw order: C1 primaries (insertion order), then C2 primaries.
            # ------------------------------------------------------------------
            # pending: (attacker_state, target_state, weapon_runtime) — hits only
            _pending: list[tuple[_CombatantState, _CombatantState, _PrimaryWeaponRuntime]] = []

            for _attacker, _target in ((c1, c2), (c2, c1)):
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
                        # D5/D-014: no accuracy roll; epicenter sampled via injected RNG from the
                        # two-regime window (one uniform draw — RNG sequence shape preserved).
                        _win_lo, _win_hi = _nuke_window(
                            current_distance,
                            threshold_m=_nuke_threshold_m,
                            lr_near_frac=_nuke_lr_near,
                            cr_short_m=_nuke_cr_short,
                            cr_overshoot_m=_nuke_cr_over,
                        )
                        _epicenter = _rng.uniform(_win_lo, _win_hi)
                        _d_firer = _epicenter  # firer at position 0
                        _d_opp = abs(_epicenter - current_distance)
                        _eff_mag = _sw.magnitude_m * _nuke_mag_scale
                        # D-014 yield interference: each successive detonation by this side
                        # multiplies yield by nuke_stack_falloff (whole detonation, self incl.)
                        _stack_mult = _nuke_stack**_attacker.nukes_detonated
                        _attacker.nukes_detonated += 1
                        _opp_raw = _nuke_dmg(_d_opp, _sw.damage_per_shot, _eff_mag) * _stack_mult
                        _self_raw = _nuke_dmg(_d_firer, _sw.damage_per_shot, _eff_mag) * _stack_mult * _nuke_friendly
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
                                    "window_lo": _win_lo,  # D-014
                                    "window_hi": _win_hi,  # D-014
                                    "stack_mult": _stack_mult,  # D-014 yield interference
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
                        # Only fire when the enemy is close; at long range the distance-reset is pointless
                        # (resets to starting_distance_m) and would waste a cooldown.
                        if current_distance >= _shock_trigger_m:
                            continue
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
            # Auto-turrets always fire (alongside primaries, in any phase).
            # Manual-turrets fire only while NO primary is in range (range-driven switch).
            # RNG draw order: C1 auto-turrets, C2 auto-turrets, C1 manual-turrets, C2 manual-turrets.
            # One auto-turret accuracy per combatant per tick (§6.3 correctness statement).
            # Plasma-collectors are NOT in effective_turrets — already filtered at init.
            # ------------------------------------------------------------------
            # Turret pending: (attacker_state, target_state, turret_runtime, damage_per_shot) — hits only
            _turret_pending: list[tuple[_CombatantState, _CombatantState, _TurretWeaponRuntime, int]] = []

            # Pre-bake auto-turret accuracy once per combatant per tick (§6.3 / Appendix A)
            # ONE value per combatant per tick — correctness statement (§6.3), not just perf.
            _c1_auto_acc = _clamp_accuracy(
                c1.pilot_turret_acc * _auto_turret_multiplier, _acc_clamp_min, _acc_clamp_max
            )
            _c2_auto_acc = _clamp_accuracy(
                c2.pilot_turret_acc * _auto_turret_multiplier, _acc_clamp_min, _acc_clamp_max
            )

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

            # Manual-turrets: C1 then C2 — range-driven gap-closer (§6.3).
            # A manual turret fires ONLY while no primary is in range (approach phase,
            # post-shock-blast reset, or booster pushback). The instant any primary
            # comes into range — cooldown state irrelevant — primaries take over and
            # manual turrets go inert. A ship with zero primaries uses its manual
            # turrets for the whole fight (subject to the turret's own range gate).
            for _attacker, _target in ((c1, c2), (c2, c1)):
                if any(current_distance <= _pw.range_m for _pw in _attacker.effective_primaries):
                    continue  # primary-phase: manual turrets inert
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
            _eval_emergency_system(c1, tick, events, _emerg_invuln_ms)
            _eval_emergency_system(c2, tick, events, _emerg_invuln_ms)

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
                    _sb_new_dist = _shock_blast_apply(_sb_att, current_distance, _starting_dist)
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
                winner_side = None
                ticks_elapsed = tick + 1
            elif c2_dead:
                # c2 hull depleted → c1 (challenger/side-1) wins
                outcome, reason = "win", "hp_depleted"
                winner_name, loser_name = c1.name, c2.name
                winner_side = 1
                ticks_elapsed = tick + 1
            elif c1_dead:
                # c1 hull depleted → c2 (target/criminal/side-2) wins
                outcome, reason = "win", "hp_depleted"
                winner_name, loser_name = c2.name, c1.name
                winner_side = 2
                ticks_elapsed = tick + 1
            elif is_last_tick:
                outcome, reason = "stalemate", "time_cap"
                winner_name, loser_name = None, None
                winner_side = None
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
            winner_side=winner_side,
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
        # P2-T8b: use winner_side (death-branch keyed, unambiguous even for same-name ships)
        # rather than winner_name comparison (would mis-assign ttk when c1.name == c2.name).
        c1_ttk: float | None = None
        c2_ttk: float | None = None
        if not is_stalemate:
            if winner_side == 1:
                c2_ttk = duration_s  # c1 won → c2 died
            elif winner_side == 2:
                c1_ttk = duration_s  # c2 won → c1 died

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
            # P2-T0b: populated from death-branch c1_dead/c2_dead logic, NOT from winner_name.
            winner_side=winner_side,
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
# Key-event extraction helpers (moved from combat_log_service)
# ---------------------------------------------------------------------------

# Tick duration used when persisting (10 ms per tick — from GameConstants default).
# Key-event time conversion uses this value.
_TICK_MS: int = 10

# HP layer labels (used for layer_depleted event detail)
# CI-15: "hull" added to match the new layer_depleted/hull event emitted by _apply_damage.
_LAYER_LABELS: dict[str, str] = {
    "shield": "Shield depleted",
    "armour": "Armour depleted",
    "hull": "Hull depleted (dead)",
}


def _ticks_to_seconds(tick: int, tick_ms: int = _TICK_MS) -> float:
    """Convert a tick number to elapsed seconds."""
    return round(tick * tick_ms / 1000, 3)


def _extract_key_events(
    timeline: list[dict],
    tick_ms: int = _TICK_MS,
    combatants_map: dict | None = None,
) -> list[dict]:
    """Condense the full timeline into a curated highlight reel.

    Design: DESIGN_COMBAT_LOG_RECAP.md (extract-only; no resolver/tick-loop change).

    The recap is keyed on the moments that change a fight, NOT on every shot:

      * Weapon range-in beats (R1/R2): the first time each weapon enters firing
        range, and each time it re-enters range after a displacement
        (shock-blast / booster push) knocked it out and closure brought it back.
        Multi-range loadouts therefore "spool up" in range order as the gap
        closes (and again after each displacement). Detection is purely from the
        stored timeline and uses the weapon's firing cadence: a ready weapon fires
        every reload-interval while in range (closure only shrinks distance), so a
        fire separated from the previous same-weapon fire by a gap materially
        larger than that cadence marks a re-acquire after a displacement pushed it
        out. No resolver change. Nukes and shock-blasts are excluded here (they get
        per-fire lines below). Duplicate-named instances collapse to one line/tick.

      * Per-fire beats: every nuke detonation (with opp/self damage) and every
        shock-blast use (with the reset distance).

      * Effect beats: layer breaks (incl. genuine re-breaks — the engine already
        gates re-emission to >=25% recovery) and 50%/25% HP milestones, each
        attributed to the weapon that dealt the most damage to the target that
        tick. Same-tick kill collapse: when a target dies, its break/milestone
        lines on the kill tick are dropped in favour of the single outcome line.

      * Module activations (cloak/booster/emergency_system), ammo-out lines, and
        the outcome (winner + killing weapon, or a stalemate why-line).

    Lines are ordered by (tick, causal-sub-order) so within a tick the order is
    weapon actions -> effects -> module activations -> ammo-out -> outcome.

    Output rows match api.schemas.combat_log_schema.KeyEvent: tick, time_s, actor,
    event_type, detail.  combatants_map is data.summary.combatants ({"1": {...}})
    and is used for side->name resolution and the stalemate why-line.
    """
    cmap = combatants_map or {}

    def _label_for_side(side_val) -> str | None:
        if side_val is None:
            return None
        return cmap.get(str(side_val), {}).get("name")

    def _actor_label(actor: str | None, data: dict) -> str:
        return _label_for_side(data.get("side")) or actor or "?"

    # ---- pass 1: distance track, per-(side,weapon) fires, shock resets, attribution, start HP ----
    # Keyed by (side, weapon) — NOT weapon name alone. When both combatants carry the same-named
    # weapon, keying on the name merges their fire ticks into one interleaved list, which collapses
    # the per-ship cadence (min inter-fire gap) to the tiny inter-combatant offset and floods the
    # re-enter detector with false positives. Per-(side, weapon) preserves each ship's own cadence.
    fires: dict[tuple, list[dict]] = {}
    shock_reset: dict[tuple[int, str], float] = {}
    start_total: dict[str, int] = {}
    attrib: dict[tuple[int, str], str] = {}
    attrib_best: dict[tuple[int, str], float] = {}

    for ev in timeline:
        data = ev.get("data", {}) or {}
        typ = ev.get("type", "")
        tick = int(ev.get("tick", 0))
        if typ == "fight_start":
            for i, cb in enumerate(data.get("combatants", [])):
                hp = cb.get("hp", {})
                start_total[str(i + 1)] = hp.get("hull", 0) + hp.get("armour", 0) + hp.get("shield", 0)
        elif typ == "weapon_fire":
            w = data.get("weapon", "?")
            fires.setdefault((data.get("side"), w), []).append({"tick": tick, "side": data.get("side"), "data": data})
        elif typ == "distance":
            if data.get("cause") == "shock_blast":
                shock_reset[(tick, str(data.get("side")))] = data.get("to")
        elif typ == "damage":
            # Attribution: weapon that removed the most HP from the target this tick.
            # Guard absorbed > 0 (sentinel 0): EmergencySystem-invuln damage rows carry
            # absorbed=0 and must NOT win attribution for the resulting break/milestone.
            absorbed = data.get("absorbed", 0) or 0
            tside = str(data.get("side")) if data.get("side") is not None else None
            wpn = (data.get("source", {}) or {}).get("weapon")
            if tside and wpn and absorbed > attrib_best.get((tick, tside), 0):
                attrib_best[(tick, tside)] = absorbed
                attrib[(tick, tside)] = wpn

    # (tick, sub_order, row) — sub_order makes intra-tick order causal
    staged: list[tuple[int, int, dict]] = []

    def _emit(
        tick: int,
        k: int,
        event_type: str,
        detail: str,
        actor: str | None = None,
        _collapse_key: tuple | None = None,
    ) -> None:
        row: dict = {
            "tick": tick,
            "time_s": _ticks_to_seconds(tick, tick_ms),
            "actor": actor,
            "event_type": event_type,
            "detail": detail,
        }
        if _collapse_key is not None:
            row["_collapse_key"] = _collapse_key
        staged.append((tick, k, row))

    # ---- Engagement (k=0) ----
    fs = next((e for e in timeline if e.get("type") == "fight_start"), None)
    if fs:
        cd = (fs.get("data", {}) or {}).get("combatants", [])
        if len(cd) >= 2:
            a, b = cd[0], cd[1]
            al = a.get("display_name") or a.get("name", "?")
            bl = b.get("display_name") or b.get("name", "?")
            dist_m = int((fs.get("data", {}) or {}).get("initial_distance", 0))
            _emit(
                0,
                0,
                "Engagement",
                f"Engagement: {al} ({a.get('ship', '?')}) vs {bl} ({b.get('ship', '?')}) — {dist_m}m",
            )

    # ---- Weapon range-in beats R1/R2 (k=1); skip nuke + shock-blast ----
    def _hitstr(d: dict) -> str:
        if d.get("subtype") == "cluster-missile":
            h, f = d.get("hits", 0), d.get("fired", 0)
            return f"{h}/{f} hit" if h else "miss"
        return "hit" if d.get("hit") else "miss"

    for (_fside, w), flist in fires.items():
        if flist[0]["data"].get("subtype") in ("nuke", "shock-blast"):
            continue
        # Collapse duplicate-named instances to one entry per tick (prefer a hit for display).
        by_tick: dict[int, dict] = {}
        for f in flist:
            t = f["tick"]
            cur = by_tick.get(t)
            hitful = bool(f["data"].get("hit") or f["data"].get("hits"))
            cur_hitful = bool(cur and (cur["data"].get("hit") or cur["data"].get("hits")))
            if cur is None or (hitful and not cur_hitful):
                by_tick[t] = f
        fire_ticks = sorted(by_tick)
        # Re-enter detection via firing CADENCE, not a distance proxy. A ready weapon fires
        # every `cadence` ticks (its reload) for as long as it is in range; closure only ever
        # shrinks distance, so the sole cause of a gap materially larger than cadence is the
        # weapon having been pushed out of range (shock-blast/booster) and re-acquiring. The
        # first fire after such a gap is a re-enter. This needs no range estimate and so cannot
        # be fooled by it — a long-range weapon firing straight through a reset keeps gap ==
        # cadence and is correctly NOT flagged.
        diffs = [b - a for a, b in itertools.pairwise(fire_ticks)]
        cadence = min(diffs) if diffs else None
        prev_tick: int | None = None
        for t in fire_ticks:
            f = by_tick[t]
            acquire = prev_tick is None
            if prev_tick is not None and cadence is not None and (t - prev_tick) > 1.5 * cadence:
                acquire = True
            if acquire:
                lbl = _label_for_side(f["side"]) or "?"
                phrase = "enters range" if prev_tick is None else "re-enters range"
                _emit(
                    t,
                    1,
                    "Weapon in range",
                    f"{lbl}'s {w} {phrase} — {_hitstr(f['data'])}",
                    actor=lbl,
                    _collapse_key=("Weapon in range", lbl, w, None),
                )
            prev_tick = t

    # ---- Per-fire beats: nuke + shock-blast (k=1) ----
    # Collect nuke fires grouped by (actor_label, weapon).  One Key-Event row is
    # emitted per detonation; significance folding happens downstream in
    # build_recap_sections().  Each entry: {"tick", "lbl", "w", "opp", "self"}.
    _nuke_fires: dict[tuple[str, str], list[dict]] = {}
    _shock_fires: list[dict] = []
    for ev in timeline:
        if ev.get("type") != "weapon_fire":
            continue
        d = ev.get("data", {}) or {}
        sub = d.get("subtype")
        tick = int(ev.get("tick", 0))
        lbl = _actor_label(ev.get("actor"), d)
        w = d.get("weapon", "?")
        if sub == "nuke":
            _nuke_fires.setdefault((lbl, w), []).append(
                {
                    "tick": tick,
                    "lbl": lbl,
                    "w": w,
                    "opp": int(d.get("opponent_damage", 0) or 0),
                    "self": int(d.get("self_damage", 0) or 0),
                }
            )
        elif sub == "shock-blast":
            to = shock_reset.get((tick, str(d.get("side"))), GameConstants.STARTING_DISTANCE_M)
            _shock_fires.append({"tick": tick, "lbl": lbl, "w": w, "to": to})

    # Emit one raw row per nuke detonation (v3 contract: the extractor never
    # collapses).  Significance filtering — keeping high-impact detonations as
    # individual Key Events while folding a run of low-impact ones into a single
    # Recurring bullet — is owned by build_recap_sections() in combat_recap.py,
    # which has the full per-occurrence set and the presentation context.
    for (lbl, w), fires in _nuke_fires.items():
        for f in fires:
            _emit(
                f["tick"],
                1,
                "Nuke detonation",
                f"{lbl} fired {w} — detonated (opp: {f['opp']}, self: {f['self']})",
                actor=lbl,
            )

    # Shock-blast: count-based treatment if ≥ RECAP_NUKE_SUMMARY_MIN_COUNT fires per (actor, weapon).
    _shock_min_count = GameConstants.RECAP_NUKE_SUMMARY_MIN_COUNT
    _shock_by_key: dict[tuple[str, str], list[dict]] = {}
    for sf in _shock_fires:
        _shock_by_key.setdefault((sf["lbl"], sf["w"]), []).append(sf)

    for (lbl, w), slist in _shock_by_key.items():
        if len(slist) < _shock_min_count:
            for sf in slist:
                _emit(
                    sf["tick"],
                    1,
                    "Shock blast",
                    f"{lbl} fired {sf['w']} — distance reset to {int(sf['to'])}m",
                    actor=lbl,
                )
        else:
            # Summarise: keep first individual (shows the reset distance for context), fold the rest.
            first = slist[0]
            _emit(
                first["tick"],
                1,
                "Shock blast",
                f"{lbl} fired {first['w']} — distance reset to {int(first['to'])}m",
                actor=lbl,
            )
            rest = slist[1:]
            if rest:
                rest_tick = rest[0]["tick"]
                _emit(
                    rest_tick,
                    1,
                    "Shock blast",
                    f"{lbl} fired {w} ×{len(rest)} (additional resets)",
                    actor=lbl,
                )

    # ---- Resolve kill (for same-tick collapse + outcome attribution) ----
    fe = next((e for e in timeline if e.get("type") == "fight_end"), None)
    kill_tick: int | None = None
    loser_slot: str | None = None
    if fe:
        fd = fe.get("data", {}) or {}
        fh = fd.get("final_hp", {})
        c1h = fh.get("c1", {}).get("hull", 1)
        c2h = fh.get("c2", {}).get("hull", 1)
        if fd.get("winner") is not None:
            if c1h <= 0 and c2h > 0:
                loser_slot, kill_tick = "1", int(fe.get("tick", 0))
            elif c2h <= 0 and c1h > 0:
                loser_slot, kill_tick = "2", int(fe.get("tick", 0))

    # ---- Effect beats: layer breaks (k=2), HP milestones (k=2), modules (k=3), ammo-out (k=4) ----
    milestone_fired: dict[str, set] = {"1": set(), "2": set()}
    for ev in timeline:
        typ = ev.get("type", "")
        d = ev.get("data", {}) or {}
        tick = int(ev.get("tick", 0))
        side = str(d.get("side")) if d.get("side") is not None else None
        if typ == "layer_depleted":
            layer = d.get("layer", "")
            if layer == "hull":
                continue  # the kill is rendered by the outcome line
            if kill_tick is not None and tick == kill_tick and side == loser_slot:
                continue  # collapse into kill
            tgt = _label_for_side(d.get("side")) or ev.get("actor") or "?"
            by = attrib.get((tick, side)) if side else None
            tag = f" (by {by})" if by else ""
            _emit(
                tick,
                2,
                "Layer depleted",
                f"{tgt}: {_LAYER_LABELS.get(layer, layer)}{tag}",
                actor=ev.get("actor"),
                _collapse_key=("Layer depleted", side, layer, None),
            )
        elif typ == "damage" and side and start_total.get(side):
            hp = d.get("hp_after", {})
            total = hp.get("hull", 0) + hp.get("armour", 0) + hp.get("shield", 0)
            pct = total / start_total[side]
            for milestone in (50, 25):
                if milestone in milestone_fired[side] or pct > milestone / 100:
                    continue
                milestone_fired[side].add(milestone)
                if kill_tick is not None and tick == kill_tick and side == loser_slot:
                    continue  # collapse into kill
                lbl = _label_for_side(d.get("side")) or side
                by = attrib.get((tick, side))
                tag = f" (by {by})" if by else ""
                # k=3 so HP milestones sort AFTER Layer depleted (k=2) on the same tick.
                # This preserves the approved recap ordering: layer breaks before HP milestones.
                _emit(tick, 3, f"HP milestone ({milestone}%)", f"{lbl} dropped to ≤{milestone}% HP{tag}")
        elif typ == "module_activation":
            lbl = _actor_label(ev.get("actor"), d)
            module_name = d.get("module", d.get("name", "module"))
            # Thread-5 (BALANCE_JOURNAL §E): annotate WHY the module activated so the chained
            # activations read clearly in both the detailed log and the user-facing summary.
            # Distinct "trigger" markers from the emergency-system chain vs the HP-threshold path:
            #   trigger=="emergency_activate"  → booster chained off ES activating
            #   trigger=="emergency_end"       → cloak chained off ES ending
            #   else falls back to the trigger_hp_pct HP-threshold crossing.
            _trigger = d.get("trigger")
            if _trigger == "emergency_activate":
                _why = " (emergency system activated)"
            elif _trigger == "emergency_end":
                _why = " (emergency system ended)"
            elif d.get("trigger_hp_pct") is not None:
                _why = f" (at {d.get('trigger_hp_pct')}% HP)"
            else:
                _why = ""
            _emit(
                tick,
                3,
                "Module activated",
                f"{lbl} activated {module_name}{_why}",
                actor=ev.get("actor"),
                _collapse_key=("Module activated", lbl, module_name, None),
            )
        elif typ == "secondary_depleted":
            lbl = _actor_label(ev.get("actor"), d)
            _emit(tick, 4, "Ammo depleted", f"{lbl} out of {d.get('weapon', '?')}", actor=ev.get("actor"))

    # ---- Outcome / stalemate why-line (k=9) ----
    if fe:
        fd = fe.get("data", {}) or {}
        winner = fd.get("winner")
        dur_s = _ticks_to_seconds(int(fd.get("duration_ticks", 0)), tick_ms)
        ftick = int(fe.get("tick", 0))
        if winner is None:
            # Two stalemate reasons (§9): "mutual" — both hulls hit 0 on the same tick (a true
            # double-KO) — and "time_cap" — the clock expired with both still alive. Neither
            # warrants the old per-combatant "couldn't out-damage their regen" framing: it was
            # wordy AND presumptuous (regen is only one of several reasons a fight runs the clock
            # — high effective HP, evasion, or a simply tanky opponent do too). Per-combatant
            # damage/hit stats already live in the Summary field, so the Outcome line just states
            # the headline reason. Both variants stay well under the gateway's per-line clamp.
            if fd.get("reason") == "mutual":
                detail = f"Stalemate ({dur_s:.0f}s) — mutual destruction; both ships destroyed."
            else:
                detail = f"Stalemate ({dur_s:.0f}s) — neither side could score a fatal blow in the time allotted."
            _emit(ftick, 9, "Outcome", detail)
        elif loser_slot is not None:
            winner_slot = "2" if loser_slot == "1" else "1"
            winner_label = cmap.get(winner_slot, {}).get("name", winner)
            loser_label = cmap.get(loser_slot, {}).get("name", "opponent")
            kby = attrib.get((kill_tick, loser_slot)) if kill_tick is not None else None
            ktag = f" by {kby}" if kby else ""
            _emit(ftick, 9, "Outcome", f"{winner_label} wins — {loser_label} destroyed{ktag} ({dur_s:.1f}s)")
        else:
            _emit(ftick, 9, "Outcome", f"{winner} wins ({dur_s:.1f}s)")

    staged.sort(key=lambda x: (x[0], x[1]))

    # Pass all rows through in (tick, k) order, stripping internal _collapse_key.
    # The old Rule-1 global-aggregation fold has been removed: build_recap_sections()
    # in combat_recap.py now owns the presentation logic (chronological Key Events +
    # Recurring bullets with per-occurrence qualifiers).  _extract_key_events returns
    # one raw row per occurrence so that the recap builder has full information.
    return [{k: v for k, v in row.items() if k != "_collapse_key"} for _, _, row in staged]
