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

from shared import bblogger

from services.combat_models import (
    CombatResolver,
    CombatStats,
    FightResults,
    FightStats,
    ShipLoadout,
)
from services.game_constants import GameConstants, resolve_constant

flogger = bblogger.get_logger(__name__)

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
