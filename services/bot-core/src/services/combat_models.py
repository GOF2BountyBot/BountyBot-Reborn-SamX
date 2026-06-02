"""
Combat data models for BountyBot.

Defines the data structures used by the combat system:
- Input types: WeaponStats, ModuleStats, UpgradeStats, ShipLoadout
- Intermediate types: CombatStats
- Output types: FightStats, FightResults
- Protocol: CombatResolver (swappable resolution strategy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Protocol

# ---------------------------------------------------------------------------
# Input data structures — assembled by callers from DB models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeaponStats:
    """Stats for a single weapon (primary, secondary, or turret).

    Attributes:
        name: Weapon display name.
        dps: Damage per second (flat value from weapon definition).

    Future extension fields (unused now, reserved for fire-rate combat):
        fire_rate: Shots per second (None = use averaged DPS model).
        damage_per_shot: Damage dealt per individual shot.

    T6 discriminator fields (D0): default zero/empty for backward compat.
        subtype: Secondary weapon subtype string (e.g. "rocket", "missile", "nuke").
                 Empty string for primaries/turrets.
        burst_count: Cluster-missile sub-munition count (D4). 0 for non-cluster.
        emp_damage: EMP damage value (phase-2+ deferred; baked for log fidelity). 0 if none.
        magnitude_m: Nuke blast radius seed value (D5). 0.0 if non-nuke.
        steerable: Nuke/missile steerable flag (data-only in Phase-1; no behaviour branch). False by default.
    """

    name: str
    dps: float
    # Tick-resolver fields (T5+): used by TickResolver for per-shot simulation
    fire_rate: float | None = None  # shots/sec — DPS-model concept; kept for legacy compat
    damage_per_shot: float | None = None  # physical damage per shot (§4/§6.1); None → 0 in resolver
    loading_speed_ms: int = 0  # cooldown in ms between shots (§1/§6.1); 0 = DPS-model only
    range_m: float = 0.0  # binary fire gate: fires when current_distance ≤ range_m (§2)
    # T6 discriminator fields (D0) — default zero/empty so legacy code paths are unaffected
    subtype: str = ""  # secondary subtype: "rocket"|"missile"|"cluster-missile"|"nuke"|"shock-blast"|...
    burst_count: int = 0  # cluster-missile sub-munition count (§6.2 D4); 0 for non-cluster
    emp_damage: int = 0  # EMP damage (baked for log fidelity; deferred to phase-2+)
    magnitude_m: float = 0.0  # nuke blast radius seed value (§6.2 D5); 0.0 for non-nukes
    steerable: bool = False  # steerable flag — data-only in Phase-1; no behaviour branch (§6.2 D5)


@dataclass(frozen=True, slots=True)
class ModuleStats:
    """Combat-relevant stats extracted from a module's extra_atts.

    All additive bonuses default to 0; all multipliers default to 1.0.
    This means a module with no combat stats has zero effect.

    Attributes:
        name: Module display name.
        armour: Flat armour HP bonus.
        armour_multiplier: Multiplicative armour scaling (stacks with others).
        shield: Flat shield HP bonus.
        shield_multiplier: Multiplicative shield scaling.
        dps: Flat DPS bonus (noted as "unused" in legacy, but summed).
        dps_multiplier: Multiplicative DPS scaling (stacks multiplicatively).

    Future extension fields:
        accuracy_modifier: Effect on owner's accuracy (e.g., scanner +0.1).
        evasion_modifier: Effect on owner's evasion (e.g., thruster +0.1).
        enemy_accuracy_modifier: Effect on enemy's accuracy (e.g., cloak -0.2).
        shield_recharge_rate: Shield HP recovered per second (for tick sim).
        repair_rate: Hull HP recovered per second (for repair bots).
    """

    name: str
    armour: int = 0
    armour_multiplier: float = 1.0
    shield: int = 0
    shield_multiplier: float = 1.0
    dps: int = 0
    dps_multiplier: float = 1.0
    # Future fields — unused in SimpleTTKResolver
    accuracy_modifier: float = 0.0
    evasion_modifier: float = 0.0
    enemy_accuracy_modifier: float = 0.0
    shield_recharge_ms: int = 0  # raw recharge time (ms); used by TickResolver §3 schedule
    shield_recharge_rate: float = 0.0
    repair_rate: float = 0.0
    # Tick-resolver fields (T5+): STI discriminator + PrimaryWeaponMod stats (§7.8/§10)
    module_type: str = ""  # STI discriminator from Item.type (e.g. "PrimaryWeaponModModule")
    damage_pct: int = 0  # PrimaryWeaponMod: per-shot damage modifier (§7.8); can be negative
    fire_rate_pct: int = 0  # PrimaryWeaponMod: fire-rate modifier (§7.8); positive = faster (lower cooldown)


@dataclass(frozen=True, slots=True)
class UpgradeStats:
    """Combat-relevant stats from a permanent ship upgrade.

    Only armour-related stats are used in combat currently.
    Upgrades cannot be removed once applied (strategic weight).

    Attributes:
        name: Upgrade display name.
        armour: Flat armour HP bonus.
        armour_multiplier: Multiplicative armour scaling.
    """

    name: str
    armour: int = 0
    armour_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class ShipLoadout:
    """Complete ship configuration for combat stat computation.

    Assembled by the caller from DB models (Ship, PlayerShip, etc.).
    This is the primary input to CombatService.collect_stats().

    Attributes:
        ship_name: Ship model name (e.g. "Betty").
        base_armour: Ship's intrinsic armour HP (from Ship.armour).
        weapons: Equipped primary weapons.
        turrets: Equipped turret weapons.
        modules: Equipped modules with combat stats extracted.
        upgrades: Permanently applied ship upgrades.

    Future extension fields:
        base_accuracy: Ship's intrinsic accuracy (1.0 = perfect aim).
        base_evasion: Ship's intrinsic evasion chance (0.0 = no evasion).
        base_handling: Ship's handling stat (may feed into evasion).
    """

    ship_name: str
    base_armour: int
    manual_turret_mode: bool = False
    weapons: list[WeaponStats] = field(default_factory=list)
    turrets: list[WeaponStats] = field(default_factory=list)
    modules: list[ModuleStats] = field(default_factory=list)
    upgrades: list[UpgradeStats] = field(default_factory=list)
    # T6 (D0): secondary weapons — runtime home for secondaries consumed by TickResolver
    secondary_weapons: list[WeaponStats] = field(default_factory=list)
    # Future fields
    base_accuracy: float = 1.0
    base_evasion: float = 0.0
    base_handling: int = 0


# ---------------------------------------------------------------------------
# Intermediate — computed combat statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CombatStats:
    """Computed combat statistics for a single ship.

    Produced by CombatService.collect_stats() from a ShipLoadout.
    Consumed by CombatResolver.resolve() to determine fight outcome.

    Attributes:
        ship_name: Ship model name (carried through for result reporting).
        dps: Total effective DPS (weapons + turrets + module flat, x multipliers).
        armour: Total effective armour HP (base + modules + upgrades, x multipliers).
        shield: Total effective shield HP (modules only, x multipliers).
        total_hp: armour + shield (convenience property computed at creation).

    Future extension fields:
        accuracy: Effective hit chance after all modifiers (0.0-1.0+).
        evasion: Effective dodge chance after all modifiers.
        weapon_profiles: Per-weapon breakdown for fire-rate simulation.
    """

    ship_name: str
    dps: float
    armour: int
    shield: int
    total_hp: int
    # Future fields
    accuracy: float = 1.0
    evasion: float = 0.0


# ---------------------------------------------------------------------------
# Output — fight results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FightStats:
    """Per-ship combat statistics from a resolved fight.

    Contains both raw (pre-variance) and varied (post-variance) values,
    plus the computed time-to-kill.

    Attributes:
        ship_name: Ship model name.
        raw_hp: Total HP before variance.
        raw_dps: Total DPS before variance.
        varied_hp: HP after variance roll.
        varied_dps: DPS after variance roll.
        ttk: Time-to-kill in seconds (how long this ship survives).
              None if opponent has zero DPS (ship survives indefinitely).
    """

    ship_name: str
    raw_hp: int
    raw_dps: float
    varied_hp: int
    varied_dps: float
    ttk: float | None


@dataclass(frozen=True, slots=True)
class FightResults:
    """Complete results of a ship-vs-ship combat resolution.

    Attributes:
        winner_name: Name of the winning ship, or None on stalemate.
        loser_name: Name of the losing ship, or None on stalemate.
        is_stalemate: True if neither ship can defeat the other.
        ship1_stats: Detailed combat stats for the first ship.
        ship2_stats: Detailed combat stats for the second ship.
        variance_percent: The variance percentage that was applied.

    Future extension fields:
        combat_log: Ordered list of combat events (for tick-based sim).
        metadata: Arbitrary key-value data for extensibility.
    """

    winner_name: str | None
    loser_name: str | None
    is_stalemate: bool
    ship1_stats: FightStats
    ship2_stats: FightStats
    variance_percent: float
    # Future fields
    combat_log: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Combat event — one timeline row (§12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CombatEvent:
    """One tick-timeline entry in a fight's combat log (§12).

    type is an open str — use CombatEventType constants at emit sites.
    actor / target are combatant display names; None for global events.
    """

    tick: int
    type: str
    actor: str | None
    target: str | None
    data: dict[str, Any] = field(default_factory=dict)


class CombatEventType:
    """Event-type string constants for CombatEvent.type (§12 vocabulary table).

    The field stays open (str) for extensibility; these constants are
    documentation + reusable identifiers for emit-site code in T3+.
    """

    fight_start: Final[str] = "fight_start"
    fight_end: Final[str] = "fight_end"
    regen: Final[str] = "regen"
    weapon_fire: Final[str] = "weapon_fire"
    damage: Final[str] = "damage"
    module_activation: Final[str] = "module_activation"
    cooldown_end: Final[str] = "cooldown_end"
    layer_depleted: Final[str] = "layer_depleted"
    distance: Final[str] = "distance"


# ---------------------------------------------------------------------------
# Protocol — swappable combat resolution strategy
# ---------------------------------------------------------------------------


class CombatResolver(Protocol):
    """Protocol for combat resolution strategies.

    The combat service delegates the actual fight computation to an
    implementation of this protocol. This allows swapping from the
    simple TTK model to a tick-based simulation without changing
    the stat collection or result handling code.

    Current implementation: SimpleTTKResolver (in combat_service.py)
    Future implementation: TickBasedSimulator (not yet built)
    """

    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults:
        """Resolve combat between two ships.

        Args:
            ship1_stats: Pre-computed combat stats for ship 1.
            ship2_stats: Pre-computed combat stats for ship 2.
            variance_percent: Random variance to apply (0.0-1.0).
                              0.0 = deterministic; 0.05 = ±5%.

        Returns:
            FightResults with winner, loser, detailed stats, and
            stalemate flag.
        """
        ...  # pylint: disable=unnecessary-ellipsis
