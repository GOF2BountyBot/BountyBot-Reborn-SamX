"""T10 — PvC loot balance-sanity pass (LOOT_JOURNAL §10 T10, §5.8).

A statistical, seeded, large-N pass over the REAL ``LootService`` cache (real
preload from the seeded throwaway Postgres) that asserts the *shipped* default
knobs produce the intended loot economy:

* band-select split ≈ **10 / 20 / 70** (Band 1 / 2 / 3, §5.8.4 step 1);
* per-band quantity means ≈ **1.67 / 8 / 16** with every qty inside its band
  range (§5.8.1–.3);
* every Band-1 pick is within **±1 TL** of the criminal (§5.8.4 step 2);
* **plasma** and **mission** commodities NEVER appear (§3 exclusion);
* the 3 excluded module kinds (Jump/TimeExtender/ShieldInjector) NEVER appear
  in a Band-1 pick (§3 exclusion);
* every drop is a genuine catalog item (no synthetic/empty rolls — §5.1 100 %
  carry guarantee).

Determinism & tolerance
-----------------------
The RNG is a SEEDED ``random.Random`` and the sample is large (N = 30 000), so
the suite is reproducible run-to-run.  Tolerances are deliberately GENEROUS so
the test cannot flake on a different seed / future minor pool change:

* **band split:** ±2.5 percentage points of the 10/20/70 targets.  At
  N = 30 000 the binomial standard error of a 10 % proportion is ≈ 0.17 pp, so
  ±2.5 pp is ~14 σ — far beyond any seeded variation.
* **qty means:** ±0.4 of the 1.67 / 8 / 16 targets (Band-1 sample is ~3 000
  draws over {1,2,3}; ±0.4 is many σ on all three).
* **TL window / exclusions:** these are HARD invariants (not statistical) and
  are asserted on every single draw — zero tolerance.

These assert the *integrated* statistics: the real cached pools + the real
per-guild-resolved default knobs + the real engine samplers, end-to-end.
"""

from __future__ import annotations

import os
import random
import sys
from collections import Counter
from contextlib import asynccontextmanager

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from persist.repositories.commodity_repository import CommodityRepository
from persist.repositories.module_repository import ModuleRepository
from services.loot_service import EXCLUDED_MODULE_TYPES, LootService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# pg_env lives in tests/ (one level up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")

# Large, seeded sample → reproducible + tight statistics.
_N = 30_000
_SEED = 2026_06_20

# Documented tolerances (see module docstring for the σ justification).
_BAND_SPLIT_TOL_PP = 2.5  # percentage points
_QTY_MEAN_TOL = 0.4  # absolute, on the per-band mean

# §5.8.4 default band-select targets (percent) + §5.8.1-.3 qty-mean targets.
_BAND_SELECT_TARGET = {1: 10.0, 2: 20.0, 3: 70.0}
_QTY_MEAN_TARGET = {1: 1.667, 2: 8.0, 3: 16.0}
_QTY_RANGE = {1: (1, 3), 2: (4, 12), 3: (10, 22)}


@asynccontextmanager
async def _pg_session():
    engine = create_async_engine(PG_ASYNC_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _warm_service_and_meta() -> tuple[LootService, dict, dict, set, set]:
    """Preload a real LootService and capture the metadata the asserts need.

    Returns ``(loot, band1_by_name, all_band_names, excluded_module_names,
    forbidden_commodity_names)`` — all read from the live seed data so the
    invariants are checked against the genuine catalog, never a hardcoded list.
    """
    async with _pg_session() as db:
        loot = LootService()
        await loot.preload_static_data(db)
        assert loot.is_loaded

        band1_by_name = {c.name: c for c in loot._band1_pool}

        # Every lootable catalog name (for the "real item" invariant).
        all_band_names = (
            {c.name for c in loot._band1_pool} | {c.name for c in loot._band2_pool} | {c.name for c in loot._band3_pool}
        )

        # The 3 excluded module kinds — must NEVER appear in a Band-1 pick (§3).
        modules = await ModuleRepository().list_all(db)
        excluded_module_names = {m.name for m in modules if getattr(m, "type", None) in EXCLUDED_MODULE_TYPES}

        # plasma + mission commodities — must NEVER appear in ANY pick (§3).
        commodities = await CommodityRepository().list_all(db)
        forbidden_commodity_names = {
            c.name for c in commodities if getattr(c, "subcategory", None) in ("plasma", "mission")
        }

    return loot, band1_by_name, all_band_names, excluded_module_names, forbidden_commodity_names


@pytest.mark.asyncio
async def test_band_split_and_qty_distributions_at_default_knobs() -> None:
    """Large-N seeded roll: band split ≈ 10/20/70 and qty means ≈ 1.67/8/16."""
    loot, band1_by_name, all_names, excluded_modules, forbidden_commodities = await _warm_service_and_meta()

    criminal_tl = 5  # mid-range so the ±1 TL window has a healthy Band-1 pool
    rng = random.Random(_SEED)

    band_counts: Counter[int] = Counter()
    qty_by_band: dict[int, list[int]] = {1: [], 2: [], 3: []}

    for _ in range(_N):
        roll = loot.roll_loot(criminal_tl, rng)
        assert roll is not None  # §5.1 — every roll yields a real carried item
        assert roll.item_name in all_names  # genuine catalog row
        band_counts[roll.band] += 1
        qty_by_band[roll.band].append(roll.quantity)

        lo, hi = _QTY_RANGE[roll.band]
        assert lo <= roll.quantity <= hi  # qty inside the band range (§5.8.1-.3)

        # HARD exclusions (zero tolerance) on every single draw.
        assert roll.item_name not in forbidden_commodities  # no plasma / mission (§3)
        if roll.band == 1:
            assert roll.item_name not in excluded_modules  # no Jump/TimeExt/ShieldInj (§3)
            cand = band1_by_name[roll.item_name]
            assert cand.tech_level is not None
            assert abs(cand.tech_level - criminal_tl) <= 1  # ±1 TL window (§5.8.4)

    # --- band-select split ≈ 10/20/70 (±2.5 pp) ---
    for band, target in _BAND_SELECT_TARGET.items():
        pct = 100.0 * band_counts[band] / _N
        assert abs(pct - target) <= _BAND_SPLIT_TOL_PP, (
            f"Band {band} select {pct:.2f}% off target {target}% (tol ±{_BAND_SPLIT_TOL_PP}pp); "
            f"counts={dict(band_counts)}"
        )

    # --- per-band qty means ≈ 1.67 / 8 / 16 (±0.4) ---
    for band, target in _QTY_MEAN_TARGET.items():
        samples = qty_by_band[band]
        assert samples, f"Band {band} never selected — cannot check qty mean"
        mean = sum(samples) / len(samples)
        assert abs(mean - target) <= _QTY_MEAN_TOL, (
            f"Band {band} qty mean {mean:.3f} off target {target} (tol ±{_QTY_MEAN_TOL}); n={len(samples)}"
        )


@pytest.mark.asyncio
async def test_band1_tl_window_holds_across_every_division_tl() -> None:
    """At every division-anchor TL (Bronze 1 / Silver 3 / Gold 6 / Platinum 8),
    EVERY Band-1 pick stays within ±1 TL of the criminal (clamped to [1,10])."""
    loot, band1_by_name, _all, excluded_modules, _forbidden = await _warm_service_and_meta()

    for criminal_tl in (1, 3, 6, 8, 10):  # division anchors + the clamp edge
        rng = random.Random(_SEED + criminal_tl)
        saw_band1 = False
        for _ in range(4_000):
            roll = loot.roll_loot(criminal_tl, rng)
            assert roll is not None
            if roll.band != 1:
                continue
            saw_band1 = True
            assert roll.item_name not in excluded_modules
            tl = band1_by_name[roll.item_name].tech_level
            assert tl is not None
            lo = max(1, criminal_tl - 1)
            hi = min(10, criminal_tl + 1)
            assert lo <= tl <= hi, f"TL {tl} outside [{lo},{hi}] at criminal_tl={criminal_tl}"
        assert saw_band1, f"Band-1 never drew at criminal_tl={criminal_tl} over 4000 rolls (10% weight)"


@pytest.mark.asyncio
async def test_band2_band3_membership_and_no_forbidden_commodities() -> None:
    """Band-2 picks are only ore_core/rare; Band-3 only the 5 bulk subcats; and
    plasma/mission never appear in either (§3 / §5.8.4 step 2)."""
    loot, _b1, _all, _excl, forbidden_commodities = await _warm_service_and_meta()

    band2_names = {c.name for c in loot._band2_pool}
    band3_names = {c.name for c in loot._band3_pool}
    # The pools themselves must exclude plasma/mission.
    assert forbidden_commodities.isdisjoint(band2_names)
    assert forbidden_commodities.isdisjoint(band3_names)

    rng = random.Random(_SEED + 99)
    for _ in range(_N):
        roll = loot.roll_loot(5, rng)
        assert roll is not None
        if roll.band == 2:
            assert roll.item_type == "commodity"
            assert roll.item_name in band2_names
        elif roll.band == 3:
            assert roll.item_type == "commodity"
            assert roll.item_name in band3_names
        assert roll.item_name not in forbidden_commodities
