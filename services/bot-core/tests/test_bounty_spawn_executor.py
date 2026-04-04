"""
Unit tests for utils.executors.bounty_spawn_executor.

Tests verify:
 - Skips spawn when all slots are full (active count >= max_bounties)
 - Spawns a bounty when a slot is open
 - Handles spawn_bounty returning None (no criminal / route failure)
 - Schedules expiry job after successful spawn
 - Announces to discord-gateway after successful spawn
 - HTTP errors in expiry scheduling are non-fatal
 - HTTP errors in gateway announcement are non-fatal
 - Bulk mode (no guild_id) processes all configured guilds
 - No-guilds configured returns early with zero count
 - Single-guild mode respects the guild_id payload field
 - Specific division mode respects the division payload field
 - job_executor.py dispatches bounty_spawn job_type
 - bounty_channel_id=None skips announcement with warning
 - bounty_channel_id set causes POST to /channels/{id}/messages

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because bounty_spawn_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.bounty_service.BountyService"
  - "persist.repositories.bounty_repository.BountyRepository"
  - "persist.repositories.config_repository.ConfigRepository"
  - "services.temperature_service.TemperatureService"
We pre-register stub modules in sys.modules so deferred imports inside
execute_bounty_spawn_job resolve without requiring real ORM code.
"""

import os as _os
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger before importing any source modules.
# conftest.py handles this at collection time; guard is here for standalone runs.
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
# Pre-register stub modules so deferred imports in bounty_spawn_executor work
# without requiring a live database or installed ORM extras.
# ---------------------------------------------------------------------------


def _ensure_stub(module_path: str, **attrs) -> types.ModuleType:
    """Create and register a stub module if not already present."""
    if module_path not in sys.modules:
        mod = types.ModuleType(module_path)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_path] = mod
    return sys.modules[module_path]


# Stub for persist.database.manager — only db_manager attribute needed.
_mock_db_mgr_instance = MagicMock()
_ensure_stub("persist.database.manager", db_manager=_mock_db_mgr_instance)

# Stubs for repositories and services.
_MockBountyRepository = MagicMock()
_ensure_stub("persist.repositories.bounty_repository", BountyRepository=_MockBountyRepository)

_MockConfigRepository = MagicMock()
_ensure_stub("persist.repositories.config_repository", ConfigRepository=_MockConfigRepository)

_MockBountyService = MagicMock()
_ensure_stub("services.bounty_service", BountyService=_MockBountyService)

_MockTemperatureService = MagicMock()
_ensure_stub("services.temperature_service", TemperatureService=_MockTemperatureService)

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guild_config(guild_id: int, bounty_channel_id: int | None = 123456) -> MagicMock:
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.bounty_channel_id = bounty_channel_id
    return cfg


def _make_bounty(
    bounty_id: int = 1,
    guild_id: int = 100,
    division: str = "bronze",
    criminal_name: str = "Kato Vort",
    criminal_faction: str = "Vossk",
    reward: int = 50000,
    tech_level: int = 5,
    route: list | None = None,
    end_time: datetime | None = None,
) -> MagicMock:
    """Build a mock Bounty-like object with standard attributes."""
    b = MagicMock()
    b.id = bounty_id
    b.guild_id = guild_id
    b.division = division
    b.criminal_name = criminal_name
    b.criminal_faction = criminal_faction
    b.reward = reward
    b.tech_level = tech_level
    b.route = route if route is not None else ["SysA", "SysB", "SysC"]
    b.end_time = end_time if end_time is not None else datetime.now(UTC) + timedelta(days=3)
    return b


def _mock_session_ctx(session: AsyncMock) -> MagicMock:
    """Return an async context manager that yields *session*."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _configure_db_manager(mock_db: AsyncMock) -> None:
    """Configure the stub db_manager to yield *mock_db* on get_session()."""
    mgr = sys.modules["persist.database.manager"].db_manager
    mgr.get_session = MagicMock(return_value=_mock_session_ctx(mock_db))


def _configure_temperature_service(max_bounties: int = 5) -> None:
    """Set TemperatureService.get_max_bounties to return *max_bounties*."""
    sys.modules["services.temperature_service"].TemperatureService.get_max_bounties = MagicMock(
        return_value=max_bounties
    )


def _configure_bounty_repo(active_lists: dict) -> AsyncMock:
    """Configure the BountyRepository mock.

    *active_lists* maps ``(guild_id, division)`` → ``list[MagicMock bounty]``.
    """
    mock_repo = AsyncMock()

    async def _get_active(db, guild_id, division):
        return active_lists.get((guild_id, division), [])

    mock_repo.get_active_by_guild_and_division = _get_active
    sys.modules["persist.repositories.bounty_repository"].BountyRepository = MagicMock(return_value=mock_repo)
    return mock_repo


def _configure_bounty_service(spawn_return) -> AsyncMock:
    """Configure BountyService.spawn_bounty to return *spawn_return*."""
    mock_svc = AsyncMock()
    mock_svc.spawn_bounty = AsyncMock(return_value=spawn_return)
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)
    return mock_svc


def _configure_config_repo(guild_configs: list) -> AsyncMock:
    """Configure ConfigRepository.list_all to return *guild_configs*."""
    mock_repo = AsyncMock()
    mock_repo.list_all = AsyncMock(return_value=guild_configs)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_repo)
    return mock_repo


# ===========================================================================
# Tests: capacity enforcement (max_bounties gate)
# ===========================================================================


@pytest.mark.asyncio
async def test_full_slots_skips_spawn():
    """When active bounties == max_bounties, spawn_bounty is NOT called."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # Simulate 5 active bounties (slots full) for division bronze.
    _configure_bounty_repo({(100, "bronze"): [MagicMock()] * 5})
    mock_svc = _configure_bounty_service(None)

    result = await execute_bounty_spawn_job(
        "job-full",
        {"job_type": "bounty_spawn", "guild_id": 100, "division": "Bronze", "temperature": 5.0},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["status"] == "success"
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_partial_slots_spawns_one():
    """When active bounties < max_bounties, spawn_bounty IS called once."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # 3 of 5 slots used.
    _configure_bounty_repo({(200, "silver"): [MagicMock()] * 3})
    bounty = _make_bounty(bounty_id=42, guild_id=200, division="silver")
    mock_svc = _configure_bounty_service(bounty)

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-partial",
            {"job_type": "bounty_spawn", "guild_id": 200, "division": "Silver", "temperature": 5.0},
        )

    mock_svc.spawn_bounty.assert_awaited_once_with(mock_db, 200, "silver")
    assert result["status"] == "success"
    assert result["total_spawned"] == 1


@pytest.mark.asyncio
async def test_empty_slots_spawns_bounty():
    """When there are no active bounties at all, one is spawned."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=3)

    _configure_bounty_repo({})  # empty — no active bounties
    bounty = _make_bounty(bounty_id=7, guild_id=300, division="gold")
    _configure_bounty_service(bounty)

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-empty",
            {"job_type": "bounty_spawn", "guild_id": 300, "division": "Gold", "temperature": 3.0},
        )

    assert result["total_spawned"] == 1
    assert result["results"][300]["divisions"]["Gold"]["bounty_id"] == 7


# ===========================================================================
# Tests: spawn_bounty returns None
# ===========================================================================


@pytest.mark.asyncio
async def test_spawn_returns_none_does_not_count():
    """If spawn_bounty returns None, total_spawned stays 0 and no downstream calls happen."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    _configure_bounty_repo({(400, "bronze"): []})  # slot available
    mock_svc = _configure_bounty_service(None)  # spawn fails

    mock_expiry = AsyncMock()
    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=mock_expiry),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
    ):
        result = await execute_bounty_spawn_job(
            "job-none",
            {"job_type": "bounty_spawn", "guild_id": 400, "division": "Bronze"},
        )

    mock_svc.spawn_bounty.assert_awaited_once()
    mock_expiry.assert_not_awaited()
    mock_announce.assert_not_awaited()
    assert result["total_spawned"] == 0


# ===========================================================================
# Tests: expiry scheduling
# ===========================================================================


@pytest.mark.asyncio
async def test_expiry_scheduled_after_spawn():
    """_schedule_expiry_job is called with the job_id and spawned bounty."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=99, guild_id=500, division="bronze")
    _configure_bounty_repo({})
    _configure_bounty_service(bounty)

    mock_expiry = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=mock_expiry),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        await execute_bounty_spawn_job(
            "job-expiry",
            {"job_type": "bounty_spawn", "guild_id": 500, "division": "Bronze"},
        )

    mock_expiry.assert_awaited_once_with("job-expiry", bounty)


@pytest.mark.asyncio
async def test_expiry_http_error_is_non_fatal():
    """An HTTP error in _schedule_expiry_job does not propagate to the caller."""
    import httpx
    from utils.executors.bounty_spawn_executor import _schedule_expiry_job

    bounty = _make_bounty(bounty_id=1)

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise.
        await _schedule_expiry_job("parent-job", bounty)


@pytest.mark.asyncio
async def test_expiry_skipped_when_no_end_time():
    """When bounty.end_time is None, _schedule_expiry_job logs a warning and skips HTTP."""
    from utils.executors.bounty_spawn_executor import _schedule_expiry_job

    # Build a bounty-like object with end_time explicitly set to None.
    bounty = MagicMock()
    bounty.id = 1
    bounty.end_time = None  # explicitly None — not the _make_bounty default

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        await _schedule_expiry_job("parent-job", bounty)
        mock_cls.assert_not_called()


# ===========================================================================
# Tests: discord-gateway announcement
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_called_after_spawn():
    """_announce_bounty is called with the job_id, bounty, and bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=55, guild_id=600, division="gold")
    _configure_bounty_repo({})
    _configure_bounty_service(bounty)
    # Single-guild mode creates a _SingleGuildConfig with bounty_channel_id=None
    # so _announce_bounty will be called with None as the channel_id.
    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
    ):
        await execute_bounty_spawn_job(
            "job-announce",
            {"job_type": "bounty_spawn", "guild_id": 600, "division": "Gold"},
        )

    # _announce_bounty is called with (job_id, bounty, bounty_channel_id)
    mock_announce.assert_awaited_once_with("job-announce", bounty, None)


@pytest.mark.asyncio
async def test_announce_called_with_channel_id_from_config():
    """_announce_bounty is called with bounty_channel_id from the guild config."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=66, guild_id=700, division="bronze")
    # Only one division slot open (bronze), rest full to ensure one spawn per guild.
    _configure_bounty_repo({(700, "silver"): [MagicMock()] * 5, (700, "gold"): [MagicMock()] * 5})
    _configure_bounty_service(bounty)
    # Bulk mode uses config objects from ConfigRepository which have bounty_channel_id.
    _configure_config_repo([_make_guild_config(700, bounty_channel_id=999111)])

    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
    ):
        await execute_bounty_spawn_job(
            "job-announce-channel",
            {"job_type": "bounty_spawn"},
        )

    # Should be called exactly once (only bronze spawned) with the channel_id from config.
    mock_announce.assert_awaited_once_with("job-announce-channel", bounty, 999111)


@pytest.mark.asyncio
async def test_announce_http_error_is_non_fatal():
    """An HTTP error in _announce_bounty does not propagate to the caller."""
    import httpx
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=2)

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise even with a channel_id set.
        await _announce_bounty("parent-job", bounty, 12345)


@pytest.mark.asyncio
async def test_announce_skipped_when_no_channel_id():
    """_announce_bounty skips HTTP when bounty_channel_id is None."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=3, guild_id=100)

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        await _announce_bounty("parent-job", bounty, None)
        # No HTTP call should be made.
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_announce_posts_to_correct_channel_endpoint():
    """_announce_bounty POSTs to /channels/{channel_id}/messages with embed payload."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=4, guild_id=101, criminal_name="Pirate X", reward=75000)
    channel_id = 987654

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_bounty("parent-job", bounty, channel_id)

        # Verify the correct URL was used.
        call_args = mock_client.post.call_args
        posted_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert f"/channels/{channel_id}/messages" in posted_url

        # Verify the body has the expected EmbedPayload structure.
        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})
        assert "content" in posted_body
        assert posted_body["content"]["title"] == "🎯 New Bounty!"
        assert posted_body["content"]["footer_text"] == "Use /check to hunt this bounty!"
        assert "message_type" in posted_body


# ===========================================================================
# Tests: bulk mode (no guild_id in payload)
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_mode_processes_all_guilds():
    """No guild_id → all configured guilds are processed."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # Two guilds, one slot free in each (bronze division only).
    bounty1 = _make_bounty(bounty_id=10, guild_id=10, division="bronze")
    bounty2 = _make_bounty(bounty_id=20, guild_id=20, division="bronze")

    spawn_side_effects = {
        (10, "bronze"): bounty1,
        (20, "bronze"): bounty2,
        (10, "silver"): None,
        (20, "silver"): None,
        (10, "gold"): None,
        (20, "gold"): None,
    }

    mock_svc = AsyncMock()

    async def _spawn(db, gid, div):
        return spawn_side_effects.get((gid, div))

    mock_svc.spawn_bounty = _spawn
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)

    _configure_bounty_repo({})  # no active bounties anywhere
    _configure_config_repo([_make_guild_config(10), _make_guild_config(20)])

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job("job-bulk", {"job_type": "bounty_spawn"})

    assert result["status"] == "success"
    assert result["guilds_processed"] == 2
    # Each guild spawned 1 (bronze only); silver and gold returned None.
    assert result["total_spawned"] == 2


@pytest.mark.asyncio
async def test_bulk_mode_no_guilds_returns_zero():
    """Bulk mode with no configured guilds returns guilds_processed=0."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)
    _configure_config_repo([])

    result = await execute_bounty_spawn_job("job-no-guilds", {"job_type": "bounty_spawn"})

    assert result["status"] == "success"
    assert result["guilds_processed"] == 0
    assert result["total_spawned"] == 0


# ===========================================================================
# Tests: single-division mode
# ===========================================================================


@pytest.mark.asyncio
async def test_specific_division_only_checks_that_division():
    """When division is specified, only that division is processed."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)
    _configure_bounty_repo({})

    bounty = _make_bounty(bounty_id=77, guild_id=700, division="silver")
    mock_svc = _configure_bounty_service(bounty)

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-div",
            {"job_type": "bounty_spawn", "guild_id": 700, "division": "Silver"},
        )

    # spawn_bounty should be called once (silver only).
    assert mock_svc.spawn_bounty.await_count == 1
    called_args = mock_svc.spawn_bounty.call_args
    assert called_args.args[2] == "silver"  # division normalised to lowercase
    assert result["total_spawned"] == 1


# ===========================================================================
# Tests: TemperatureService integration
# ===========================================================================


@pytest.mark.asyncio
async def test_temperature_1_allows_only_1_slot():
    """At temperature=1, max_bounties=1; 1 active bounty fills the slot."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=1)

    # 1 active bounty == max_bounties(1) → full.
    _configure_bounty_repo({(800, "bronze"): [MagicMock()]})
    mock_svc = _configure_bounty_service(None)

    result = await execute_bounty_spawn_job(
        "job-temp1",
        {"job_type": "bounty_spawn", "guild_id": 800, "division": "Bronze", "temperature": 1.0},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_temperature_passed_to_get_max_bounties():
    """The temperature from the payload is forwarded to TemperatureService.get_max_bounties."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_get_max = MagicMock(return_value=5)
    sys.modules["services.temperature_service"].TemperatureService.get_max_bounties = mock_get_max

    _configure_bounty_repo({})
    _configure_bounty_service(None)

    await execute_bounty_spawn_job(
        "job-tempcheck",
        {"job_type": "bounty_spawn", "guild_id": 900, "division": "Gold", "temperature": 3.5},
    )

    mock_get_max.assert_called_once_with(3.5)


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_job_executor_dispatches_bounty_spawn():
    """JobExecutor.execute routes bounty_spawn payload to execute_bounty_spawn_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn", "guild_id": 123, "division": "Bronze"}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_bounty_spawn_job", mock_fn):
        await executor.execute("job-dispatch-bounty", payload)

    mock_fn.assert_awaited_once_with("job-dispatch-bounty", payload)


@pytest.mark.asyncio
async def test_job_executor_does_not_dispatch_bounty_spawn_for_shop_refresh():
    """Shop refresh payloads do NOT trigger execute_bounty_spawn_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "shop_refresh", "guild_id": 555, "tier": "Gold"}

    mock_bounty_fn = AsyncMock()
    mock_shop_fn = AsyncMock(return_value={"status": "success"})

    with (
        patch("utils.job_executor.execute_bounty_spawn_job", mock_bounty_fn),
        patch("utils.job_executor.execute_shop_refresh_job", mock_shop_fn),
    ):
        await executor.execute("job-shop", payload)

    mock_bounty_fn.assert_not_awaited()
    mock_shop_fn.assert_awaited_once()
