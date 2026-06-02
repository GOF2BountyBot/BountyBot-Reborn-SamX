"""
Combat balance hooks for the Phase-1 tick-based resolver (§5).

SUBTYPE_ACCURACY_MOD: empty in Phase-1; future homing-vs-must-aim split slots here.
weapon_accuracy(): Phase-1 passthrough; the [0.05, 0.99] clamp lives in the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.combat_models import ShipLoadout, WeaponStats

# Per-weapon-subtype accuracy modifier map — empty until a subtype split is added.
SUBTYPE_ACCURACY_MOD: dict[str, float] = {}

# Combat scanner tier mapping by module name (§7.1 fixed inventory).
# Plasma scanners (separate ScannerModule subclass) are NOT listed here — they default to Tier A.
# Future scanner additions must be added here; flag as a candidate for a
# ModuleStats.combat_scanner_tier field refactor when the scanner inventory grows.
_SCANNER_TIER_BY_NAME: dict[str, str] = {
    "Telta Quickscan": "B",
    "Telta Ecoscan": "B",
    "Hiroto Proscan": "C",
    "Hiroto Ultrascan": "C",
}


@dataclass(frozen=True, slots=True)
class ScannerTier:
    """Scanner tier result for one combatant (§7.1).

    missile_tracking_active is unused by T4 — it is consumed by T6's missile branch.
    """

    tier: str  # "A" | "B" | "C"
    accuracy_bonus_pp: float  # 0 / 5 / 10
    missile_tracking_active: bool


def weapon_accuracy(pilot_acc: float, weapon: WeaponStats) -> float:
    """Phase-1 passthrough. Future homing-vs-must-aim split slots in here."""
    return pilot_acc


def compute_pilot_accuracy(
    *,
    combatant_base: float,
    own_scanner_bonus_pp: float,
    own_thruster_bonus_pp: float,
    opponent_booster_debuff_pp: float,
    opponent_cloak_active: bool,
    cloak_set_value: float,
    clamp_min: float,
    clamp_max: float,
) -> tuple[float, float]:
    """Return (pilot_primary_acc, pilot_turret_acc) per §5 / §6.3.

    Cloak override REPLACES the layered formula (does NOT stack). It applies to BOTH
    the primary and turret variant. The turret variant differs from the primary variant
    ONLY by excluding the thruster bonus term.

    All arithmetic in pp-space; each variant is divided by 100 and clamped independently
    to avoid bound-interaction bugs between the two variants.
    """
    if opponent_cloak_active:
        clamped = max(clamp_min, min(clamp_max, cloak_set_value))
        return (clamped, clamped)

    # Layered formula in percentage-point space
    layered_pp = combatant_base * 100 + own_scanner_bonus_pp + own_thruster_bonus_pp - opponent_booster_debuff_pp
    pilot_primary_acc = max(clamp_min, min(clamp_max, layered_pp / 100))
    # Turret variant: exclude the thruster bonus term; clamp independently
    turret_pp = layered_pp - own_thruster_bonus_pp
    pilot_turret_acc = max(clamp_min, min(clamp_max, turret_pp / 100))
    return (pilot_primary_acc, pilot_turret_acc)


def thruster_ramp(
    current_distance: float,
    *,
    thruster_window_m: float,
    min_distance_m: float,
) -> float:
    """Distance-driven ramp for the thruster accuracy bonus (§5 / §7.4).

    Returns 0.0 when current_distance >= thruster_window_m (default 750 m).
    Returns 1.0 when current_distance <= min_distance_m (default 300 m).
    Linear interpolation between.

    ramp = clamp((thruster_window_m - current_distance) / (thruster_window_m - min_distance_m), 0.0, 1.0)
    """
    if current_distance >= thruster_window_m:
        return 0.0
    ramp = (thruster_window_m - current_distance) / (thruster_window_m - min_distance_m)
    return max(0.0, min(1.0, ramp))


def booster_debuff_pp(effect_pct: float, *, k_boost: float) -> float:
    """Booster accuracy debuff in percentage-points (positive magnitude; caller subtracts).

    debuff_pp = effect_pct × k_boost
    Example: Polytron (effect_pct=300) at default k_boost=0.10 → 30.0 pp.
    """
    return effect_pct * k_boost


def resolve_scanner_tier(
    loadout: ShipLoadout,
    *,
    tier_b_bonus_pp: float,
    tier_c_bonus_pp: float,
) -> ScannerTier:
    """Inspect equipped modules and return the combatant's scanner tier (§7.1).

    Per §7.1: combat scanner is unique-equip on its own subclass. Plasma scanner
    shares the ScannerModule class but is inert in combat — discriminate by module
    name via _SCANNER_TIER_BY_NAME, not by class membership.

    If multiple combat scanners are equipped (loadout builder dedupes via unique-equip
    rule, but be defensive): highest tier wins (C > B > A).
    """
    best_tier = "A"
    for mod in loadout.modules:
        t = _SCANNER_TIER_BY_NAME.get(mod.name)
        if t is None:
            continue
        if t == "C" or (t == "B" and best_tier == "A"):
            best_tier = t

    if best_tier == "C":
        return ScannerTier(tier="C", accuracy_bonus_pp=tier_c_bonus_pp, missile_tracking_active=True)
    if best_tier == "B":
        return ScannerTier(tier="B", accuracy_bonus_pp=tier_b_bonus_pp, missile_tracking_active=True)
    return ScannerTier(tier="A", accuracy_bonus_pp=0.0, missile_tracking_active=False)
