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
 - Routes announcement to per-division bounty board channel
 - Uses BountyAnnouncementBuilder (rich embed) instead of basic embed
 - Uploads route map to image_channel_id when configured
 - Persists DiscordMessage record after successful announcement
 - Continues announcement even if map upload fails

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

# Stubs for new deferred imports in _announce_bounty.
# NOTE: discord_message_repository is safe to stub here as it lives in persist.repositories
# which is already a stub package.  We do NOT stub message_builders here because doing
# so at module level would pollute sys.modules and break tests that import the real
# message_builders package (e.g. test_time_announcement_router.py).  Instead, each
# _announce_bounty test that needs BountyAnnouncementBuilder stubs it in-test.
_MockDiscordMessageRepository = MagicMock()
_ensure_stub(
    "persist.repositories.discord_message_repository",
    DiscordMessageRepository=_MockDiscordMessageRepository,
)

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guild_config(
    guild_id: int,
    bronze_bounty_channel_id: int | None = 123456,
    silver_bounty_channel_id: int | None = None,
    gold_bounty_channel_id: int | None = None,
    platinum_bounty_channel_id: int | None = None,
    image_channel_id: int | None = None,
    bounty_hunter_role_id: int | None = None,
    bounty_max_per_tier: dict | None = None,
    bounty_expiry_minutes: int | None = 480,
    bounty_spawn_interval_minutes: int | None = 60,
    next_spawn_check_at=None,
    division_temperatures: dict | None = None,
) -> MagicMock:
    """Build a mock GuildConfig-like object with per-division channel IDs and bounty config."""
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.bronze_bounty_channel_id = bronze_bounty_channel_id
    cfg.silver_bounty_channel_id = silver_bounty_channel_id
    cfg.gold_bounty_channel_id = gold_bounty_channel_id
    cfg.platinum_bounty_channel_id = platinum_bounty_channel_id
    cfg.image_channel_id = image_channel_id
    cfg.bounty_hunter_role_id = bounty_hunter_role_id
    cfg.bounty_max_per_tier = bounty_max_per_tier or {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
    cfg.bounty_expiry_minutes = bounty_expiry_minutes
    cfg.bounty_spawn_interval_minutes = bounty_spawn_interval_minutes
    cfg.next_spawn_check_at = next_spawn_check_at
    cfg.division_temperatures = division_temperatures or {}
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
    criminal_ship: dict | None = None,
    checked: dict | None = None,
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
    b.criminal_ship = criminal_ship
    b.checked = checked
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


def _make_config_for_announce(
    guild_id: int = 100,
    bronze_channel: int | None = 111,
    silver_channel: int | None = 222,
    gold_channel: int | None = 333,
    image_channel: int | None = None,
    role_id: int | None = None,
) -> MagicMock:
    """Build a GuildConfig mock suitable for direct _announce_bounty tests."""
    return _make_guild_config(
        guild_id=guild_id,
        bronze_bounty_channel_id=bronze_channel,
        silver_bounty_channel_id=silver_channel,
        gold_bounty_channel_id=gold_channel,
        image_channel_id=image_channel,
        bounty_hunter_role_id=role_id,
    )


def _make_default_embed_payload() -> dict:
    """Build the embed payload structure returned by BountyAnnouncementBuilder."""
    return {
        "content": None,
        "embed": {
            "title": "Kato Vort",
            "color": 1752220,
            "fields": [],
            "thumbnail_url": None,
            "image_url": None,
            "footer_text": "Vossk",
        },
    }


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
    _configure_config_repo([_make_guild_config(100)])

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

    # 3 of 5 slots used — use max=5 per tier so slot is available.
    _configure_bounty_repo({(200, "silver"): [MagicMock()] * 3})
    bounty = _make_bounty(bounty_id=42, guild_id=200, division="silver")
    mock_svc = _configure_bounty_service(bounty)
    _configure_config_repo([_make_guild_config(200, bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5})])

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-partial",
            {"job_type": "bounty_spawn", "guild_id": 200, "division": "Silver", "temperature": 5.0},
        )

    mock_svc.spawn_bounty.assert_awaited_once_with(mock_db, 200, "silver", expiry_minutes=480)
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
    _configure_config_repo([_make_guild_config(300)])

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
    _configure_config_repo([_make_guild_config(400)])

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
    _configure_config_repo([_make_guild_config(500)])

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
# Tests: discord-gateway announcement — new per-division routing (SEG-07)
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_routes_to_bronze_channel():
    """Bronze division bounty → announcement POSTed to bronze_bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=10, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111222)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 999}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-bronze", bounty, config, mock_db)

        # Assert the announcement was posted to the bronze channel.
        post_calls = [c for c in mock_client.post.call_args_list if "messages" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/channels/111222/messages" in posted_url


@pytest.mark.asyncio
async def test_announce_routes_to_silver_channel():
    """Silver division bounty → announcement POSTed to silver_bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=20, guild_id=100, division="silver")
    config = _make_config_for_announce(guild_id=100, bronze_channel=None, silver_channel=222333)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 888}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-silver", bounty, config, mock_db)

        post_calls = [c for c in mock_client.post.call_args_list if "messages" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/channels/222333/messages" in posted_url


@pytest.mark.asyncio
async def test_announce_routes_to_gold_channel():
    """Gold division bounty → announcement POSTed to gold_bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=30, guild_id=100, division="gold")
    config = _make_config_for_announce(guild_id=100, bronze_channel=None, silver_channel=None, gold_channel=333444)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 777}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-gold", bounty, config, mock_db)

        post_calls = [c for c in mock_client.post.call_args_list if "messages" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/channels/333444/messages" in posted_url


@pytest.mark.asyncio
async def test_announce_skips_when_channel_not_configured():
    """When the division channel is None, a warning is logged and no HTTP POST is made."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=40, guild_id=100, division="bronze")
    # All per-division channels are None.
    config = _make_config_for_announce(guild_id=100, bronze_channel=None, silver_channel=None, gold_channel=None)
    mock_db = AsyncMock()

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        await _announce_bounty("job-skip", bounty, config, mock_db)
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_announce_uses_rich_embed_builder():
    """The POST body uses the BountyAnnouncementBuilder format (title=criminal_name, color=faction color)."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(
        bounty_id=50, guild_id=100, division="bronze", criminal_name="Vossk Raider", criminal_faction="Vossk"
    )
    config = _make_config_for_announce(guild_id=100, bronze_channel=555666)
    mock_db = AsyncMock()

    embed_payload = {
        "content": None,
        "embed": {
            "title": "Vossk Raider",
            "color": 1752220,  # Vossk faction color
            "fields": [],
            "thumbnail_url": None,
            "image_url": None,
            "footer_text": "Vossk",
        },
    }
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 12345}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-rich", bounty, config, mock_db)

        # Verify builder was called (BountyAnnouncementBuilder instantiated and used).
        mock_builder.build_payload.assert_called_once()
        build_data = mock_builder.build_payload.call_args.args[0]
        assert build_data["criminal_name"] == "Vossk Raider"
        assert build_data["criminal_faction"] == "Vossk"

        # Verify the POSTed body uses embed format (not the old basic format).
        post_calls = [c for c in mock_client.post.call_args_list if "messages" in str(c)]
        assert len(post_calls) >= 1
        posted_body = post_calls[-1].kwargs.get("json") or (
            post_calls[-1].args[1] if len(post_calls[-1].args) > 1 else {}
        )
        assert "content" in posted_body
        assert posted_body["content"]["title"] == "Vossk Raider"
        assert posted_body["content"]["color"] == 1752220


@pytest.mark.asyncio
async def test_announce_uploads_route_map():
    """When image_channel_id is set, a PNG is fetched from the map endpoint and uploaded."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=60, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111, image_channel=999888)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    map_response = MagicMock()
    map_response.content = b"\x89PNG\r\n"
    map_response.raise_for_status = MagicMock()

    upload_response = MagicMock()
    upload_response.raise_for_status = MagicMock()
    upload_response.json = MagicMock(return_value={"data": {"attachment_url": "https://cdn.example.com/map.png"}})

    msg_response = MagicMock()
    msg_response.raise_for_status = MagicMock()
    msg_response.json = MagicMock(return_value={"data": {"message_id": 777}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=map_response)
        # First POST is upload, second POST is the channel message.
        mock_client.post = AsyncMock(side_effect=[upload_response, msg_response])
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_bounty("job-map", bounty, config, mock_db)

        # Verify a GET was made to the map endpoint.
        mock_client.get.assert_awaited_once()
        get_url = (
            mock_client.get.call_args.args[0] if mock_client.get.call_args.args else str(mock_client.get.call_args)
        )
        assert f"/bounties/{bounty.id}/map" in get_url

        # Verify upload POST was made to image channel.
        upload_call = mock_client.post.call_args_list[0]
        upload_url = upload_call.args[0] if upload_call.args else upload_call.kwargs.get("url", "")
        assert f"/channels/{config.image_channel_id}/upload" in upload_url

        # Verify the embed builder received the CDN URL.
        build_data = mock_builder.build_payload.call_args.args[0]
        assert build_data.get("route_map_url") == "https://cdn.example.com/map.png"


@pytest.mark.asyncio
async def test_announce_skips_map_when_no_image_channel():
    """When image_channel_id is None, no map fetch or upload is attempted."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=70, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111, image_channel=None)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 321}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-no-img", bounty, config, mock_db)

        # No GET to the map endpoint.
        mock_client.get.assert_not_awaited()

        # build_payload should be called with route_map_url=None.
        build_data = mock_builder.build_payload.call_args.args[0]
        assert build_data.get("route_map_url") is None


@pytest.mark.asyncio
async def test_announce_persists_discord_message():
    """After successful POST, a DiscordMessage is created with correct fields."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=80, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=444555)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    discord_message_id = 888777666
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": discord_message_id}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-persist", bounty, config, mock_db)

        # Verify create_or_update was called with correct fields.
        mock_msg_repo.create_or_update.assert_awaited_once()
        call_kwargs = mock_msg_repo.create_or_update.call_args
        raw = call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("raw", {})
        assert raw["guild_id"] == bounty.guild_id
        assert raw["channel_id"] == 444555
        assert raw["message_id"] == discord_message_id
        assert raw["message_type"] == "bounty_announcement"
        assert raw["reference_id"] == bounty.id


@pytest.mark.asyncio
async def test_announce_continues_if_map_upload_fails():
    """If the map upload fails, the announcement is still posted (without image)."""
    import httpx
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=90, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111, image_channel=999)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    map_response = MagicMock()
    map_response.content = b"\x89PNG"
    map_response.raise_for_status = MagicMock()

    msg_response = MagicMock()
    msg_response.raise_for_status = MagicMock()
    msg_response.json = MagicMock(return_value={"data": {"message_id": 555}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=map_response)
        # First POST (upload) fails; second POST (channel message) succeeds.
        mock_client.post = AsyncMock(side_effect=[httpx.ConnectError("upload failed"), msg_response])
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise — the announcement should continue without the image.
        await _announce_bounty("job-fail-map", bounty, config, mock_db)

        # The channel message POST should still have been called.
        assert mock_client.post.await_count == 2
        msg_call = mock_client.post.call_args_list[1]
        msg_url = msg_call.args[0] if msg_call.args else msg_call.kwargs.get("url", "")
        assert "/channels/111/messages" in msg_url

        # route_map_url should be None when upload failed.
        build_data = mock_builder.build_payload.call_args.args[0]
        assert build_data.get("route_map_url") is None


# ===========================================================================
# Tests: execute_bounty_spawn_job integration — new announce signature
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_called_after_spawn():
    """_announce_bounty is called with (job_id, bounty, config, db) after spawn."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=55, guild_id=600, division="gold")
    _configure_bounty_repo({})
    _configure_bounty_service(bounty)
    # Single-guild mode: fetch config from config_repo.
    cfg = _make_guild_config(600)
    _configure_config_repo([cfg])

    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
    ):
        await execute_bounty_spawn_job(
            "job-announce",
            {"job_type": "bounty_spawn", "guild_id": 600, "division": "Gold"},
        )

    # _announce_bounty is called with (job_id, bounty, config, db)
    mock_announce.assert_awaited_once()
    call_args = mock_announce.call_args
    assert call_args.args[0] == "job-announce"
    assert call_args.args[1] == bounty


@pytest.mark.asyncio
async def test_announce_called_with_config_from_repo():
    """execute_bounty_spawn_job passes the actual GuildConfig (with division channel IDs) to _announce_bounty."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=66, guild_id=700, division="bronze")
    # Only one division slot open (bronze), rest full to ensure one spawn per guild.
    _configure_bounty_repo(
        {(700, "silver"): [MagicMock()] * 5, (700, "gold"): [MagicMock()] * 5, (700, "platinum"): [MagicMock()] * 5}
    )
    _configure_bounty_service(bounty)
    # Bulk mode uses config objects from ConfigRepository with per-division channels.
    cfg = _make_guild_config(700, bronze_bounty_channel_id=999111)
    _configure_config_repo([cfg])

    mock_announce = AsyncMock()

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=mock_announce),
    ):
        await execute_bounty_spawn_job(
            "job-announce-channel",
            {"job_type": "bounty_spawn"},
        )

    # Should be called exactly once (only bronze spawned) with the full config object.
    mock_announce.assert_awaited_once()
    call_args = mock_announce.call_args
    passed_config = call_args.args[2]
    assert passed_config.bronze_bounty_channel_id == 999111


@pytest.mark.asyncio
async def test_announce_http_error_is_non_fatal():
    """An HTTP error during _announce_bounty does not propagate to the caller."""
    import httpx
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=2)
    config = _make_config_for_announce(guild_id=100, bronze_channel=12345)
    mock_db = AsyncMock()

    embed_payload = _make_default_embed_payload()
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise even with a channel configured.
        await _announce_bounty("parent-job", bounty, config, mock_db)


@pytest.mark.asyncio
async def test_announce_skipped_when_no_channel_id():
    """_announce_bounty skips HTTP when division channel is None."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=3, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=None)
    mock_db = AsyncMock()

    with patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls:
        await _announce_bounty("parent-job", bounty, config, mock_db)
        # No HTTP call should be made.
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_announce_posts_to_correct_channel_endpoint():
    """_announce_bounty POSTs to /channels/{channel_id}/messages using the rich embed builder."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=4, guild_id=101, division="bronze", criminal_name="Pirate X", reward=75000)
    channel_id = 987654
    config = _make_config_for_announce(guild_id=101, bronze_channel=channel_id)
    mock_db = AsyncMock()

    embed_payload = {
        "content": None,
        "embed": {
            "title": "Pirate X",
            "color": 10181046,  # default color
            "fields": [],
            "thumbnail_url": None,
            "image_url": None,
            "footer_text": "Unknown",
        },
    }
    mock_builder = MagicMock()
    mock_builder.build_payload = MagicMock(return_value=embed_payload)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"message_id": 11111}})

    with (
        patch(
            "message_builders.builders.bounty_announcement.BountyAnnouncementBuilder",
            return_value=mock_builder,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_bounty("parent-job", bounty, config, mock_db)

        # Verify the correct URL was used.
        post_calls = [c for c in mock_client.post.call_args_list if "messages" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url")
        assert f"/channels/{channel_id}/messages" in posted_url

        # Verify the body uses the new rich embed builder format (criminal_name as title).
        posted_body = post_calls[-1].kwargs.get("json") or (
            post_calls[-1].args[1] if len(post_calls[-1].args) > 1 else {}
        )
        assert "content" in posted_body
        assert posted_body["content"]["title"] == "Pirate X"
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

    async def _spawn(db, gid, div, expiry_minutes=None):
        return spawn_side_effects.get((gid, div))

    mock_svc.spawn_bounty = _spawn
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)

    _configure_bounty_repo({})  # no active bounties anywhere
    _configure_config_repo(
        [
            _make_guild_config(10, bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5}),
            _make_guild_config(20, bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5}),
        ]
    )

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
    _configure_config_repo([_make_guild_config(700)])

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
    _configure_config_repo([_make_guild_config(800)])

    result = await execute_bounty_spawn_job(
        "job-temp1",
        {"job_type": "bounty_spawn", "guild_id": 800, "division": "Bronze", "temperature": 1.0},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_temperature_passed_to_get_max_bounties():
    """Per-guild division_temperatures are forwarded to TemperatureService.get_max_bounties."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_get_max = MagicMock(return_value=5)
    sys.modules["services.temperature_service"].TemperatureService.get_max_bounties = mock_get_max

    _configure_bounty_repo({})
    _configure_bounty_service(None)
    # Use per-guild division_temperatures with gold=3.5
    _configure_config_repo([_make_guild_config(900, division_temperatures={"bronze": 1.0, "silver": 1.0, "gold": 3.5})])

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


# ===========================================================================
# Tests: next_spawn_check_at interval gating
# ===========================================================================


@pytest.mark.asyncio
async def test_guild_skipped_when_next_spawn_check_at_in_future():
    """Guild is skipped when next_spawn_check_at is in the future."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # next_spawn_check_at is 10 minutes in the future
    future_ts = datetime.now(UTC) + timedelta(minutes=10)
    _configure_config_repo([_make_guild_config(500, next_spawn_check_at=future_ts)])
    mock_svc = _configure_bounty_service(None)
    _configure_bounty_repo({})

    result = await execute_bounty_spawn_job(
        "job-gate",
        {"job_type": "bounty_spawn", "guild_id": 500, "division": "Bronze"},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    # Guild was seen but skipped (no spawns)
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_guild_processed_when_next_spawn_check_at_in_past():
    """Guild is processed when next_spawn_check_at is in the past."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # next_spawn_check_at is 10 minutes ago
    past_ts = datetime.now(UTC) - timedelta(minutes=10)
    bounty = _make_bounty(bounty_id=77, guild_id=600)
    _configure_config_repo(
        [
            _make_guild_config(
                600,
                next_spawn_check_at=past_ts,
                bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5},
            )
        ]
    )
    _configure_bounty_service(bounty)
    _configure_bounty_repo({})

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-gate-past",
            {"job_type": "bounty_spawn", "guild_id": 600, "division": "Bronze"},
        )

    # Should have processed and spawned
    assert result["total_spawned"] == 1


@pytest.mark.asyncio
async def test_expiry_minutes_passed_to_spawn_bounty():
    """bounty_expiry_minutes from guild config is passed to spawn_bounty."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=88, guild_id=700, division="gold")
    # Guild with custom expiry of 120 minutes
    _configure_config_repo(
        [
            _make_guild_config(
                700,
                bounty_expiry_minutes=120,
                bounty_max_per_tier={"bronze": 5, "silver": 5, "gold": 5},
            )
        ]
    )
    mock_svc = _configure_bounty_service(bounty)
    _configure_bounty_repo({})

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        await execute_bounty_spawn_job(
            "job-expiry",
            {"job_type": "bounty_spawn", "guild_id": 700, "division": "Gold"},
        )

    mock_svc.spawn_bounty.assert_awaited_once_with(mock_db, 700, "gold", expiry_minutes=120)


@pytest.mark.asyncio
async def test_per_guild_max_bounties_used():
    """Per-guild bounty_max_per_tier is used instead of global temperature-only max."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=10)

    # Guild has max=2 for bronze, active count=2 → should skip
    _configure_config_repo(
        [
            _make_guild_config(
                800,
                bounty_max_per_tier={"bronze": 2, "silver": 5, "gold": 5},
            )
        ]
    )
    mock_svc = _configure_bounty_service(None)
    _configure_bounty_repo({(800, "bronze"): [MagicMock(), MagicMock()]})

    result = await execute_bounty_spawn_job(
        "job-perguild",
        {"job_type": "bounty_spawn", "guild_id": 800, "division": "Bronze"},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["total_spawned"] == 0
