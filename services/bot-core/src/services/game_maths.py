"""
Game Maths module for BountyBot.

Implements the probabilistic and formulaic game mechanics from the legacy
``gameMaths.py``, including tech-level item selection, bounty reward
calculation, and ship/player classification helpers.
"""

import random

from shared import bblogger

from services.game_constants import GameConstants

flogger = bblogger.get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tl_weight(item_tl: int, shop_tl: int, spread: float = 2.3) -> float:
    """Quadratic probability kernel for tech-level proximity.

    Returns a value in [0, 1] that is highest when *item_tl == shop_tl*
    and reaches 0 when ``|item_tl - shop_tl| >= spread``.

    Args:
        item_tl: Candidate item tech level.
        shop_tl: Shop's tech level (bell-curve centre).
        spread:  Half-width of the bell curve (default 2.3 from legacy code).

    Returns:
        Non-negative weight (0.0 - 1.0).
    """
    return max(0.0, 1 - ((item_tl - shop_tl) / spread) ** 2)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def pick_random_item_tl(shop_tl: int) -> int:
    """Pick a random item tech level for a shop at the given TL.

    Uses a quadratic bell curve centred on *shop_tl* with a spread of 2.3.
    Items closer to the shop TL are more likely; items more than ~2.3 levels
    away receive zero weight.

    Args:
        shop_tl: Shop's tech level (1-10).

    Returns:
        Item tech level in the range [MIN_TECH_LEVEL, MAX_TECH_LEVEL].
    """
    tl_range = range(GameConstants.MIN_TECH_LEVEL, GameConstants.MAX_TECH_LEVEL + 1)

    # Raw (un-normalised) probabilities
    raw_probs = [_tl_weight(item_tl, shop_tl) for item_tl in tl_range]

    total = sum(raw_probs)
    if total == 0:
        # Degenerate case - should not occur for valid shop_tl values
        return shop_tl

    # Normalise
    probs = [p / total for p in raw_probs]

    # Sample via cumulative distribution
    rand_val = random.random()
    cumulative = 0.0
    for i, p in enumerate(probs):
        cumulative += p
        if rand_val <= cumulative:
            selected = i + GameConstants.MIN_TECH_LEVEL
            flogger.debug(f"pick_random_item_tl: shop_tl={shop_tl} → selected TL={selected}")
            return selected

    # Floating-point safety fallback
    flogger.debug(f"pick_random_item_tl: shop_tl={shop_tl} → selected TL={GameConstants.MAX_TECH_LEVEL} (fallback)")
    return GameConstants.MAX_TECH_LEVEL


def pick_division_tech_level(division: str, division_max_tl: dict[str, int]) -> int:
    """Draw a tech level for a division the way criminal spawns do.

    Samples :func:`pick_random_item_tl` around the division's centre, then
    applies the division cap.  Both ``spawn_bounty`` and the shop-refresh
    batch TL call this, so a tier's stock is drawn from the same distribution
    as the enemies players actually face there — including per-guild cap
    overrides.
    """
    key = division.lower()
    center = GameConstants.DIVISION_TL_CENTERS.get(key, 5)
    cap = division_max_tl.get(key, GameConstants.MAX_TECH_LEVEL)
    return min(pick_random_item_tl(center), cap)


def reward_per_sys_check(tech_level: int, loadout_value: int) -> int:
    """Calculate the bounty credit reward for each system checked.

    Implements the legacy formula from ``gameMaths.py`` lines 217-227.
    Tech-level 1 criminals receive a 1.3x multiplier as a beginner bonus.

    .. deprecated::
        This function is superseded for the spawn path by the new
        ``consolation_pool / route_length`` formula introduced alongside
        ``BOUNTY_WINNER_RESERVE_FACTOR``.  ``spawn_bounty()`` no longer calls
        this function.  It is kept because ``tests/services/test_game_maths.py``
        exercises it directly; do not delete until those tests are updated.

    Args:
        tech_level:    Bounty (criminal) tech level (1-10).
        loadout_value: Total credit value of the criminal's equipment.

    Returns:
        Credit reward, floored at ``CLASSIC_CREDITS_PER_CHECK``.
    """
    multiplier = 1.3 if tech_level == 1 else 1
    divisor_offset = 1 if tech_level == 1 else 2
    reward = max(
        GameConstants.CLASSIC_CREDITS_PER_CHECK,
        int((loadout_value * multiplier) / (2 * (tech_level + divisor_offset) * 10)),
    )
    flogger.debug(f"reward_per_sys_check: tl={tech_level} loadout_value={loadout_value} → {reward} credits")
    return reward


def ship_tech_level_for_value(value: int) -> int:
    """Determine a ship's tech level from its credit value.

    Iterates through ``GameConstants.SHIP_PRICE_THRESHOLDS`` and returns the
    first TL whose threshold is greater than or equal to *value*.  If *value*
    exceeds every threshold the maximum tech level is returned.

    Args:
        value: Ship's credit value.

    Returns:
        Tech level in the range [1, MAX_TECH_LEVEL].
    """
    for tl, threshold in enumerate(GameConstants.SHIP_PRICE_THRESHOLDS, start=1):
        if value <= threshold:
            return tl
    return GameConstants.MAX_TECH_LEVEL


# B.48: deleted vestigial ``calculate_user_level`` and ``calculate_xp_for_level``.
# The level/division progression system was removed in favour of the
# configurable per-guild tier-threshold system.
