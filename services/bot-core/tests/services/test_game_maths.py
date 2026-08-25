"""
Unit tests for the game_maths module.

Covers:
- enemy_tech_level_band - actual possible enemy TLs by division
- pick_random_item_tl  - probability distribution correctness
- reward_per_sys_check - legacy reward formula
- ship_tech_level_for_value - price-threshold classification

B.48: ``calculate_user_level`` and its tests were removed alongside the
vestigial level/division progression system.
"""

import itertools
import random
from collections import Counter
from unittest.mock import patch

import pytest
from services.game_constants import GameConstants
from services.game_maths import (
    pick_division_tech_level,
    pick_random_item_tl,
    pick_shop_tech_level,
    reward_per_sys_check,
    ship_tech_level_for_value,
)

# ---------------------------------------------------------------------------
# TestPickDivisionTechLevel
# ---------------------------------------------------------------------------


class TestPickDivisionTechLevel:
    """Tests for the division TL draw shared by bounty spawns and shop refreshes."""

    @pytest.mark.parametrize(
        ("division", "expected"),
        [
            ("bronze", {1, 2}),
            ("silver", {1, 2, 3, 4}),
            ("gold", {4, 5, 6, 7}),
            ("platinum", {6, 7, 8, 9, 10}),
        ],
    )
    def test_stays_within_the_division_support_and_cap(self, division: str, expected: set[int]) -> None:
        drawn = {pick_division_tech_level(division, GameConstants.DIVISION_MAX_TL) for _ in range(400)}

        assert drawn <= expected

    def test_applies_per_guild_cap_override(self) -> None:
        caps = {"bronze": 2, "silver": 4, "gold": 5, "platinum": 10}

        assert all(pick_division_tech_level("gold", caps) <= 5 for _ in range(200))

    def test_tier_name_case_is_ignored(self) -> None:
        """Shop tiers arrive capitalised ("Gold"); divisions are stored lowercase."""
        with patch("services.game_maths.pick_random_item_tl", return_value=6) as picker:
            assert pick_division_tech_level("Gold", GameConstants.DIVISION_MAX_TL) == 6

        picker.assert_called_once_with(6)  # gold centre, not the unknown-division default of 5

    def test_explicit_center_override_used(self) -> None:
        """When an explicit center is passed, DIVISION_TL_CENTERS is not consulted.

        Bronze center is normally 1; passing center=4 should draw from TL 4
        (patched to return 4 deterministically).
        """
        with patch("services.game_maths.pick_random_item_tl", return_value=4) as picker:
            result = pick_division_tech_level("bronze", GameConstants.DIVISION_MAX_TL, center=4)

        picker.assert_called_once_with(4)
        assert result == 2  # capped at bronze MAX_TL=2 even with center override

    def test_explicit_center_none_falls_back_to_global(self) -> None:
        """Passing center=None (legacy call shape) falls back to DIVISION_TL_CENTERS."""
        with patch("services.game_maths.pick_random_item_tl", return_value=3) as picker:
            result = pick_division_tech_level("silver", GameConstants.DIVISION_MAX_TL, center=None)

        picker.assert_called_once_with(3)  # silver center from DIVISION_TL_CENTERS is 3
        assert result == 3  # silver cap=4; 3 <= 4 so no clamping


# ---------------------------------------------------------------------------
# TestPickShopTechLevel
# ---------------------------------------------------------------------------


class TestPickShopTechLevel:
    """Characterisation of the two-bucket shop batch-TL draw.

    Pins the intended per-tier distribution (design: uniform in-band bucket +
    asymmetric exponential out-of-band taper, banded weight 0.70, up-decay 0.60,
    down-decay 0.45).  A seeded large sample is asserted against the target grid
    within a small tolerance so a future constant/formula tweak that re-widens a
    tier (the "TL10 Bronze shop" regression) trips this test.
    """

    # Per-tier (band_lo, band_hi) from the SHOP_TL_BAND_* scalars.
    _BANDS = {
        "bronze": (1, 2),
        "silver": (1, 4),
        "gold": (4, 7),
        "platinum": (7, 10),
    }
    # Target probability (%) per TL 1..10 — the agreed design grid.
    _EXPECTED_PCT = {
        "bronze": [35, 35, 12, 7, 4, 3, 2, 1, 1, 0],
        "silver": [18, 18, 18, 18, 13, 8, 5, 3, 2, 1],
        "gold": [2, 4, 8, 18, 18, 18, 18, 8, 5, 3],
        "platinum": [0, 1, 2, 3, 7, 17, 18, 18, 18, 18],
    }

    def _sample(self, tier: str, n: int) -> Counter:
        lo, hi = self._BANDS[tier]
        return Counter(
            pick_shop_tech_level(
                lo,
                hi,
                GameConstants.SHOP_BANDED_TL_WEIGHT,
                GameConstants.SHOP_UPTIER_TL_DECAY,
                GameConstants.SHOP_DOWNTIER_TL_DECAY,
            )
            for _ in range(n)
        )

    @pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "platinum"])
    def test_distribution_matches_the_design_grid(self, tier: str) -> None:
        random.seed(1234)
        n = 40000
        counts = self._sample(tier, n)

        for tl in range(1, 11):
            observed = 100.0 * counts.get(tl, 0) / n
            expected = self._EXPECTED_PCT[tier][tl - 1]
            assert abs(observed - expected) <= 3.0, f"{tier} TL{tl}: observed {observed:.1f}% vs expected {expected}%"

    @pytest.mark.parametrize(
        ("tier", "band"),
        [("bronze", (1, 2)), ("silver", (1, 4)), ("gold", (4, 7)), ("platinum", (7, 10))],
    )
    def test_modal_tl_is_in_band(self, tier: str, band: tuple[int, int]) -> None:
        """The single most common shop TL must always be tier-appropriate."""
        random.seed(99)
        counts = self._sample(tier, 40000)
        modal_tl = counts.most_common(1)[0][0]
        assert band[0] <= modal_tl <= band[1], f"{tier} modal TL{modal_tl} is out of band {band}"

    def test_out_of_band_decays_with_distance(self) -> None:
        """Further from the band edge is strictly rarer (Bronze upper tail)."""
        random.seed(7)
        counts = self._sample("bronze", 60000)
        # Bronze band tops at 2; TL3 > TL4 > ... monotonically down the up-tail.
        tail = [counts.get(tl, 0) for tl in range(3, 11)]
        assert all(earlier >= later for earlier, later in itertools.pairwise(tail)), tail

    def test_weight_one_is_pure_in_band_uniform(self) -> None:
        random.seed(3)
        drawn = Counter(pick_shop_tech_level(4, 7, 1.0, 0.6, 0.45) for _ in range(4000))
        assert set(drawn) == {4, 5, 6, 7}  # never out of band

    def test_weight_zero_is_pure_out_of_band(self) -> None:
        random.seed(3)
        drawn = {pick_shop_tech_level(4, 7, 0.0, 0.6, 0.45) for _ in range(4000)}
        assert drawn.isdisjoint({4, 5, 6, 7})  # never in band


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


# B.48: TestCalculateUserLevel deleted along with calculate_user_level().
