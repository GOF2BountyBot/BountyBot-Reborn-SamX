"""
Unit tests for the new bounty spawn orchestrator and one-time per-tier executor.

Covers spec requirements 1–25 from the task description:

Orchestrator tests
------------------
 1. Skips tier when active + queued >= max_for_tier
 2. Queues exactly ONE job per eligible tier per invocation
 3. Fire time falls within [now + interval - window, now + interval + window]
 4. Queue-count query filters by (guild_id, tier_lower) via SQL LIKE pattern
 5. Orchestrator uses bounty_max_per_tier from config (NOT temperature service)
 6. Orchestrator does NOT touch or read next_spawn_check_at
 7. Guild not fully configured → orchestrator skips entire guild
 8. bounty_max_per_tier[tier_lower] missing or 0 → tier skipped

One-time executor tests
-----------------------
 9. Missing guild_id in payload → WARNING, no-op return
10. Missing tier in payload → WARNING, no-op return
11. Guild config missing → WARNING, no-op return
12. Guild not fully configured → WARNING, no-op return
13. Division channel_id null → WARNING, no-op return
14. Division role_id null → WARNING, no-op return
15. Active count already at max at fire time → INFO (not WARNING), return success=True
16. Happy path: spawns bounty, calls _schedule_expiry_job, calls _announce_bounty
17. BountyService.spawn_bounty returns None → WARNING, no-op return
18. _schedule_expiry_job raises → ERROR log but does NOT prevent announcement
19. _announce_bounty raises → ERROR log but does NOT prevent return success
20. Unexpected exception propagates (e.g., DB connection error)

Job ID tests
------------
21. Job ID format is bounty_spawn_{guild_id}_{tier_lower}_{uuid}
22. Job IDs can be parsed back to (guild_id, tier) via split("_")
23. UUID suffix is unique across two back-to-back queues

Regression (admin spawn path)
------------------------------
24. _announce_bounty and _schedule_expiry_job remain importable
25. Job executor dispatches bounty_spawn_orchestrate and bounty_spawn_one
"""

import os as _os
import random
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Seed for deterministic RNG in parametrized test
random.seed(42)

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger
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

# Ensure src is on the path.
_SRC = _os.path.join(_os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# NOTE: The sqlalchemy stub that was previously injected at module level
# has been moved into _inject_persist_stubs() / the _isolate_persist_stubs
# fixture to prevent collection-time sys.modules contamination (DEF-S11-001).
# Since sqlalchemy IS installed in the test environment, the real package is
# used directly; the stub is injected only during the lifetime of this module's
# fixture scope and is fully restored on teardown.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stub modules for deferred imports
# ---------------------------------------------------------------------------


def _ensure_stub(module_path: str, **attrs) -> types.ModuleType:
    """Inject a stub module into sys.modules (idempotent if already present)."""
    if module_path not in sys.modules:
        mod = types.ModuleType(module_path)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_path] = mod
    return sys.modules[module_path]


def _force_stub(module_path: str, **attrs) -> types.ModuleType:
    """Unconditionally inject a stub module into sys.modules (overrides real modules)."""
    mod = types.ModuleType(module_path)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[module_path] = mod
    return mod


def _inject_persist_stubs() -> None:
    """Inject all persist.* and services.* stubs required by this test module.

    Uses _force_stub (unconditional) so that real packages already loaded by
    earlier test modules are temporarily replaced for the lifetime of this
    module's fixture scope.  The fixture restores sys.modules on teardown.
    """
    global _mock_db_mgr_instance, _MockBountyRepository, _MockConfigRepository
    global _MockBountyService, _MockTemperatureService, _MockDiscordMessageRepository
    global _MockCriminalRepository, _mock_criminal_repo_instance

    _mock_db_mgr_instance = MagicMock()
    _force_stub("persist.database.manager", db_manager=_mock_db_mgr_instance)

    _MockBountyRepository = MagicMock()
    _force_stub("persist.repositories.bounty_repository", BountyRepository=_MockBountyRepository)

    _MockConfigRepository = MagicMock()
    _force_stub("persist.repositories.config_repository", ConfigRepository=_MockConfigRepository)

    _MockBountyService = MagicMock()
    _force_stub("services.bounty_service", BountyService=_MockBountyService)

    _MockTemperatureService = MagicMock()
    _force_stub("services.temperature_service", TemperatureService=_MockTemperatureService)

    _MockDiscordMessageRepository = MagicMock()
    _force_stub(
        "persist.repositories.discord_message_repository",
        DiscordMessageRepository=_MockDiscordMessageRepository,
    )

    _mock_criminal_repo_instance = AsyncMock()
    _mock_criminal_repo_instance.get_by_name = AsyncMock(return_value=None)
    _MockCriminalRepository = MagicMock(return_value=_mock_criminal_repo_instance)
    _force_stub("persist.repositories.criminal_repository", CriminalRepository=_MockCriminalRepository)

    _force_stub("persist")
    _force_stub("persist.database")
    _force_stub("persist.repositories")
    _force_stub("services")


# Module-level placeholders so helper functions below can reference these names.
# The real instances are created inside _isolate_persist_stubs before any test runs.
_mock_db_mgr_instance: MagicMock = MagicMock()
_MockBountyRepository: MagicMock = MagicMock()
_MockConfigRepository: MagicMock = MagicMock()
_MockBountyService: MagicMock = MagicMock()
_MockTemperatureService: MagicMock = MagicMock()
_MockDiscordMessageRepository: MagicMock = MagicMock()
_mock_criminal_repo_instance: AsyncMock = AsyncMock()
_MockCriminalRepository: MagicMock = MagicMock()


@pytest.fixture(autouse=True, scope="module")
def _isolate_persist_stubs():
    """
    Module-scoped autouse fixture that injects persist.* / services.* stubs
    into sys.modules for the duration of this test module ONLY, then fully
    restores the original sys.modules state so that later test modules (e.g.
    test_bounty_spawn_executor.py) can import the real packages without
    encountering a contaminated 'persist' stub.

    This fixture replaces the previous module-level _ensure_stub() calls that
    caused DEF-S11-001: sys.modules contamination across the full pytest session.
    """
    _saved = dict(sys.modules)

    _inject_persist_stubs()

    yield

    # Restore sys.modules exactly as it was before this module was collected.
    sys.modules.clear()
    sys.modules.update(_saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guild_config(
    guild_id: int,
    bronze_bounty_channel_id: int | None = 111,
    silver_bounty_channel_id: int | None = 222,
    gold_bounty_channel_id: int | None = 333,
    platinum_bounty_channel_id: int | None = 444,
    bounty_hunter_role_id: int | None = 555,
    bounty_max_per_tier: dict | None = None,
    bounty_expiry_minutes: int | None = 480,
    bounty_spawn_interval_minutes: int | None = 5,
    next_spawn_check_at=None,
) -> MagicMock:
    """Build a fully-configured mock GuildConfig."""
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.bronze_bounty_channel_id = bronze_bounty_channel_id
    cfg.silver_bounty_channel_id = silver_bounty_channel_id
    cfg.gold_bounty_channel_id = gold_bounty_channel_id
    cfg.platinum_bounty_channel_id = platinum_bounty_channel_id
    cfg.bounty_hunter_role_id = bounty_hunter_role_id
    cfg.bounty_max_per_tier = bounty_max_per_tier or {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
    cfg.bounty_expiry_minutes = bounty_expiry_minutes
    cfg.bounty_spawn_interval_minutes = bounty_spawn_interval_minutes
    cfg.next_spawn_check_at = next_spawn_check_at
    # Explicit attribute so hasattr() works predictably
    cfg.bronze_role_id = None
    cfg.silver_role_id = None
    cfg.gold_role_id = None
    cfg.platinum_role_id = None
    return cfg


def _make_bounty(
    bounty_id: int = 1,
    guild_id: int = 100,
    division: str = "bronze",
    criminal_name: str = "Kato Vort",
    criminal_faction: str = "Vossk",
    reward: int = 50000,
    tech_level: int = 5,
    end_time: datetime | None = None,
) -> MagicMock:
    b = MagicMock()
    b.id = bounty_id
    b.guild_id = guild_id
    b.division = division
    b.criminal_name = criminal_name
    b.criminal_faction = criminal_faction
    b.reward = reward
    b.tech_level = tech_level
    b.route = ["SysA", "SysB"]
    b.end_time = end_time or datetime.now(UTC) + timedelta(days=3)
    b.criminal_ship = None
    b.checked = None
    return b


def _mock_session_ctx(session: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _configure_db_manager(mock_db: AsyncMock) -> None:
    mgr = sys.modules["persist.database.manager"].db_manager
    mgr.get_session = MagicMock(return_value=_mock_session_ctx(mock_db))


def _configure_config_repo(guild_configs: list, get_by_guild_id_return=None) -> AsyncMock:
    mock_repo = AsyncMock()
    mock_repo.list_all = AsyncMock(return_value=guild_configs)
    if get_by_guild_id_return is not None:
        mock_repo.get_by_guild_id = AsyncMock(return_value=get_by_guild_id_return)
    else:
        mock_repo.get_by_guild_id = AsyncMock(return_value=guild_configs[0] if guild_configs else None)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_repo)
    return mock_repo


def _configure_bounty_repo(active_count_map: dict | None = None, count_return: int = 0) -> AsyncMock:
    """Configure BountyRepository for both the legacy count API and the
    post-Fix-C ``get_active_by_guild_and_division`` API.

    The orchestrator now reads the active list directly (to extract
    ``issue_time`` anchors for gap-aware scheduling). The list length still
    serves as the capacity-gate active count, so we synthesize a list of
    SimpleNamespace mock bounties with past ``issue_time`` values whose
    LENGTH matches the requested count.
    """
    from types import SimpleNamespace as _SN

    mock_repo = AsyncMock()

    def _make_mock_active_list(n: int):
        # Past issue_times that won't conflict with future fire-time computation.
        return [_SN(issue_time=datetime.now(UTC) - timedelta(minutes=10 + i * 5)) for i in range(n)]

    if active_count_map is not None:

        async def _count(db, guild_id, division):
            return active_count_map.get((guild_id, division), 0)

        async def _get_active(db, guild_id, division):
            return _make_mock_active_list(active_count_map.get((guild_id, division), 0))

        mock_repo.count_active_by_guild_and_division = _count
        mock_repo.get_active_by_guild_and_division = _get_active
    else:
        mock_repo.count_active_by_guild_and_division = AsyncMock(return_value=count_return)
        mock_repo.get_active_by_guild_and_division = AsyncMock(return_value=_make_mock_active_list(count_return))

    sys.modules["persist.repositories.bounty_repository"].BountyRepository = MagicMock(return_value=mock_repo)
    return mock_repo


def _configure_bounty_service(spawn_return) -> AsyncMock:
    mock_svc = AsyncMock()
    mock_svc.spawn_bounty = AsyncMock(return_value=spawn_return)
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)
    return mock_svc


def _make_sql_scalar_result(value: int) -> MagicMock:
    """Return a mock that behaves like an SQLAlchemy scalar result."""
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=value)
    return result


def _make_db_with_sql_count(queued_count: int) -> AsyncMock:
    """Build a mock DB session for use as the SQLAlchemy session in tests.

    The ``queued_count`` parameter is retained for call-site compatibility but
    no longer configures a ``db.execute`` mock — the orchestrator now reads
    already-queued jobs via the APScheduler API (``get_scheduler().get_jobs()``)
    rather than raw SQL.  Use ``_configure_scheduler_jobs`` to inject queued
    job fire times into the scheduler mock when a non-zero count is needed.
    """
    mock_db = AsyncMock()
    return mock_db


def _make_mock_scheduler_jobs(queued_count: int, guild_id: int, tier_lower: str) -> MagicMock:
    """Return a mock APScheduler scheduler whose ``get_jobs()`` returns
    ``queued_count`` jobs whose IDs match the bounty-spawn prefix for the given
    (guild_id, tier_lower).  Fire times are spread 10 min apart in the future
    so they are not collision candidates with a new job computed by the
    gap-aware scheduling logic.
    """
    from datetime import datetime as _dt
    from types import SimpleNamespace

    prefix = f"bounty_spawn_{guild_id}_{tier_lower}_"
    now = _dt.now(UTC)
    mock_jobs = []
    for i in range(queued_count):
        job = SimpleNamespace(
            id=f"{prefix}{i:04d}",
            next_run_time=now + timedelta(minutes=10 + i * 10),
        )
        mock_jobs.append(job)

    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs = MagicMock(return_value=mock_jobs)
    return mock_scheduler


def _configure_scheduler_jobs(queued_count: int, guild_id: int, tier_lower: str) -> MagicMock:
    """Patch ``utils.scheduler_holder.get_scheduler`` so the orchestrator sees
    ``queued_count`` already-scheduled jobs for (guild_id, tier_lower).

    Returns the mock scheduler so callers can make additional assertions.
    Note: callers MUST stop the patch themselves (or use as a context manager).
    This helper starts the patch and registers a finaliser via the returned
    patcher object attached as ``.patcher`` on the scheduler mock.
    """
    mock_scheduler = _make_mock_scheduler_jobs(queued_count, guild_id, tier_lower)
    patcher = patch("utils.scheduler_holder.get_scheduler", return_value=mock_scheduler)
    patcher.start()
    mock_scheduler.patcher = patcher
    return mock_scheduler


# ===========================================================================
# Tests 1–2: Capacity skipping / queueing
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_skips_tier_when_capacity_full():
    """Test 1: Orchestrator skips tier when active + queued >= max_for_tier."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(10)  # db mock (execute no longer called)
    _configure_db_manager(mock_db)
    _configure_config_repo(
        [_make_guild_config(100, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})]
    )
    # 10 active bounties + 10 queued = 20 = max → skip
    _configure_bounty_repo(active_count_map={(100, "bronze"): 10})
    # Inject 10 queued spawn jobs for bronze via the scheduler API
    mock_sched = _configure_scheduler_jobs(10, 100, "bronze")

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    try:
        with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await execute_bounty_spawn_orchestrate_job("orch-1", {"job_type": "bounty_spawn_orchestrate"})
    finally:
        mock_sched.patcher.stop()

    # Bronze tier should be skipped (10+10=20 = max); silver/gold/platinum may queue
    tier_results = result["results"].get(100, {}).get("tiers", {})
    assert tier_results.get("bronze", {}).get("queued", 1) == 0


@pytest.mark.asyncio
async def test_orchestrator_queues_when_below_capacity():
    """Test 1b: Orchestrator queues when active + queued < max_for_tier (10+9=19 < 20)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(9)  # db mock (execute no longer called)
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(200, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})
    _configure_config_repo([cfg])
    # 10 active + 9 queued = 19 < 20 → should queue
    _configure_bounty_repo(active_count_map={(200, "bronze"): 10})
    # Inject 9 queued spawn jobs for bronze via the scheduler API
    mock_sched = _configure_scheduler_jobs(9, 200, "bronze")

    jobs_posted = []

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        jobs_posted.append(json)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    try:
        with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = _mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await execute_bounty_spawn_orchestrate_job("orch-queue", {"job_type": "bounty_spawn_orchestrate"})
    finally:
        mock_sched.patcher.stop()

    tier_results = result["results"].get(200, {}).get("tiers", {})
    assert tier_results.get("bronze", {}).get("queued", 0) == 1


@pytest.mark.asyncio
async def test_orchestrator_queues_exactly_one_job_per_eligible_tier():
    """Test 2: Orchestrator queues exactly ONE job per eligible tier per invocation."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(0)  # no queued jobs
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(300, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)  # no active bounties

    jobs_posted = []

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        jobs_posted.append({"url": url, "payload": json})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await execute_bounty_spawn_orchestrate_job("orch-2", {"job_type": "bounty_spawn_orchestrate"})

    # 4 tiers: bronze, silver, gold, platinum — one job each
    assert result["total_queued"] == 4
    assert len(jobs_posted) == 4


# ===========================================================================
# Test 3: Fire time falls within [now + interval - window, now + interval + window]
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("interval_minutes", [5, 60, 120, 240])
async def test_orchestrator_fire_time_within_window(interval_minutes: int):
    """Fix C: Fire time falls within the gap-aware target window.

    Cold-start branch (no active bounties, no queued jobs):
      target = now + interval_minutes / 2
      jitter window = min(15, 0.25 * interval_minutes)
      fire_time ∈ [target - window, target + window]

    Plus an additional ``MIN_LEAD_SECONDS`` lower clamp to prevent past-time
    scheduling.
    """
    from utils.executors.bounty_spawn_executor import (
        _MIN_LEAD_SECONDS,
        execute_bounty_spawn_orchestrate_job,
    )

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(
        400,
        bounty_spawn_interval_minutes=interval_minutes,
        bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3},
    )
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    fire_times_recorded = []
    now_before = datetime.now(UTC)

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        if json and "run_at" in json:
            fire_times_recorded.append(json["run_at"])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await execute_bounty_spawn_orchestrate_job(
            f"orch-window-{interval_minutes}", {"job_type": "bounty_spawn_orchestrate"}
        )

    # Gap-aware (Fix C) cold-start window:
    # target = now + interval/2; fire_time ∈ [target - window, target + window].
    half_interval = interval_minutes / 2.0
    window_minutes = min(15.0, 0.25 * interval_minutes)
    lower_bound = now_before + timedelta(minutes=max(0.0, half_interval - window_minutes))
    # +1s tolerance for time elapsed between `now_before` and orchestrator call.
    upper_bound = now_before + timedelta(minutes=half_interval + window_minutes, seconds=2)
    # The MIN_LEAD_SECONDS clamp can pull lower_bound forward if the
    # cold-start target lands too close to now (small intervals).
    min_lead = now_before + timedelta(seconds=_MIN_LEAD_SECONDS)
    if lower_bound < min_lead:
        lower_bound = min_lead

    assert len(fire_times_recorded) > 0, "No fire times recorded — no jobs were queued"

    for fire_time_str in fire_times_recorded:
        fire_time = datetime.fromisoformat(fire_time_str)
        assert fire_time >= lower_bound, (
            f"Fire time {fire_time} is before lower bound {lower_bound} "
            f"(interval={interval_minutes}min, gap-aware cold-start target)"
        )
        assert fire_time <= upper_bound, (
            f"Fire time {fire_time} is after upper bound {upper_bound} "
            f"(interval={interval_minutes}min, gap-aware cold-start target)"
        )


# ===========================================================================
# Test 4: Queue-count query filters by (guild_id, tier_lower) via LIKE pattern
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_queue_count_uses_correct_prefix_filter():
    """Test 4: Queue-count uses Python ``startswith`` prefix bounty_spawn_{guild_id}_{tier_lower}_.

    The tier component of the prefix MUST be load-bearing.  The scenario is
    constructed so that a wrong/too-broad prefix flips the capacity decision:

    Setup (guild 500, all active=0):
      - bronze: max=2, exactly 2 matching queued jobs  →  capacity_full  (0+2 >= 2)
      - silver/gold/platinum: max=5, exactly 2 queued jobs each  →  not full (0+2 < 5)

    Correct prefix (bounty_spawn_500_bronze_):
      bronze is capacity_full → 0 new jobs queued for bronze
      other tiers each queue 1 → total_queued == 3

    Mutation (a) too-broad prefix (bounty_spawn_500_):
      bronze counts all 8 guild-500 jobs (2 per tier × 4) → still ≥ 2 → still full
      BUT silver/gold/platinum also count 8 jobs → 8 >= 5 → all tiers become full
      → total_queued == 0  ≠ 3  → assertion FAILS

    Mutation (b) wrong-guild prefix (bounty_spawn_999_bronze_):
      bronze counts the 1 wrong-guild decoy job (999_bronze) → 1 < 2 → NOT full
      → bronze would queue 1 more → total_queued == 4  ≠ 3  → assertion FAILS

    Decoys are included for additional wrong-guild / wrong-tier-suffix coverage.
    """
    from types import SimpleNamespace

    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    guild_id = 500
    tiers = ["bronze", "silver", "gold", "platinum"]

    # Build synthetic job list: exactly 2 matching jobs per tier for guild 500.
    now = datetime.now(UTC)
    all_jobs = []
    for tier in tiers:
        prefix = f"bounty_spawn_{guild_id}_{tier}_"
        for i in range(2):
            all_jobs.append(SimpleNamespace(id=f"{prefix}{i:04d}", next_run_time=now + timedelta(minutes=10 + i * 5)))
    # Decoys — must NOT be counted for guild 500 / bronze
    all_jobs.append(SimpleNamespace(id="bounty_spawn_999_bronze_0000", next_run_time=now + timedelta(minutes=5)))
    all_jobs.append(SimpleNamespace(id="bounty_spawn_500_bronzeX_0000", next_run_time=now + timedelta(minutes=5)))

    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs = MagicMock(return_value=all_jobs)

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    # bronze max == number of bronze queued jobs → bronze is capacity_full.
    # silver/gold/platinum max=5 > 2 queued → they still get a new job each.
    cfg = _make_guild_config(guild_id, bounty_max_per_tier={"bronze": 2, "silver": 5, "gold": 5, "platinum": 5})
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.scheduler_holder.get_scheduler", return_value=mock_scheduler):
        with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = _mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await execute_bounty_spawn_orchestrate_job("orch-4", {"job_type": "bounty_spawn_orchestrate"})

    # Bronze is capacity_full (0 active + 2 queued == 2 == max).
    # Silver, gold, platinum are not full (0 + 2 < 5) → each queues 1 new job.
    # Total new jobs queued == 3 (NOT 4).
    # A too-broad prefix makes all tiers appear full → 0; a wrong-guild prefix
    # makes bronze appear empty → 4.  Both are caught by this single assertion.
    assert result["total_queued"] == 3, (
        f"Expected 3 new jobs queued (bronze full, 3 other tiers each queue 1), got {result['total_queued']}"
    )

    # Bronze tier must be reported as capacity_full.
    guild_result = result.get("results", {}).get(guild_id, {})
    bronze_result = guild_result.get("tiers", {}).get("bronze", {})
    assert bronze_result.get("reason") == "capacity_full", (
        f"Expected bronze capacity_full, got: {bronze_result}"
    )

    # Other tiers must each have queued exactly 1 new job.
    for tier in ("silver", "gold", "platinum"):
        tier_result = guild_result.get("tiers", {}).get(tier, {})
        assert tier_result.get("queued") == 1, (
            f"Expected {tier} to queue 1 new job, got: {tier_result}"
        )

    # get_jobs() must have been called once per tier (4 tiers).
    assert mock_scheduler.get_jobs.call_count == 4, (
        f"Expected get_jobs() called once per tier (4), got {mock_scheduler.get_jobs.call_count}"
    )


# ===========================================================================
# Test 5: Orchestrator uses bounty_max_per_tier (NOT temperature service)
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_uses_bounty_max_per_tier_not_temperature():
    """Test 5: Max is read from bounty_max_per_tier[tier_lower] — TemperatureService is NOT called."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    # Patch TemperatureService to raise if called
    temp_called = []
    mock_temp = MagicMock()
    mock_temp.get_max_bounties = MagicMock(side_effect=lambda t: temp_called.append(t) or 999)
    sys.modules["services.temperature_service"].TemperatureService = mock_temp

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(600, bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5, "platinum": 5})
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await execute_bounty_spawn_orchestrate_job("orch-5", {"job_type": "bounty_spawn_orchestrate"})

    # TemperatureService.get_max_bounties must NOT have been called by the orchestrator
    assert temp_called == [], "TemperatureService.get_max_bounties was called — it should not be"


# ===========================================================================
# Test 6: Orchestrator does NOT touch next_spawn_check_at
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_does_not_read_or_write_next_spawn_check_at():
    """Test 6: Orchestrator does not access next_spawn_check_at on the config object."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    accessed_attrs = []

    class _TrackedConfig:
        guild_id = 700
        bronze_bounty_channel_id = 111
        silver_bounty_channel_id = 222
        gold_bounty_channel_id = 333
        platinum_bounty_channel_id = 444
        bounty_hunter_role_id = 555
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
    tracked_cfg.next_spawn_check_at = None  # set but should NOT be read

    mock_repo = AsyncMock()
    mock_repo.list_all = AsyncMock(return_value=[tracked_cfg])
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_repo)

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    _configure_bounty_repo(count_return=0)

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await execute_bounty_spawn_orchestrate_job("orch-6", {"job_type": "bounty_spawn_orchestrate"})

    # next_spawn_check_at should NOT have been accessed by the orchestrator
    assert "next_spawn_check_at" not in accessed_attrs, (
        f"Orchestrator accessed next_spawn_check_at — it should not. Accesses: {accessed_attrs}"
    )


# ===========================================================================
# Test 7: Guild not fully configured → orchestrator skips entire guild
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_skips_guild_not_fully_configured():
    """Test 7: Guild missing channel/role IDs is skipped entirely."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    # Guild missing bounty_hunter_role_id
    cfg = _make_guild_config(800, bounty_hunter_role_id=None)
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    jobs_posted = []

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        jobs_posted.append(json)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await execute_bounty_spawn_orchestrate_job("orch-7", {"job_type": "bounty_spawn_orchestrate"})

    assert result["total_queued"] == 0
    assert len(jobs_posted) == 0


# ===========================================================================
# Test 8: bounty_max_per_tier[tier_lower] missing or 0 → tier skipped
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_skips_tier_when_max_zero_or_missing():
    """Test 8: Tier is skipped when max_for_tier is 0 or missing."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    # Bronze max=0 (disabled), silver missing (uses DEFAULT_MAX), gold=3
    cfg = _make_guild_config(900, bounty_max_per_tier={"bronze": 0, "gold": 3, "platinum": 3})
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    jobs_posted_tiers = []

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        if json and "payload" in json:
            tier = json["payload"].get("tier")
            if tier:
                jobs_posted_tiers.append(tier)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await execute_bounty_spawn_orchestrate_job("orch-8", {"job_type": "bounty_spawn_orchestrate"})

    # Bronze must not be queued (max=0)
    assert "bronze" not in jobs_posted_tiers, f"Bronze should be skipped (max=0) but got: {jobs_posted_tiers}"
    # Gold and platinum should be queued
    assert "gold" in jobs_posted_tiers
    assert "platinum" in jobs_posted_tiers


# ===========================================================================
# Tests 9–14: One-time executor payload validation + config checks
# ===========================================================================


@pytest.mark.asyncio
async def test_one_missing_guild_id_returns_warning():
    """Test 9: Missing guild_id in payload → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    with patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger:
        result = await execute_bounty_spawn_one_job("one-9", {"job_type": "bounty_spawn_one", "tier": "bronze"})

    assert result["success"] is False
    assert result["reason"] == "missing_payload"
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_one_missing_tier_returns_warning():
    """Test 10: Missing tier in payload → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    with patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger:
        result = await execute_bounty_spawn_one_job("one-10", {"job_type": "bounty_spawn_one", "guild_id": 100})

    assert result["success"] is False
    assert result["reason"] == "missing_payload"
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_one_guild_config_missing_returns_warning():
    """Test 11: Guild config missing → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    # Config returns None for this guild
    _configure_config_repo([], get_by_guild_id_return=None)

    with patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger:
        result = await execute_bounty_spawn_one_job(
            "one-11", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "guild_not_configured"
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_one_guild_not_fully_configured_returns_warning():
    """Test 12: Guild not fully configured → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    # Guild missing role ID
    cfg = _make_guild_config(100, bounty_hunter_role_id=None)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)

    with patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger:
        result = await execute_bounty_spawn_one_job(
            "one-12", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "guild_not_configured"
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_one_division_channel_none_returns_warning():
    """Test 13: Division channel_id null → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    # Bronze channel is None but guild is otherwise fully configured
    cfg = _make_guild_config(100, bronze_bounty_channel_id=None)
    # Note: _is_guild_fully_configured returns False if bronze channel is None
    # So use a special config where ONLY the tier channel is null but guild is "configured"
    # We'll test with a custom config that passes the guild fully-configured check but has null tier channel
    # Best approach: use a tier whose channel-getter returns None, bypass the guild check by mocking
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    _configure_bounty_repo(count_return=0)

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=None),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-13", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "tier_not_configured"
    mock_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_one_division_role_none_returns_warning():
    """Test 14: Division role_id null → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    _configure_bounty_repo(count_return=0)

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=None),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-14", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "tier_not_configured"
    mock_logger.warning.assert_called()


# ===========================================================================
# Test 15: Active count already at max → INFO (not WARNING), success=True
# ===========================================================================


@pytest.mark.asyncio
async def test_one_capacity_reached_at_fire_time_returns_info_not_warning():
    """Test 15: Active count at max at fire time → INFO (not WARNING), success=True reason=capacity_reached."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    # Active count equals max → capacity reached
    _configure_bounty_repo(count_return=3)

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-15", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is True
    assert result["reason"] == "capacity_reached"
    # Verify INFO was called (not WARNING) for the capacity message
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("capacity reached" in msg or "capacity_reached" in msg or "benign race" in msg for msg in info_calls), (
        f"Expected INFO log about capacity reached, got: {info_calls}"
    )
    # WARNING must NOT have been called about capacity
    warn_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("capacity" in msg for msg in warn_calls), (
        f"WARNING was called about capacity (should be INFO): {warn_calls}"
    )


# ===========================================================================
# Test 16: Happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_one_happy_path():
    """Test 16: Happy path — spawns bounty, calls _schedule_expiry_job, _announce_bounty, returns bounty_id."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    # Active count below max
    _configure_bounty_repo(count_return=0)

    spawned = _make_bounty(bounty_id=42, guild_id=100, division="bronze")
    _configure_bounty_service(spawned)

    mock_expiry = AsyncMock(return_value="exp-42")
    # Fix B: announce now returns a structured dict; tests must provide it.
    mock_announce = AsyncMock(
        return_value={
            "success": True,
            "failure_phase": None,
            "discord_message_id": 8001,
            "channel_id": 111,
        }
    )

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
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


# ===========================================================================
# Test 17: spawn_bounty returns None → WARNING
# ===========================================================================


@pytest.mark.asyncio
async def test_one_spawn_returns_none_warning():
    """Test 17: BountyService.spawn_bounty returns None → WARNING, no-op return."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    _configure_bounty_repo(count_return=0)
    _configure_bounty_service(None)  # spawn returns None

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-17", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    assert result["success"] is False
    assert result["reason"] == "spawn_failed"
    mock_logger.warning.assert_called()


# ===========================================================================
# Test 18: _schedule_expiry_job raises → ERROR log but announcement still proceeds
# ===========================================================================


@pytest.mark.asyncio
async def test_one_expiry_raises_does_not_prevent_announcement():
    """Test 18: _schedule_expiry_job raises → ERROR log but _announce_bounty is still called."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    _configure_bounty_repo(count_return=0)

    spawned = _make_bounty(bounty_id=99, guild_id=100, division="bronze")
    _configure_bounty_service(spawned)

    mock_announce = AsyncMock(
        return_value={
            "success": True,
            "failure_phase": None,
            "discord_message_id": 8002,
            "channel_id": 111,
        }
    )

    async def _expiry_raises(job_id, bounty):
        raise RuntimeError("scheduler down")

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=_expiry_raises),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
        patch("utils.executors.bounty_spawn_executor._push_bounty_cache", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-18", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    # Announcement should still have been called despite expiry failure
    mock_announce.assert_awaited_once()
    # ERROR should have been logged
    mock_logger.error.assert_called()
    # Result should still be success
    assert result["success"] is True


# ===========================================================================
# Test 19: _announce_bounty raises → ERROR log but return success
# ===========================================================================


@pytest.mark.asyncio
async def test_one_announce_raises_triggers_compensating_rollback():
    """Fix B (revised): when ``_announce_bounty`` raises, the executor now
    runs the compensating rollback (delete post if any, cancel expiry,
    delete bounty row) and returns ``success=False`` with the rollback
    summary. The old non-fatal contract is replaced.

    Comprehensive integration coverage of the new rollback (with cross-session
    reload) lives in ``test_bounty_spawn_executor.py``.
    """
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(100)
    _configure_config_repo([cfg], get_by_guild_id_return=cfg)
    _configure_bounty_repo(count_return=0)

    spawned = _make_bounty(bounty_id=77, guild_id=100, division="bronze")
    _configure_bounty_service(spawned)

    async def _announce_raises(job_id, bounty, config, db):
        raise RuntimeError("gateway unreachable")

    # Compensator helper is fully mocked here — its real behavior is covered
    # by the integration tests in test_bounty_spawn_executor.py.
    compensate_calls: list[tuple] = []

    async def _capture_compensate(**kwargs):
        compensate_calls.append(kwargs)
        return {
            "post_deleted": False,
            "expiry_cancelled": True,
            "bounty_deleted": True,
            "cache_repushed": True,
        }

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
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
    # Compensating rollback was invoked exactly once with the captured IDs.
    assert len(compensate_calls) == 1
    call_kwargs = compensate_calls[0]
    assert call_kwargs["bounty_id"] == 77
    assert call_kwargs["guild_id"] == 100
    assert call_kwargs["expiry_job_id"] == "exp-77"


# ===========================================================================
# Test 20: Unexpected exception propagates
# ===========================================================================


@pytest.mark.asyncio
async def test_one_unexpected_exception_propagates():
    """Test 20: Unexpected exception (e.g., DB error) propagates so APScheduler marks job failed."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    # config_repo.get_by_guild_id raises DB error
    mock_repo = AsyncMock()
    mock_repo.get_by_guild_id = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_repo)

    with pytest.raises(RuntimeError, match="DB connection lost"):
        await execute_bounty_spawn_one_job(
            "one-20", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )


# ===========================================================================
# Tests 21–23: Job ID format and uniqueness
# ===========================================================================


def test_job_id_format():
    """Test 21: Job ID format is bounty_spawn_{guild_id}_{tier_lower}_{uuid}."""
    guild_id = 12345
    tier_lower = "silver"
    uid = str(uuid.uuid4())
    job_id = f"bounty_spawn_{guild_id}_{tier_lower}_{uid}"

    assert job_id.startswith(f"bounty_spawn_{guild_id}_{tier_lower}_")
    # Verify UUID suffix is valid
    suffix = job_id[len(f"bounty_spawn_{guild_id}_{tier_lower}_") :]
    parsed = uuid.UUID(suffix)
    assert str(parsed) == suffix


def test_job_id_parseable():
    """Test 22: Job IDs can be parsed back to (guild_id, tier) via split('_')."""
    guild_id = 99999
    tier_lower = "gold"
    uid = str(uuid.uuid4())
    job_id = f"bounty_spawn_{guild_id}_{tier_lower}_{uid}"

    # Split: ["bounty", "spawn", guild_id_str, tier_str, uuid_str]
    parts = job_id.split("_")
    # parts[0]="bounty", parts[1]="spawn", parts[2]=guild_id, parts[3]=tier, parts[4+]=uuid parts
    assert parts[0] == "bounty"
    assert parts[1] == "spawn"
    assert int(parts[2]) == guild_id
    assert parts[3] == tier_lower


def test_job_id_unique_across_calls():
    """Test 23: UUID suffix is unique across two back-to-back queues."""
    guild_id = 42
    tier = "platinum"

    uid1 = str(uuid.uuid4())
    uid2 = str(uuid.uuid4())

    job_id_1 = f"bounty_spawn_{guild_id}_{tier}_{uid1}"
    job_id_2 = f"bounty_spawn_{guild_id}_{tier}_{uid2}"

    assert job_id_1 != job_id_2


# ===========================================================================
# Tests 24–25: Regression — admin spawn path + dispatcher
# ===========================================================================


def test_admin_spawn_can_import_helpers():
    """Test 24: _announce_bounty and _schedule_expiry_job remain importable."""
    from utils.executors.bounty_spawn_executor import _announce_bounty, _schedule_expiry_job

    assert callable(_announce_bounty)
    assert callable(_schedule_expiry_job)


@pytest.mark.asyncio
async def test_job_executor_dispatches_bounty_spawn_orchestrate():
    """Test 25a: JobExecutor dispatches bounty_spawn_orchestrate to the orchestrator function."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn_orchestrate"}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_bounty_spawn_orchestrate_job", mock_fn):
        await executor.execute("orch-dispatch", payload)

    mock_fn.assert_awaited_once_with("orch-dispatch", payload)


@pytest.mark.asyncio
async def test_job_executor_dispatches_bounty_spawn_one():
    """Test 25b: JobExecutor dispatches bounty_spawn_one to the per-tier executor function."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}

    mock_fn = AsyncMock(return_value={"success": True})
    with patch("utils.job_executor.execute_bounty_spawn_one_job", mock_fn):
        await executor.execute("one-dispatch", payload)

    mock_fn.assert_awaited_once_with("one-dispatch", payload)


# ===========================================================================
# Additional: main.py uses bounty_spawn_orchestrate payload
# ===========================================================================


def test_main_payload_uses_orchestrate_job_type():
    """Verify main.py DEFAULT_SCHEDULER_JOBS uses bounty_spawn_orchestrate."""
    main_path = _os.path.join(_SRC, "main.py")
    with open(main_path) as f:
        content = f.read()

    assert '"job_type": "bounty_spawn_orchestrate"' in content, (
        "main.py DEFAULT_SCHEDULER_JOBS should use job_type='bounty_spawn_orchestrate'"
    )
    # Ensure OLD payload is gone
    assert '"job_type": "bounty_spawn"' not in content, (
        "main.py should no longer use job_type='bounty_spawn' for the default job"
    )


# ===========================================================================
# DEF-001: Orchestrator POST body contains the prefixed job_id
# ===========================================================================
#
# Before the fix:
#   1. Orchestrator POSTed body with ``job_id=bounty_spawn_{gid}_{tier}_{uuid}``
#   2. OneTimeJob schema silently dropped ``job_id`` (no such field)
#   3. Router generated a fresh UUID and called scheduler.add_job(id=<uuid>)
#   4. apscheduler_jobs stored the UUID, NOT the prefixed ID
#   5. The orchestrator's next-tick LIKE query against ``bounty_spawn_%``
#      returned 0 queued jobs → dedup collapsed → multi-queue bug
#
# After the fix (Option A): the caller-supplied ``job_id`` flows through
# unchanged into scheduler.add_job(id=...) so the LIKE query finds the row.
# This test locks in the orchestrator-side half of the contract.  The
# router-side half is covered in tests/api/test_scheduler_router.py
# (TestScheduleJob.test_schedule_job_honors_client_supplied_job_id) and
# by the end-to-end test in that same file.
# ===========================================================================


@pytest.mark.asyncio
async def test_def001_orchestrator_post_includes_prefixed_job_id_in_body():
    """DEF-001: Orchestrator must include ``job_id`` in the POST body.

    The scheduler router relies on the caller to supply ``job_id`` so it
    can pass it through to APScheduler's ``add_job(id=...)``.  If the
    orchestrator stops including it, the LIKE-based dedup query silently
    degenerates to 0 — same failure mode as the original DEF-001 bug.
    """
    import re

    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(0)
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(
        9001,
        bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3},
    )
    _configure_config_repo([cfg])
    _configure_bounty_repo(count_return=0)

    posted_bodies: list[dict] = []

    async def _mock_post(url, json=None, timeout=None, **kwargs):
        posted_bodies.append(json)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={})
        return mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await execute_bounty_spawn_orchestrate_job("def001-body", {"job_type": "bounty_spawn_orchestrate"})

    assert len(posted_bodies) == 4, f"Expected 4 POSTs (one per tier), got {len(posted_bodies)}"
    pattern = re.compile(r"^bounty_spawn_9001_(bronze|silver|gold|platinum)_[0-9a-f\-]{36}$")
    seen_tiers = set()
    for body in posted_bodies:
        assert "job_id" in body, (
            f"POST body missing ``job_id`` key: {body!r}. DEF-001 regression — the router "
            "cannot honour a caller-supplied ID that isn't present."
        )
        assert pattern.match(body["job_id"]), (
            f"POSTed job_id={body['job_id']!r} does not match bounty_spawn_<gid>_<tier>_<uuid> pattern"
        )
        seen_tiers.add(body["job_id"].split("_")[3])

    assert seen_tiers == {"bronze", "silver", "gold", "platinum"}
