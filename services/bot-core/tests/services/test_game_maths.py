"""
Unit tests for the game_maths module.

Covers:
- pick_random_item_tl  - probability distribution correctness
- reward_per_sys_check - legacy reward formula
- ship_tech_level_for_value - price-threshold classification
- calculate_user_level  - XP to level mapping
"""

import random
from collections import Counter

import pytest
from src.services.game_constants import GameConstants
from src.services.game_maths import (
    calculate_user_level,
    pick_random_item_tl,
    reward_per_sys_check,
    ship_tech_level_for_value,
)

# ---------------------------------------------------------------------------
# TestPickRandomItemTL
# ---------------------------------------------------------------------------


class TestPickRandomItemTL:
    """Tests for pick_random_item_tl()."""

    # -- result always in valid range --

    @pytest.mark.parametrize("shop_tl", range(1, 11))
    def test_result_in_valid_range(self, shop_tl: int) -> None:
        """Result must always be between MIN and MAX tech level."""
        for _ in range(200):
            result = pick_random_item_tl(shop_tl)
            assert GameConstants.MIN_TECH_LEVEL <= result <= GameConstants.MAX_TECH_LEVEL, (
                f"shop_tl={shop_tl}: got {result} outside [1, 10]"
            )

    # -- modal TL matches shop TL --

    def test_modal_tl_equals_shop_tl_midrange(self) -> None:
        """For shop_tl=5, TL 5 should be the most common result over 1000 draws."""
        random.seed(42)
        counts: Counter[int] = Counter(pick_random_item_tl(5) for _ in range(1000))
        most_common_tl, _ = counts.most_common(1)[0]
        assert most_common_tl == 5, f"Expected modal TL=5, got TL={most_common_tl}; distribution={counts}"

    # -- boundary shop TLs --

    def test_modal_tl_shop_tl_1(self) -> None:
        """For shop_tl=1, TL 1 should dominate."""
        random.seed(7)
        counts: Counter[int] = Counter(pick_random_item_tl(1) for _ in range(1000))
        most_common_tl, _ = counts.most_common(1)[0]
        assert most_common_tl == 1

    def test_modal_tl_shop_tl_10(self) -> None:
        """For shop_tl=10, TL 10 should dominate."""
        random.seed(13)
        counts: Counter[int] = Counter(pick_random_item_tl(10) for _ in range(1000))
        most_common_tl, _ = counts.most_common(1)[0]
        assert most_common_tl == 10

    # -- spread: items within ±2 of shop_tl appear --

    def test_nearby_tls_appear_for_midrange_shop(self) -> None:
        """TLs 3-7 should all appear when shop_tl=5 (within ±2)."""
        random.seed(99)
        counts: Counter[int] = Counter(pick_random_item_tl(5) for _ in range(2000))
        for tl in range(3, 8):
            assert counts[tl] > 0, f"Expected TL {tl} to appear at least once, but got 0"

    # -- far-away TLs have zero probability --

    def test_far_tls_are_absent_for_shop_tl_10(self) -> None:
        """TL 1 has zero weight for shop_tl=10 (distance > 2.3)."""
        # distance from TL 1 to shop TL 10 is 9, which is >> 2.3
        random.seed(55)
        results = [pick_random_item_tl(10) for _ in range(2000)]
        assert 1 not in results, "TL 1 should have zero probability for shop_tl=10"

    def test_far_tls_are_absent_for_shop_tl_1(self) -> None:
        """TL 10 has zero weight for shop_tl=1 (distance > 2.3)."""
        random.seed(55)
        results = [pick_random_item_tl(1) for _ in range(2000)]
        assert 10 not in results, "TL 10 should have zero probability for shop_tl=1"

    # -- distribution is not degenerate (not all the same TL) --

    def test_distribution_is_not_degenerate_for_shop_tl_5(self) -> None:
        """Multiple distinct TLs should appear for shop_tl=5."""
        random.seed(17)
        results = {pick_random_item_tl(5) for _ in range(500)}
        assert len(results) > 1, "Expected variety in item TLs, got only one value"

    def test_distribution_is_not_degenerate_for_shop_tl_1(self) -> None:
        """Multiple distinct TLs should appear near the boundary."""
        random.seed(17)
        results = {pick_random_item_tl(1) for _ in range(500)}
        assert len(results) > 1, "Expected variety in item TLs for shop_tl=1"

    def test_distribution_is_not_degenerate_for_shop_tl_10(self) -> None:
        """Multiple distinct TLs should appear near the upper boundary."""
        random.seed(17)
        results = {pick_random_item_tl(10) for _ in range(500)}
        assert len(results) > 1, "Expected variety in item TLs for shop_tl=10"


# ---------------------------------------------------------------------------
# TestRewardPerSysCheck
# ---------------------------------------------------------------------------


class TestRewardPerSysCheck:
    """Tests for reward_per_sys_check()."""

    def test_tl1_known_input(self) -> None:
        """TL 1, loadout 100 000 → expected 3250.

        max(1000, int(100000 * 1.3 / (2 * (1+1) * 10)))
        = max(1000, int(130000 / 40))
        = max(1000, 3250)
        = 3250
        """
        assert reward_per_sys_check(1, 100_000) == 3250

    def test_tl5_known_input(self) -> None:
        """TL 5, loadout 1 000 000 → expected 7142.

        max(1000, int(1000000 * 1 / (2 * (5+2) * 10)))
        = max(1000, int(1000000 / 140))
        = max(1000, 7142)
        = 7142
        """
        assert reward_per_sys_check(5, 1_000_000) == 7142

    def test_minimum_floor_applied(self) -> None:
        """Very low loadout value → floored at CLASSIC_CREDITS_PER_CHECK."""
        result = reward_per_sys_check(5, 0)
        assert result == GameConstants.CLASSIC_CREDITS_PER_CHECK

    def test_minimum_floor_applied_for_tl1(self) -> None:
        """TL 1 with loadout=0 → floored at CLASSIC_CREDITS_PER_CHECK."""
        result = reward_per_sys_check(1, 0)
        assert result == GameConstants.CLASSIC_CREDITS_PER_CHECK

    def test_tl1_has_multiplier(self) -> None:
        """TL 1 reward should be > TL 2 reward for the same loadout (1.3x bonus)."""
        loadout = 500_000
        r1 = reward_per_sys_check(1, loadout)
        r2 = reward_per_sys_check(2, loadout)
        # TL 1: int(500000 * 1.3 / (2 * 2 * 10)) = int(650000/40) = 16250
        # TL 2: int(500000 * 1.0 / (2 * 4 * 10)) = int(500000/80) = 6250
        assert r1 > r2, f"Expected TL1 reward ({r1}) > TL2 reward ({r2})"

    def test_tl2_no_multiplier(self) -> None:
        """TL 2 should not apply the 1.3x multiplier."""
        loadout = 800_000
        # TL 2: int(800000 / (2 * 4 * 10)) = int(800000/80) = 10000
        expected = max(GameConstants.CLASSIC_CREDITS_PER_CHECK, int(800_000 / 80))
        assert reward_per_sys_check(2, loadout) == expected

    def test_higher_tl_generally_lower_reward(self) -> None:
        """Higher TL with same loadout yields a lower reward (larger divisor)."""
        loadout = 5_000_000
        rewards = [reward_per_sys_check(tl, loadout) for tl in range(2, 11)]
        # Rewards should be non-increasing for TL 2-10
        for i in range(len(rewards) - 1):
            assert rewards[i] >= rewards[i + 1], (
                f"Expected reward[TL{i + 2}] >= reward[TL{i + 3}], got {rewards[i]} < {rewards[i + 1]}"
            )

    def test_return_type_is_int(self) -> None:
        """Return value must be an integer."""
        result = reward_per_sys_check(3, 250_000)
        assert isinstance(result, int)

    @pytest.mark.parametrize("tl", range(1, 11))
    def test_all_tls_return_at_least_floor(self, tl: int) -> None:
        """Every TL must return at least CLASSIC_CREDITS_PER_CHECK."""
        result = reward_per_sys_check(tl, 0)
        assert result >= GameConstants.CLASSIC_CREDITS_PER_CHECK


# ---------------------------------------------------------------------------
# TestShipTechLevelForValue
# ---------------------------------------------------------------------------


class TestShipTechLevelForValue:
    """Tests for ship_tech_level_for_value()."""

    def test_low_value_is_tl1(self) -> None:
        """Value 10 000 → TL 1 (well below 50 000 threshold)."""
        assert ship_tech_level_for_value(10_000) == 1

    def test_exactly_at_tl1_threshold(self) -> None:
        """Value == 50 000 → TL 1 (inclusive boundary)."""
        assert ship_tech_level_for_value(50_000) == 1

    def test_just_above_tl1_threshold_is_tl2(self) -> None:
        """Value 50 001 → TL 2."""
        assert ship_tech_level_for_value(50_001) == 2

    def test_each_threshold_boundary(self) -> None:
        """Every threshold value maps to its own TL (inclusive lower bound)."""
        thresholds = GameConstants.SHIP_PRICE_THRESHOLDS
        for expected_tl, threshold_value in enumerate(thresholds, start=1):
            result = ship_tech_level_for_value(threshold_value)
            assert result == expected_tl, f"value={threshold_value}: expected TL={expected_tl}, got TL={result}"

    def test_each_threshold_plus_one(self) -> None:
        """One credit above each threshold (except the last) maps to TL+1."""
        thresholds = GameConstants.SHIP_PRICE_THRESHOLDS
        for tl_index in range(len(thresholds) - 1):
            value = thresholds[tl_index] + 1
            expected = tl_index + 2  # next TL
            result = ship_tech_level_for_value(value)
            assert result == expected, f"value={value}: expected TL={expected}, got TL={result}"

    def test_near_max_threshold(self) -> None:
        """Value 999 999 999 → TL 10 (at the final threshold)."""
        assert ship_tech_level_for_value(999_999_999) == 10

    def test_beyond_all_thresholds_is_max_tl(self) -> None:
        """Value beyond every threshold → MAX_TECH_LEVEL."""
        assert ship_tech_level_for_value(1_000_000_000) == GameConstants.MAX_TECH_LEVEL

    def test_zero_value_is_tl1(self) -> None:
        """Zero-credit ship → TL 1 (below first threshold)."""
        assert ship_tech_level_for_value(0) == 1

    def test_return_type_is_int(self) -> None:
        """Return value must be an integer."""
        assert isinstance(ship_tech_level_for_value(100_000), int)


# ---------------------------------------------------------------------------
# TestCalculateUserLevel
# ---------------------------------------------------------------------------


class TestCalculateUserLevel:
    """Tests for calculate_user_level()."""

    def test_negative_xp_is_level_0(self) -> None:
        """XP below 0 → level 0."""
        assert calculate_user_level(-5) == 0

    def test_xp_at_level1_boundary(self) -> None:
        """XP == 0 → level 1 (boundary[1] == 0)."""
        assert calculate_user_level(0) == 1

    def test_xp_just_below_level2_is_level1(self) -> None:
        """XP == 1049 → level 1 (just below the 1050 boundary)."""
        assert calculate_user_level(1049) == 1

    def test_xp_at_level2_boundary(self) -> None:
        """XP == 1050 → level 2."""
        assert calculate_user_level(1050) == 2

    def test_xp_just_below_level3_is_level2(self) -> None:
        """XP == 1999 → level 2."""
        assert calculate_user_level(1999) == 2

    def test_xp_at_level3_boundary(self) -> None:
        """XP == 2000 → level 3."""
        assert calculate_user_level(2000) == 3

    def test_xp_at_level4_boundary(self) -> None:
        """XP == 3500 → level 4."""
        assert calculate_user_level(3500) == 4

    def test_xp_at_level5_boundary(self) -> None:
        """XP == 10 000 → level 5."""
        assert calculate_user_level(10_000) == 5

    def test_xp_at_level6_boundary(self) -> None:
        """XP == 18 000 → level 6."""
        assert calculate_user_level(18_000) == 6

    def test_xp_at_level7_boundary(self) -> None:
        """XP == 61 000 → level 7."""
        assert calculate_user_level(61_000) == 7

    def test_xp_at_level8_boundary(self) -> None:
        """XP == 71 000 → level 8."""
        assert calculate_user_level(71_000) == 8

    def test_xp_at_level9_boundary(self) -> None:
        """XP == 90 000 → level 9."""
        assert calculate_user_level(90_000) == 9

    def test_xp_just_below_level10_is_level9(self) -> None:
        """XP == 999 999 → level 9."""
        assert calculate_user_level(999_999) == 9

    def test_xp_at_level10_boundary(self) -> None:
        """XP == 1 000 000 → level 10."""
        assert calculate_user_level(1_000_000) == 10

    def test_xp_well_above_level10_boundary(self) -> None:
        """XP >> max boundary → level 10 (capped)."""
        assert calculate_user_level(9_999_999) == 10

    def test_return_type_is_int(self) -> None:
        """Return value must be an integer."""
        assert isinstance(calculate_user_level(500), int)

    @pytest.mark.parametrize(
        ("xp", "expected_level"),
        [
            (-5, 0),
            (0, 1),
            (1049, 1),
            (1050, 2),
            (999_999, 9),
            (1_000_000, 10),
            (9_999_999, 10),
        ],
    )
    def test_parametrized_xp_to_level(self, xp: int, expected_level: int) -> None:
        """Parametrized coverage of key XP → level mappings."""
        assert calculate_user_level(xp) == expected_level
