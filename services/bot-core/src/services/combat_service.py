"""
Combat Service for BountyBot.

Implements the legacy-compatible DPS-vs-HP combat model with a
future-proof architecture that separates stat collection from
combat resolution.

Stat collection (get_dps, get_armour, get_shield, collect_stats)
follows the legacy formulas documented in docs/analysis/03b-combat-system.md.

Combat resolution is delegated to a CombatResolver implementation.
The default SimpleTTKResolver implements the single-shot analytical
TTK comparison from the legacy system.
"""

import math
import random
from dataclasses import dataclass

from shared import bblogger

from services.combat_models import (
    CombatEvent,
    CombatEventType,
    CombatResolver,
    CombatStats,
    FightResults,
    FightStats,
    ShipLoadout,
)
from services.game_constants import GameConstants, resolve_constant

flogger = bblogger.get_logger(__name__)

# ---------------------------------------------------------------------------
# TickResolver — tick-based combat simulation (T3 skeleton)
# ---------------------------------------------------------------------------

# Ketar Repair Bot module name constants (used for rate detection in _init_combatant)
_KETAR_II_NAME = "Ketar Repair Bot II"
_KETAR_I_NAME = "Ketar Repair Bot I"


@dataclass
class _CombatantState:
    """Per-side mutable runtime state for TickResolver. Not frozen — mutated every tick."""

    name: str
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
    # Carry-forward state for T7 / T9
    manual_turret_mode: bool
    emergency_system_consumed: bool


def _init_combatant(loadout: ShipLoadout, *, is_player: bool) -> _CombatantState:
    """Build combatant runtime state from a ShipLoadout.

    Called once before the tick loop begins (§1 implementation note).
    All weapons enter at cooldown_remaining = 0; all HP layers start at max;
    all regen accumulators are dormant (layers at max).
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
    weapon_cooldowns = {w.name: 0 for w in loadout.weapons + loadout.turrets}
    module_cooldowns = {m.name: 0 for m in loadout.modules}

    return _CombatantState(
        name=loadout.ship_name,
        loadout=loadout,
        is_player=is_player,
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
                        data={"layer": "shield", "amount": 1, "hp_after": state.current_shield},
                    )
                )

    # Discard partial accumulation when shield returns to max
    if state.current_shield >= state.max_shield:
        state.shield_regen_accumulators = [0] * len(state.shield_regen_schedules)


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
                    data={"layer": "hull", "amount": hull_add, "hp_after": state.current_hull},
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
                        data={"layer": "armour", "amount": armour_add, "hp_after": state.current_armour},
                    )
                )

    # Discard partial when both layers are back at max
    if state.current_hull >= state.max_hull and state.current_armour >= state.max_armour:
        state.repair_bot_regen_accumulator = 0.0


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
    """
    # Step (i): PvC DR — first modifier, before stacking (§3 / Appendix B step 4i)
    if state.is_player and pvc_damage_reduction > 0.0:
        applied: int = round(raw_damage * (1.0 - pvc_damage_reduction))
    else:
        applied = round(raw_damage)

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

    events.append(
        CombatEvent(
            tick=tick,
            type=CombatEventType.damage,
            actor=None,
            target=state.name,
            data={
                "amount": applied,
                "breakdown": {"shield": shield_taken, "armour": armour_taken, "hull": hull_taken},
                "hp_after": {
                    "shield": state.current_shield,
                    "armour": state.current_armour,
                    "hull": state.current_hull,
                },
                "source": source,
            },
        )
    )

    # layer_depleted — shield first, then armour (hull depletion → termination at step 8)
    if shield_was_positive and state.current_shield <= 0:
        events.append(
            CombatEvent(
                tick=tick,
                type=CombatEventType.layer_depleted,
                actor=state.name,
                target=None,
                data={"layer": "shield"},
            )
        )
    if armour_was_positive and state.current_armour <= 0:
        events.append(
            CombatEvent(
                tick=tick,
                type=CombatEventType.layer_depleted,
                actor=state.name,
                target=None,
                data={"layer": "armour"},
            )
        )


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
    ) -> FightResults:
        """Run a full tick-based fight between two ShipLoadouts.

        Args:
            loadout1: C1 — challenger (player in PvC when pvc_damage_reduction > 0).
            loadout2: C2 — opponent (NPC in PvC; player2 in PvP).
            pvc_damage_reduction: Keith T. Maxwell DR (§3). 0.33 for PvC, 0.0 for PvP.
            guild_config: Reserved for per-guild constant overrides (T10+).

        Returns:
            FightResults with combat_log timeline and metadata block.
        """
        # --- Pre-loop bake: read constants once, not per-tick ---
        tick_ms = GameConstants.TICK_MS
        max_ticks = GameConstants.MAX_FIGHT_TICKS
        min_dist = float(GameConstants.MIN_DISTANCE_M)
        distance_delta = GameConstants.BASE_SHIP_SPEED_MPS * 2 * (tick_ms / 1000)

        # --- Combatant init (§1: separate from tick loop) ---
        c1 = _init_combatant(loadout1, is_player=(pvc_damage_reduction > 0.0))
        c2 = _init_combatant(loadout2, is_player=False)

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
                            "name": c1.name,
                            "ship": c1.loadout.ship_name,
                            "hp": {"shield": c1.current_shield, "armour": c1.current_armour, "hull": c1.current_hull},
                        },
                        {
                            "name": c2.name,
                            "ship": c2.loadout.ship_name,
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
            # Phase 1: Decrement cooldowns (C1 then C2; floor at 0)
            # ------------------------------------------------------------------
            for w in c1.weapon_cooldowns:
                c1.weapon_cooldowns[w] = max(0, c1.weapon_cooldowns[w] - tick_ms)
            for m in c1.module_cooldowns:
                c1.module_cooldowns[m] = max(0, c1.module_cooldowns[m] - tick_ms)
            for w in c2.weapon_cooldowns:
                c2.weapon_cooldowns[w] = max(0, c2.weapon_cooldowns[w] - tick_ms)
            for m in c2.module_cooldowns:
                c2.module_cooldowns[m] = max(0, c2.module_cooldowns[m] - tick_ms)

            # ------------------------------------------------------------------
            # Phase 2: Apply regen pulses (C1 then C2; shield + repair bot parallel)
            # ------------------------------------------------------------------
            _tick_shield_regen(c1, tick, events)
            _tick_repair_bot_regen(c1, tick, events)
            _tick_shield_regen(c2, tick, events)
            _tick_repair_bot_regen(c2, tick, events)

            # ------------------------------------------------------------------
            # Phase 3: Evaluate weapon firings — T5: weapon firing evaluation
            # ------------------------------------------------------------------
            # (no-op T3 — no weapons fire; hits list is empty)

            # ------------------------------------------------------------------
            # Phase 4: Apply damage — no hits queued in T3
            # ------------------------------------------------------------------
            # (no-op T3 — damage helper exists and is unit-tested directly)

            # ------------------------------------------------------------------
            # Phase 4a: EmergencySystem evaluation — T9: EmergencySystem evaluation
            # ------------------------------------------------------------------
            # (no-op T3)

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
            # ------------------------------------------------------------------
            # (no-op T3)

            # ------------------------------------------------------------------
            # Phase 6: Update distance — passive closure (§2); Appendix B
            # ------------------------------------------------------------------
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

        # Legacy FightStats — required by FightResults dataclass; tick resolver uses raw HP (no variance)
        total_hp1 = c1.max_shield + c1.max_armour + c1.max_hull
        total_hp2 = c2.max_shield + c2.max_armour + c2.max_hull
        ship1_stats = FightStats(
            ship_name=c1.name,
            raw_hp=total_hp1,
            raw_dps=0.0,
            varied_hp=total_hp1,
            varied_dps=0.0,
            ttk=None,
        )
        ship2_stats = FightStats(
            ship_name=c2.name,
            raw_hp=total_hp2,
            raw_dps=0.0,
            varied_hp=total_hp2,
            varied_dps=0.0,
            ttk=None,
        )

        return FightResults(
            winner_name=winner_name,
            loser_name=loser_name,
            is_stalemate=is_stalemate,
            ship1_stats=ship1_stats,
            ship2_stats=ship2_stats,
            variance_percent=0.0,
            combat_log=events,  # type: ignore[arg-type]  — stores CombatEvent, annotation is list[dict]
            metadata={
                "tick_ms": tick_ms,
                "total_ticks": ticks_elapsed,
                "resolver": "tick_v1",
                "pvc_damage_reduction": pvc_damage_reduction,
            },
        )


# ---------------------------------------------------------------------------
# SimpleTTKResolver — Legacy-compatible combat resolution
# ---------------------------------------------------------------------------


class SimpleTTKResolver:
    """Single-shot analytical TTK combat resolver.

    Implements the legacy fightShips() algorithm:
    1. Apply uniform random variance to HP and DPS (4 independent rolls)
    2. Calculate TTK = varied_HP / opponent_varied_DPS
    3. Longer-surviving ship wins
    4. Zero-DPS edge cases and stalemate handling

    This class satisfies the CombatResolver protocol.
    """

    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults:
        """Resolve combat using single-shot TTK comparison.

        Args:
            ship1_stats: Combat stats for ship 1 (initiator).
            ship2_stats: Combat stats for ship 2 (receiver).
            variance_percent: Symmetric variance range (e.g. 0.05 = +/-5%).

        Returns:
            FightResults with winner determined by longest TTK.
        """
        flogger.debug(
            f"Combat resolution initiated: {ship1_stats.ship_name} (hp={ship1_stats.total_hp}, "
            f"dps={ship1_stats.dps:.1f}) vs {ship2_stats.ship_name} (hp={ship2_stats.total_hp}, "
            f"dps={ship2_stats.dps:.1f}), variance={variance_percent * 100:.1f}%"
        )

        # 1. Apply variance to HP (2 rolls)
        ship1_hp_varied = _apply_variance(ship1_stats.total_hp, variance_percent)
        ship2_hp_varied = _apply_variance(ship2_stats.total_hp, variance_percent)

        # 2. Apply variance to DPS (2 rolls)
        ship1_dps_varied = _apply_variance_float(ship1_stats.dps, variance_percent)
        ship2_dps_varied = _apply_variance_float(ship2_stats.dps, variance_percent)

        flogger.debug(
            f"Variance applied: {ship1_stats.ship_name} hp={ship1_stats.total_hp}→{ship1_hp_varied}"
            f" dps={ship1_stats.dps:.1f}→{ship1_dps_varied:.1f};"
            f" {ship2_stats.ship_name} hp={ship2_stats.total_hp}→{ship2_hp_varied}"
            f" dps={ship2_stats.dps:.1f}→{ship2_dps_varied:.1f}"
        )

        flogger.trace(
            f"Variance calculation step: ship1_hp_varied={ship1_hp_varied}, ship2_hp_varied={ship2_hp_varied}"
        )
        flogger.trace(
            f"Variance calculation step: ship1_dps_varied={ship1_dps_varied:.1f}, "
            f"ship2_dps_varied={ship2_dps_varied:.1f}"
        )

        # 3. Handle zero-DPS edge cases
        ship1_ttk: float | None = None
        ship2_ttk: float | None = None

        both_zero = ship1_stats.dps == 0 and ship2_stats.dps == 0
        flogger.trace(
            f"Zero-DPS edge case check: ship1_dps={ship1_stats.dps}, ship2_dps={ship2_stats.dps}, both_zero={both_zero}"
        )

        if both_zero:
            # Neither ship can deal damage — stalemate
            flogger.info(
                f"Fight result: STALEMATE due to zero DPS for both {ship1_stats.ship_name} and {ship2_stats.ship_name}"
            )
            return FightResults(
                winner_name=None,
                loser_name=None,
                is_stalemate=True,
                ship1_stats=FightStats(
                    ship_name=ship1_stats.ship_name,
                    raw_hp=ship1_stats.total_hp,
                    raw_dps=ship1_stats.dps,
                    varied_hp=ship1_hp_varied,
                    varied_dps=ship1_dps_varied,
                    ttk=None,
                ),
                ship2_stats=FightStats(
                    ship_name=ship2_stats.ship_name,
                    raw_hp=ship2_stats.total_hp,
                    raw_dps=ship2_stats.dps,
                    varied_hp=ship2_hp_varied,
                    varied_dps=ship2_dps_varied,
                    ttk=None,
                ),
                variance_percent=variance_percent,
            )

        # Calculate TTK for each ship
        # ship1_ttk = how long ship1 survives ship2's fire
        ship1_ttk = ship1_hp_varied / ship2_dps_varied if ship2_dps_varied > 0 else None
        flogger.trace(f"TTK calculation: ship1_ttk = {ship1_hp_varied} / {ship2_dps_varied} = {ship1_ttk}")

        # ship2_ttk = how long ship2 survives ship1's fire
        ship2_ttk = ship2_hp_varied / ship1_dps_varied if ship1_dps_varied > 0 else None
        flogger.trace(f"TTK calculation: ship2_ttk = {ship2_hp_varied} / {ship1_dps_varied} = {ship2_ttk}")

        # 4. Determine winner (longest survivor wins)
        winner_name: str | None = None
        loser_name: str | None = None
        is_stalemate = False

        flogger.trace(f"Winner determination: comparing TTKs — ship1_ttk={ship1_ttk}, ship2_ttk={ship2_ttk}")

        if ship1_ttk is None and ship2_ttk is None:
            # Both survive indefinitely (shouldn't happen if both_zero handled above)
            is_stalemate = True
            flogger.debug("Both ships survive indefinitely — stalemate condition")
        elif ship1_ttk is None:
            # Ship1 survives indefinitely, ship2 doesn't → ship1 wins
            winner_name = ship1_stats.ship_name
            loser_name = ship2_stats.ship_name
            flogger.debug(f"{ship1_stats.ship_name} survives indefinitely vs {ship2_stats.ship_name}")
        elif ship2_ttk is None:
            # Ship2 survives indefinitely → ship2 wins
            winner_name = ship2_stats.ship_name
            loser_name = ship1_stats.ship_name
            flogger.debug(f"{ship2_stats.ship_name} survives indefinitely vs {ship1_stats.ship_name}")
        elif ship1_ttk > ship2_ttk:
            winner_name = ship1_stats.ship_name
            loser_name = ship2_stats.ship_name
            flogger.debug(f"{ship1_stats.ship_name} TTK {ship1_ttk:.2f} > {ship2_stats.ship_name} TTK {ship2_ttk:.2f}")
        elif ship2_ttk > ship1_ttk:
            winner_name = ship2_stats.ship_name
            loser_name = ship1_stats.ship_name
            flogger.debug(f"{ship2_stats.ship_name} TTK {ship2_ttk:.2f} > {ship1_stats.ship_name} TTK {ship1_ttk:.2f}")
        else:
            # Exact tie
            is_stalemate = True
            flogger.debug(f"Exact tie: both ships have TTK {ship1_ttk:.2f}")

        ttk1_str = f"{ship1_ttk:.2f}" if ship1_ttk is not None else "∞"
        ttk2_str = f"{ship2_ttk:.2f}" if ship2_ttk is not None else "∞"
        if is_stalemate:
            flogger.info(
                f"Fight result: STALEMATE between {ship1_stats.ship_name} and {ship2_stats.ship_name}"
                f" (ttk1={ttk1_str}, ttk2={ttk2_str})"
            )
        else:
            flogger.info(f"Fight result: winner={winner_name} loser={loser_name} ttk1={ttk1_str} ttk2={ttk2_str}")

        return FightResults(
            winner_name=winner_name,
            loser_name=loser_name,
            is_stalemate=is_stalemate,
            ship1_stats=FightStats(
                ship_name=ship1_stats.ship_name,
                raw_hp=ship1_stats.total_hp,
                raw_dps=ship1_stats.dps,
                varied_hp=ship1_hp_varied,
                varied_dps=ship1_dps_varied,
                ttk=ship1_ttk,
            ),
            ship2_stats=FightStats(
                ship_name=ship2_stats.ship_name,
                raw_hp=ship2_stats.total_hp,
                raw_dps=ship2_stats.dps,
                varied_hp=ship2_hp_varied,
                varied_dps=ship2_dps_varied,
                ttk=ship2_ttk,
            ),
            variance_percent=variance_percent,
        )


# ---------------------------------------------------------------------------
# Variance helpers (module-level, used by resolvers)
# ---------------------------------------------------------------------------


def _apply_variance(value: int, variance_percent: float) -> int:
    """Apply symmetric uniform random variance to an integer value.

    Matches legacy behavior: int-truncated range, inclusive randint.

    Args:
        value: Raw stat value.
        variance_percent: Fraction (e.g. 0.05 for +/-5%).

    Returns:
        Varied value as int. Returns 0 if value is 0.
    """
    if value == 0 or variance_percent == 0.0:
        flogger.trace(f"_apply_variance(int): value={value}, variance_percent={variance_percent}, no variance applied")
        return value
    delta = int(value * variance_percent)
    varied = random.randint(value - delta, value + delta)
    flogger.trace(
        f"_apply_variance(int): value={value}, variance_percent={variance_percent}, delta={delta}, result={varied}"
    )
    return varied


def _apply_variance_float(value: float, variance_percent: float) -> float:
    """Apply symmetric uniform random variance to a float value.

    Uses int-truncated range + randint to match legacy behavior where
    DPS variance uses random.randint on int-cast bounds.

    Args:
        value: Raw stat value.
        variance_percent: Fraction (e.g. 0.05 for +/-5%).

    Returns:
        Varied value as float. Returns 0.0 if value is 0.
    """
    if value == 0 or variance_percent == 0.0:
        flogger.trace(f"_apply_variance_float: value={value}, variance_percent={variance_percent}, no variance applied")
        return value
    low = int(value - value * variance_percent)
    high = int(value + value * variance_percent)
    flogger.trace(f"_apply_variance_float: value={value}, variance_percent={variance_percent}, low={low}, high={high}")
    if low > high:
        low, high = high, low
        flogger.trace(f"_apply_variance_float: bounds swapped — low={low}, high={high}")
    if low == high:
        flogger.trace(f"_apply_variance_float: low==high, returning {float(low)}")
        return float(low)
    varied = float(random.randint(low, high))
    flogger.trace(f"_apply_variance_float: random selection in [{low}, {high}] = {varied}")
    return varied


# ---------------------------------------------------------------------------
# CombatService — stat collection + fight orchestration
# ---------------------------------------------------------------------------


class CombatService:
    """Service for ship combat stat computation and fight resolution.

    Separates stat collection (deterministic formulas) from combat
    resolution (randomized fight). The resolver can be swapped to
    change the combat model without affecting stat computation.

    Usage:
        service = CombatService()
        stats1 = service.collect_stats(loadout1)
        stats2 = service.collect_stats(loadout2)
        result = service.fight_ships(loadout1, loadout2)
    """

    def __init__(self, resolver: CombatResolver | None = None) -> None:
        """Initialize CombatService with an optional custom resolver.

        Args:
            resolver: Combat resolution strategy. Defaults to
                      SimpleTTKResolver if not provided.
        """
        self._resolver: CombatResolver = resolver or SimpleTTKResolver()
        flogger.debug(f"CombatService initialized with resolver: {self._resolver.__class__.__name__}")

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
    # Fight Resolution
    # ------------------------------------------------------------------

    def fight_ships(
        self,
        loadout1: ShipLoadout,
        loadout2: ShipLoadout,
        variance_percent: float | None = None,
        player_armour_buff: float = 1.0,
        guild_config=None,
    ) -> FightResults:
        """Simulate a fight between two ship loadouts.

        Collects stats for both ships, then delegates to the configured
        CombatResolver for the actual fight computation.

        Args:
            loadout1: First ship (initiator / player in PvC combat).
            loadout2: Second ship (receiver / criminal in PvC combat).
            variance_percent: Random variance to apply. Defaults to
                              GameConstants.DUEL_VARIANCE_PERCENT.
            player_armour_buff: Multiplier applied to loadout1's armour before
                                combat resolution. 1.0 = no buff (default).
                                Used by PvC (bounty) combat to give the player
                                a +50% armour advantage over criminals.
                                Only armour is buffed — shield and DPS are
                                unaffected. Has no effect in PvP duels.

        Returns:
            FightResults with winner, loser, stats, and stalemate flag.
        """
        flogger.debug(f"fight_ships initiated: {loadout1.ship_name} vs {loadout2.ship_name}")
        if variance_percent is None:
            variance_percent = resolve_constant(
                guild_config, "duel_variance_percent", GameConstants.DUEL_VARIANCE_PERCENT
            )
            source = "per-guild override" if guild_config is not None else "GameConstants"
            flogger.debug(
                f"Variance percent not specified, using {source}.DUEL_VARIANCE_PERCENT={variance_percent * 100:.1f}%"
            )

        flogger.debug(f"Collecting combat stats for {loadout1.ship_name} (initiator)")
        stats1 = self.collect_stats(loadout1)

        # Apply optional armour buff to loadout1 (player in PvC combat).
        # Only armour is modified — shield and DPS are unchanged.
        if player_armour_buff != 1.0:
            buffed_armour = int(stats1.armour * player_armour_buff)
            flogger.debug(
                f"PvC armour buff applied to {loadout1.ship_name}: "
                f"armour {stats1.armour} → {buffed_armour} (×{player_armour_buff})"
            )
            stats1 = CombatStats(
                ship_name=stats1.ship_name,
                dps=stats1.dps,
                armour=buffed_armour,
                shield=stats1.shield,
                total_hp=buffed_armour + stats1.shield,
                accuracy=stats1.accuracy,
                evasion=stats1.evasion,
            )

        flogger.debug(f"Collecting combat stats for {loadout2.ship_name} (receiver)")
        stats2 = self.collect_stats(loadout2)

        flogger.debug(f"Delegating to resolver: {self._resolver.__class__.__name__}")
        result = self._resolver.resolve(stats1, stats2, variance_percent)
        flogger.debug(
            f"fight_ships completed: winner={result.winner_name}, "
            f"loser={result.loser_name}, stalemate={result.is_stalemate}"
        )
        return result
