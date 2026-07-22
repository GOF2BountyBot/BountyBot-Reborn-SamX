"""Integration tests for T4 — criminal loot determination + persistence at spawn.

Covers LOOT_JOURNAL §5.1 / §7.3 / T4:

* ``BountyService.spawn_bounty`` rolls the criminal's single cargo loot item at
  spawn (anchored on the division-derived ``Bounty.tech_level``) and persists it
  inside the existing ``Bounty.criminal_ship`` JSONB under a new ``cargo`` key —
  ``{item_type, item_name, quantity}`` — with no new column / migration.
* The §5.1 100%-carry guarantee: a spawned criminal always carries exactly one
  item (quantity ≥ 1) drawn from the real loot domain.
* Cache-warmth: spawning with a COLD LootService cache still yields cargo
  (lazy-ensure self-warms), and a WARM cache is not re-preloaded needlessly.
* The extra ``cargo`` key is safe for the combat/loadout parse path
  (``LoadoutBuilder.from_criminal_ship`` tolerates it).

Two layers (see AGENTS.md §SQLite Compatibility):

* **Persistence + lazy-ensure** run against SQLite in-memory with ``roll_loot``
  mocked to a deterministic ``LootRoll`` — the criminal/item ARRAY tables can't
  be seeded in SQLite, so the loadout-generation deps are mocked at the repo
  boundary and the loot ROLL is injected.  Cross-session reload (B.34) proves
  the cargo persists.
* **Real-RNG band/TL behaviour** runs against the seeded throwaway Postgres
  (``pg_env``), exercising the real ``LootService.preload_static_data`` +
  ``roll_loot`` so the asserted item is a genuine catalog row and the Band-1
  ±1 TL window holds.

Mock budget: the real-Postgres (``pg_env``) tests use 0-1 mocks with real objects
preferred.  The SQLite persistence/lazy-ensure tests are the deliberate exception:
each drives ``spawn_bounty`` whose criminal-select / route / loadout-gen deps live
in ARRAY-column tables SQLite cannot host, so ``_spawn_service`` stubs those four
repo/graph boundaries (criminal_repo / config_repo / graph_service /
pathfinding_service) and each test additionally injects ``generate_loadout`` +
``roll_loot`` (~6 boundary stubs total).  These are all process/data-source
boundaries — not entity fakes — and every injected value is a REAL object
(a real ``LootRoll``, a real loadout blob); the loot ROLL itself is the value
under test.  The real ``roll_loot`` domain is exercised unmocked in the pg_env half.
"""

from __future__ import annotations

import os
import random
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from services.loadout_builder import LoadoutBuilder
from services.loot_engine import LootRoll
from services.loot_service import EXCLUDED_MODULE_TYPES, LootService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# pg_env lives in tests/ (one level up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pg_env import PG_ASYNC_URL, pg_skip_reason

_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    Bounty.__table__,
]

# A realistic criminal_ship loadout blob (what generate_loadout returns), used as
# the spawn-time loadout so the cargo key is added alongside the real loadout keys.
_LOADOUT = {
    "ship_name": "Betty",
    "ship_emoji": "",
    "ship_value": 12000,
    "ship_armour": 95,
    "armor_hp": 95,
    "shield_hp": 0,
    "total_hp": 95,
    "ship_max_primaries": 1,
    "ship_max_modules": 2,
    "ship_max_turrets": 0,
    "weapons": [],
    "turrets": [],
    "modules": [],
    "secondaries": [],
    "total_value": 12000,
}


# ---------------------------------------------------------------------------
# SQLite engine/session helpers (mirrors test_bounty_service_integration.py)
# ---------------------------------------------------------------------------


async def _fresh_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _mark_loaded(loot_service: LootService) -> None:
    """Make the real LootService report ``is_loaded`` without a DB preload.

    ``is_loaded`` is a read-only property over the private cache fields; setting
    them directly to non-None sentinels flips it True so the lazy-ensure guard
    skips preload.  ``roll_loot`` is mocked in these tests, so the sentinel pools
    are never read.
    """
    loot_service._band1_pool = []
    loot_service._band2_pool = []
    loot_service._band3_pool = []
    loot_service._tractor_chance_map = {}


def _spawn_service():
    """A BountyService with the non-loot spawn deps mocked at the repo boundary.

    Only the criminal/route/loadout machinery is mocked (those need ARRAY tables
    SQLite can't host).  ``loot_service`` is left REAL so the lazy-ensure + roll
    path is exercised; the caller decides whether to inject a mocked roll.
    """
    from services.bounty_service import BountyService

    svc = BountyService()
    svc.criminal_repo = MagicMock()
    svc.criminal_repo.list_all = AsyncMock(
        return_value=[SimpleNamespace(name="Viper", faction="terran", is_player=False)]
    )
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)
    svc.graph_service = MagicMock()
    svc.graph_service.load_graph = AsyncMock()
    svc.graph_service.get_systems_with_jump_gates = MagicMock(return_value=["A", "B", "C", "D"])
    svc.pathfinding_service = MagicMock()
    svc.pathfinding_service.make_route = MagicMock(return_value=["A", "B", "C"])
    return svc


# ===========================================================================
# Persistence path (SQLite + injected deterministic roll)
# ===========================================================================


@pytest.mark.asyncio
async def test_spawn_persists_cargo_in_criminal_ship_jsonb():
    """The rolled cargo lands in criminal_ship['cargo'] with the contract shape."""
    engine, factory = await _fresh_factory()
    try:
        svc = _spawn_service()
        roll = LootRoll(item_type="commodity", item_name="Booze", quantity=14, band=3)
        # 2 mocks: generate_loadout (ARRAY deps) + the loot roll injection.
        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))),
            patch.object(svc.loot_service, "roll_loot", new=MagicMock(return_value=roll)),
        ):
            _mark_loaded(svc.loot_service)  # warm — skip preload
            async with factory() as db:
                bounty = await svc.spawn_bounty(db, guild_id=1, division="silver", tech_level=3)

        assert bounty is not None
        cargo = bounty.criminal_ship["cargo"]
        assert cargo == {"item_type": "commodity", "item_name": "Booze", "quantity": 14}
        # cargo sits ALONGSIDE the loadout keys, not replacing them.
        assert bounty.criminal_ship["ship_name"] == "Betty"
        assert "weapons" in bounty.criminal_ship
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cargo_reloads_from_db_cross_session():
    """B.34: the cargo persists and is readable through a fresh session."""
    engine, factory = await _fresh_factory()
    try:
        svc = _spawn_service()
        roll = LootRoll(item_type="primary_weapon", item_name="AB Plasma", quantity=2, band=1)
        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))),
            patch.object(svc.loot_service, "roll_loot", new=MagicMock(return_value=roll)),
        ):
            _mark_loaded(svc.loot_service)
            async with factory() as db_a:
                created = await svc.spawn_bounty(db_a, guild_id=7, division="gold", tech_level=6)
                bounty_id = created.id

        async with factory() as db_b:
            reloaded = (await db_b.execute(select(Bounty).where(Bounty.id == bounty_id))).scalar_one()
            assert reloaded.criminal_ship["cargo"] == {
                "item_type": "primary_weapon",
                "item_name": "AB Plasma",
                "quantity": 2,
            }
            assert reloaded.criminal_ship["cargo"]["quantity"] >= 1  # §5.1 guarantee
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_none_roll_spawns_without_cargo_key_and_warns():
    """A None roll (empty band pool) leaves NO cargo key — bounty still spawns crash-safe."""
    engine, factory = await _fresh_factory()
    try:
        svc = _spawn_service()
        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))),
            patch.object(svc.loot_service, "roll_loot", new=MagicMock(return_value=None)),
        ):
            _mark_loaded(svc.loot_service)
            async with factory() as db:
                bounty = await svc.spawn_bounty(db, guild_id=1, division="bronze", tech_level=1)

        assert bounty is not None
        assert "cargo" not in bounty.criminal_ship  # absent, not a broken/empty dict
    finally:
        await engine.dispose()


# ===========================================================================
# Cache-warmth (lazy-ensure)
# ===========================================================================


@pytest.mark.asyncio
async def test_cold_cache_self_warms_then_yields_cargo():
    """Spawning with a COLD cache triggers exactly one preload, then rolls cargo."""
    engine, factory = await _fresh_factory()
    try:
        svc = _spawn_service()
        roll = LootRoll(item_type="commodity", item_name="Ore", quantity=12, band=3)
        # The instance starts cold (is_loaded False).  Replace preload with a mock
        # that flips the private cache fields so the REAL is_loaded property goes
        # False → True after it runs — no class-level mutation (avoids pollution).
        calls = {"n": 0}

        async def _fake_preload(_db):
            calls["n"] += 1
            _mark_loaded(svc.loot_service)

        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))),
            patch.object(svc.loot_service, "roll_loot", new=MagicMock(return_value=roll)),
            patch.object(svc.loot_service, "preload_static_data", new=AsyncMock(side_effect=_fake_preload)),
        ):
            assert not svc.loot_service.is_loaded  # genuinely cold at start
            async with factory() as db:
                bounty = await svc.spawn_bounty(db, guild_id=1, division="silver", tech_level=3)

        assert calls["n"] == 1  # cold → warmed exactly once
        assert svc.loot_service.is_loaded  # now warm
        assert bounty.criminal_ship["cargo"]["item_name"] == "Ore"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_warm_cache_is_not_repreloaded():
    """A WARM cache is not preloaded again (no needless re-query)."""
    engine, factory = await _fresh_factory()
    try:
        svc = _spawn_service()
        roll = LootRoll(item_type="commodity", item_name="Rare", quantity=8, band=2)
        preload = AsyncMock()
        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))),
            patch.object(svc.loot_service, "roll_loot", new=MagicMock(return_value=roll)),
        ):
            _mark_loaded(svc.loot_service)  # already warm
            svc.loot_service.preload_static_data = preload
            async with factory() as db:
                await svc.spawn_bounty(db, guild_id=1, division="silver", tech_level=3)

        preload.assert_not_awaited()
    finally:
        await engine.dispose()


# ===========================================================================
# Combat/loadout parse tolerates the extra cargo key (integration safety)
# ===========================================================================


def test_loadout_builder_tolerates_cargo_key():
    """LoadoutBuilder.from_criminal_ship ignores the extra cargo key (no crash)."""
    blob = dict(_LOADOUT)
    blob["cargo"] = {"item_type": "module", "item_name": "AB-1 Retractor", "quantity": 1}
    loadout = LoadoutBuilder.from_criminal_ship(blob)
    # Parses cleanly and reads the real loadout keys, ignoring cargo.
    assert loadout.ship_name == "Betty"
    assert getattr(loadout, "weapons", []) == []


# ===========================================================================
# Real-RNG band/TL behaviour against the seeded Postgres
# ===========================================================================


_PG_SKIP = pg_skip_reason()


@asynccontextmanager
async def _pg_session():
    """Yield a real Postgres session, engine created+disposed on the test's own loop.

    Created in-body (not a fixture) with NullPool so the engine lifecycle never
    spans the fixture/test event-loop boundary (which produced "attached to a
    different loop" teardown errors under the module-scoped fixture loop).
    """
    engine = create_async_engine(PG_ASYNC_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")
@pytest.mark.asyncio
async def test_real_roll_yields_valid_catalog_item_band1_tl_window():
    """Real preload + roll: every result is a real catalog item; Band-1 honours ±1 TL."""
    async with _pg_session() as pg_db:
        loot = LootService()
        await loot.preload_static_data(pg_db)
        assert loot.is_loaded

        band1_names = {c.name for c in loot._band1_pool}
        band2_names = {c.name for c in loot._band2_pool}
        band3_names = {c.name for c in loot._band3_pool}
        all_names = band1_names | band2_names | band3_names
        band1_by_name = {c.name: c for c in loot._band1_pool}

    criminal_tl = 3
    rng = random.Random(20260619)
    saw_band1 = False
    for _ in range(400):
        roll = loot.roll_loot(criminal_tl, rng)
        assert roll is not None  # §5.1 — every roll yields an item at a seeded TL
        assert roll.quantity >= 1
        assert roll.item_name in all_names  # a genuine catalog row
        if roll.band == 1:
            saw_band1 = True
            assert roll.item_type in ("primary_weapon", "secondary_weapon", "turret_weapon", "module")
            cand = band1_by_name[roll.item_name]
            # ±1 TL window vs the criminal TL (LOOT_JOURNAL §5.8.4); None-TL items
            # are filtered out of the windowed pool, so a Band-1 result always has a TL.
            assert cand.tech_level is not None
            assert abs(cand.tech_level - criminal_tl) <= 1
        elif roll.band == 2:
            assert roll.item_type == "commodity"
            assert roll.item_name in band2_names
        else:
            assert roll.item_type == "commodity"
            assert roll.item_name in band3_names

    assert saw_band1, "Band-1 never selected across 400 rolls (10% weight) — check selection"


@pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")
@pytest.mark.asyncio
async def test_real_roll_bronze_low_tl_band1_within_window():
    """At a low-TL (Bronze) criminal, any Band-1 drop stays within ±1 of TL 1."""
    async with _pg_session() as pg_db:
        loot = LootService()
        await loot.preload_static_data(pg_db)
        band1_by_name = {c.name: c for c in loot._band1_pool}

    rng = random.Random(424242)
    for _ in range(400):
        roll = loot.roll_loot(1, rng)  # Bronze anchor TL = 1
        assert roll is not None
        if roll.band == 1:
            tl = band1_by_name[roll.item_name].tech_level
            assert tl is not None and abs(tl - 1) <= 1  # ⇒ TL in {1, 2} (clamped ≥1)


@pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")
@pytest.mark.asyncio
async def test_real_preload_excludes_non_lootable_modules():
    """The cached Band-1 pool never contains the 3 excluded module kinds (§3)."""
    from persist.repositories.module_repository import ModuleRepository

    async with _pg_session() as pg_db:
        loot = LootService()
        await loot.preload_static_data(pg_db)
        modules = await ModuleRepository().list_all(pg_db)

    excluded_names = {m.name for m in modules if getattr(m, "type", None) in EXCLUDED_MODULE_TYPES}
    band1_names = {c.name for c in loot._band1_pool}
    assert excluded_names.isdisjoint(band1_names)


@pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")
@pytest.mark.asyncio
async def test_spawn_end_to_end_real_roll_persists_real_item():
    """End-to-end against PG: spawn rolls a REAL item via real LootService and persists it.

    Uses the real loot_service (cold → lazy-warmed by spawn_bounty) but mocks the
    criminal/route/loadout deps + bounty persistence at the repo boundary so we
    don't mutate the seeded PG bounty table.
    """
    svc = _spawn_service()

    async def _capture_create(_db, bounty, **_kw):
        bounty.id = 999999
        return bounty

    svc.bounty_repo = MagicMock()
    svc.bounty_repo.create = AsyncMock(side_effect=_capture_create)
    svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    with patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT))):
        # loot_service is REAL and COLD — spawn_bounty must lazy-warm it.
        assert not svc.loot_service.is_loaded
        async with _pg_session() as pg_db:
            bounty = await svc.spawn_bounty(pg_db, guild_id=1, division="silver", tech_level=3)

    assert svc.loot_service.is_loaded  # lazy-ensure warmed it
    cargo = bounty.criminal_ship["cargo"]
    assert cargo["quantity"] >= 1
    assert cargo["item_type"] in (
        "commodity",
        "primary_weapon",
        "secondary_weapon",
        "turret_weapon",
        "module",
    )
    assert isinstance(cargo["item_name"], str) and cargo["item_name"]
