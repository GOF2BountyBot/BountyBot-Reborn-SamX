"""Pure, RNG-injectable loot selection engine (PvC looting, LOOT_JOURNAL T3).

This module is the *stateless* probability/selection core of the PvC looting
system.  It is deliberately free of any DB, config-object, or global-RNG
dependency so it can be exhaustively unit-tested with a seeded
:class:`random.Random`:

* :func:`roll_triangular` — discrete triangular ``(min, mode, max)`` quantity
  sampler (LOOT_JOURNAL §5.8.1–.3).
* :func:`select_band` — weighted band choice over Band 1/2/3
  (LOOT_JOURNAL §5.8.4 step 1).
* :func:`pick_band1_item` — Band-1 within-band pick with the ±TL window +
  nearest-TL net (§5.8.4 step 2, Band 1).
* :func:`pick_commodity_item` — Band-2/3 uniform within-band pick (§5.8.4 step 2,
  Bands 2 & 3).
* :func:`roll_loot` — the full spawn-side roll API (band → item → qty), used
  later by T4.
* :func:`tractor_chance` / :func:`tractor_success` — the M-5 tractor→chance
  resolver + success roll, used later by T5 at win.

Every sampler is **defensive**: it never trusts the numeric config it is handed
(T2 added no cross-field validation), so misconfigured triples (``min > max``,
``mode`` out of range, negative weights, …) are clamped/normalised to a safe
value with a logged warning rather than crashing or looping forever.

The caller (``LootService``) is responsible for resolving the per-guild knob
values and for owning the cached item pools; this module only does arithmetic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from shared import bblogger

flogger = bblogger.get_logger("loot-engine")

# Concrete inventory item_type strings for Band-1 equippables (LOOT_JOURNAL §7.4).
ItemType = Literal["primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"]

#: Identifier for each drop band.
Band = Literal[1, 2, 3]


@dataclass(frozen=True, slots=True)
class LootCandidate:
    """A single lootable entry in a cached pool.

    *item_type* is the CONCRETE inventory type used by the inventory write
    path (never the generic ``weapon`` alias).  *tech_level* is the item's TL
    (may be ``None`` for the rare un-levelled weapon); commodity entries carry
    their face *value* for the C-2 sell price and a ``None`` tech_level is fine.
    """

    item_type: ItemType
    name: str
    tech_level: int | None = None
    value: int = 0


@dataclass(frozen=True, slots=True)
class LootRoll:
    """The result of a full :func:`roll_loot` — what a criminal carries."""

    item_type: ItemType
    item_name: str
    quantity: int
    band: Band


# ---------------------------------------------------------------------------
# Triangular quantity sampler (§5.8.1–.3)
# ---------------------------------------------------------------------------


def triangular_weights(qty_min: int, qty_mode: int, qty_max: int) -> tuple[list[int], list[float]]:
    """Return ``(values, weights)`` for the discrete triangular ``(min, mode, max)``.

    The weight of value ``v`` ramps linearly up from ``min`` to ``mode`` and back
    down to ``max`` (a discrete tent).  ``mode == min`` gives a descending ramp
    (Band 1 → 50/33/17); a centred mode gives the symmetric Band-2/3 vectors.

    DEFENSIVE: the inputs are NOT trusted.  ``min``/``max`` are coerced to ints
    and swapped if reversed; ``mode`` is clamped into ``[min, max]``.  A
    degenerate single-point range (``min == max``) yields the single value at
    weight 1.  A warning is logged whenever a value had to be repaired.
    """
    lo, mode, hi = int(qty_min), int(qty_mode), int(qty_max)

    if lo > hi:
        flogger.warning(f"triangular: min {lo} > max {hi}; swapping to keep range valid")
        lo, hi = hi, lo
    if mode < lo or mode > hi:
        clamped = min(max(mode, lo), hi)
        flogger.warning(f"triangular: mode {mode} outside [{lo}, {hi}]; clamping to {clamped}")
        mode = clamped

    values = list(range(lo, hi + 1))
    # Peak height at the mode = the longer leg + 1 so the weight ramps down to 1
    # at the farther edge (and stays >= 1 at the nearer edge).  This reproduces
    # the journal's canonical vectors: (1,1,3) → [3,2,1] (50/33/17 descending
    # ramp); (4,8,12) → [1,2,3,4,5,4,3,2,1]; (10,16,22) → symmetric peak 7 at 16.
    peak = max(mode - lo, hi - mode) + 1
    weights = [float(peak - abs(v - mode)) for v in values]

    total = sum(weights)
    if total <= 0:
        # Cannot happen for a valid range (every weight >= 1), but stay total.
        flogger.warning("triangular: non-positive weight total; falling back to uniform")
        weights = [1.0] * len(values)
    return values, weights


def roll_triangular(qty_min: int, qty_mode: int, qty_max: int, rng: random.Random) -> int:
    """Sample one quantity from the discrete triangular ``(min, mode, max)``.

    ``rng`` is an injected :class:`random.Random` for deterministic tests; the
    global ``random`` module is never touched.  Always returns a value in the
    (repaired) ``[min, max]`` range.
    """
    values, weights = triangular_weights(qty_min, qty_mode, qty_max)
    return rng.choices(values, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Band selection (§5.8.4 step 1)
# ---------------------------------------------------------------------------


def select_band(band1_pct: int, band2_pct: int, band3_pct: int, rng: random.Random) -> Band:
    """Weighted choice of Band 1/2/3 from the three select-percentage knobs.

    DEFENSIVE: negative weights are floored to 0; if all three are non-positive
    the choice falls back to a uniform split (and warns).  The weights need NOT
    sum to 100 — ``random.choices`` normalises — but a warning is logged if they
    do not, so a transposed/typo'd knob is visible in the logs.
    """
    weights = [max(0.0, float(band1_pct)), max(0.0, float(band2_pct)), max(0.0, float(band3_pct))]
    total = sum(weights)
    if total <= 0:
        flogger.warning(f"select_band: non-positive weights {band1_pct}/{band2_pct}/{band3_pct}; using uniform")
        weights = [1.0, 1.0, 1.0]
    elif round(total) != 100:
        flogger.warning(
            f"select_band: select weights {band1_pct}/{band2_pct}/{band3_pct} sum to {total:g}, not 100; normalising"
        )
    return rng.choices((1, 2, 3), weights=weights, k=1)[0]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Within-band item pick (§5.8.4 step 2)
# ---------------------------------------------------------------------------


def band1_window_pool(
    pool: list[LootCandidate], criminal_tl: int, tl_window: int, min_tl: int, max_tl: int
) -> list[LootCandidate]:
    """Filter the cached Band-1 base pool to the ±``tl_window`` window of *criminal_tl*.

    The window ``[criminal_tl - tl_window, criminal_tl + tl_window]`` is clamped
    to ``[min_tl, max_tl]``.  Entries with a ``None`` tech_level are treated as
    out-of-window (they only enter via the nearest-TL net).  This is a pure
    in-memory filter over the cached set — no DB query (§5.8.4 static-cache req).
    """
    window = max(0, int(tl_window))
    lo = max(min_tl, criminal_tl - window)
    hi = min(max_tl, criminal_tl + window)
    return [c for c in pool if c.tech_level is not None and lo <= c.tech_level <= hi]


def nearest_tl_candidates(pool: list[LootCandidate], criminal_tl: int) -> list[LootCandidate]:
    """Return the cached Band-1 entries whose ``tech_level`` is closest to *criminal_tl*.

    Ties (multiple items at the same minimal distance) are all returned so the
    caller picks uniformly among them.  Used only by the nearest-TL safety net
    when the ±window pool is (theoretically) empty — NOT the criminal-equipment
    ``nearest_tl_pick`` helper (different input set; LOOT_JOURNAL §5.8.4 / m-3).
    """
    levelled = [c for c in pool if c.tech_level is not None]
    if not levelled:
        return list(pool)  # last-ditch: every entry, even un-levelled
    best = min(abs(c.tech_level - criminal_tl) for c in levelled)  # type: ignore[operator]
    return [c for c in levelled if abs(c.tech_level - criminal_tl) == best]  # type: ignore[operator]


def pick_band1_item(
    pool: list[LootCandidate],
    criminal_tl: int,
    tl_window: int,
    rng: random.Random,
    min_tl: int = 1,
    max_tl: int = 10,
) -> LootCandidate | None:
    """Pick one Band-1 item: uniform within the ±TL window, nearest-TL net on empty.

    Returns ``None`` only if the entire cached Band-1 pool is empty (a degenerate
    no-data state).  The window is never empty in practice (§5.8.4 — 14–63 items
    at every criminal TL), so the nearest-TL branch is a total-function guard.
    """
    if not pool:
        flogger.warning("pick_band1_item: empty Band-1 pool; cannot select")
        return None
    window_pool = band1_window_pool(pool, criminal_tl, tl_window, min_tl, max_tl)
    if window_pool:
        return rng.choice(window_pool)
    flogger.warning(f"pick_band1_item: empty ±{tl_window} TL window at criminal_tl={criminal_tl}; using nearest-TL net")
    return rng.choice(nearest_tl_candidates(pool, criminal_tl))


def pick_commodity_item(pool: list[LootCandidate], rng: random.Random) -> LootCandidate | None:
    """Uniform pick over a cached commodity item pool (Bands 2 & 3).

    Uniform over the *item* rows — a subcategory with more rows is proportionally
    likelier (booze over-representation is intentional, §5.8.4 C4).  Returns
    ``None`` only on an empty pool.
    """
    if not pool:
        flogger.warning("pick_commodity_item: empty commodity pool; cannot select")
        return None
    return rng.choice(pool)


# ---------------------------------------------------------------------------
# Tractor → chance resolver (M-5, §5.3)
# ---------------------------------------------------------------------------


def tractor_chance(tractor_module_names: list[str], chance_map: dict[str, int], no_tractor: int) -> int:
    """Resolve the loot chance (int percent) for an equipped-loadout module list.

    *chance_map* maps a tractor-beam name → its chance knob (built by
    ``LootService`` from the M-5 static map).  *tractor_module_names* is the list
    of equipped module names (the in-scope ``player_loadout`` at the win branch,
    LOOT_JOURNAL §7.6).  The first recognised beam wins; tractor beams are
    unique-equip (m-1) so there is at most one.  No/unknown beam → *no_tractor*.
    """
    for name in tractor_module_names:
        if name in chance_map:
            return chance_map[name]
    return no_tractor


def tractor_success(chance_pct: int, rng: random.Random) -> bool:
    """Roll the tractor success gate: ``True`` with probability ``chance_pct`` %.

    DEFENSIVE: the percentage is clamped to ``[0, 100]`` so a misconfigured knob
    can never produce a >100% or negative chance.  Uses ``rng.random()`` (the
    injected RNG) so a draw strictly below ``chance/100`` is a success — a 0%
    chance is always a miss, 100% always a hit.
    """
    pct = min(100, max(0, int(chance_pct)))
    return rng.random() < (pct / 100.0)


# ---------------------------------------------------------------------------
# Full spawn-side roll API (§5.8.4) — used by T4
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BandConfig:
    """The fully-resolved numeric knobs the engine needs for one full roll.

    ``LootService`` builds this from the per-guild-resolved GameConstants so the
    engine itself stays config-object-free and unit-testable with plain ints.
    """

    band1_select_pct: int
    band2_select_pct: int
    band3_select_pct: int
    tl_window: int
    band1_qty: tuple[int, int, int]  # (min, mode, max)
    band2_qty: tuple[int, int, int]
    band3_qty: tuple[int, int, int]
    min_tl: int = 1
    max_tl: int = 10


def roll_loot(
    cfg: BandConfig,
    band1_pool: list[LootCandidate],
    band2_pool: list[LootCandidate],
    band3_pool: list[LootCandidate],
    criminal_tl: int,
    rng: random.Random,
) -> LootRoll | None:
    """Full criminal-cargo roll: band-select → within-band item pick → qty roll.

    Returns the ``LootRoll`` a criminal carries, or ``None`` if the chosen band's
    pool is empty (degenerate no-data state).  Deterministic for a seeded *rng*.
    """
    band = select_band(cfg.band1_select_pct, cfg.band2_select_pct, cfg.band3_select_pct, rng)

    if band == 1:
        chosen = pick_band1_item(band1_pool, criminal_tl, cfg.tl_window, rng, cfg.min_tl, cfg.max_tl)
        qty_triple = cfg.band1_qty
    elif band == 2:
        chosen = pick_commodity_item(band2_pool, rng)
        qty_triple = cfg.band2_qty
    else:
        chosen = pick_commodity_item(band3_pool, rng)
        qty_triple = cfg.band3_qty

    if chosen is None:
        flogger.warning(f"roll_loot: band {band} pool empty at criminal_tl={criminal_tl}; no loot")
        return None

    quantity = roll_triangular(qty_triple[0], qty_triple[1], qty_triple[2], rng)
    return LootRoll(item_type=chosen.item_type, item_name=chosen.name, quantity=quantity, band=band)
