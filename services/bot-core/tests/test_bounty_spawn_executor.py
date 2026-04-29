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
# Stub sqlalchemy and related ORM packages so that utils/__init__.py's
# auto-import of auto_seeder.py (which does `from sqlalchemy import ...`)
# doesn't fail in environments where sqlalchemy is not installed.
# ---------------------------------------------------------------------------
if "sqlalchemy" not in sys.modules:
    _mock_sa = types.ModuleType("sqlalchemy")
    _mock_sa.func = MagicMock()
    _mock_sa.select = MagicMock()
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

# Stub for CriminalRepository — used in _announce_bounty to look up criminal_icon.
# Default: returns a mock criminal with icon=None (no icon configured)
_mock_criminal_repo_instance = AsyncMock()
_mock_criminal_repo_instance.get_by_name = AsyncMock(return_value=None)
_MockCriminalRepository = MagicMock(return_value=_mock_criminal_repo_instance)
_ensure_stub(
    "persist.repositories.criminal_repository",
    CriminalRepository=_MockCriminalRepository,
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
    silver_bounty_channel_id: int | None = 234567,
    gold_bounty_channel_id: int | None = 345678,
    platinum_bounty_channel_id: int | None = 456789,
    image_channel_id: int | None = None,
    bounty_hunter_role_id: int | None = 567890,
    bounty_max_per_tier: dict | None = None,
    bounty_expiry_minutes: int | None = 480,
    bounty_spawn_interval_minutes: int | None = 60,
    next_spawn_check_at=None,
    division_temperatures: dict | None = None,
) -> MagicMock:
    """Build a mock GuildConfig-like object with per-division channel IDs and bounty config.

    Defaults to a fully-configured guild (all 5 eligibility fields set) so that
    tests exercising the spawn path work correctly with the eligibility guard.
    Tests that need to verify the skip behaviour should explicitly pass None for
    the desired fields.
    """
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


# ===========================================================================
# Tests: capacity enforcement (max_bounties gate)
# ===========================================================================


def _make_announce_request(bounty, *, route_map_url=None, bounty_hunter_role_id=None, captured=False):
    """Return a minimal BountyAnnouncementRequest-shaped dict (A.48 wire shape)."""
    name = getattr(bounty, "criminal_name", None) or "Unknown"
    faction = getattr(bounty, "criminal_faction", None)
    title = f"✅ {name} — CAPTURED" if captured else name
    return {
        "text_content": (f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id else None),
        "loadout_response": {"subject_kind": "criminal", "subject_name": name},
        "metadata": {
            "title": title,
            "color": 0,
            "footer_text": faction,
            "image_url": route_map_url,
            "prefix_fields": [],
            "suffix_fields": [],
        },
    }


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
    assert result["total_spawned"] == 0


# ===========================================================================
# Tests: Fix 3 — criminal_icon passed to BountyAnnouncementBuilder
# ===========================================================================


def _configure_criminal_repo(icon_url: str | None = None, return_none: bool = False) -> AsyncMock:
    """Configure CriminalRepository to return a criminal with the given icon URL."""
    mock_repo = AsyncMock()
    if return_none:
        mock_repo.get_by_name = AsyncMock(return_value=None)
    else:
        mock_criminal = MagicMock()
        mock_criminal.icon = icon_url
        mock_repo.get_by_name = AsyncMock(return_value=mock_criminal)
    sys.modules["persist.repositories.criminal_repository"].CriminalRepository = MagicMock(return_value=mock_repo)
    return mock_repo


@pytest.mark.asyncio
async def test_announce_passes_criminal_icon_to_builder():
    """_announce_bounty looks up the criminal's icon URL and passes it to BountyAnnouncementBuilder."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=200, guild_id=100, division="bronze", criminal_name="Bartholomeu Drew")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111222)
    mock_db = AsyncMock()

    criminal_icon_url = "https://i.postimg.cc/fT1cpwPc/bartholomeu-drew.png"
    _configure_criminal_repo(icon_url=criminal_icon_url)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 55555}})

    # A.48 wire shape: assert criminal_icon is passed as a kwarg.
    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_bounty("job-icon", bounty, config, mock_db)

    # A.48: criminal_icon is passed as a kwarg to build_bounty_announcement_request.
    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("criminal_icon") == criminal_icon_url, (
        f"Expected criminal_icon={criminal_icon_url!r} but got {mock_helper.call_args.kwargs.get('criminal_icon')!r}"
    )


@pytest.mark.asyncio
async def test_announce_passes_none_icon_when_criminal_not_found():
    """_announce_bounty passes criminal_icon=None when criminal DB lookup returns nothing (non-fatal)."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=201, guild_id=100, division="bronze", criminal_name="Unknown Villain")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111222)
    mock_db = AsyncMock()

    # Criminal not found in DB
    _configure_criminal_repo(return_none=True)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 66666}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise — criminal not found is non-fatal
        await _announce_bounty("job-no-icon", bounty, config, mock_db)

    # A.48: criminal_icon=None is passed gracefully.
    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("criminal_icon") is None


@pytest.mark.asyncio
async def test_announce_passes_none_icon_when_criminal_has_no_icon():
    """_announce_bounty passes criminal_icon=None when criminal exists but has icon=None."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=202, guild_id=100, division="bronze", criminal_name="No Icon Villain")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111222)
    mock_db = AsyncMock()

    # Criminal found but icon is None
    _configure_criminal_repo(icon_url=None)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 77777}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_bounty("job-no-icon-2", bounty, config, mock_db)

    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("criminal_icon") is None


@pytest.mark.asyncio
async def test_announce_icon_lookup_failure_is_non_fatal():
    """If CriminalRepository raises during icon lookup, _announce_bounty continues with icon=None."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=203, guild_id=100, division="bronze", criminal_name="DB Error Criminal")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111222)
    mock_db = AsyncMock()

    # CriminalRepository raises an exception during lookup
    mock_failing_repo = AsyncMock()
    mock_failing_repo.get_by_name = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    sys.modules["persist.repositories.criminal_repository"].CriminalRepository = MagicMock(
        return_value=mock_failing_repo
    )

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 88888}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise — icon lookup failure is non-fatal
        await _announce_bounty("job-icon-fail", bounty, config, mock_db)

    # Announcement should still proceed with icon=None.
    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("criminal_icon") is None


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


@pytest.mark.asyncio
async def test_expiry_uses_direct_scheduler_when_holder_has_scheduler():
    """B.23a: _schedule_expiry_job uses the direct APScheduler API when scheduler_holder
    has a scheduler instance, bypassing the HTTP POST entirely."""
    from utils.executors.bounty_spawn_executor import _schedule_expiry_job

    bounty = _make_bounty(bounty_id=42)
    mock_scheduler = MagicMock()
    mock_scheduler.add_job = MagicMock()

    with (
        patch("utils.scheduler_holder.get_scheduler", return_value=mock_scheduler),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_http_cls,
    ):
        await _schedule_expiry_job("parent-job-direct", bounty)

    # Direct API must have been called; HTTP must NOT have been used
    mock_scheduler.add_job.assert_called_once()
    add_job_kwargs = mock_scheduler.add_job.call_args
    # Verify the job is a date-trigger one-time job with the correct payload
    assert add_job_kwargs.kwargs.get("trigger") == "date" or (
        len(add_job_kwargs.args) > 1 and add_job_kwargs.args[1] == "date"
    )
    mock_http_cls.assert_not_called()


@pytest.mark.asyncio
async def test_expiry_falls_back_to_http_when_holder_returns_none():
    """B.23a: _schedule_expiry_job falls back to HTTP POST when scheduler_holder has no
    scheduler (e.g. test environments or early startup before set_scheduler is called)."""
    from utils.executors.bounty_spawn_executor import _schedule_expiry_job

    bounty = _make_bounty(bounty_id=43)

    with (
        patch("utils.scheduler_holder.get_scheduler", return_value=None),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_http_cls,
    ):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _schedule_expiry_job("parent-job-fallback", bounty)

    # HTTP fallback must have been used
    mock_http_cls.assert_called_once()
    mock_client.post.assert_awaited_once()


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

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 999}})

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
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
        post_calls = [c for c in mock_client.post.call_args_list if "/announcements/bounty/channel/" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/announcements/bounty/channel/111222" in posted_url


@pytest.mark.asyncio
async def test_announce_routes_to_silver_channel():
    """Silver division bounty → announcement POSTed to silver_bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=20, guild_id=100, division="silver")
    config = _make_config_for_announce(guild_id=100, bronze_channel=None, silver_channel=222333)
    mock_db = AsyncMock()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 888}})

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-silver", bounty, config, mock_db)

        post_calls = [c for c in mock_client.post.call_args_list if "/announcements/bounty/channel/" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/announcements/bounty/channel/222333" in posted_url


@pytest.mark.asyncio
async def test_announce_routes_to_gold_channel():
    """Gold division bounty → announcement POSTed to gold_bounty_channel_id."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=30, guild_id=100, division="gold")
    config = _make_config_for_announce(guild_id=100, bronze_channel=None, silver_channel=None, gold_channel=333444)
    mock_db = AsyncMock()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 777}})

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-gold", bounty, config, mock_db)

        post_calls = [c for c in mock_client.post.call_args_list if "/announcements/bounty/channel/" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url", "")
        assert "/announcements/bounty/channel/333444" in posted_url


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

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 12345}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("utils.executors.bounty_spawn_executor.httpx.AsyncClient") as mock_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=MagicMock(content=b"PNG", raise_for_status=MagicMock()))

        await _announce_bounty("job-rich", bounty, config, mock_db)

        # A.48: build_bounty_announcement_request was called with the correct bounty.
        mock_helper.assert_awaited_once()
        called_bounty = mock_helper.call_args.args[1]
        assert called_bounty.criminal_name == "Vossk Raider"
        assert called_bounty.criminal_faction == "Vossk"

        # POST body uses the unified bounty-announcement shape
        # (loadout_response + metadata, not pre-rendered embed dict).
        post_calls = [c for c in mock_client.post.call_args_list if "/announcements/bounty/channel/" in str(c)]
        assert len(post_calls) >= 1
        posted_body = post_calls[-1].kwargs.get("json") or (
            post_calls[-1].args[1] if len(post_calls[-1].args) > 1 else {}
        )
        assert "loadout_response" in posted_body
        assert "metadata" in posted_body
        assert posted_body["metadata"]["title"] == "Vossk Raider"
        assert "color" in posted_body["metadata"]


@pytest.mark.asyncio
async def test_announce_uploads_route_map():
    """When image_channel_id is set, a PNG is fetched from the map endpoint and uploaded."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=60, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111, image_channel=999888)
    mock_db = AsyncMock()

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
    msg_response.json = MagicMock(return_value={"data": {"id": 777}})

    mock_helper = AsyncMock(
        return_value=_make_announce_request(bounty, route_map_url="https://cdn.example.com/map.png")
    )

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
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

        # A.48: build_bounty_announcement_request was called with the CDN URL as route_map_url kwarg.
        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("route_map_url") == "https://cdn.example.com/map.png"


@pytest.mark.asyncio
async def test_announce_skips_map_when_no_image_channel():
    """When image_channel_id is None, no map fetch or upload is attempted."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=70, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=111, image_channel=None)
    mock_db = AsyncMock()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 321}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
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

        # A.48: build_bounty_announcement_request called with route_map_url=None.
        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("route_map_url") is None


@pytest.mark.asyncio
async def test_announce_persists_discord_message():
    """After successful POST, a DiscordMessage is created with correct fields."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=80, guild_id=100, division="bronze")
    config = _make_config_for_announce(guild_id=100, bronze_channel=444555)
    mock_db = AsyncMock()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    discord_message_id = 888777666
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": discord_message_id}})

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
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
    msg_response.json = MagicMock(return_value={"data": {"id": 555}})

    mock_helper = AsyncMock(return_value=_make_announce_request(bounty))

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
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
        assert "/announcements/bounty/channel/111" in msg_url

        # A.48: route_map_url=None when upload failed (helper was called without a map URL).
        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("route_map_url") is None


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

    mock_msg_repo = AsyncMock()
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
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
    """_announce_bounty POSTs to /announcements/bounty/channel/{channel_id} using the rich embed builder."""
    from utils.executors.bounty_spawn_executor import _announce_bounty

    bounty = _make_bounty(bounty_id=4, guild_id=101, division="bronze", criminal_name="Pirate X", reward=75000)
    channel_id = 987654
    config = _make_config_for_announce(guild_id=101, bronze_channel=channel_id)
    mock_db = AsyncMock()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.create_or_update = AsyncMock(return_value=MagicMock())
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_msg_repo
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": 11111}})

    with (
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_announce_request(bounty)),
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
        post_calls = [c for c in mock_client.post.call_args_list if "/announcements/bounty/channel/" in str(c)]
        assert len(post_calls) >= 1
        posted_url = post_calls[-1].args[0] if post_calls[-1].args else post_calls[-1].kwargs.get("url")
        assert f"/announcements/bounty/channel/{channel_id}" in posted_url

        # A.48: body uses the unified bounty-announcement shape (metadata + loadout_response).
        posted_body = post_calls[-1].kwargs.get("json") or (
            post_calls[-1].args[1] if len(post_calls[-1].args) > 1 else {}
        )
        assert "metadata" in posted_body
        assert posted_body["metadata"]["title"] == "Pirate X"
        assert "loadout_response" in posted_body


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


# ===========================================================================
# Tests: eligibility guard — _is_guild_fully_configured
# ===========================================================================


def test_is_guild_fully_configured_returns_true_when_all_ids_set():
    """_is_guild_fully_configured returns True when all 5 required fields are non-null."""
    from utils.executors.bounty_spawn_executor import _is_guild_fully_configured

    cfg = _make_guild_config(
        guild_id=1,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=444,
        bounty_hunter_role_id=555,
    )
    assert _is_guild_fully_configured(cfg) is True


def test_is_guild_fully_configured_returns_false_when_all_null():
    """_is_guild_fully_configured returns False when all channel/role IDs are None."""
    from utils.executors.bounty_spawn_executor import _is_guild_fully_configured

    cfg = _make_guild_config(
        guild_id=2,
        bronze_bounty_channel_id=None,
        silver_bounty_channel_id=None,
        gold_bounty_channel_id=None,
        platinum_bounty_channel_id=None,
        bounty_hunter_role_id=None,
    )
    assert _is_guild_fully_configured(cfg) is False


def test_is_guild_fully_configured_returns_false_when_only_role_missing():
    """_is_guild_fully_configured returns False when only bounty_hunter_role_id is None."""
    from utils.executors.bounty_spawn_executor import _is_guild_fully_configured

    cfg = _make_guild_config(
        guild_id=3,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=444,
        bounty_hunter_role_id=None,  # only this is missing
    )
    assert _is_guild_fully_configured(cfg) is False


def test_is_guild_fully_configured_returns_false_when_platinum_channel_missing():
    """_is_guild_fully_configured returns False when platinum_bounty_channel_id is None."""
    from utils.executors.bounty_spawn_executor import _is_guild_fully_configured

    cfg = _make_guild_config(
        guild_id=4,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=None,  # platinum channel missing
        bounty_hunter_role_id=555,
    )
    assert _is_guild_fully_configured(cfg) is False


@pytest.mark.asyncio
async def test_unconfigured_guild_is_skipped_no_bounties_created():
    """A guild with all null channel/role IDs is skipped — spawn_bounty is never called."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # Guild with NO channel or role IDs configured (skeleton skeleton row)
    skeleton_cfg = _make_guild_config(
        guild_id=9001,
        bronze_bounty_channel_id=None,
        silver_bounty_channel_id=None,
        gold_bounty_channel_id=None,
        platinum_bounty_channel_id=None,
        bounty_hunter_role_id=None,
    )
    _configure_config_repo([skeleton_cfg])
    mock_svc = _configure_bounty_service(MagicMock())
    _configure_bounty_repo({})

    result = await execute_bounty_spawn_job(
        "job-unconfigured",
        {"job_type": "bounty_spawn"},
    )

    # spawn_bounty must NOT have been called
    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_partially_configured_guild_is_skipped():
    """A guild missing only bounty_hunter_role_id is skipped — spawn_bounty is never called."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    # Guild has all channels but is missing bounty_hunter_role_id
    partial_cfg = _make_guild_config(
        guild_id=9002,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=444,
        bounty_hunter_role_id=None,  # missing!
    )
    _configure_config_repo([partial_cfg])
    mock_svc = _configure_bounty_service(MagicMock())
    _configure_bounty_repo({})

    result = await execute_bounty_spawn_job(
        "job-partial",
        {"job_type": "bounty_spawn"},
    )

    mock_svc.spawn_bounty.assert_not_awaited()
    assert result["total_spawned"] == 0


@pytest.mark.asyncio
async def test_fully_configured_guild_proceeds_normally():
    """A guild with all 5 required IDs set is NOT skipped and proceeds to spawn bounties."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    bounty = _make_bounty(bounty_id=300, guild_id=9003, division="bronze")
    full_cfg = _make_guild_config(
        guild_id=9003,
        bronze_bounty_channel_id=111,
        silver_bounty_channel_id=222,
        gold_bounty_channel_id=333,
        platinum_bounty_channel_id=444,
        bounty_hunter_role_id=555,
    )
    _configure_config_repo([full_cfg])
    mock_svc = _configure_bounty_service(bounty)
    _configure_bounty_repo({})

    with (
        patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new=AsyncMock()),
        patch("utils.executors.bounty_spawn_executor._announce_bounty", new=AsyncMock()),
    ):
        result = await execute_bounty_spawn_job(
            "job-full-config",
            {"job_type": "bounty_spawn", "guild_id": 9003, "division": "Bronze"},
        )

    # spawn_bounty MUST have been called
    mock_svc.spawn_bounty.assert_awaited_once()
    assert result["total_spawned"] == 1


@pytest.mark.asyncio
async def test_skip_logs_info_message_for_unconfigured_guild():
    """When a guild is skipped due to missing config, an INFO log message is emitted."""
    from utils.executors.bounty_spawn_executor import execute_bounty_spawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_temperature_service(max_bounties=5)

    skeleton_cfg = _make_guild_config(
        guild_id=9004,
        bronze_bounty_channel_id=None,
        silver_bounty_channel_id=None,
        gold_bounty_channel_id=None,
        platinum_bounty_channel_id=None,
        bounty_hunter_role_id=None,
    )
    _configure_config_repo([skeleton_cfg])
    _configure_bounty_service(MagicMock())
    _configure_bounty_repo({})

    with patch("utils.executors.bounty_spawn_executor.flogger") as mock_logger:
        await execute_bounty_spawn_job(
            "job-skip-log",
            {"job_type": "bounty_spawn"},
        )

    # Verify an INFO log was emitted mentioning the guild and the skip reason
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    skip_logged = any("skipping guild=9004" in msg and "not fully configured" in msg for msg in info_calls)
    assert skip_logged, f"Expected skip INFO log for guild=9004, got: {info_calls}"
