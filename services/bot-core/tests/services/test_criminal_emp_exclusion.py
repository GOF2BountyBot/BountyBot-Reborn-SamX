"""Tests for Thread 6 — exclude primarily-EMP weapons from CRIMINAL selection.

BALANCE_JOURNAL §A Thread 6 (locked): a primary or secondary is dropped from
the CRIMINAL candidate pool when ``emp_damage > real_damage`` (toggle, default
ON), because the engine applies 0 HP delta for ``emp_damage`` (phase-2+ deferred)
so EMP-dominant weapons do ~no real damage → free player win.

real_damage source per class:
  - PRIMARY:   ``damage_per_shot`` from inner extra_atts.
  - SECONDARY: the ``damage`` column on the secondary_weapon ORM row.

Covers:
* ``_is_primarily_emp`` classification of the full locked example set.
* ``_select_primaries`` excludes EMP-dominant primaries with toggle ON, includes
  with toggle OFF, and never starves a TL band (selection still succeeds).
* the criminal secondary path drops EMP-dominant secondaries (EMP Rockets) with
  toggle ON, keeps real-damage EMP (Dephase EMP) and includes the rockets OFF.

Test rules: real ``SimpleNamespace`` rows; ≤2 mocks/test; RNG seeded.
"""

from __future__ import annotations

import random
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.bounty_service import BountyService, _is_primarily_emp

# ---------------------------------------------------------------------------
# Builders (mirror real catalog fields)
# ---------------------------------------------------------------------------


def _primary(name: str, tech_level: int, range_m: float, dps: float, emp: float | None = None):
    """A PrimaryWeapon-like row. ``dps`` -> damage_per_shot; ``emp`` -> emp_damage
    (omitted entirely when None, to exercise the absent-key default path)."""
    inner: dict = {"range_m": range_m, "subtype": "auto-cannon", "damage_per_shot": dps}
    if emp is not None:
        inner["emp_damage"] = emp
    return SimpleNamespace(
        name=name,
        tech_level=tech_level,
        value=100,
        dps=float(dps),
        extra_atts={"extra_atts": inner},
    )


def _secondary(name: str, tech_level: int, damage: int, subtype: str, emp: float | None = None):
    """A SecondaryWeapon-like row: ``damage`` is the ORM column; ``emp`` goes in
    inner extra_atts (omitted when None)."""
    inner: dict = {"subtype": subtype, "range_m": 3000.0}
    if emp is not None:
        inner["emp_damage"] = emp
    return SimpleNamespace(
        name=name,
        tech_level=tech_level,
        value=100,
        dps=5.0,
        damage=damage,
        extra_atts={"extra_atts": inner},
    )


# ---------------------------------------------------------------------------
# 1. _is_primarily_emp — full locked example set
# ---------------------------------------------------------------------------

# (name, builder, expected) — values mirror the live catalog (read-only DB verified).
_PRIMARY_CASES = [
    # EMP-blasters: dps 0 < emp → EXCLUDE
    ("Dia EMP Mk III", _primary("Dia EMP Mk III", 6, 2300.0, dps=0, emp=8), True),
    ("Sol EMP Mk II", _primary("Sol EMP Mk II", 5, 2000.0, dps=0, emp=5), True),
    ("Luna EMP Mk I", _primary("Luna EMP Mk I", 4, 1400.0, dps=0, emp=3), True),
    # Real-damage primaries with no emp key → KEEP
    ("Micro Gun MK I", _primary("Micro Gun MK I", 1, 1300.0, dps=12), False),
    ("64MJ Railgun", _primary("64MJ Railgun", 3, 2500.0, dps=40), False),
]

_SECONDARY_CASES = [
    # EMP-dominant secondaries → EXCLUDE
    ("EMP Rocket Mk I", _secondary("EMP Rocket Mk I", 6, damage=10, subtype="rocket", emp=45), True),
    ("EMP Rocket Mk II", _secondary("EMP Rocket Mk II", 7, damage=30, subtype="rocket", emp=60), True),
    ("Mamba EMP", _secondary("Mamba EMP", 7, damage=0, subtype="missile", emp=100), True),
    ("Neétha EMP", _secondary("Neétha EMP", 3, damage=0, subtype="mine", emp=500), True),
    ("EMP GL I", _secondary("EMP GL I", 4, damage=2, subtype="emp-bomb", emp=80), True),
    ("EMP GL II", _secondary("EMP GL II", 5, damage=2, subtype="emp-bomb", emp=150), True),
    ("EMP GL DX", _secondary("EMP GL DX", 6, damage=4, subtype="emp-bomb", emp=300), True),
    # KEEP: real damage >= emp (Dephase EMP: 120 >= 100), or no emp at all.
    ("Dephase EMP", _secondary("Dephase EMP", 8, damage=120, subtype="missile", emp=100), False),
    ("Edo", _secondary("Edo", 1, damage=70, subtype="missile"), False),
    ("S'koonn", _secondary("S'koonn", 3, damage=140, subtype="missile"), False),
    ("Armour Rocket", _secondary("Armour Rocket", 5, damage=72, subtype="rocket"), False),
    ("Intelli Jet", _secondary("Intelli Jet", 5, damage=100, subtype="missile"), False),
    ("Jet Rocket", _secondary("Jet Rocket", 3, damage=70, subtype="rocket"), False),
    ("Nuke", _secondary("Hades Nuke", 9, damage=2000, subtype="nuke"), False),
    ("Shock Blast", _secondary("Shock Blast", 9, damage=140, subtype="shock-blast"), False),
]


@pytest.mark.parametrize("name, weapon, expected", _PRIMARY_CASES, ids=[c[0] for c in _PRIMARY_CASES])
def test_is_primarily_emp_primary(name, weapon, expected):
    assert _is_primarily_emp(weapon, is_secondary=False) is expected


@pytest.mark.parametrize("name, weapon, expected", _SECONDARY_CASES, ids=[c[0] for c in _SECONDARY_CASES])
def test_is_primarily_emp_secondary(name, weapon, expected):
    assert _is_primarily_emp(weapon, is_secondary=True) is expected


def test_is_primarily_emp_tie_keeps_weapon():
    """emp == real_damage is NOT > → kept (strict greater-than)."""
    w = _secondary("Tie", 5, damage=100, subtype="missile", emp=100)
    assert _is_primarily_emp(w, is_secondary=True) is False


def test_is_primarily_emp_absent_emp_key_is_zero():
    """A weapon with no emp_damage key is never EMP-dominant (default 0)."""
    w = _primary("NoEmp", 5, 1300.0, dps=0)  # dps 0, emp absent → 0 > 0 is False
    assert _is_primarily_emp(w, is_secondary=False) is False


# ---------------------------------------------------------------------------
# 2. _select_primaries — toggle ON excludes, OFF includes, no starvation
# ---------------------------------------------------------------------------


def _service_with_weapons(weapons: list) -> BountyService:
    svc = BountyService()
    svc.item_repo = MagicMock()
    svc.item_repo.get_all = AsyncMock(return_value=weapons)
    svc.config_repo = MagicMock()
    return svc


def _primary_catalog_with_emp() -> list:
    """Real-damage SHORT+LONG at TL 4-6, plus the 3 SHORT EMP-blasters at their TLs."""
    out: list = []
    for tl in range(4, 7):
        out.append(_primary(f"short-{tl}", tl, range_m=1300.0, dps=15))
        out.append(_primary(f"long-{tl}", tl, range_m=3000.0, dps=15))
    out.append(_primary("Luna EMP Mk I", 4, range_m=1400.0, dps=0, emp=3))
    out.append(_primary("Sol EMP Mk II", 5, range_m=2000.0, dps=0, emp=5))
    out.append(_primary("Dia EMP Mk III", 6, range_m=2300.0, dps=0, emp=8))
    return out


@pytest.mark.asyncio
async def test_select_primaries_toggle_on_excludes_emp():
    """cfg None → toggle default ON → no EMP-blaster ever appears in the loadout."""
    svc = _service_with_weapons(_primary_catalog_with_emp())
    emp_names = {"Luna EMP Mk I", "Sol EMP Mk II", "Dia EMP Mk III"}
    for seed in range(60):
        random.seed(seed)
        equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=4, cfg=None)
        assert len(equipped) == 4
        assert not (emp_names & {w.name for w in equipped}), f"EMP leaked at seed {seed}"


@pytest.mark.asyncio
async def test_select_primaries_toggle_off_allows_emp():
    """Toggle OFF → EMP-blasters are selectable again (appear across seeds)."""
    cfg = SimpleNamespace(criminal_exclude_emp_weapons=False)
    svc = _service_with_weapons(_primary_catalog_with_emp())
    seen_emp = False
    emp_names = {"Luna EMP Mk I", "Sol EMP Mk II", "Dia EMP Mk III"}
    for seed in range(120):
        random.seed(seed)
        equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=4, cfg=cfg)
        if emp_names & {w.name for w in equipped}:
            seen_emp = True
            break
    assert seen_emp, "EMP-blaster never selected with toggle OFF — filter not gated by cfg"


@pytest.mark.asyncio
async def test_select_primaries_not_starved_after_emp_removal():
    """Removing the 3 SHORT EMP-blasters must not empty any band: selection still
    fills every slot across all item_tl in [1, 9] and divisions."""
    # Mirror the real SHORT-by-TL coverage: ≥1 real SHORT primary at every TL 1-9.
    catalog: list = []
    for tl in range(1, 10):
        catalog.append(_primary(f"short-{tl}", tl, range_m=1300.0, dps=15))
    # LONG present at most TLs (TL4 intentionally LONG-empty, like the live catalog).
    for tl in (1, 2, 3, 5, 6, 7, 8, 9):
        catalog.append(_primary(f"long-{tl}", tl, range_m=3000.0, dps=15))
    # The EMP-blasters that get filtered out.
    catalog.append(_primary("Luna EMP Mk I", 4, range_m=1400.0, dps=0, emp=3))
    catalog.append(_primary("Sol EMP Mk II", 5, range_m=2000.0, dps=0, emp=5))
    catalog.append(_primary("Dia EMP Mk III", 6, range_m=2300.0, dps=0, emp=8))
    svc = _service_with_weapons(catalog)
    for item_tl in range(1, 10):
        for seed in range(10):
            random.seed(seed)
            equipped = await svc._select_primaries(db=AsyncMock(), item_tl=item_tl, n_slots=3, cfg=None)
            assert len(equipped) == 3, f"starved at item_tl={item_tl}, seed={seed}"


@pytest.mark.asyncio
async def test_select_primaries_all_emp_catalog_returns_empty():
    """Edge: a catalog of ONLY EMP-dominant primaries filters to empty → []."""
    only_emp = [
        _primary("Luna EMP Mk I", 4, range_m=1400.0, dps=0, emp=3),
        _primary("Sol EMP Mk II", 5, range_m=2000.0, dps=0, emp=5),
        _primary("Dia EMP Mk III", 6, range_m=2300.0, dps=0, emp=8),
    ]
    svc = _service_with_weapons(only_emp)
    random.seed(0)
    equipped = await svc._select_primaries(db=AsyncMock(), item_tl=5, n_slots=3, cfg=None)
    assert equipped == []


# ---------------------------------------------------------------------------
# 3. Secondary path — generate_loadout drops EMP-dominant secondaries
# ---------------------------------------------------------------------------


def _secondary_pool() -> list:
    """A realistic small secondary catalog around TL5-7, incl. the EMP Rockets
    (rocket subtype — survive the deferred/min-damage filters) and Dephase EMP."""
    return [
        _secondary("Armour Rocket", 5, damage=72, subtype="rocket"),
        _secondary("Intelli Jet", 5, damage=100, subtype="missile"),
        _secondary("EMP Rocket Mk I", 6, damage=10, subtype="rocket", emp=45),
        _secondary("EMP Rocket Mk II", 7, damage=30, subtype="rocket", emp=60),
        _secondary("Dephase EMP", 8, damage=120, subtype="missile", emp=100),
    ]


def _ship(max_secondaries: int):
    return SimpleNamespace(
        name="Pirate Ship",
        value=1000,
        armour=200,
        emoji=None,
        max_primaries=0,
        max_modules=0,
        max_turrets=0,
        max_secondaries=max_secondaries,
    )


def _loadout_service(secondaries: list, ship) -> BountyService:
    """A BountyService stubbed so generate_loadout reaches ONLY the secondary
    block: ship query returns *ship*; SecondaryWeaponRepository.list_all returns
    *secondaries*; find_item_tl is unused (no primaries/turrets/modules)."""
    svc = BountyService()
    svc.item_repo = MagicMock()
    svc.item_repo.get_all = AsyncMock(return_value=[])

    # Patch the Ship query path: generate_loadout does db.execute(select(Ship)...)
    # then .scalars().all().  Return our single ship.
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[ship])
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    svc_db = AsyncMock()
    svc_db.execute = AsyncMock(return_value=result)
    return svc, svc_db


@pytest.mark.asyncio
async def test_generate_loadout_secondary_toggle_on_excludes_emp_rockets(monkeypatch):
    """Toggle ON (cfg None) → EMP Rockets never equipped; real-damage kept."""
    import services.bounty_service as bs

    ship = _ship(max_secondaries=3)
    svc, db = _loadout_service(_secondary_pool(), ship)

    fake_repo = MagicMock()
    fake_repo.list_all = AsyncMock(return_value=_secondary_pool())
    monkeypatch.setattr(bs, "SecondaryWeaponRepository", lambda: fake_repo)
    # ship_tech_level_for_value isn't relevant — force ship via the all-ships path.
    monkeypatch.setattr(bs, "ship_tech_level_for_value", lambda v: 6)
    svc.find_item_tl = AsyncMock(return_value=6)

    emp = {"EMP Rocket Mk I", "EMP Rocket Mk II"}
    for seed in range(40):
        random.seed(seed)
        out = await svc.generate_loadout(db, tech_level=7, division="gold", cfg=None)
        names = {s["name"] for s in out["secondaries"]}
        assert not (emp & names), f"EMP Rocket leaked at seed {seed}: {names}"


@pytest.mark.asyncio
async def test_generate_loadout_secondary_toggle_off_allows_emp_rockets(monkeypatch):
    """Toggle OFF → EMP Rockets are eligible again (appear across seeds)."""
    import services.bounty_service as bs

    ship = _ship(max_secondaries=3)
    svc, db = _loadout_service(_secondary_pool(), ship)

    fake_repo = MagicMock()
    fake_repo.list_all = AsyncMock(return_value=_secondary_pool())
    monkeypatch.setattr(bs, "SecondaryWeaponRepository", lambda: fake_repo)
    monkeypatch.setattr(bs, "ship_tech_level_for_value", lambda v: 6)
    svc.find_item_tl = AsyncMock(return_value=6)

    cfg = SimpleNamespace(criminal_exclude_emp_weapons=False)
    emp = {"EMP Rocket Mk I", "EMP Rocket Mk II"}
    seen = False
    for seed in range(80):
        random.seed(seed)
        out = await svc.generate_loadout(db, tech_level=7, division="gold", cfg=cfg)
        if emp & {s["name"] for s in out["secondaries"]}:
            seen = True
            break
    assert seen, "EMP Rocket never appeared with toggle OFF — secondary filter not cfg-gated"


@pytest.mark.asyncio
async def test_generate_loadout_secondary_keeps_dephase_emp(monkeypatch):
    """Dephase EMP (real 120 >= emp 100) must remain eligible with toggle ON."""
    import services.bounty_service as bs

    # Pool of only Dephase EMP so it MUST be picked if eligible.
    pool = [_secondary("Dephase EMP", 8, damage=120, subtype="missile", emp=100)]
    ship = _ship(max_secondaries=1)
    svc, db = _loadout_service(pool, ship)

    fake_repo = MagicMock()
    fake_repo.list_all = AsyncMock(return_value=pool)
    monkeypatch.setattr(bs, "SecondaryWeaponRepository", lambda: fake_repo)
    monkeypatch.setattr(bs, "ship_tech_level_for_value", lambda v: 9)
    svc.find_item_tl = AsyncMock(return_value=9)

    random.seed(0)
    out = await svc.generate_loadout(db, tech_level=9, division="platinum", cfg=None)
    assert {s["name"] for s in out["secondaries"]} == {"Dephase EMP"}
