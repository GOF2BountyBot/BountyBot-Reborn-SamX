"""Unit tests for LootService (services/loot_service.py).

Covers the static-cache build (eligibility pools, tractor map), the cache
"built once / not re-queried per call" property (via repo call-count spies),
and the per-guild knob resolution.  Item rows are SimpleNamespace stand-ins
whose ``type``/``subcategory``/``tech_level`` mirror the REAL DB schema (module
rows carry the module-kind string in ``Item.type``; commodities carry their band
subcategory), verified against the live seed data — NOT the bot-core game-data
fixture, whose module ``type`` is the inventory alias ``"module"``.
"""

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
if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services.game_constants import GameConstants
from services.loot_service import (
    BAND2_SUBCATEGORIES,
    BAND3_SUBCATEGORIES,
    EXCLUDED_MODULE_TYPES,
    LootService,
)

# asyncio_mode=auto (pytest.ini) auto-detects async tests — no module-level mark
# needed (and a module mark would falsely tag the sync resolution tests).


# ---------------------------------------------------------------------------
# Fake item rows mirroring the real DB schema.
# ---------------------------------------------------------------------------


def _module(name: str, kind: str, tl: int, value: int = 1000) -> SimpleNamespace:
    return SimpleNamespace(name=name, type=kind, tech_level=tl, value=value)


def _weapon(name: str, tl: int | None, value: int = 5000) -> SimpleNamespace:
    return SimpleNamespace(name=name, type="PrimaryWeapon", tech_level=tl, value=value)


def _commodity(name: str, sub: str, tl: int | None, value: int) -> SimpleNamespace:
    return SimpleNamespace(name=name, type="commodity", subcategory=sub, tech_level=tl, value=value)


def _modules() -> list[SimpleNamespace]:
    return [
        _module("E2 Exoclad", "ArmourModule", 1),
        _module('AB-1 "Retractor"', "TractorBeamModule", 4, value=8516),
        _module('AB-2 "Glue Gun"', "TractorBeamModule", 5, value=25638),
        _module('AB-3 "Kingfisher"', "TractorBeamModule", 7, value=56378),
        _module('AB-4 "Octopus"', "TractorBeamModule", 8, value=179300),
        # The three EXCLUDED kinds — must never appear in Band 1.
        _module("Jumpy", "JumpDriveModule", 6),
        _module("Stretcher", "TimeExtenderModule", 6),
        _module("Injector", "ShieldInjectorModule", 6),
    ]


def _commodities() -> list[SimpleNamespace]:
    return [
        _commodity("Iron Ore Core", "ore_core", 3, 800),
        _commodity("Platinum", "rare", 6, 1500),
        _commodity("Cheap Booze", "booze", 1, 30),
        _commodity("Tech Widget", "technical", 4, 120),
        _commodity("Iron Ore", "ore", 2, 60),
        _commodity("Standard Goods", "standard", 3, 90),
        _commodity("Space Junk", "waste", 5, 10),
        # plasma + mission are out of domain (§3) — must be excluded from all bands.
        _commodity("Plasma Cell", "plasma", 7, 999),
        _commodity("Secret Cargo", "mission", 9, 0),
    ]


def _make_service() -> tuple[LootService, dict[str, AsyncMock]]:
    """Return a LootService with all repos mocked + a dict of the mocks."""
    svc = LootService()
    repos = {
        "primary": AsyncMock(),
        "secondary": AsyncMock(),
        "turret": AsyncMock(),
        "module": AsyncMock(),
        "commodity": AsyncMock(),
    }
    repos["module"].list_all.return_value = _modules()
    repos["primary"].list_all.return_value = [_weapon("Micro Gun", 1), _weapon("Wolverine", 9)]
    repos["secondary"].list_all.return_value = [_weapon("Rocket", None)]  # un-levelled
    repos["turret"].list_all.return_value = [_weapon("Hammerhead", 5)]
    repos["commodity"].list_all.return_value = _commodities()
    svc.primary_weapon_repo = repos["primary"]
    svc.secondary_weapon_repo = repos["secondary"]
    svc.turret_weapon_repo = repos["turret"]
    svc.module_repo = repos["module"]
    svc.commodity_repo = repos["commodity"]
    return svc, repos


async def _loaded_service() -> tuple[LootService, dict[str, AsyncMock]]:
    svc, repos = _make_service()
    await svc.preload_static_data(MagicMock())
    return svc, repos


# ===========================================================================
# Cache build + eligibility pools (m-2)
# ===========================================================================


class TestCacheBuild:
    async def test_is_loaded_flag(self) -> None:
        svc, _ = _make_service()
        assert not svc.is_loaded
        await svc.preload_static_data(MagicMock())
        assert svc.is_loaded

    async def test_band1_excludes_three_module_kinds(self) -> None:
        svc, _ = await _loaded_service()
        names = {c.name for c in svc._band1_pool if c.item_type == "module"}
        assert "Jumpy" not in names and "Stretcher" not in names and "Injector" not in names
        assert "E2 Exoclad" in names
        # All 4 tractor beams are lootable (they are NOT excluded kinds).
        assert 'AB-4 "Octopus"' in names

    async def test_band1_tags_concrete_item_types(self) -> None:
        svc, _ = await _loaded_service()
        by_type = {}
        for c in svc._band1_pool:
            by_type.setdefault(c.item_type, []).append(c.name)
        assert "module" in by_type
        assert "Micro Gun" in by_type["primary_weapon"]
        assert "Rocket" in by_type["secondary_weapon"]
        assert "Hammerhead" in by_type["turret_weapon"]
        # No generic "weapon" alias ever used (M-3).
        assert "weapon" not in by_type

    async def test_band2_exact_subcategories(self) -> None:
        svc, _ = await _loaded_service()
        names = {c.name for c in svc._band2_pool}
        assert names == {"Iron Ore Core", "Platinum"}  # ore_core + rare only

    async def test_band3_exact_subcategories(self) -> None:
        svc, _ = await _loaded_service()
        names = {c.name for c in svc._band3_pool}
        assert names == {"Cheap Booze", "Tech Widget", "Iron Ore", "Standard Goods", "Space Junk"}

    async def test_plasma_and_mission_never_appear(self) -> None:
        svc, _ = await _loaded_service()
        all_names = {c.name for c in svc._band1_pool + svc._band2_pool + svc._band3_pool}
        assert "Plasma Cell" not in all_names
        assert "Secret Cargo" not in all_names

    async def test_commodity_values_cached_for_sell_price(self) -> None:
        svc, _ = await _loaded_service()
        booze = next(c for c in svc._band3_pool if c.name == "Cheap Booze")
        assert booze.value == 30  # Item.value carried for C-2 sell price

    async def test_subcategory_frozensets_are_disjoint_and_complete(self) -> None:
        # Sanity on the band membership constants themselves.
        assert BAND2_SUBCATEGORIES.isdisjoint(BAND3_SUBCATEGORIES)
        assert "plasma" not in (BAND2_SUBCATEGORIES | BAND3_SUBCATEGORIES)
        assert "mission" not in (BAND2_SUBCATEGORIES | BAND3_SUBCATEGORIES)
        assert {"JumpDriveModule", "TimeExtenderModule", "ShieldInjectorModule"} == EXCLUDED_MODULE_TYPES


# ===========================================================================
# Cache: built once, not re-queried per call
# ===========================================================================


class TestCacheNotRequeriedPerCall:
    async def test_repos_queried_once_then_rolls_hit_cache(self) -> None:
        svc, repos = await _loaded_service()
        for r in repos.values():
            assert r.list_all.call_count == 1
        rng = random.Random(3)
        for _ in range(500):
            svc.roll_loot(5, rng)
            svc.loot_chance(['AB-4 "Octopus"'])
        # Still exactly one DB load — rolls read the in-memory cache only.
        for r in repos.values():
            assert r.list_all.call_count == 1

    async def test_clear_cache_resets(self) -> None:
        svc, _ = await _loaded_service()
        assert svc.is_loaded
        svc.clear_static_cache()
        assert not svc.is_loaded
        with pytest.raises(RuntimeError):
            svc.roll_loot(5, random.Random(1))

    async def test_rebuild_on_reload(self) -> None:
        svc, repos = await _loaded_service()
        await svc.preload_static_data(MagicMock())  # simulate seed reload
        for r in repos.values():
            assert r.list_all.call_count == 2  # rebuilt
        assert svc.is_loaded


# ===========================================================================
# Tractor → chance static map (M-5)
# ===========================================================================


class TestTractorChanceMap:
    async def test_four_beams_map_to_tiers(self) -> None:
        svc, _ = await _loaded_service()
        assert svc._tractor_chance_map == {
            'AB-1 "Retractor"': 20,
            'AB-2 "Glue Gun"': 40,
            'AB-3 "Kingfisher"': 60,
            'AB-4 "Octopus"': 80,
        }

    async def test_loot_chance_for_each_beam(self) -> None:
        svc, _ = await _loaded_service()
        assert svc.loot_chance(['AB-1 "Retractor"']) == 20
        assert svc.loot_chance(['AB-3 "Kingfisher"']) == 60
        assert svc.loot_chance(['AB-4 "Octopus"']) == 80

    async def test_no_beam_zero_chance(self) -> None:
        svc, _ = await _loaded_service()
        assert svc.loot_chance(["E2 Exoclad", "Beamshield II"]) == 0
        assert svc.loot_chance([]) == 0

    async def test_unknown_module_zero_chance(self) -> None:
        svc, _ = await _loaded_service()
        assert svc.loot_chance(["Totally Made Up Beam"]) == 0

    async def test_unexpected_tractor_tl_maps_to_no_tractor(self) -> None:
        svc, repos = _make_service()
        repos["module"].list_all.return_value = [_module('AB-X "Weird"', "TractorBeamModule", 99)]
        repos["primary"].list_all.return_value = []
        repos["secondary"].list_all.return_value = []
        repos["turret"].list_all.return_value = []
        repos["commodity"].list_all.return_value = []
        await svc.preload_static_data(MagicMock())
        assert svc._tractor_chance_map == {'AB-X "Weird"': GameConstants.LOOT_CHANCE_NO_TRACTOR}

    async def test_per_guild_chance_override(self) -> None:
        svc, _ = await _loaded_service()
        cfg = SimpleNamespace(loot_chance_tractor_t4=95)  # override Octopus to 95
        assert svc.loot_chance(['AB-4 "Octopus"'], guild_config=cfg) == 95
        # Other beams fall back to defaults.
        assert svc.loot_chance(['AB-1 "Retractor"'], guild_config=cfg) == 20

    async def test_resolve_tractor_map_requires_load(self) -> None:
        svc, _ = _make_service()
        with pytest.raises(RuntimeError):
            svc.resolve_tractor_chance_map(None)


# ===========================================================================
# Per-guild BandConfig resolution
# ===========================================================================


class TestBandConfigResolution:
    def test_defaults_when_no_guild(self) -> None:
        cfg = LootService.resolve_band_config(None)
        assert cfg.band1_select_pct == 10
        assert cfg.band2_select_pct == 20
        assert cfg.band3_select_pct == 70
        assert cfg.tl_window == 1
        assert cfg.band1_qty == (1, 1, 3)  # (min, mode, max)
        assert cfg.band2_qty == (4, 8, 12)
        assert cfg.band3_qty == (10, 16, 22)
        assert cfg.min_tl == GameConstants.MIN_TECH_LEVEL
        assert cfg.max_tl == GameConstants.MAX_TECH_LEVEL

    def test_guild_override_applies(self) -> None:
        guild = SimpleNamespace(
            loot_band1_select_pct=50,
            loot_band1_tl_window=2,
            loot_band3_qty_max=99,
        )
        cfg = LootService.resolve_band_config(guild)
        assert cfg.band1_select_pct == 50  # overridden
        assert cfg.band2_select_pct == 20  # default (not overridden)
        assert cfg.tl_window == 2
        assert cfg.band3_qty == (10, 16, 99)

    def test_zero_override_is_respected(self) -> None:
        # resolve_constant treats 0 as a valid override, not None.
        guild = SimpleNamespace(loot_band1_select_pct=0)
        cfg = LootService.resolve_band_config(guild)
        assert cfg.band1_select_pct == 0


# ===========================================================================
# roll_loot integration through the service (uses real engine + cache)
# ===========================================================================


class TestServiceRollLoot:
    async def test_roll_requires_load(self) -> None:
        svc, _ = _make_service()
        with pytest.raises(RuntimeError):
            svc.roll_loot(5, random.Random(1))

    async def test_band1_window_uses_criminal_tl(self) -> None:
        svc, repos = _make_service()
        # Band-1 only; modules at TL 1 and TL 9 (E2=1, plus tractors 4/5/7/8).
        repos["commodity"].list_all.return_value = []
        await svc.preload_static_data(MagicMock())
        guild = SimpleNamespace(loot_band1_select_pct=100, loot_band2_select_pct=0, loot_band3_select_pct=0)
        rng = random.Random(11)
        picked_tls = set()
        for _ in range(300):
            roll = svc.roll_loot(8, rng, guild_config=guild)
            assert roll is not None
            picked_tls.add(roll.band)
        assert picked_tls == {1}  # always band 1 with these weights

    async def test_success_helper(self) -> None:
        assert LootService.roll_loot_success(100, random.Random(1)) is True
        assert LootService.roll_loot_success(0, random.Random(1)) is False
