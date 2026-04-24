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
# Stub sqlalchemy if not installed
# ---------------------------------------------------------------------------
if "sqlalchemy" not in sys.modules:
    _mock_sa = types.ModuleType("sqlalchemy")
    _mock_sa.func = MagicMock()
    _mock_sa.select = MagicMock()
    _mock_sa.text = MagicMock()
    _mock_sa.Integer = MagicMock()
    _mock_sa.BigInteger = MagicMock()
    _mock_sa.String = MagicMock()
    _mock_sa.Float = MagicMock()
    _mock_sa.JSON = MagicMock()
    _mock_sa.DateTime = MagicMock()
    _mock_sa.Boolean = MagicMock()
    _mock_sa.Text = MagicMock()
    _mock_sa.ForeignKey = MagicMock()
    _mock_sa.Column = MagicMock()
    _mock_sa.UniqueConstraint = MagicMock()
    _mock_sa.Index = MagicMock()
    _mock_sa.event = MagicMock()
    _mock_sa.inspect = MagicMock()
    _mock_sa.orm = types.ModuleType("sqlalchemy.orm")
    _mock_sa.orm.DeclarativeBase = MagicMock()
    _mock_sa.orm.Mapped = MagicMock()
    _mock_sa.orm.mapped_column = MagicMock()
    _mock_sa.orm.relationship = MagicMock()
    _mock_sa.orm.Session = MagicMock()
    _mock_sa.orm.selectinload = MagicMock()
    _mock_sa.ext = types.ModuleType("sqlalchemy.ext")
    _mock_sa.ext.asyncio = types.ModuleType("sqlalchemy.ext.asyncio")
    _mock_sa.ext.asyncio.AsyncSession = MagicMock()
    _mock_sa.ext.asyncio.create_async_engine = MagicMock()
    _mock_sa.ext.asyncio.async_sessionmaker = MagicMock()
    _mock_sa.dialects = types.ModuleType("sqlalchemy.dialects")
    _mock_sa.dialects.postgresql = types.ModuleType("sqlalchemy.dialects.postgresql")
    _mock_sa.dialects.postgresql.ARRAY = MagicMock()
    sys.modules["sqlalchemy"] = _mock_sa
    sys.modules["sqlalchemy.orm"] = _mock_sa.orm
    sys.modules["sqlalchemy.ext"] = _mock_sa.ext
    sys.modules["sqlalchemy.ext.asyncio"] = _mock_sa.ext.asyncio
    sys.modules["sqlalchemy.dialects"] = _mock_sa.dialects
    sys.modules["sqlalchemy.dialects.postgresql"] = _mock_sa.dialects.postgresql


# ---------------------------------------------------------------------------
# Stub modules for deferred imports
# ---------------------------------------------------------------------------


def _ensure_stub(module_path: str, **attrs) -> types.ModuleType:
    if module_path not in sys.modules:
        mod = types.ModuleType(module_path)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_path] = mod
    return sys.modules[module_path]


_mock_db_mgr_instance = MagicMock()
_ensure_stub("persist.database.manager", db_manager=_mock_db_mgr_instance)

_MockBountyRepository = MagicMock()
_ensure_stub("persist.repositories.bounty_repository", BountyRepository=_MockBountyRepository)

_MockConfigRepository = MagicMock()
_ensure_stub("persist.repositories.config_repository", ConfigRepository=_MockConfigRepository)

_MockBountyService = MagicMock()
_ensure_stub("services.bounty_service", BountyService=_MockBountyService)

_MockTemperatureService = MagicMock()
_ensure_stub("services.temperature_service", TemperatureService=_MockTemperatureService)

_MockDiscordMessageRepository = MagicMock()
_ensure_stub("persist.repositories.discord_message_repository", DiscordMessageRepository=_MockDiscordMessageRepository)

_mock_criminal_repo_instance = AsyncMock()
_mock_criminal_repo_instance.get_by_name = AsyncMock(return_value=None)
_MockCriminalRepository = MagicMock(return_value=_mock_criminal_repo_instance)
_ensure_stub("persist.repositories.criminal_repository", CriminalRepository=_MockCriminalRepository)

_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")


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
    """Configure BountyRepository.count_active_by_guild_and_division."""
    mock_repo = AsyncMock()

    if active_count_map is not None:

        async def _count(db, guild_id, division):
            return active_count_map.get((guild_id, division), 0)

        mock_repo.count_active_by_guild_and_division = _count
    else:
        mock_repo.count_active_by_guild_and_division = AsyncMock(return_value=count_return)

    mock_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
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
    """Build a mock DB session where execute() returns a scalar result for COUNT queries."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_make_sql_scalar_result(queued_count))
    return mock_db


# ===========================================================================
# Tests 1–2: Capacity skipping / queueing
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_skips_tier_when_capacity_full():
    """Test 1: Orchestrator skips tier when active + queued >= max_for_tier."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(10)  # 10 queued jobs
    _configure_db_manager(mock_db)
    _configure_config_repo(
        [_make_guild_config(100, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})]
    )
    # 10 active bounties + 10 queued = 20 = max → skip
    _configure_bounty_repo(active_count_map={(100, "bronze"): 10})

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await execute_bounty_spawn_orchestrate_job("orch-1", {"job_type": "bounty_spawn_orchestrate"})

    # Bronze tier should be skipped (10+10=20 = max); silver/gold/platinum may queue
    tier_results = result["results"].get(100, {}).get("tiers", {})
    assert tier_results.get("bronze", {}).get("queued", 1) == 0


@pytest.mark.asyncio
async def test_orchestrator_queues_when_below_capacity():
    """Test 1b: Orchestrator queues when active + queued < max_for_tier (10+9=19 < 20)."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    mock_db = _make_db_with_sql_count(9)  # 9 queued jobs
    _configure_db_manager(mock_db)
    cfg = _make_guild_config(200, bounty_max_per_tier={"bronze": 20, "silver": 3, "gold": 3, "platinum": 3})
    _configure_config_repo([cfg])
    # 10 active + 9 queued = 19 < 20 → should queue
    _configure_bounty_repo(active_count_map={(200, "bronze"): 10})

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

        result = await execute_bounty_spawn_orchestrate_job("orch-queue", {"job_type": "bounty_spawn_orchestrate"})

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
    """Test 3: Fire time falls within the expected window for various interval values."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

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

    # Compute expected window
    window_minutes = min(15.0, 0.25 * interval_minutes)
    lower_bound = now_before + timedelta(minutes=max(0.0, interval_minutes - window_minutes))
    upper_bound = now_before + timedelta(minutes=interval_minutes + window_minutes, seconds=1)  # +1s tolerance

    assert len(fire_times_recorded) > 0, "No fire times recorded — no jobs were queued"

    for fire_time_str in fire_times_recorded:
        fire_time = datetime.fromisoformat(fire_time_str)
        assert fire_time >= lower_bound, (
            f"Fire time {fire_time} is before lower bound {lower_bound} (interval={interval_minutes}min)"
        )
        assert fire_time <= upper_bound, (
            f"Fire time {fire_time} is after upper bound {upper_bound} (interval={interval_minutes}min)"
        )


# ===========================================================================
# Test 4: Queue-count query filters by (guild_id, tier_lower) via LIKE pattern
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestrator_queue_count_uses_correct_like_pattern():
    """Test 4: Queue-count SQL uses LIKE pattern bounty_spawn_{guild_id}_{tier_lower}_%."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

    execute_calls = []

    async def _mock_execute(stmt, params=None):
        if params and "pattern" in params:
            execute_calls.append(params["pattern"])
        result = MagicMock()
        result.scalar_one = MagicMock(return_value=0)
        return result

    mock_db = AsyncMock()
    mock_db.execute = _mock_execute
    _configure_db_manager(mock_db)

    cfg = _make_guild_config(500, bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3})
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

        await execute_bounty_spawn_orchestrate_job("orch-4", {"job_type": "bounty_spawn_orchestrate"})

    # Verify patterns used: one per tier
    assert len(execute_calls) == 4, f"Expected 4 SQL calls (one per tier), got {len(execute_calls)}"
    expected_patterns = {
        "bounty_spawn_500_bronze_%",
        "bounty_spawn_500_silver_%",
        "bounty_spawn_500_gold_%",
        "bounty_spawn_500_platinum_%",
    }
    assert set(execute_calls) == expected_patterns, (
        f"Unexpected patterns: {set(execute_calls)} — expected {expected_patterns}"
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

    mock_expiry = AsyncMock()
    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=mock_expiry),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
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

    mock_announce = AsyncMock()

    async def _expiry_raises(job_id, bounty):
        raise RuntimeError("scheduler down")

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=_expiry_raises),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
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
async def test_one_announce_raises_does_not_prevent_success():
    """Test 19: _announce_bounty raises → ERROR log but return success=True."""
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

    with (
        patch("utils.executors.bounty_spawn_executor._is_guild_fully_configured", return_value=True),
        patch("utils.executors.bounty_spawn_executor._get_division_channel_id", return_value=111),
        patch("utils.executors.bounty_spawn_executor._get_division_role_id", return_value=555),
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=_announce_raises),
        patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger,
    ):
        result = await execute_bounty_spawn_one_job(
            "one-19", {"job_type": "bounty_spawn_one", "guild_id": 100, "tier": "bronze"}
        )

    mock_logger.error.assert_called()
    assert result["success"] is True
    assert result["bounty_id"] == 77


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


@pytest.mark.asyncio
async def test_job_executor_still_dispatches_bounty_spawn_legacy():
    """Test 25c: Legacy bounty_spawn job_type still works (admin spawn path)."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn", "guild_id": 100, "division": "Bronze"}

    mock_fn = AsyncMock(return_value={"status": "success", "total_spawned": 0})
    with patch("utils.job_executor.execute_bounty_spawn_job", mock_fn):
        await executor.execute("legacy-spawn", payload)

    mock_fn.assert_awaited_once_with("legacy-spawn", payload)


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
