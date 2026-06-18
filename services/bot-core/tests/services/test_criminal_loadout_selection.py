"""Unit tests for criminal loadout SELECTION logic (BALANCE_JOURNAL Task 2).

Covers the two rewritten sections of ``BountyService.generate_loadout``:

* § Spec C — MODULE selection: ``nearest_tl_pick`` (tie-break / random / empty),
  the priority walk (order, stop-at-max, no displacement, GUARANTEED always,
  TWO-GATE per-division pass/fail), and the filler tail (Filler-A before
  Filler-B, never-equip exclusion).
* § Spec D — PRIMARY long-range selection: ``tl_band_pick`` band redistribution
  (side-push, center-split incl. the TL4-LONG 55/45 case, TL1/TL9 boundary),
  and ``_select_primaries`` (min_long floor, total-long range, category-first).

Test rules: real deterministic objects (``SimpleNamespace``) preferred; at most
two mocks per test; RNG seeded for determinism.  Mirrors the style of
``tests/services/test_bounty_service.py``.
"""

from __future__ import annotations

import random
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: mock shared.bblogger before importing service code (defends against
# running this file in isolation; conftest also does this).
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.bounty_service import (
    BountyService,
    nearest_tl_pick,
    tl_band_pick,
)
from services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _mod(type_: str, tech_level: int, name: str | None = None, value: int = 100):
    """A Module-like row: only ``type`` / ``tech_level`` / ``name`` / ``value``
    matter to the selection rubric."""
    return SimpleNamespace(
        name=name or f"{type_}-TL{tech_level}",
        type=type_,
        tech_level=tech_level,
        value=value,
        extra_atts=None,
    )


def _weapon(tech_level: int, range_m: float, name: str | None = None, value: int = 100):
    """A PrimaryWeapon-like row with the canonical doubly-nested extra_atts."""
    return SimpleNamespace(
        name=name or f"w-TL{tech_level}-{int(range_m)}",
        tech_level=tech_level,
        value=value,
        dps=10.0,
        extra_atts={"extra_atts": {"range_m": range_m, "subtype": "auto-cannon"}},
    )


def _service_with_modules(modules: list) -> BountyService:
    """A BountyService whose item_repo.get_all('module') returns *modules*."""
    svc = BountyService()
    svc.item_repo = MagicMock()
    svc.item_repo.get_all = AsyncMock(return_value=modules)
    svc.config_repo = MagicMock()
    return svc


def _service_with_weapons(weapons: list) -> BountyService:
    svc = BountyService()
    svc.item_repo = MagicMock()
    svc.item_repo.get_all = AsyncMock(return_value=weapons)
    svc.config_repo = MagicMock()
    return svc


# ===========================================================================
# nearest_tl_pick
# ===========================================================================


def test_nearest_tl_pick_empty_returns_none():
    assert nearest_tl_pick([], item_tl=5, division="gold") is None


def test_nearest_tl_pick_picks_closest_tl():
    variants = [_mod("ArmourModule", 1), _mod("ArmourModule", 7), _mod("ArmourModule", 10)]
    # item_tl=6 → closest is TL7 (|7-6|=1 beats |10-6|=4 and |1-6|=5).
    chosen = nearest_tl_pick(variants, item_tl=6, division="bronze")
    assert chosen.tech_level == 7


@pytest.mark.parametrize("division", ["gold", "platinum"])
def test_nearest_tl_pick_tie_prefers_higher_for_gold_platinum(division):
    variants = [_mod("CloakModule", 4), _mod("CloakModule", 6)]
    # item_tl=5 equidistant to TL4 and TL6 → gold/platinum pick the HIGHER (TL6).
    chosen = nearest_tl_pick(variants, item_tl=5, division=division)
    assert chosen.tech_level == 6


@pytest.mark.parametrize("division", ["bronze", "silver"])
def test_nearest_tl_pick_tie_prefers_lower_for_bronze_silver(division):
    variants = [_mod("CloakModule", 4), _mod("CloakModule", 6)]
    # item_tl=5 equidistant → bronze/silver pick the LOWER (TL4).
    chosen = nearest_tl_pick(variants, item_tl=5, division=division)
    assert chosen.tech_level == 4


def test_nearest_tl_pick_same_tl_uniform_random():
    # Two distinct variants at the SAME (best) TL → both reachable, chosen at random.
    a = _mod("WeaponModModule", 5, name="Overdrive")
    b = _mod("PrimaryWeaponModModule", 5, name="Overcharge")
    variants = [a, b]
    random.seed(0)
    seen = {nearest_tl_pick(variants, item_tl=5, division="gold").name for _ in range(50)}
    assert seen == {"Overdrive", "Overcharge"}


# ===========================================================================
# § Spec C — priority walk (via _select_modules)
# ===========================================================================


def _all_combat_modules() -> list:
    """One variant of each of the 9 combat module types, at a single TL each so
    nearest-TL is deterministic."""
    return [
        _mod("ScannerModule", 2),
        _mod("ArmourModule", 3),
        _mod("ShieldModule", 3),
        _mod("CloakModule", 4),
        _mod("BoosterModule", 4),
        _mod("EmergencySystemModule", 6),
        _mod("RepairBotModule", 4),
        _mod("PrimaryWeaponModModule", 5),
        _mod("ThrusterModule", 3),
    ]


@pytest.mark.asyncio
async def test_priority_walk_fills_in_order_and_stops_at_max():
    """Walk fills Scanner→Armour→Shield→Cloak… in order and STOPS at max_modules
    (no displacement).  Platinum → all gated %s = 100 so every category fires."""
    svc = _service_with_modules(_all_combat_modules())
    random.seed(1)
    # max_modules=4 → exactly Scanner, Armour, Shield, Cloak (gold/plat gate passes).
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="platinum", max_modules=4, cfg=None)
    types_ = [m.type for m in equipped]
    assert types_ == ["ScannerModule", "ArmourModule", "ShieldModule", "CloakModule"]
    assert len(equipped) == 4  # stopped exactly at max_modules


@pytest.mark.asyncio
async def test_priority_walk_full_order_platinum():
    """Platinum with 9 slots equips all 9 combat categories in priority order."""
    svc = _service_with_modules(_all_combat_modules())
    random.seed(2)
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=5, division="platinum", max_modules=9, cfg=None)
    types_ = [m.type for m in equipped]
    assert types_ == [
        "ScannerModule",
        "ArmourModule",
        "ShieldModule",
        "CloakModule",
        "BoosterModule",
        "EmergencySystemModule",
        "RepairBotModule",
        "PrimaryWeaponModModule",
        "ThrusterModule",
    ]


@pytest.mark.asyncio
async def test_guaranteed_always_equipped_even_bronze():
    """GUARANTEED categories (Scanner/Armour/Shield/RepairBot/Thruster) equip for
    bronze too (no per-division gate)."""
    svc = _service_with_modules(_all_combat_modules())
    random.seed(3)
    # Bronze: cloak%=0, emergency%=0, weaponmod%=0, booster%=50.  With a big slot
    # budget the 5 guaranteed categories must ALL appear regardless of gate rolls.
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=3, division="bronze", max_modules=9, cfg=None)
    types_ = {m.type for m in equipped}
    for guaranteed in ("ScannerModule", "ArmourModule", "ShieldModule", "RepairBotModule", "ThrusterModule"):
        assert guaranteed in types_, f"{guaranteed} must always be equipped (bronze): {types_}"


@pytest.mark.asyncio
async def test_two_gate_bronze_zero_chance_never_equips_cloak():
    """Bronze cloak chance = 0 → cloak never equipped even with slots to spare."""
    svc = _service_with_modules(_all_combat_modules())
    for seed in range(25):
        random.seed(seed)
        equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="bronze", max_modules=9, cfg=None)
        assert "CloakModule" not in {m.type for m in equipped}, "bronze cloak% is 0"


@pytest.mark.asyncio
async def test_two_gate_platinum_full_chance_always_equips_cloak():
    """Platinum cloak chance = 100 → cloak always equipped when a slot is free."""
    svc = _service_with_modules(_all_combat_modules())
    for seed in range(25):
        random.seed(seed)
        equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="platinum", max_modules=9, cfg=None)
        assert "CloakModule" in {m.type for m in equipped}, "platinum cloak% is 100"


@pytest.mark.asyncio
async def test_two_gate_honors_per_division_rate_statistically():
    """Silver cloak chance = 25%: equip rate across many trials lands near 25%.

    Only Scanner/Armour/Shield precede Cloak and all have variants, so Cloak is
    always reached with a free slot (max_modules large)."""
    svc = _service_with_modules(_all_combat_modules())
    hits = 0
    trials = 400
    random.seed(12345)
    for _ in range(trials):
        equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="silver", max_modules=9, cfg=None)
        if "CloakModule" in {m.type for m in equipped}:
            hits += 1
    rate = hits / trials
    assert 0.15 <= rate <= 0.35, f"silver cloak rate {rate:.2f} should be ~0.25"


@pytest.mark.asyncio
async def test_two_gate_honors_per_division_rate_statistically_gold():
    """Gold cloak chance = 66%: equip rate across many trials lands near 66%.

    Mirrors the silver@25 case for the spec-called-out middle value (gold@66).
    Only Scanner/Armour/Shield precede Cloak and all have variants, so Cloak is
    always reached with a free slot (max_modules large) — the only thing gating
    it is the per-division 66% roll."""
    svc = _service_with_modules(_all_combat_modules())
    hits = 0
    trials = 400
    random.seed(98765)
    for _ in range(trials):
        equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="gold", max_modules=9, cfg=None)
        if "CloakModule" in {m.type for m in equipped}:
            hits += 1
    rate = hits / trials
    assert 0.58 <= rate <= 0.74, f"gold cloak rate {rate:.2f} should be ~0.66"


@pytest.mark.asyncio
async def test_failed_gate_leaves_slot_for_next_category():
    """A failed TWO-GATE roll does NOT consume the slot — the next category fills it.

    Bronze (cloak%=0, emergency%=0, weaponmod%=0) with booster%=50: with only
    base-3 + the gated categories present, a 4-slot ship still fills slot 4 from
    whichever later category succeeds, never leaving it empty when a guaranteed
    category (RepairBot/Thruster) remains."""
    svc = _service_with_modules(_all_combat_modules())
    random.seed(7)
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="bronze", max_modules=5, cfg=None)
    types_ = [m.type for m in equipped]
    # Base 3 always present; cloak (bronze 0) is skipped, slot passes onward.
    assert types_[:3] == ["ScannerModule", "ArmourModule", "ShieldModule"]
    assert "CloakModule" not in types_
    assert len(equipped) == 5  # all 5 slots filled despite the cloak gate failing


# ===========================================================================
# § Spec C — filler tail
# ===========================================================================


@pytest.mark.asyncio
async def test_filler_a_before_filler_b():
    """Filler-A (limit-1 uniques) is exhausted before Filler-B (∞-limit repeats)."""
    # No combat categories present; only one Filler-A type (Signature) + Filler-B.
    modules = [
        _mod("SignatureModule", 8),
        _mod("CompressorModule", 1),
        _mod("CabinModule", 3),
    ]
    svc = _service_with_modules(modules)
    random.seed(4)
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=4, division="gold", max_modules=4, cfg=None)
    types_ = [m.type for m in equipped]
    # Signature (Filler-A) appears exactly once and BEFORE any Filler-B fill.
    assert types_[0] == "SignatureModule"
    assert types_.count("SignatureModule") == 1
    # Remaining 3 slots are Filler-B (Compressor/Cabin), repeatable.
    assert all(t in {"CompressorModule", "CabinModule"} for t in types_[1:])
    assert len(equipped) == 4


@pytest.mark.asyncio
async def test_never_equip_types_excluded():
    """Banned / misleading no-op types are never equipped even if in the catalog."""
    modules = [
        _mod("TransfusionBeamModule", 8),
        _mod("ShieldInjectorModule", 9),
        _mod("TimeExtenderModule", 9),
        _mod("JumpDriveModule", 10),
        _mod("CabinModule", 3),  # the only legitimate filler
    ]
    svc = _service_with_modules(modules)
    random.seed(5)
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=5, division="platinum", max_modules=5, cfg=None)
    types_ = {m.type for m in equipped}
    for banned in ("TransfusionBeamModule", "ShieldInjectorModule", "TimeExtenderModule", "JumpDriveModule"):
        assert banned not in types_, f"{banned} must never be equipped"
    # Only Cabin (Filler-B) is eligible → fills all slots.
    assert types_ == {"CabinModule"}


@pytest.mark.asyncio
async def test_empty_catalog_yields_no_modules():
    svc = _service_with_modules([])
    equipped = await svc._select_modules(db=AsyncMock(), item_tl=5, division="gold", max_modules=8, cfg=None)
    assert equipped == []


# ===========================================================================
# § Spec D — tl_band_pick redistribution
# ===========================================================================

_WEIGHTS = GameConstants.PRIMARY_TL_BAND_WEIGHTS


def _by_tl(*tls: int) -> dict[int, list]:
    """Build a {tl: [weapon]} map for the given TLs (one weapon each)."""
    return {tl: [_weapon(tl, range_m=3000.0)] for tl in tls}


def test_band_pick_all_valid_distribution():
    """All three bands valid → ~70/20/10 split around target."""
    by_tl = _by_tl(4, 5, 6)  # target=5 → center 5, minus 4, plus 6 all valid
    random.seed(0)
    counts = {4: 0, 5: 0, 6: 0}
    for _ in range(2000):
        w = tl_band_pick(by_tl, target=5, weights=_WEIGHTS)
        counts[w.tech_level] += 1
    # center (TL5) dominant, minus (TL4) > plus (TL6).
    assert counts[5] > counts[4] > counts[6]


def test_band_pick_center_empty_tl4_long_splits_55_45():
    """TL4 LONG center-empty: target=4 invalid, splits 70 evenly → TL3=55 / TL5=45.

    Catalog fact: every LONG variant exists at TL3 and TL5 but NOT TL4."""
    by_tl = {3: [_weapon(3, 3000.0)], 5: [_weapon(5, 3000.0)]}  # no TL4
    random.seed(0)
    counts = {3: 0, 5: 0}
    for _ in range(4000):
        w = tl_band_pick(by_tl, target=4, weights=_WEIGHTS)
        counts[w.tech_level] += 1
    p3 = counts[3] / 4000
    # Expected TL3=0.55, TL5=0.45.  Allow sampling slack.
    assert 0.50 <= p3 <= 0.60, f"TL3 share {p3:.3f} should be ~0.55"
    assert counts[3] > counts[5]


def test_band_pick_boundary_tl1_pushes_minus_to_plus():
    """TL1: minus band (TL0) is OOB → its weight pushes to the plus band (TL2)."""
    by_tl = _by_tl(1, 2)  # target=1, minus=0 OOB, plus=2 valid
    random.seed(0)
    counts = {1: 0, 2: 0}
    for _ in range(2000):
        w = tl_band_pick(by_tl, target=1, weights=_WEIGHTS)
        counts[w.tech_level] += 1
    # center (TL1) keeps 70; plus (TL2) gets 10 + 20 (pushed minus) = 30.
    assert counts[1] > counts[2] > 0
    p2 = counts[2] / 2000
    assert 0.22 <= p2 <= 0.38, f"TL2 share {p2:.3f} should be ~0.30"


def test_band_pick_boundary_tl9_long_pushes_plus_to_minus():
    """TL9 LONG: plus band (TL10) has no LONG variant → pushes to minus (TL8)."""
    by_tl = _by_tl(8, 9)  # target=9, plus=10 absent, minus=8 valid
    random.seed(0)
    counts = {8: 0, 9: 0}
    for _ in range(2000):
        w = tl_band_pick(by_tl, target=9, weights=_WEIGHTS)
        counts[w.tech_level] += 1
    # center (TL9) keeps 70; minus (TL8) gets 20 + 10 (pushed plus) = 30.
    assert counts[9] > counts[8] > 0
    p8 = counts[8] / 2000
    assert 0.22 <= p8 <= 0.38, f"TL8 share {p8:.3f} should be ~0.30"


def test_band_pick_no_valid_band_returns_none():
    assert tl_band_pick({}, target=5, weights=_WEIGHTS) is None


# ===========================================================================
# § Spec D — _select_primaries floor + category-first
# ===========================================================================


def _mixed_catalog() -> list:
    """A LONG + SHORT weapon at every TL 3-7 (so every band is satisfiable)."""
    out: list = []
    for tl in range(3, 8):
        out.append(_weapon(tl, range_m=1300.0, name=f"short-{tl}"))  # SHORT (<=2600)
        out.append(_weapon(tl, range_m=3000.0, name=f"long-{tl}"))  # LONG (>2600)
    return out


def _is_long(w) -> bool:
    return w.extra_atts["extra_atts"]["range_m"] > GameConstants.LONG_RANGE_THRESHOLD_M


@pytest.mark.asyncio
async def test_select_primaries_min_long_floor_and_total_range():
    """min_long = ceil(pct*N); total long ∈ [min_long, N].  pct default 0.5."""
    svc = _service_with_weapons(_mixed_catalog())
    n = 5
    import math

    min_long = math.ceil(0.5 * n)  # 3
    for seed in range(40):
        random.seed(seed)
        equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=n, cfg=None)
        assert len(equipped) == n
        n_long = sum(1 for w in equipped if _is_long(w))
        assert min_long <= n_long <= n, f"long count {n_long} out of [{min_long}, {n}]"


@pytest.mark.asyncio
async def test_select_primaries_single_slot_is_long():
    """N=1 → ceil(0.5*1)=1 → the only slot is always LONG (the Betty/odd-count skew)."""
    svc = _service_with_weapons(_mixed_catalog())
    for seed in range(20):
        random.seed(seed)
        equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=1, cfg=None)
        assert len(equipped) == 1
        assert _is_long(equipped[0]), "single-slot ships are always long (floor)"


@pytest.mark.asyncio
async def test_select_primaries_category_first_long_only_catalog():
    """Category-first: a LONG slot picks a LONG weapon; SHORT slots fall back to
    LONG only if no SHORT exists anywhere (cross-bucket guard).  Here every slot
    ends LONG because the catalog has only LONG weapons."""
    long_only = [_weapon(tl, range_m=3000.0) for tl in range(3, 8)]
    svc = _service_with_weapons(long_only)
    random.seed(0)
    equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=4, cfg=None)
    assert len(equipped) == 4
    assert all(_is_long(w) for w in equipped)


@pytest.mark.asyncio
async def test_select_primaries_empty_catalog():
    svc = _service_with_weapons([])
    equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=3, cfg=None)
    assert equipped == []
