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

import random

from services.combat_models import (
    CombatResolver,
    CombatStats,
    FightResults,
    FightStats,
    ShipLoadout,
)
from services.game_constants import GameConstants

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
        # 1. Apply variance to HP (2 rolls)
        ship1_hp_varied = _apply_variance(ship1_stats.total_hp, variance_percent)
        ship2_hp_varied = _apply_variance(ship2_stats.total_hp, variance_percent)

        # 2. Apply variance to DPS (2 rolls)
        ship1_dps_varied = _apply_variance_float(ship1_stats.dps, variance_percent)
        ship2_dps_varied = _apply_variance_float(ship2_stats.dps, variance_percent)

        # 3. Handle zero-DPS edge cases
        ship1_ttk: float | None = None
        ship2_ttk: float | None = None

        both_zero = ship1_stats.dps == 0 and ship2_stats.dps == 0

        if both_zero:
            # Neither ship can deal damage — stalemate
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

        # ship2_ttk = how long ship2 survives ship1's fire
        ship2_ttk = ship2_hp_varied / ship1_dps_varied if ship1_dps_varied > 0 else None

        # 4. Determine winner (longest survivor wins)
        winner_name: str | None = None
        loser_name: str | None = None
        is_stalemate = False

        if ship1_ttk is None and ship2_ttk is None:
            # Both survive indefinitely (shouldn't happen if both_zero handled above)
            is_stalemate = True
        elif ship1_ttk is None:
            # Ship1 survives indefinitely, ship2 doesn't → ship1 wins
            winner_name = ship1_stats.ship_name
            loser_name = ship2_stats.ship_name
        elif ship2_ttk is None:
            # Ship2 survives indefinitely → ship2 wins
            winner_name = ship2_stats.ship_name
            loser_name = ship1_stats.ship_name
        elif ship1_ttk > ship2_ttk:
            winner_name = ship1_stats.ship_name
            loser_name = ship2_stats.ship_name
        elif ship2_ttk > ship1_ttk:
            winner_name = ship2_stats.ship_name
            loser_name = ship1_stats.ship_name
        else:
            # Exact tie
            is_stalemate = True

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
        return value
    delta = int(value * variance_percent)
    return random.randint(value - delta, value + delta)


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
        return value
    low = int(value - value * variance_percent)
    high = int(value + value * variance_percent)
    if low > high:
        low, high = high, low
    if low == high:
        return float(low)
    return float(random.randint(low, high))


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
        total = 0.0
        multiplier = 1.0

        for weapon in loadout.weapons:
            total += weapon.dps

        for turret in loadout.turrets:
            total += turret.dps

        for module in loadout.modules:
            total += module.dps
            multiplier *= module.dps_multiplier

        return total * multiplier

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
        total = loadout.base_armour
        multiplier = 1.0

        for module in loadout.modules:
            total += module.armour
            multiplier *= module.armour_multiplier

        for upgrade in loadout.upgrades:
            total += upgrade.armour
            multiplier *= upgrade.armour_multiplier

        return int(total * multiplier)

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
        total = 0
        multiplier = 1.0

        for module in loadout.modules:
            total += module.shield
            multiplier *= module.shield_multiplier

        return int(total * multiplier)

    def collect_stats(self, loadout: ShipLoadout) -> CombatStats:
        """Compute all combat statistics for a ship loadout.

        Combines get_dps, get_armour, and get_shield into a single
        CombatStats object. total_hp = armour + shield.

        Args:
            loadout: Complete ship loadout.

        Returns:
            CombatStats with all computed values.
        """
        dps = self.get_dps(loadout)
        armour = self.get_armour(loadout)
        shield = self.get_shield(loadout)

        return CombatStats(
            ship_name=loadout.ship_name,
            dps=dps,
            armour=armour,
            shield=shield,
            total_hp=armour + shield,
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
    ) -> FightResults:
        """Simulate a fight between two ship loadouts.

        Collects stats for both ships, then delegates to the configured
        CombatResolver for the actual fight computation.

        Args:
            loadout1: First ship (initiator).
            loadout2: Second ship (receiver).
            variance_percent: Random variance to apply. Defaults to
                              GameConstants.DUEL_VARIANCE_PERCENT.

        Returns:
            FightResults with winner, loser, stats, and stalemate flag.
        """
        if variance_percent is None:
            variance_percent = GameConstants.DUEL_VARIANCE_PERCENT

        stats1 = self.collect_stats(loadout1)
        stats2 = self.collect_stats(loadout2)

        return self._resolver.resolve(stats1, stats2, variance_percent)
