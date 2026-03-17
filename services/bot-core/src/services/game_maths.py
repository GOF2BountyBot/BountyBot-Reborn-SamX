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


def reward_per_sys_check(tech_level: int, loadout_value: int) -> int:
    """Calculate the bounty credit reward for each system checked.

    Implements the legacy formula from ``gameMaths.py`` lines 217-227.
    Tech-level 1 criminals receive a 1.3x multiplier as a beginner bonus.

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
        int(
            (loadout_value * multiplier)
            / (2 * (tech_level + divisor_offset) * 10)
        ),
    )
    flogger.debug(
        f"reward_per_sys_check: tl={tech_level} loadout_value={loadout_value} → {reward} credits"
    )
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


def calculate_user_level(xp: int) -> int:
    """Calculate a player's bounty-hunting level from their accumulated XP.

    Uses ``GameConstants.XP_LEVEL_BOUNDARIES`` whose entries are indexed by
    level (``boundaries[0]`` is the sentinel for level 0).

    The algorithm mirrors the legacy one-liner::

        next(i - 1 for i, v in enumerate(boundaries) if v > xp)

    i.e. find the first boundary value that strictly exceeds *xp* and return
    the preceding index.

    Args:
        xp: Player's current XP (may be negative).

    Returns:
        Level in the range [0, len(XP_LEVEL_BOUNDARIES) - 1].
    """
    boundaries = GameConstants.XP_LEVEL_BOUNDARIES
    for i, threshold in enumerate(boundaries):
        if threshold > xp:
            return max(0, i - 1)
    return len(boundaries) - 1
