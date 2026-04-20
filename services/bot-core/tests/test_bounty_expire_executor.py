"""
Unit tests for utils.executors.bounty_expire_executor.

Tests verify:
 - Returns error dict when bounty_id is missing from payload
 - Fetches the bounty BEFORE calling expire_bounty (needed for announcement lookup)
 - Calls BountyService.expire_bounty() with the correct bounty_id
 - Returns 'skipped' when bounty is not found in the database at all
 - Returns 'success' dict with bounty_id on successful expiry
 - Returns 'success' when expire_bounty returns None but bounty_obj exists (already captured)
 - ALWAYS deletes the announcement, regardless of expire_bounty return value
 - Does NOT call _announce_expiry (function removed; expiry no longer posts a message)
 - BountyService exceptions propagate (re-raised)
 - job_executor.py dispatches bounty_expire job_type
 - bounty_spawn payloads do NOT trigger execute_bounty_expire_job

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because bounty_expire_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.bounty_service.BountyService"
  - "persist.repositories.bounty_repository.BountyRepository"
We pre-register stub modules in sys.modules so deferred imports inside
execute_bounty_expire_job resolve without pulling in real ORM code.
"""

import os as _os
import sys
import types
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
# Pre-register stub modules so deferred imports in bounty_expire_executor work
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

# Stub for services.bounty_service — BountyService class.
_MockBountyService = MagicMock()
_ensure_stub("services.bounty_service", BountyService=_MockBountyService)

# Stub for bounty_repository — BountyRepository class (used for pre-fetch).
_MockBountyRepository = MagicMock()
_ensure_stub("persist.repositories.bounty_repository", BountyRepository=_MockBountyRepository)

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(
    bounty_id: int = 1,
    guild_id: int = 100,
    division: str = "bronze",
    criminal_name: str = "Kato Vort",
    criminal_faction: str = "Vossk",
    reward: int = 50000,
    tech_level: int = 5,
) -> MagicMock:
    """Build a mock Bounty-like object."""
    b = MagicMock()
    b.id = bounty_id
    b.guild_id = guild_id
    b.division = division
    b.criminal_name = criminal_name
    b.criminal_faction = criminal_faction
    b.reward = reward
    b.tech_level = tech_level
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


def _configure_bounty_service(expire_return) -> AsyncMock:
    """Configure BountyService.expire_bounty to return *expire_return*."""
    mock_svc = AsyncMock()
    mock_svc.expire_bounty = AsyncMock(return_value=expire_return)
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)
    return mock_svc


def _configure_bounty_repo(get_by_id_return) -> AsyncMock:
    """Configure BountyRepository.get_by_id to return *get_by_id_return*."""
    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=get_by_id_return)
    sys.modules["persist.repositories.bounty_repository"].BountyRepository = MagicMock(return_value=mock_repo)
    return mock_repo


# ===========================================================================
# Tests: payload validation
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_bounty_id_returns_error():
    """When bounty_id is absent from the payload, return an error dict immediately."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    result = await execute_bounty_expire_job("job-no-id", {"job_type": "bounty_expire"})

    assert result["status"] == "error"
    assert result["bounty_id"] is None


# ===========================================================================
# Tests: successful expiry
# ===========================================================================


@pytest.mark.asyncio
async def test_expire_bounty_called_with_correct_id():
    """BountyService.expire_bounty is called with the bounty_id from the payload."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=42, guild_id=100, division="bronze")
    mock_svc = _configure_bounty_service(bounty)
    _configure_bounty_repo(bounty)  # pre-fetch returns the same bounty

    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=AsyncMock()):
        await execute_bounty_expire_job(
            "job-expire-42",
            {"job_type": "bounty_expire", "bounty_id": 42},
        )

    mock_svc.expire_bounty.assert_awaited_once_with(mock_db, 42)


@pytest.mark.asyncio
async def test_successful_expiry_returns_success_dict():
    """A successful expiry returns status='success' with the correct bounty_id."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=7, guild_id=100, division="bronze")
    _configure_bounty_service(bounty)
    _configure_bounty_repo(bounty)

    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=AsyncMock()):
        result = await execute_bounty_expire_job(
            "job-ok",
            {"job_type": "bounty_expire", "bounty_id": 7},
        )

    assert result["status"] == "success"
    assert result["bounty_id"] == 7


# ===========================================================================
# Tests: expire_bounty returns None (bounty already captured/completed)
# ===========================================================================


@pytest.mark.asyncio
async def test_expire_returns_none_but_bounty_obj_exists_returns_success():
    """When expire_bounty returns None but bounty_obj exists (already captured),
    the result is still status='success' and the announcement is deleted.
    """
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=55, guild_id=100, division="bronze")
    _configure_bounty_service(None)  # already captured — expire returns None
    _configure_bounty_repo(bounty)  # but bounty_obj still exists in DB

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-captured",
            {"job_type": "bounty_expire", "bounty_id": 55},
        )

    # Should succeed (not skipped) — bounty was captured, timer fired, announcement deleted
    assert result["status"] == "success"
    assert result["bounty_id"] == 55
    # Announcement must ALWAYS be deleted
    mock_delete.assert_awaited_once_with("job-captured", bounty, mock_db)


@pytest.mark.asyncio
async def test_expire_returns_none_when_bounty_not_in_db_gives_skipped():
    """When bounty_obj is also None (not in DB at all), the result is 'skipped'."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_bounty_service(None)  # expire returns None
    _configure_bounty_repo(None)  # bounty not found at all

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-notfound",
            {"job_type": "bounty_expire", "bounty_id": 999},
        )

    assert result["status"] == "skipped"
    assert result["bounty_id"] == 999
    # No bounty object → no announcement to delete
    mock_delete.assert_not_awaited()


# ===========================================================================
# Tests: always-delete behaviour
# ===========================================================================


@pytest.mark.asyncio
async def test_always_deletes_announcement_when_bounty_active():
    """_delete_bounty_announcement is called when expire_bounty succeeds (active bounty)."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=99, guild_id=100, division="bronze")
    _configure_bounty_service(bounty)
    _configure_bounty_repo(bounty)

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-always-delete-active",
            {"job_type": "bounty_expire", "bounty_id": 99},
        )

    assert result["status"] == "success"
    mock_delete.assert_awaited_once_with("job-always-delete-active", bounty, mock_db)


@pytest.mark.asyncio
async def test_always_deletes_announcement_when_already_captured():
    """_delete_bounty_announcement is called even when expire_bounty returns None (already captured)."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=77, guild_id=100, division="silver")
    _configure_bounty_service(None)  # expire returns None — already captured
    _configure_bounty_repo(bounty)  # but bounty_obj is present

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-always-delete-captured",
            {"job_type": "bounty_expire", "bounty_id": 77},
        )

    assert result["status"] == "success"
    mock_delete.assert_awaited_once_with("job-always-delete-captured", bounty, mock_db)


# ===========================================================================
# Tests: exception propagation
# ===========================================================================


@pytest.mark.asyncio
async def test_bounty_service_exception_propagates():
    """When BountyService.expire_bounty raises, the exception is re-raised."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=1)
    _configure_bounty_repo(bounty)

    mock_svc = AsyncMock()
    mock_svc.expire_bounty = AsyncMock(side_effect=RuntimeError("DB gone"))
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)

    with pytest.raises(RuntimeError, match="DB gone"):
        await execute_bounty_expire_job(
            "job-err",
            {"job_type": "bounty_expire", "bounty_id": 1},
        )


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_job_executor_dispatches_bounty_expire():
    """JobExecutor.execute routes bounty_expire payload to execute_bounty_expire_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_expire", "bounty_id": 42}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_bounty_expire_job", mock_fn):
        await executor.execute("job-dispatch-expire", payload)

    mock_fn.assert_awaited_once_with("job-dispatch-expire", payload)


@pytest.mark.asyncio
async def test_job_executor_does_not_dispatch_expire_for_bounty_spawn():
    """bounty_spawn payloads do NOT trigger execute_bounty_expire_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_spawn", "guild_id": 123, "division": "Bronze"}

    mock_expire_fn = AsyncMock()
    mock_spawn_fn = AsyncMock(return_value={"status": "success"})

    with (
        patch("utils.job_executor.execute_bounty_expire_job", mock_expire_fn),
        patch("utils.job_executor.execute_bounty_spawn_job", mock_spawn_fn),
    ):
        await executor.execute("job-no-expire", payload)

    mock_expire_fn.assert_not_awaited()
    mock_spawn_fn.assert_awaited_once()


# ===========================================================================
# Tests: _delete_bounty_announcement
# ===========================================================================

# Pre-register stubs for discord_message_repository used in delete tests.
_MockDiscordMsgRepo = MagicMock()
_ensure_stub("persist.repositories.discord_message_repository", DiscordMessageRepository=_MockDiscordMsgRepo)


def _make_discord_message(
    message_id: int = 42000,
    guild_id: int = 100,
    channel_id: int = 55000,
) -> MagicMock:
    """Return a mock DiscordMessage-like object."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.guild_id = guild_id
    msg.channel_id = channel_id
    return msg


def _configure_msg_repo(get_return=None, delete_return=True) -> AsyncMock:
    """Patch DiscordMessageRepository to return controlled values."""
    mock_repo = AsyncMock()
    mock_repo.get_by_guild_type_and_reference = AsyncMock(return_value=get_return)
    mock_repo.delete_by_guild_type_and_reference = AsyncMock(return_value=delete_return)
    sys.modules["persist.repositories.discord_message_repository"].DiscordMessageRepository = MagicMock(
        return_value=mock_repo
    )
    return mock_repo


@pytest.mark.asyncio
async def test_expire_deletes_announcement_message():
    """After expire, _delete_bounty_announcement sends DELETE to channel-specific URL and cleans DB record."""
    from utils.executors.bounty_expire_executor import _delete_bounty_announcement

    mock_db = AsyncMock()
    bounty = _make_bounty(bounty_id=10, guild_id=100)
    channel_id = 66666
    discord_msg = _make_discord_message(message_id=55555, guild_id=100, channel_id=channel_id)

    mock_repo = _configure_msg_repo(get_return=discord_msg)

    captured_calls = {}

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def capture_delete(url, timeout=None):
            captured_calls["url"] = url
            resp = MagicMock()
            resp.status_code = 204
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.delete = capture_delete
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _delete_bounty_announcement("parent-job", bounty, mock_db)

    # Discord DELETE was called with the channel-specific URL (not /messages/{id}).
    url = captured_calls.get("url", "")
    assert f"/channels/{channel_id}/messages/55555" in url, (
        f"Expected channel-specific DELETE URL /channels/{channel_id}/messages/55555 but got: {url}"
    )
    # DB record was deleted
    mock_repo.delete_by_guild_type_and_reference.assert_awaited_once_with(mock_db, 100, "bounty_announcement", 10)


@pytest.mark.asyncio
async def test_expire_handles_no_announcement_gracefully():
    """When no DiscordMessage exists, no HTTP DELETE is sent and no error is raised."""
    from utils.executors.bounty_expire_executor import _delete_bounty_announcement

    mock_db = AsyncMock()
    bounty = _make_bounty(bounty_id=20, guild_id=200)

    _configure_msg_repo(get_return=None)  # No message found

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        # Must NOT raise, and httpx must NOT be used
        await _delete_bounty_announcement("parent-job", bounty, mock_db)

    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_expire_handles_discord_delete_failure():
    """Discord DELETE returning an error is logged but DB record is still deleted."""
    from utils.executors.bounty_expire_executor import _delete_bounty_announcement

    mock_db = AsyncMock()
    bounty = _make_bounty(bounty_id=30, guild_id=300)
    discord_msg = _make_discord_message(message_id=77777, guild_id=300, channel_id=30000)

    mock_repo = _configure_msg_repo(get_return=discord_msg)

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=Exception("Connection refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise — non-fatal
        await _delete_bounty_announcement("parent-job", bounty, mock_db)

    # DB record should still be deleted even after Discord failure
    mock_repo.delete_by_guild_type_and_reference.assert_awaited_once_with(mock_db, 300, "bounty_announcement", 30)


@pytest.mark.asyncio
async def test_expire_handles_discord_404_gracefully():
    """Discord returning 404 (message already deleted) is treated as success."""
    from utils.executors.bounty_expire_executor import _delete_bounty_announcement

    mock_db = AsyncMock()
    bounty = _make_bounty(bounty_id=40, guild_id=400)
    discord_msg = _make_discord_message(message_id=88888, guild_id=400, channel_id=40000)

    mock_repo = _configure_msg_repo(get_return=discord_msg)

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def return_404(url, timeout=None):
            resp = MagicMock()
            resp.status_code = 404
            resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))
            return resp

        mock_client.delete = return_404
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise — 404 is acceptable
        await _delete_bounty_announcement("parent-job", bounty, mock_db)

    # DB record should still be deleted
    mock_repo.delete_by_guild_type_and_reference.assert_awaited_once_with(mock_db, 400, "bounty_announcement", 40)


@pytest.mark.asyncio
async def test_execute_job_calls_delete_announcement_on_success():
    """execute_bounty_expire_job calls _delete_bounty_announcement using the pre-fetched bounty_obj."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=50, guild_id=500, division="gold")
    _configure_bounty_service(bounty)
    _configure_bounty_repo(bounty)

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-delete-test",
            {"job_type": "bounty_expire", "bounty_id": 50},
        )

    assert result["status"] == "success"
    mock_delete.assert_awaited_once_with("job-delete-test", bounty, mock_db)


@pytest.mark.asyncio
async def test_delete_uses_channel_specific_url():
    """_delete_bounty_announcement uses /channels/{channel_id}/messages/{message_id} not /messages/{id}."""
    from utils.executors.bounty_expire_executor import _delete_bounty_announcement

    mock_db = AsyncMock()
    bounty = _make_bounty(bounty_id=80, guild_id=800)
    channel_id = 88000
    discord_msg = _make_discord_message(message_id=12345, guild_id=800, channel_id=channel_id)

    _configure_msg_repo(get_return=discord_msg)
    captured_url = []

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def capture_delete(url, timeout=None):
            captured_url.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.delete = capture_delete
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _delete_bounty_announcement("parent-job", bounty, mock_db)

    assert len(captured_url) == 1
    url = captured_url[0]
    # Must use channel-specific path.
    assert f"/channels/{channel_id}/messages/12345" in url, f"Expected channel-specific URL but got: {url}"
    # Must NOT use the generic /messages/{id} path.
    assert "/messages/12345" in url and f"/channels/{channel_id}" in url


# ===========================================================================
# Tests: new lifecycle — captured bounty timer fires, announcement deleted
# ===========================================================================


@pytest.mark.asyncio
async def test_captured_bounty_timer_fires_deletes_announcement():
    """When timer fires on a captured bounty:
    - expire_bounty returns None (bounty is 'completed', not 'active')
    - job still returns 'success'
    - _delete_bounty_announcement is called with the pre-fetched bounty_obj
    """
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty_obj = _make_bounty(bounty_id=999, guild_id=100, division="bronze")
    _configure_bounty_service(None)  # already captured/completed
    _configure_bounty_repo(bounty_obj)  # but bounty_obj fetched successfully

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-captured-timer",
            {"job_type": "bounty_expire", "bounty_id": 999},
        )

    assert result["status"] == "success"
    mock_delete.assert_awaited_once_with("job-captured-timer", bounty_obj, mock_db)


@pytest.mark.asyncio
async def test_completed_bounty_timer_fires_deletes_announcement():
    """When timer fires on a completed bounty (same as captured):
    - expire_bounty returns None
    - job returns 'success'
    - _delete_bounty_announcement is called
    """
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty_obj = _make_bounty(bounty_id=998, guild_id=200, division="silver")
    _configure_bounty_service(None)
    _configure_bounty_repo(bounty_obj)

    mock_delete = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._delete_bounty_announcement", new=mock_delete):
        result = await execute_bounty_expire_job(
            "job-completed-timer",
            {"job_type": "bounty_expire", "bounty_id": 998},
        )

    assert result["status"] == "success"
    mock_delete.assert_awaited_once_with("job-completed-timer", bounty_obj, mock_db)
