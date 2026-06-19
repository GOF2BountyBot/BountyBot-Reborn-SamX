"""Unit tests for the pure loot selection engine (services/loot_engine.py).

All randomness is driven by a seeded :class:`random.Random`, so distribution
assertions are deterministic across runs.  The engine never touches the global
``random`` module — these tests would be non-deterministic if it did, which is
itself part of the coverage.
"""

import random
import sys
import types
from collections import Counter
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub shared.bblogger before importing the engine (models/__init__ pulls in
# sqlalchemy_utils transitively via service imports; mirror the suite guard).
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.loot_engine import (
    BandConfig,
    LootCandidate,
    band1_window_pool,
    nearest_tl_candidates,
    pick_band1_item,
    pick_commodity_item,
    roll_loot,
    roll_triangular,
    select_band,
    tractor_chance,
    tractor_success,
    triangular_weights,
)

from services import loot_engine

SEED = 1234
DRAWS = 60_000
# Empirical-fraction tolerance for distribution assertions at DRAWS samples.
TOL = 0.015


def _rng() -> random.Random:
    return random.Random(SEED)


# ===========================================================================
# Triangular quantity sampler (§5.8.1–.3)
# ===========================================================================


class TestTriangularWeights:
    def test_band1_weights_50_33_17(self) -> None:
        """Band 1 (1,1,3) → descending ramp 3:2:1 → 50/33/17%."""
        values, weights = triangular_weights(1, 1, 3)
        assert values == [1, 2, 3]
        assert weights == [3.0, 2.0, 1.0]

    def test_band2_symmetric_vector(self) -> None:
        """Band 2 (4,8,12) → symmetric tent 1..5..1 → 4/8/12/16/20%/…/4."""
        values, weights = triangular_weights(4, 8, 12)
        assert values == list(range(4, 13))
        assert weights == [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]

    def test_band3_symmetric_peak_at_mode(self) -> None:
        values, weights = triangular_weights(10, 16, 22)
        assert values == list(range(10, 23))
        assert weights[values.index(16)] == max(weights)
        # Symmetric about 16.
        assert weights == weights[::-1]


class TestRollTriangularDistribution:
    def test_band1_empirical_50_33_17(self) -> None:
        rng = _rng()
        counts = Counter(roll_triangular(1, 1, 3, rng) for _ in range(DRAWS))
        assert pytest.approx(1 / 2, abs=TOL) == counts[1] / DRAWS
        assert pytest.approx(1 / 3, abs=TOL) == counts[2] / DRAWS
        assert pytest.approx(1 / 6, abs=TOL) == counts[3] / DRAWS

    def test_band2_empirical_symmetric_vector(self) -> None:
        rng = _rng()
        counts = Counter(roll_triangular(4, 8, 12, rng) for _ in range(DRAWS))
        expected = {4: 0.04, 5: 0.08, 6: 0.12, 7: 0.16, 8: 0.20, 9: 0.16, 10: 0.12, 11: 0.08, 12: 0.04}
        for q, frac in expected.items():
            assert pytest.approx(frac, abs=TOL) == counts[q] / DRAWS
        assert pytest.approx(8.0, abs=0.05) == sum(counts.elements()) / DRAWS  # mean == 8

    def test_band3_empirical_mean_16(self) -> None:
        rng = _rng()
        counts = Counter(roll_triangular(10, 16, 22, rng) for _ in range(DRAWS))
        assert pytest.approx(16.0, abs=0.05) == sum(counts.elements()) / DRAWS
        assert pytest.approx(0.143, abs=TOL) == counts[16] / DRAWS  # peak 14.3%
        assert set(counts) <= set(range(10, 23))  # never out of range

    def test_deterministic_for_same_seed(self) -> None:
        a = [roll_triangular(4, 8, 12, random.Random(7)) for _ in range(20)]
        b = [roll_triangular(4, 8, 12, random.Random(7)) for _ in range(20)]
        assert a == b


class TestTriangularDefensive:
    def test_min_greater_than_max_swaps(self) -> None:
        values, weights = triangular_weights(12, 8, 4)  # reversed
        assert values == list(range(4, 13))
        assert len(weights) == len(values)

    def test_mode_below_range_clamps(self) -> None:
        values, weights = triangular_weights(4, 1, 12)  # mode < min
        # Clamped to min ⇒ descending ramp from 4.
        assert weights[0] == max(weights)
        assert all(roll_triangular(4, 1, 12, _rng()) in values for _ in range(5))

    def test_mode_above_range_clamps(self) -> None:
        values, _ = triangular_weights(4, 99, 12)  # mode > max
        for _ in range(100):
            assert roll_triangular(4, 99, 12, _rng()) in values

    def test_single_point_range(self) -> None:
        assert roll_triangular(5, 5, 5, _rng()) == 5

    def test_negative_and_degenerate_never_crash_or_loop(self) -> None:
        # A grab-bag of nonsense triples must each return a value in-range, fast.
        for lo, mode, hi in [(-3, 0, -1), (0, 0, 0), (3, -5, 3), (7, 7, 2)]:
            v = roll_triangular(lo, mode, hi, _rng())
            assert isinstance(v, int)


# ===========================================================================
# Band selection (§5.8.4 step 1)
# ===========================================================================


class TestSelectBand:
    def test_default_10_20_70_split(self) -> None:
        rng = _rng()
        counts = Counter(select_band(10, 20, 70, rng) for _ in range(DRAWS))
        assert pytest.approx(0.10, abs=TOL) == counts[1] / DRAWS
        assert pytest.approx(0.20, abs=TOL) == counts[2] / DRAWS
        assert pytest.approx(0.70, abs=TOL) == counts[3] / DRAWS

    def test_normalises_when_not_summing_to_100(self) -> None:
        """Weights 1/2/7 (sum 10) must give the SAME 10/20/70 split as 10/20/70."""
        rng = _rng()
        counts = Counter(select_band(1, 2, 7, rng) for _ in range(DRAWS))
        assert pytest.approx(0.10, abs=TOL) == counts[1] / DRAWS
        assert pytest.approx(0.70, abs=TOL) == counts[3] / DRAWS

    def test_negative_weight_floored_to_zero(self) -> None:
        rng = _rng()
        counts = Counter(select_band(-5, 50, 50, rng) for _ in range(5000))
        assert counts[1] == 0  # band 1 never selected
        assert counts[2] + counts[3] == 5000

    def test_all_zero_weights_uniform_fallback(self) -> None:
        rng = _rng()
        counts = Counter(select_band(0, 0, 0, rng) for _ in range(DRAWS))
        for band in (1, 2, 3):
            assert pytest.approx(1 / 3, abs=TOL) == counts[band] / DRAWS


# ===========================================================================
# Band-1 window + nearest-TL net (§5.8.4 step 2, Band 1)
# ===========================================================================


def _band1_pool() -> list[LootCandidate]:
    # TLs 1..10, one module per level, plus a couple of weapons.
    pool = [LootCandidate("module", f"Mod TL{tl}", tech_level=tl, value=tl * 100) for tl in range(1, 11)]
    pool.append(LootCandidate("primary_weapon", "Gun TL5", tech_level=5))
    pool.append(LootCandidate("secondary_weapon", "Unlevelled", tech_level=None))
    return pool


class TestBand1Window:
    @pytest.mark.parametrize(
        "criminal_tl,window,expected_tls",
        [
            (5, 1, {4, 5, 6}),
            (1, 1, {1, 2}),  # clamped at min
            (10, 1, {9, 10}),  # clamped at max
            (5, 2, {3, 4, 5, 6, 7}),
            (5, 0, {5}),
        ],
    )
    def test_window_eligible_set(self, criminal_tl, window, expected_tls) -> None:
        pool = _band1_pool()
        eligible = band1_window_pool(pool, criminal_tl, window, 1, 10)
        got_tls = {c.tech_level for c in eligible}
        assert got_tls == expected_tls
        # The un-levelled (None TL) weapon is never inside a window.
        assert all(c.tech_level is not None for c in eligible)

    def test_pick_is_uniform_within_window(self) -> None:
        pool = _band1_pool()
        rng = _rng()
        picks = Counter(pick_band1_item(pool, 5, 1, rng).tech_level for _ in range(30_000))
        # TL5 has two items (module + gun) so it should appear ~2x the others.
        # Window {4,5,6}: 1 + 2 + 1 = 4 items ⇒ TL5 ≈ 50%, TL4/TL6 ≈ 25% each.
        assert pytest.approx(0.50, abs=0.02) == picks[5] / 30_000
        assert pytest.approx(0.25, abs=0.02) == picks[4] / 30_000

    def test_window_never_empty_returns_in_window(self) -> None:
        pool = _band1_pool()
        for tl in range(1, 11):
            pick = pick_band1_item(pool, tl, 1, _rng())
            assert pick is not None
            assert abs(pick.tech_level - tl) <= 1


class TestNearestTLNet:
    def test_nearest_when_window_empty(self) -> None:
        # Pool only has TL1 and TL10; criminal TL5 with window 1 ⇒ empty window.
        pool = [
            LootCandidate("module", "Low", tech_level=1),
            LootCandidate("module", "High", tech_level=10),
        ]
        assert band1_window_pool(pool, 5, 1, 1, 10) == []
        pick = pick_band1_item(pool, 5, 1, _rng())
        # |1-5|=4 < |10-5|=5 ⇒ nearest is the TL1 item.
        assert pick.name == "Low"

    def test_nearest_ties_returned_both(self) -> None:
        pool = [
            LootCandidate("module", "A", tech_level=3),
            LootCandidate("module", "B", tech_level=7),
        ]
        cands = nearest_tl_candidates(pool, 5)  # both distance 2
        assert {c.name for c in cands} == {"A", "B"}

    def test_empty_pool_returns_none(self) -> None:
        assert pick_band1_item([], 5, 1, _rng()) is None


# ===========================================================================
# Commodity within-band pick (§5.8.4 step 2, Bands 2 & 3)
# ===========================================================================


class TestCommodityPick:
    def test_uniform_over_item_rows_skew_intentional(self) -> None:
        # 3 "booze" rows vs 1 "waste" row ⇒ booze ~75% (over-rep is intentional).
        pool = [
            LootCandidate("commodity", "Booze A", value=1),
            LootCandidate("commodity", "Booze B", value=1),
            LootCandidate("commodity", "Booze C", value=1),
            LootCandidate("commodity", "Waste A", value=1),
        ]
        rng = _rng()
        names = Counter(pick_commodity_item(pool, rng).name for _ in range(20_000))
        booze = sum(v for k, v in names.items() if k.startswith("Booze"))
        assert pytest.approx(0.75, abs=0.02) == booze / 20_000

    def test_empty_pool_returns_none(self) -> None:
        assert pick_commodity_item([], _rng()) is None


# ===========================================================================
# Tractor chance resolver + success (M-5, §5.3)
# ===========================================================================


class TestTractorChance:
    CHANCE_MAP = {
        'AB-1 "Retractor"': 20,
        'AB-2 "Glue Gun"': 40,
        'AB-3 "Kingfisher"': 60,
        'AB-4 "Octopus"': 80,
    }

    @pytest.mark.parametrize(
        "beam,expected",
        [
            ('AB-1 "Retractor"', 20),
            ('AB-2 "Glue Gun"', 40),
            ('AB-3 "Kingfisher"', 60),
            ('AB-4 "Octopus"', 80),
        ],
    )
    def test_each_beam_maps_to_its_chance(self, beam, expected) -> None:
        assert tractor_chance([beam], self.CHANCE_MAP, no_tractor=0) == expected

    def test_no_beam_returns_no_tractor(self) -> None:
        assert tractor_chance(["E2 Exoclad", "Beamshield II"], self.CHANCE_MAP, no_tractor=0) == 0

    def test_empty_loadout_returns_no_tractor(self) -> None:
        assert tractor_chance([], self.CHANCE_MAP, no_tractor=0) == 0

    def test_unknown_module_name_returns_no_tractor(self) -> None:
        assert tractor_chance(["Mystery Beam"], self.CHANCE_MAP, no_tractor=0) == 0

    def test_first_recognised_beam_wins_unique_equip(self) -> None:
        # Unique-equip means at most one beam; if two were ever present, the
        # first recognised one is used (deterministic, no tie-break needed).
        loadout = ['AB-4 "Octopus"', 'AB-1 "Retractor"']
        assert tractor_chance(loadout, self.CHANCE_MAP, no_tractor=0) == 80


class TestTractorSuccess:
    def test_zero_chance_always_miss(self) -> None:
        assert not any(tractor_success(0, random.Random(s)) for s in range(50))

    def test_full_chance_always_hit(self) -> None:
        assert all(tractor_success(100, random.Random(s)) for s in range(50))

    def test_empirical_rate_matches_chance(self) -> None:
        rng = _rng()
        hits = sum(tractor_success(60, rng) for _ in range(DRAWS))
        assert pytest.approx(0.60, abs=TOL) == hits / DRAWS

    def test_over_100_clamped(self) -> None:
        assert all(tractor_success(150, random.Random(s)) for s in range(20))

    def test_negative_clamped(self) -> None:
        assert not any(tractor_success(-30, random.Random(s)) for s in range(20))


# ===========================================================================
# Full roll API (roll_loot) — used by T4
# ===========================================================================


def _default_cfg() -> BandConfig:
    return BandConfig(
        band1_select_pct=10,
        band2_select_pct=20,
        band3_select_pct=70,
        tl_window=1,
        band1_qty=(1, 1, 3),
        band2_qty=(4, 8, 12),
        band3_qty=(10, 16, 22),
    )


class TestRollLoot:
    def test_returns_consistent_band_item_qty(self) -> None:
        cfg = _default_cfg()
        b1 = [LootCandidate("module", f"M{tl}", tech_level=tl) for tl in range(1, 11)]
        b2 = [LootCandidate("commodity", "OreCore", value=500)]
        b3 = [LootCandidate("commodity", "Booze", value=50)]
        rng = _rng()
        for _ in range(2000):
            roll = roll_loot(cfg, b1, b2, b3, criminal_tl=5, rng=rng)
            assert roll is not None
            if roll.band == 1:
                assert roll.item_type == "module"
                assert 1 <= roll.quantity <= 3
            elif roll.band == 2:
                assert roll.item_name == "OreCore"
                assert 4 <= roll.quantity <= 12
            else:
                assert roll.item_name == "Booze"
                assert 10 <= roll.quantity <= 22

    def test_band_distribution_within_full_roll(self) -> None:
        cfg = _default_cfg()
        b1 = [LootCandidate("module", "M5", tech_level=5)]
        b2 = [LootCandidate("commodity", "O", value=1)]
        b3 = [LootCandidate("commodity", "B", value=1)]
        rng = _rng()
        bands = Counter(roll_loot(cfg, b1, b2, b3, 5, rng).band for _ in range(DRAWS))
        assert pytest.approx(0.10, abs=TOL) == bands[1] / DRAWS
        assert pytest.approx(0.70, abs=TOL) == bands[3] / DRAWS

    def test_empty_chosen_band_returns_none(self) -> None:
        cfg = BandConfig(100, 0, 0, 1, (1, 1, 3), (4, 8, 12), (10, 16, 22))  # always band 1
        roll = roll_loot(cfg, [], [LootCandidate("commodity", "x")], [], 5, _rng())
        assert roll is None

    def test_deterministic_for_seed(self) -> None:
        cfg = _default_cfg()
        b1 = [LootCandidate("module", "M5", tech_level=5)]
        b2 = [LootCandidate("commodity", "O")]
        b3 = [LootCandidate("commodity", "B")]
        r1 = [roll_loot(cfg, b1, b2, b3, 5, random.Random(99)) for _ in range(10)]
        r2 = [roll_loot(cfg, b1, b2, b3, 5, random.Random(99)) for _ in range(10)]
        assert r1 == r2


def test_engine_does_not_touch_global_random(monkeypatch) -> None:
    """Guard: the engine must use only the injected rng, never global random."""

    def _boom(*_a, **_k):  # pragma: no cover - only fires on misuse
        raise AssertionError("engine called global random")

    monkeypatch.setattr(loot_engine.random.Random, "__init__", loot_engine.random.Random.__init__)
    monkeypatch.setattr(random, "random", _boom)
    monkeypatch.setattr(random, "choice", _boom)
    monkeypatch.setattr(random, "choices", _boom)
    rng = random.Random(5)
    # All public entrypoints exercised with the global helpers booby-trapped.
    roll_triangular(1, 1, 3, rng)
    select_band(10, 20, 70, rng)
    pick_band1_item([LootCandidate("module", "M5", tech_level=5)], 5, 1, rng)
    pick_commodity_item([LootCandidate("commodity", "B")], rng)
    tractor_success(50, rng)
