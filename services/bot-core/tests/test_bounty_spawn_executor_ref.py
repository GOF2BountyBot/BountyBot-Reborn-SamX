"""
S2 reference test: the canonical pattern for testing executor modules.

This file is the **definitive pattern** that Sprint 3 will follow when rewriting
``test_bounty_spawn_executor.py`` (currently 1785 lines / ~357 mocks — a classic
mock-overuse anti-pattern).  See ``tests/AGENTS.md`` ▸
"Executor Test Pattern (S2 — definitive)" for the prose explanation.

WHY THIS PATTERN
----------------
The legacy executor test stubbed every repository, every service, and the
``db_manager.get_session()`` context manager itself.  Symptom: it asserted
``mock.assert_called_once()`` instead of asserting on real computed values, so
every defect-class that lives below the mock boundary (capacity-gate logic,
ORM identity-map confusion, eligibility-guard arithmetic) was masked.

The replacement is a **three-tier breakdown**:

  Tier A — Pure unit tests for pure helpers (e.g. ``_is_guild_fully_configured``,
           ``_get_division_channel_id``).  ZERO mocks; deterministic inputs.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
           The executor's ``db_manager.get_session()`` is patched to yield a
           real ``AsyncSession`` against an in-memory SQLite engine that has
           the SQLite-compatible subset of tables (``guild_configs``, ``bounty``)
           created from the real model metadata.  No repository or service
           method is mocked — they execute against real SQLite.

  Tier C — respx for outbound HTTP boundaries.  The executor calls
           ``httpx.AsyncClient`` to (a) self-schedule one-time jobs at
           ``/api/v1/jobs`` on bot-core, and (b) announce bounties to the
           discord-gateway ``/api/v1/announcements/...`` endpoint.  These are
           the **only** legitimate mock surfaces at the executor layer.

This single reference test exercises Tier B and Tier C together to demonstrate
how they compose.  The Tier A helpers are trivial and live in their own files
in the Sprint-3 rewrite.

WHAT THIS TEST COVERS
---------------------
The "benign race" / capacity-reached path of ``execute_bounty_spawn_one_job``:

  1. Real GuildConfig persisted to SQLite with full eligibility (all 5 channel
     and role IDs set) and ``bounty_max_per_tier["bronze"] == 3``.
  2. Three real ``Bounty`` rows persisted with ``status="active"`` and
     ``end_time`` in the future (so the time-based filter in
     ``count_active_by_guild_and_division`` includes them).
  3. ``execute_bounty_spawn_one_job`` is invoked with a valid payload.
  4. The executor's ``count_active_by_guild_and_division`` runs against real
     SQLite, returns 3, sees ``active_count >= max_for_tier``, and short-
     circuits with ``{"success": True, "reason": "capacity_reached"}``.
  5. NO Bounty row is created (count remains 3 in fresh session B).
  6. NO HTTP call is made to either the scheduler self-API or the gateway
     (respx asserts zero matched calls).

This is the canonical "negative-path" demonstration: it proves that the
spawn-skipping logic is genuinely guarded by the DB count rather than by an
incidentally-matching mock return value.

SQLITE COMPATIBILITY NOTES
--------------------------
- The Bounty and GuildConfig models contain ONLY ``Integer``, ``BigInteger``,
  ``String``, ``DateTime(timezone=True)``, ``Float``, and ``JSON`` columns —
  all SQLite-compatible.
- The Criminal, System, Item, Module, and Weapon models contain
  ``ARRAY(String)`` columns which SQLite rejects.  This test does NOT seed any
  of those tables because the capacity-reached path returns BEFORE
  ``BountyService.spawn_bounty`` is invoked — the path that needs Criminal /
  System / Item lookups.
- ``func.now()`` in ``count_active_by_guild_and_division`` is dialect-portable
  and resolves to ``CURRENT_TIMESTAMP`` on SQLite.  The ``end_time > now()``
  filter therefore works correctly.

HOW TO ADD MORE TESTS FOLLOWING THIS PATTERN
--------------------------------------------
For tests that need ``BountyService.spawn_bounty`` to actually execute (e.g.
"happy path" tests for ``execute_bounty_spawn_one_job`` that produce a Bounty
row and an HTTP announcement), the recommended approach is to patch
``services.bounty_service.BountyService.spawn_bounty`` to a coroutine that
inserts a real ``Bounty`` ORM instance into the SQLite session and returns it.
That single patch is justified because the underlying loadout-generation code
needs the ARRAY-column tables (Item / Module / Ship / Criminal / System) which
SQLite cannot host.  Everything else — GuildConfig reads, Bounty count, HTTP
announce — runs against the real surfaces.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — mirror tests/integration/conftest.py.
# This file lives in tests/ (top-level) so it predates the integration
# conftest's auto-injection of mocked shared/sqlalchemy_utils modules; we
# replicate the bare minimum here so the test runs standalone.
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# shared.bblogger — the shared logging library is not on the test path.
if "shared" not in sys.modules:
    _mock_shared = ModuleType("shared")
    _mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
    _mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_shared.bblogger  # type: ignore[arg-type]

# sqlalchemy_utils — required by the DiscordMessage model auto-import chain.
if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sau

# ---------------------------------------------------------------------------
# Now safe to import application code.
# ---------------------------------------------------------------------------

import pytest
import respx
from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.guild_config import GuildConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The function under test — imported by absolute path matching the in-app
# package layout (deferred imports inside the executor resolve from
# ``persist.*`` and ``services.*``, both of which live under ``src/``).
from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

# ---------------------------------------------------------------------------
# Tier B fixture — SQLite-in-memory engine + session factory.
#
# Scope: ``function`` because each test wants a clean DB.  Tests that share
# a read-only seed could promote this to ``module`` scope; for executor
# tests, function scope is the safe default.
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
    Bounty.__table__,
]


@pytest.fixture
async def sqlite_engine_and_factory():
    """Yield a fresh SQLite engine + session factory.

    Scope is `function` so each test gets an isolated DB.  The dispose at
    teardown closes all in-memory connections — important because some
    failure modes can otherwise leak connections across the suite.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_SQLITE_TABLES)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers — local to the test, not promoted to a fixture because each
# test asserts on different shapes of seed data.
# ---------------------------------------------------------------------------


async def _seed_full_config(db: AsyncSession, guild_id: int, max_per_tier_bronze: int = 3) -> GuildConfig:
    """Persist a fully-eligible GuildConfig.

    All five fields that ``_is_guild_fully_configured`` checks are populated,
    so the eligibility guard passes.
    """
    config = GuildConfig(
        guild_id=guild_id,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=444,
        bounty_hunter_role_id=555,
        bronze_role_id=666,
        bounty_max_per_tier={
            "bronze": max_per_tier_bronze,
            "silver": 3,
            "gold": 3,
            "platinum": 3,
        },
        bounty_expiry_minutes=480,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_active_bounty(
    db: AsyncSession,
    guild_id: int,
    division: str,
    criminal_name: str,
) -> Bounty:
    """Persist a single active Bounty with a future end_time.

    The future end_time is required so that
    ``count_active_by_guild_and_division`` (which filters on
    ``end_time > now()``) counts this row.
    """
    now = datetime.now(UTC)
    bounty = Bounty(
        guild_id=guild_id,
        division=division,
        criminal_name=criminal_name,
        criminal_faction="TestFaction",
        route=["Sys-A", "Sys-B", "Sys-C"],
        answer="Sys-B",
        reward=10_000,
        reward_per_sys=2_500,
        checked={"Sys-A": -1, "Sys-B": -1, "Sys-C": -1},
        issue_time=now,
        end_time=now + timedelta(hours=8),
        tech_level=1,
        criminal_ship={"ship_name": "TestShip", "ship_armour": 100, "weapons": [], "turrets": []},
        status="active",
    )
    db.add(bounty)
    await db.commit()
    await db.refresh(bounty)
    return bounty


# ---------------------------------------------------------------------------
# Tier C respx fixture — establishes that NO outbound HTTP is permitted.
#
# Setting ``assert_all_called=False`` means we are explicit about the
# negative-assertion: the test asserts at the end that no routes matched.
# Setting ``assert_all_mocked=True`` (the respx default) means any unmocked
# httpx call would raise — a nice safety net.
# ---------------------------------------------------------------------------


@pytest.fixture
def http_recorder():
    """Open a respx mock context.

    The router catches ALL httpx calls (any method, any URL).  The test asserts
    on the call history at the end.
    """
    with respx.mock(assert_all_called=False) as router:
        # Catch-all route: any unexpected call would otherwise raise.  The test
        # asserts ``router.calls.call_count == 0`` to prove no HTTP was made.
        router.route().respond(200, json={"data": {"id": 999_999}})
        yield router


# ---------------------------------------------------------------------------
# THE REFERENCE TEST
# ---------------------------------------------------------------------------


async def test_bounty_spawn_one_skips_when_capacity_reached(
    sqlite_engine_and_factory,
    http_recorder,
):
    """``execute_bounty_spawn_one_job`` returns capacity_reached without spawning.

    PATTERN DEMONSTRATED
    --------------------
    - Real SQLite session (Tier B): the GuildConfig and three Bounty rows are
      persisted via real ORM operations; the executor's count query runs
      against real SQLite and returns the real ``3``.
    - Real eligibility helper (Tier A, embedded): the persisted GuildConfig
      has all five eligibility fields set, so ``_is_guild_fully_configured``
      returns True without any mocking.
    - respx HTTP boundary (Tier C): the recorder catches any httpx outbound
      call.  The test asserts zero calls — the capacity-reached path must
      short-circuit before scheduling expiry or announcing.
    - Single, justified mock: ``db_manager.get_session`` is patched to yield
      the SQLite session.  This is the bridge between the executor's
      deferred import and the test fixture; it is NOT a behavioural mock.
      No repository or service method is mocked.

    DEFECT CLASSES THIS TEST CATCHES
    --------------------------------
    - Drift in ``count_active_by_guild_and_division`` SQL semantics (e.g. a
      regression that drops the ``status=='active'`` filter would over-count).
    - Drift in the capacity-gate threshold (``>= max_for_tier``).
    - A regression that calls ``BountyService.spawn_bounty`` unconditionally
      before checking capacity (would attempt DB inserts and fail visibly).
    - A regression that fires the HTTP announce path even on benign-race
      skips (would surface as ``http_recorder.calls.call_count > 0``).
    """
    _engine, factory = sqlite_engine_and_factory

    # NOTE: SQLite stores integers as signed 64-bit but aiosqlite enforces a
    # narrower range than real Discord snowflakes (which are 17-19 digit u64).
    # Use a representative test guild ID that fits SQLite's INTEGER range —
    # the production code path is dialect-agnostic, so the precise width is
    # immaterial to the behaviour under test.
    GUILD_ID = 9_500_000_001
    DIVISION = "bronze"
    MAX_FOR_TIER = 3

    # ------------------------------------------------------------------
    # Arrange — seed the DB through session A.
    # ------------------------------------------------------------------
    async with factory() as seed_db:
        await _seed_full_config(seed_db, GUILD_ID, max_per_tier_bronze=MAX_FOR_TIER)
        for i in range(MAX_FOR_TIER):
            await _seed_active_bounty(
                seed_db,
                guild_id=GUILD_ID,
                division=DIVISION,
                criminal_name=f"TestCriminal-{i}",
            )

    # Sanity check: confirm seed before invoking the executor.
    async with factory() as verify_db:
        result = await verify_db.execute(
            select(Bounty).where(Bounty.guild_id == GUILD_ID, Bounty.division == DIVISION)
        )
        seeded_count = len(list(result.scalars().all()))
    assert seeded_count == MAX_FOR_TIER, f"Seed broken: expected {MAX_FOR_TIER} rows, got {seeded_count}"

    # ------------------------------------------------------------------
    # Act — patch db_manager.get_session() to yield a fresh SQLite
    # session, then invoke the executor.
    #
    # The factory-of-CMs idiom (side_effect=_fake_get_db) ensures each call
    # to db_manager.get_session() returns a NEW context manager — important
    # because the executor enters/exits its session block exactly once per
    # invocation; a single already-consumed CM would silently misbehave.
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake_db_manager = MagicMock()
    fake_db_manager.get_session = MagicMock(side_effect=_fake_get_db)

    payload = {
        "job_type": "bounty_spawn_one",
        "guild_id": GUILD_ID,
        "tier": DIVISION,
    }

    # The deferred-import pattern in the executor (``from
    # persist.database.manager import db_manager`` inside the function body)
    # means the symbol is resolved from the SOURCE module each invocation.
    # We therefore patch the SOURCE module attribute, not the executor
    # namespace.  This is the canonical patch target for any executor that
    # uses deferred imports — see ``tests/AGENTS.md`` ▸ "Executor Test
    # Pattern" for the full rule.
    with patch("persist.database.manager.db_manager", fake_db_manager):
        result = await execute_bounty_spawn_one_job("test-job-id", payload)

    # ------------------------------------------------------------------
    # Assert — three orthogonal checks (Tier A return value, Tier B DB
    # state, Tier C HTTP boundary).
    # ------------------------------------------------------------------

    # 1. Return value: benign-race success path.
    assert result == {"success": True, "reason": "capacity_reached"}, (
        f"Expected capacity_reached short-circuit, got {result!r}.  "
        f"This means the executor either tried to spawn (regression in the "
        f"capacity gate) or returned a different reason code."
    )

    # 2. Real DB state: no new Bounty was inserted.  We open a FRESH session
    # B (per the cross-session-reload rule in tests/AGENTS.md) to prove
    # that the assertion reads from disk, not from the seed-session cache.
    async with factory() as verify_db:
        final = await verify_db.execute(
            select(Bounty).where(Bounty.guild_id == GUILD_ID, Bounty.division == DIVISION)
        )
        final_count = len(list(final.scalars().all()))
    assert final_count == MAX_FOR_TIER, (
        f"Expected exactly {MAX_FOR_TIER} bounty rows after capacity-reached skip, "
        f"got {final_count}.  A new bounty should NOT have been spawned."
    )

    # 3. HTTP boundary: zero outbound calls.  No expiry scheduling, no
    # gateway announcement, no map upload.
    assert http_recorder.calls.call_count == 0, (
        f"Expected ZERO HTTP calls on the capacity-reached path, got "
        f"{http_recorder.calls.call_count}.  The first unexpected call: "
        f"{http_recorder.calls[0].request.url if http_recorder.calls else 'n/a'}"
    )
