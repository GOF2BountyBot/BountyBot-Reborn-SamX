"""
Unit tests for the bounty spawn orchestrator and one-time per-tier executor.

REWRITE (test true-up): this module previously force-stubbed ALL of persist.* /
services.* with bare MagicMocks and used a MagicMock GuildConfig (which returns a
truthy Mock for ANY unset attribute — the exact prod-incident failure mode).  It
now mirrors the gold-standard pattern in ``test_bounty_spawn_executor.py``:

  * real SQLite in-memory engine (function-scoped) with the SQLite-compatible
    tables (GuildConfig, Bounty, DiscordMessage),
  * real GuildConfig / Bounty ORM rows seeded per test,
  * the ONLY infra mock is the ``db_manager.get_session`` bridge,
  * the APScheduler instance stays a MagicMock (a genuine process boundary — no
    live scheduler in unit tests),
  * ``BountyService.spawn_bounty`` / ``_announce_bounty`` / ``_schedule_expiry_job``
    are mocked only where they cross an ARRAY-column / network boundary that SQLite
    cannot host (each such mock is justified in-line per tests/AGENTS.md §Mock Policy).

The eligibility / channel / role / capacity logic now runs for REAL against the
seeded config + bounties, so a regression that reads the wrong config field is no
longer silently green-lit.

Covers spec requirements 1–25 (see the original docstring history).
"""

from __future__ import annotations

import os as _os
import random
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Seed for deterministic RNG in parametrized test
random.seed(42)

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger BEFORE any src import.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Force src to the FRONT of sys.path so `services`/`persist`/`utils` resolve to
# the application packages (the tests/ dir carries a `services` test-subpackage
# that shadows src/services when tests/ precedes src/).
_SRC = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, _SRC)

from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.discord_message import DiscordMessage
from persist.models.guild_config import GuildConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# SQLite-compatible tables (no ARRAY columns).
# ---------------------------------------------------------------------------
_SQLITE_TABLES = [
    GuildConfig.__table__,
    Bounty.__table__,
    DiscordMessage.__table__,
]

# Constants — guild IDs must fit SQLite's signed 64-bit INTEGER.
BRONZE_CHANNEL = 111
SILVER_CHANNEL = 222
GOLD_CHANNEL = 333
PLATINUM_CHANNEL = 444
HUNTER_ROLE = 555


# ===========================================================================
# Fixtures + real-object seed helpers
# ===========================================================================


@pytest.fixture
async def sqlite_engine_and_factory():
    """Fresh function-scoped SQLite engine + session factory."""
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


def _make_fake_db_manager(factory):
    """MagicMock db_manager whose get_session() yields a real SQLite session.

    # 1 mock — db_manager bridge (the sole infra mock).
    """

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake


async def _seed_config(
    db: AsyncSession,
    guild_id: int,
    *,
    bronze_bounty_channel_id: int | None = BRONZE_CHANNEL,
    silver_bounty_channel_id: int | None = SILVER_CHANNEL,
    gold_bounty_channel_id: int | None = GOLD_CHANNEL,
    platinum_bounty_channel_id: int | None = PLATINUM_CHANNEL,
    bounty_hunter_role_id: int | None = HUNTER_ROLE,
    bounty_max_per_tier: dict | None = None,
    bounty_expiry_minutes: int | None = 480,
    bounty_spawn_interval_minutes: int | None = 5,
) -> GuildConfig:
    """Persist a real GuildConfig row (fully configured unless overridden)."""
    config = GuildConfig(
        guild_id=guild_id,
        bronze_bounty_channel_id=bronze_bounty_channel_id,
        silver_bounty_channel_id=silver_bounty_channel_id,
        gold_bounty_channel_id=gold_bounty_channel_id,
        platinum_bounty_channel_id=platinum_bounty_channel_id,
        bounty_hunter_role_id=bounty_hunter_role_id,
        bounty_max_per_tier=bounty_max_per_tier or {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3},
        bounty_expiry_minutes=bounty_expiry_minutes,
        bounty_spawn_interval_minutes=bounty_spawn_interval_minutes,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_active_bounty(db: AsyncSession, guild_id: int, division: str, criminal_name: str) -> Bounty:
    """Persist a single active Bounty with a future end_time (so it counts as active)."""
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


def _make_spawned_bounty(
    bounty_id: int = 1,
    guild_id: int = 100,
    division: str = "bronze",
) -> SimpleNamespace:
    """A faithful stand-in for BountyService.spawn_bounty's return value.

    SimpleNamespace (NOT MagicMock): unset attributes raise AttributeError, so a
    field-name bug in the executor surfaces instead of being silently truthy.
    Only the attributes the one-job path reads post-spawn are provided.
    """
    return SimpleNamespace(
        id=bounty_id,
        guild_id=guild_id,
        division=division,
        criminal_name="Kato Vort",
        criminal_faction="Vossk",
        reward=50_000,
        tech_level=5,
        route=["SysA", "SysB"],
        end_time=datetime.now(UTC) + timedelta(days=3),
        criminal_ship=None,
        checked=None,
    )


def _empty_scheduler() -> MagicMock:
    """MagicMock APScheduler with zero queued jobs (genuine process boundary)."""
    sched = MagicMock()
    sched.get_jobs = MagicMock(return_value=[])
    sched.add_job = MagicMock(return_value=None)
    return sched


def _scheduler_with_jobs(job_specs: list[tuple[int, str, int]]) -> MagicMock:
    """Build a MagicMock scheduler whose get_jobs() returns synthetic queued jobs.

    ``job_specs`` is a list of (guild_id, tier_lower, count) — each entry adds
    ``count`` jobs whose IDs carry the ``bounty_spawn_{guild}_{tier}_`` prefix.
    """
    now = datetime.now(UTC)
    jobs = []
    for guild_id, tier_lower, count in job_specs:
        prefix = f"bounty_spawn_{guild_id}_{tier_lower}_"
        for i in range(count):
            jobs.append(SimpleNamespace(id=f"{prefix}{i:04d}", next_run_time=now + timedelta(minutes=10 + i * 10)))
    sched = MagicMock()
    sched.get_jobs = MagicMock(return_value=jobs)
    sched.add_job = MagicMock(return_value=None)
    return sched


# ===========================================================================
# Tests 1–2: Capacity skipping / queueing (real config + real active bounties)
# ===========================================================================


async def test_orchestrator_skips_tier_when_capacity_full(sqlite_engine_and_factory):
    """Test 1: active + queued >= max_for_tier → tier skipped."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})
        for i in range(10):
            await _seed_active_bounty(db, 100, "bronze", f"Crim-{i}")

    sched = _scheduler_with_jobs([(100, "bronze", 10)])  # 10 active + 10 queued = 20 = max
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-1", {"job_type": "bounty_spawn_orchestrate"})

    tier_results = result["results"].get(100, {}).get("tiers", {})
    assert tier_results.get("bronze", {}).get("queued", 1) == 0


async def test_orchestrator_queues_when_below_capacity(sqlite_engine_and_factory):
    """Test 1b: queued < max_for_tier (19 < 20) → queue one more.

    The below-capacity path reaches ``_compute_next_fire_time`` which, when
    seeded with active-bounty anchors, mixes tz-aware scheduler fire times with
    the tz-NAIVE ``issue_time`` that aiosqlite returns (SQLite drops tzinfo on
    ``DateTime(timezone=True)`` — a SQLite-only fidelity gap; Postgres is
    tz-aware).  Test 1 already proves REAL active bounties count toward the
    capacity gate (bronze there is full → skipped, no fire-time math).  Here we
    exercise the below-capacity boundary via already-queued jobs (all tz-aware),
    so the gate arithmetic is real without the SQLite tz artifact.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 200, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})

    sched = _scheduler_with_jobs([(200, "bronze", 19)])  # 0 active + 19 queued = 19 < 20
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-queue", {"job_type": "bounty_spawn_orchestrate"})

    tier_results = result["results"].get(200, {}).get("tiers", {})
    assert tier_results.get("bronze", {}).get("queued", 0) == 1


async def test_orchestrator_queues_exactly_one_job_per_eligible_tier(sqlite_engine_and_factory):
    """Test 2: exactly ONE job queued per eligible tier per invocation (4 tiers)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 300, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})

    sched = _empty_scheduler()
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-2", {"job_type": "bounty_spawn_orchestrate"})

    assert result["total_queued"] == 4
    assert sched.add_job.call_count == 4, (
        f"Expected 4 direct add_job calls (one per tier), got {sched.add_job.call_count}"
    )


# ===========================================================================
# Test 3: Fire time within the gap-aware target window (cold start)
# ===========================================================================


@pytest.mark.parametrize("interval_minutes", [5, 60, 120, 240])
async def test_orchestrator_fire_time_within_window(interval_minutes: int, sqlite_engine_and_factory):
    """Fix C: cold-start fire time lands within [target-window, target+window]."""
    from utils.executors.bounty_spawn_executor import (
        _MIN_LEAD_SECONDS,
        execute_bounty_spawn_orchestrate_job,
    )

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(
            db,
            400,
            bounty_spawn_interval_minutes=interval_minutes,
            bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3},
        )

    fire_times_recorded: list[str] = []
    now_before = datetime.now(UTC)

    def _capture_add_job(func, trigger=None, run_date=None, args=None, id=None, **kwargs):
        if run_date is not None:
            fire_times_recorded.append(run_date.isoformat())

    sched = _empty_scheduler()
    sched.add_job = MagicMock(side_effect=_capture_add_job)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        await execute_bounty_spawn_orchestrate_job(
            f"orch-window-{interval_minutes}", {"job_type": "bounty_spawn_orchestrate"}
        )

    half_interval = interval_minutes / 2.0
    window_minutes = min(15.0, 0.25 * interval_minutes)
    lower_bound = now_before + timedelta(minutes=max(0.0, half_interval - window_minutes))
    upper_bound = now_before + timedelta(minutes=half_interval + window_minutes, seconds=2)
    min_lead = now_before + timedelta(seconds=_MIN_LEAD_SECONDS)
    if lower_bound < min_lead:
        lower_bound = min_lead

    assert len(fire_times_recorded) > 0, "No fire times recorded — no jobs were queued"
    for fire_time_str in fire_times_recorded:
        fire_time = datetime.fromisoformat(fire_time_str)
        assert fire_time >= lower_bound, f"Fire time {fire_time} before lower bound {lower_bound}"
        assert fire_time <= upper_bound, f"Fire time {fire_time} after upper bound {upper_bound}"


# ===========================================================================
# Test 4: Queue-count filters by (guild_id, tier_lower) prefix
# ===========================================================================


async def test_orchestrator_queue_count_uses_correct_prefix_filter(sqlite_engine_and_factory):
    """Test 4: the tier component of the queued-job prefix is load-bearing.

    guild 500, 0 active in every tier. bronze max=2 with 2 bronze queued → full;
    silver/gold/platinum max=5 with 2 queued each → not full → 1 new job each.
    A too-broad or wrong-guild prefix flips the count and fails the assertion.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    guild_id = 500
    async with factory() as db:
        await _seed_config(db, guild_id, bounty_max_per_tier={"bronze": 2, "silver": 5, "gold": 5, "platinum": 5})

    now = datetime.now(UTC)
    jobs = []
    for tier in ("bronze", "silver", "gold", "platinum"):
        prefix = f"bounty_spawn_{guild_id}_{tier}_"
        for i in range(2):
            jobs.append(SimpleNamespace(id=f"{prefix}{i:04d}", next_run_time=now + timedelta(minutes=10 + i * 5)))
    # Decoys — must NOT be counted for guild 500 / bronze.
    jobs.append(SimpleNamespace(id="bounty_spawn_999_bronze_0000", next_run_time=now + timedelta(minutes=5)))
    jobs.append(SimpleNamespace(id="bounty_spawn_500_bronzeX_0000", next_run_time=now + timedelta(minutes=5)))

    sched = MagicMock()
    sched.get_jobs = MagicMock(return_value=jobs)
    sched.add_job = MagicMock(return_value=None)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-4", {"job_type": "bounty_spawn_orchestrate"})

    assert result["total_queued"] == 3, (
        f"Expected 3 new jobs (bronze full, 3 other tiers each queue 1), got {result['total_queued']}"
    )
    guild_result = result.get("results", {}).get(guild_id, {})
    bronze_result = guild_result.get("tiers", {}).get("bronze", {})
    assert bronze_result.get("reason") == "capacity_full", f"Expected bronze capacity_full, got: {bronze_result}"
    for tier in ("silver", "gold", "platinum"):
        tier_result = guild_result.get("tiers", {}).get(tier, {})
        assert tier_result.get("queued") == 1, f"Expected {tier} to queue 1 new job, got: {tier_result}"
    assert sched.get_jobs.call_count == 4, (
        f"Expected get_jobs() called once per tier (4), got {sched.get_jobs.call_count}"
    )


# ===========================================================================
# Test 5: Max comes from bounty_max_per_tier (NOT TemperatureService)
# ===========================================================================


async def test_orchestrator_uses_bounty_max_per_tier_not_temperature(sqlite_engine_and_factory):
    """Test 5: TemperatureService.get_max_bounties is NOT called by the orchestrator."""
    import services.temperature_service as temp_mod
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 600, bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5, "platinum": 5})

    temp_called: list = []

    def _record(self, *a, **kw):
        temp_called.append(a)
        return 999

    sched = _empty_scheduler()
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        patch.object(temp_mod.TemperatureService, "get_max_bounties", _record, create=True),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-5", {"job_type": "bounty_spawn_orchestrate"})

    assert temp_called == [], "TemperatureService.get_max_bounties was called — it should not be"
    # Proves the config max was used: max>0 for all tiers → 4 jobs queued.
    assert result["total_queued"] == 4


# ===========================================================================
# Test 6: Orchestrator does NOT read next_spawn_check_at
# ===========================================================================


async def test_orchestrator_does_not_read_or_write_next_spawn_check_at(sqlite_engine_and_factory):
    """Test 6: next_spawn_check_at is never accessed on the config object.

    Uses a real tracking config class (NOT a MagicMock) returned by a targeted
    ConfigRepository.list_all patch — justified because the assertion is a
    negative ("attribute X is never read"), which requires an instrumented
    object.  All other collaborators run for real via the SQLite bridge.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    accessed_attrs: list[str] = []

    class _TrackedConfig:
        guild_id = 700
        bronze_bounty_channel_id = BRONZE_CHANNEL
        silver_bounty_channel_id = SILVER_CHANNEL
        gold_bounty_channel_id = GOLD_CHANNEL
        platinum_bounty_channel_id = PLATINUM_CHANNEL
        bounty_hunter_role_id = HUNTER_ROLE
        bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
        bounty_expiry_minutes = 480
        bounty_spawn_interval_minutes = 5
        bronze_role_id = None
        silver_role_id = None
        gold_role_id = None
        platinum_role_id = None

        def __getattribute__(self, name: str):
            if name == "next_spawn_check_at":
                accessed_attrs.append(name)
            return super().__getattribute__(name)

    tracked_cfg = _TrackedConfig()
    tracked_cfg.next_spawn_check_at = None

    _engine, factory = sqlite_engine_and_factory
    mock_repo = AsyncMock()
    mock_repo.list_all = AsyncMock(return_value=[tracked_cfg])

    sched = _empty_scheduler()
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("persist.repositories.config_repository.ConfigRepository", MagicMock(return_value=mock_repo)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        await execute_bounty_spawn_orchestrate_job("orch-6", {"job_type": "bounty_spawn_orchestrate"})

    assert "next_spawn_check_at" not in accessed_attrs, (
        f"Orchestrator accessed next_spawn_check_at — it should not. Accesses: {accessed_attrs}"
    )


# ===========================================================================
# Test 7: Guild not fully configured → orchestrator skips entire guild
# ===========================================================================


async def test_orchestrator_skips_guild_not_fully_configured(sqlite_engine_and_factory):
    """Test 7: real _is_guild_fully_configured returns False → guild skipped."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 800, bounty_hunter_role_id=None)  # ineligible

    sched = _empty_scheduler()
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        result = await execute_bounty_spawn_orchestrate_job("orch-7", {"job_type": "bounty_spawn_orchestrate"})

    assert result["total_queued"] == 0
    assert sched.add_job.call_count == 0, (
        f"Expected 0 add_job calls for ineligible guild, got {sched.add_job.call_count}"
    )


# ===========================================================================
# Test 8: bounty_max_per_tier[tier] == 0 → tier skipped
# ===========================================================================


async def test_orchestrator_skips_tier_when_max_zero_or_missing(sqlite_engine_and_factory):
    """Test 8: tier with max=0 is skipped; tiers with max>0 are queued."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        # bronze=0 (disabled); silver absent (uses DEFAULT_MAX>0); gold/platinum=3.
        await _seed_config(db, 900, bounty_max_per_tier={"bronze": 0, "gold": 3, "platinum": 3})

    scheduled_tiers: list[str] = []

    def _capture_tier(func, trigger=None, run_date=None, args=None, id=None, **kwargs):
        if args and len(args) >= 2 and isinstance(args[1], dict):
            t = args[1].get("tier")
            if t:
                scheduled_tiers.append(t)

    sched = _empty_scheduler()
    sched.add_job = MagicMock(side_effect=_capture_tier)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        await execute_bounty_spawn_orchestrate_job("orch-8", {"job_type": "bounty_spawn_orchestrate"})

    assert "bronze" not in scheduled_tiers, f"Bronze should be skipped (max=0) but got: {scheduled_tiers}"
    assert "gold" in scheduled_tiers
    assert "platinum" in scheduled_tiers


# ===========================================================================
# Tests 9–20: One-time executor (execute_bounty_spawn_one_job)
# ===========================================================================


async def test_one_missing_guild_id_returns_warning(sqlite_engine_and_factory):
    """Test 9: missing guild_id → missing_payload (returns before any DB work)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job("one-9", {"job_type": "bounty_spawn_one", "tier": "bronze"})

    assert result["success"] is False
    assert result["reason"] == "missing_payload"
    mock_logger.warning.assert_called()


async def test_one_missing_tier_returns_warning(sqlite_engine_and_factory):
    """Test 10: missing tier → missing_payload."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job("one-10", {"job_type": "bounty_spawn_one", "guild_id": 100})

    assert result["success"] is False
    assert result["reason"] == "missing_payload"
    mock_logger.warning.assert_called()


async def test_one_guild_config_missing_returns_warning(sqlite_engine_and_factory):
    """Test 11: no GuildConfig row for the guild → guild_not_configured (real DB read)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    # Intentionally seed NOTHING — the real get_by_guild_id returns None.
    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-11", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "guild_not_configured"
    mock_logger.warning.assert_called()


async def test_one_guild_not_fully_configured_returns_warning(sqlite_engine_and_factory):
    """Test 12: real config missing hunter role → guild_not_configured."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100, bounty_hunter_role_id=None)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-12", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "guild_not_configured"
    mock_logger.warning.assert_called()


async def test_one_division_channel_none_returns_warning(sqlite_engine_and_factory):
    """Test 13: division channel None at fire time → tier_not_configured.

    This is a DEFENSIVE branch: a fully-configured guild (which passes
    _is_guild_fully_configured) can never have a null tier channel, since that
    guard checks the very same four channel fields.  We therefore seed a REAL
    fully-configured config and patch ONLY the single getter to simulate the
    defensive scenario the branch guards against.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)  # fully configured

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=None),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-13", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "tier_not_configured"
    mock_logger.warning.assert_called()


async def test_one_division_role_none_returns_warning(sqlite_engine_and_factory):
    """Test 14: division role None at fire time → tier_not_configured (defensive branch).

    As with test 13, a fully-configured guild cannot yield a null role (the
    getter falls back to bounty_hunter_role_id, which the eligibility guard
    requires), so only the single role getter is patched.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)  # fully configured; real channel getter returns BRONZE_CHANNEL

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=None),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-14", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "tier_not_configured"
    mock_logger.warning.assert_called()


async def test_one_capacity_reached_at_fire_time_returns_info_not_warning(sqlite_engine_and_factory):
    """Test 15: real active count == max at fire time → INFO, success=True, capacity_reached."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})
        for i in range(3):  # active count == max
            await _seed_active_bounty(db, 100, "bronze", f"Crim-{i}")

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-15", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is True
    assert result["reason"] == "capacity_reached"
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("capacity reached" in m or "capacity_reached" in m or "benign race" in m for m in info_calls), (
        f"Expected INFO log about capacity reached, got: {info_calls}"
    )
    warn_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("capacity" in m for m in warn_calls), f"WARNING about capacity (should be INFO): {warn_calls}"


async def test_one_happy_path(sqlite_engine_and_factory):
    """Test 16: happy path — real config/eligibility/capacity; spawn + announce mocked (boundaries)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)  # fully configured, 0 active bounties

    spawned = _make_spawned_bounty(bounty_id=42, guild_id=100, division="bronze")
    mock_expiry = AsyncMock(return_value="exp-42")
    mock_announce = AsyncMock(
        return_value={"success": True, "failure_phase": None, "discord_message_id": 8001, "channel_id": 111}
    )

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        # spawn_bounty crosses Item/Ship ARRAY columns SQLite cannot host — bypass.
        patch("services.bounty_service.BountyService.spawn_bounty", new=AsyncMock(return_value=spawned)),
        # _schedule_expiry_job (scheduler boundary) + _announce_bounty (HTTP boundary).
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=mock_expiry),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
        patch("utils.executors.bounty_spawn_executor._push_bounty_cache", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_one_job(
            "one-16", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is True
    assert result["bounty_id"] == 42
    assert result["tier"] == "bronze"
    mock_expiry.assert_awaited_once()
    mock_announce.assert_awaited_once()


async def test_one_spawn_returns_none_warning(sqlite_engine_and_factory):
    """Test 17: BountyService.spawn_bounty returns None → spawn_failed."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("services.bounty_service.BountyService.spawn_bounty", new=AsyncMock(return_value=None)),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-17", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "spawn_failed"
    mock_logger.warning.assert_called()


async def test_one_expiry_raises_does_not_prevent_announcement(sqlite_engine_and_factory):
    """Test 18: _schedule_expiry_job raises → ERROR logged but announce still runs, success."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)

    spawned = _make_spawned_bounty(bounty_id=99, guild_id=100, division="bronze")
    mock_announce = AsyncMock(
        return_value={"success": True, "failure_phase": None, "discord_message_id": 8002, "channel_id": 111}
    )

    async def _expiry_raises(job_id, bounty):
        raise RuntimeError("scheduler down")

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("services.bounty_service.BountyService.spawn_bounty", new=AsyncMock(return_value=spawned)),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=_expiry_raises),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
        patch("utils.executors.bounty_spawn_executor._push_bounty_cache", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-18", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    mock_announce.assert_awaited_once()
    mock_logger.error.assert_called()
    assert result["success"] is True


async def test_one_announce_raises_triggers_compensating_rollback(sqlite_engine_and_factory):
    """Test 19: _announce_bounty raises → compensating rollback + success=False.

    The compensator's real behaviour (with cross-session reload) is covered by
    the integration tests in test_bounty_spawn_executor.py; here it is captured
    to assert the executor invokes it with the correct IDs.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 100)

    spawned = _make_spawned_bounty(bounty_id=77, guild_id=100, division="bronze")

    async def _announce_raises(job_id, bounty, config, db):
        raise RuntimeError("gateway unreachable")

    compensate_calls: list[dict] = []

    async def _capture_compensate(**kwargs):
        compensate_calls.append(kwargs)
        return {"post_deleted": False, "expiry_cancelled": True, "bounty_deleted": True, "cache_repushed": True}

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("services.bounty_service.BountyService.spawn_bounty", new=AsyncMock(return_value=spawned)),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock(return_value="exp-77")),
        patch("utils.executors.bounty_spawn_executor._push_bounty_cache", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=_announce_raises),
        patch("utils.executors.bounty_spawn_executor._compensate_failed_spawn", side_effect=_capture_compensate),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-19", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    mock_logger.error.assert_called()
    assert result["success"] is False
    assert result["reason"] == "announce_failed_rolled_back"
    assert result["failure_phase"] == "announce"
    assert result["bounty_id"] == 77
    assert len(compensate_calls) == 1
    call_kwargs = compensate_calls[0]
    assert call_kwargs["bounty_id"] == 77
    assert call_kwargs["guild_id"] == 100
    assert call_kwargs["expiry_job_id"] == "exp-77"


async def test_one_unexpected_exception_propagates(sqlite_engine_and_factory):
    """Test 20: an unexpected DB error propagates so APScheduler marks the job failed."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    _engine, factory = sqlite_engine_and_factory
    mock_repo = AsyncMock()
    mock_repo.get_by_guild_id = AsyncMock(side_effect=RuntimeError("DB connection lost"))

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("persist.repositories.config_repository.ConfigRepository", MagicMock(return_value=mock_repo)),
        pytest.raises(RuntimeError, match="DB connection lost"),
    ):
        await execute_bounty_spawn_one_job(
            "one-20", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )


# ===========================================================================
# Tests 21–23: Job ID format and uniqueness (pure string logic)
# ===========================================================================


def test_job_id_format():
    """Test 21: Job ID format is bounty_spawn_{guild_id}_{tier_lower}_{uuid}."""
    guild_id = 12345
    tier_lower = "silver"
    uid = str(uuid.uuid4())
    job_id = f"bounty_spawn_{guild_id}_{tier_lower}_{uid}"

    assert job_id.startswith(f"bounty_spawn_{guild_id}_{tier_lower}_")
    suffix = job_id[len(f"bounty_spawn_{guild_id}_{tier_lower}_") :]
    parsed = uuid.UUID(suffix)
    assert str(parsed) == suffix


def test_job_id_parseable():
    """Test 22: Job IDs can be parsed back to (guild_id, tier) via split('_')."""
    guild_id = 99999
    tier_lower = "gold"
    uid = str(uuid.uuid4())
    job_id = f"bounty_spawn_{guild_id}_{tier_lower}_{uid}"

    parts = job_id.split("_")
    assert parts[0] == "bounty"
    assert parts[1] == "spawn"
    assert int(parts[2]) == guild_id
    assert parts[3] == tier_lower


def test_job_id_unique_across_calls():
    """Test 23: UUID suffix is unique across two back-to-back queues."""
    guild_id = 42
    tier = "platinum"
    job_id_1 = f"bounty_spawn_{guild_id}_{tier}_{uuid.uuid4()}"
    job_id_2 = f"bounty_spawn_{guild_id}_{tier}_{uuid.uuid4()}"
    assert job_id_1 != job_id_2


# ===========================================================================
# Tests 24–25: Regression — admin spawn path + dispatcher
# ===========================================================================


def test_admin_spawn_can_import_helpers():
    """Test 24: _announce_bounty and _schedule_expiry_job remain importable."""
    from utils.executors.bounty_spawn_executor import _announce_bounty, _schedule_expiry_job

    assert callable(_announce_bounty)
    assert callable(_schedule_expiry_job)


async def test_job_executor_dispatches_bounty_spawn_orchestrate():
    """Test 25a: JobExecutor dispatches bounty_spawn_orchestrate to the orchestrator function."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn_orchestrate"}
    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_bounty_spawn_orchestrate_job", mock_fn):
        await executor.execute("orch-dispatch", payload)
    mock_fn.assert_awaited_once_with("orch-dispatch", payload)


async def test_job_executor_dispatches_bounty_spawn_one():
    """Test 25b: JobExecutor dispatches bounty_spawn_one to the per-tier executor function."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
    mock_fn = AsyncMock(return_value={"success": True})
    with patch("utils.job_executor.execute_bounty_spawn_one_job", mock_fn):
        await executor.execute("one-dispatch", payload)
    mock_fn.assert_awaited_once_with("one-dispatch", payload)


def test_main_payload_uses_orchestrate_job_type():
    """Verify main.py DEFAULT_SCHEDULER_JOBS uses bounty_spawn_orchestrate."""
    main_path = _os.path.join(_SRC, "main.py")
    with open(main_path) as f:
        content = f.read()

    assert '"job_type": "bounty_spawn_orchestrate"' in content, (
        "main.py DEFAULT_SCHEDULER_JOBS should use job_type='bounty_spawn_orchestrate'"
    )
    assert '"job_type": "bounty_spawn"' not in content, (
        "main.py should no longer use job_type='bounty_spawn' for the default job"
    )


# ===========================================================================
# DEF-001: Orchestrator passes the prefixed job_id through to add_job(id=...)
# ===========================================================================


async def test_def001_orchestrator_post_includes_prefixed_job_id_in_body(sqlite_engine_and_factory):
    """DEF-001: the orchestrator must pass ``bounty_spawn_<gid>_<tier>_<uuid>`` as add_job id=."""
    import re

    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    _engine, factory = sqlite_engine_and_factory
    async with factory() as db:
        await _seed_config(db, 9001, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})

    scheduled_ids: list[str] = []

    def _capture_id(func, trigger=None, run_date=None, args=None, id=None, **kwargs):
        if id is not None:
            scheduled_ids.append(id)

    sched = _empty_scheduler()
    sched.add_job = MagicMock(side_effect=_capture_id)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("utils.scheduler_holder.get_scheduler", return_value=sched),
    ):
        await execute_bounty_spawn_orchestrate_job("def001-body", {"job_type": "bounty_spawn_orchestrate"})

    assert len(scheduled_ids) == 4, f"Expected 4 add_job calls (one per tier), got {len(scheduled_ids)}"
    pattern = re.compile(r"^bounty_spawn_9001_(bronze|silver|gold|platinum)_[0-9a-f\-]{36}$")
    seen_tiers = set()
    for jid in scheduled_ids:
        assert pattern.match(jid), (
            f"Scheduled job id={jid!r} does not match bounty_spawn_<gid>_<tier>_<uuid> pattern (DEF-001 regression)."
        )
        seen_tiers.add(jid.split("_")[3])
    assert seen_tiers == {"bronze", "silver", "gold", "platinum"}
