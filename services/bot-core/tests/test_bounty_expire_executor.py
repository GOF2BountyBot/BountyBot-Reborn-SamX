"""
Unit tests for utils.executors.bounty_expire_executor.

Tests verify:
 - Returns error dict when bounty_id is missing from payload
 - Calls BountyService.expire_bounty() with the correct bounty_id
 - Returns 'skipped' when expire_bounty returns None (not found / wrong status)
 - Returns 'success' dict with bounty_id on successful expiry
 - Calls _announce_expiry after successful expiry
 - Does NOT call _announce_expiry when expire_bounty returns None
 - HTTP errors in gateway announcement are non-fatal
 - BountyService exceptions propagate (re-raised)
 - job_executor.py dispatches bounty_expire job_type
 - bounty_spawn payloads do NOT trigger execute_bounty_expire_job

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because bounty_expire_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.bounty_service.BountyService"
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

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
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
    bounty = _make_bounty(bounty_id=42)
    mock_svc = _configure_bounty_service(bounty)

    with patch("utils.executors.bounty_expire_executor._announce_expiry", new=AsyncMock()):
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
    bounty = _make_bounty(bounty_id=7)
    _configure_bounty_service(bounty)

    with patch("utils.executors.bounty_expire_executor._announce_expiry", new=AsyncMock()):
        result = await execute_bounty_expire_job(
            "job-ok",
            {"job_type": "bounty_expire", "bounty_id": 7},
        )

    assert result["status"] == "success"
    assert result["bounty_id"] == 7


@pytest.mark.asyncio
async def test_announce_called_after_successful_expiry():
    """_announce_expiry is called with the job_id and expired bounty on success."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=99)
    _configure_bounty_service(bounty)

    mock_announce = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._announce_expiry", new=mock_announce):
        await execute_bounty_expire_job(
            "job-announce",
            {"job_type": "bounty_expire", "bounty_id": 99},
        )

    mock_announce.assert_awaited_once_with("job-announce", bounty)


# ===========================================================================
# Tests: expire_bounty returns None (not found / wrong status)
# ===========================================================================


@pytest.mark.asyncio
async def test_expire_returns_none_gives_skipped():
    """When expire_bounty returns None, the result is status='skipped'."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_bounty_service(None)  # not found or wrong status

    mock_announce = AsyncMock()
    with patch("utils.executors.bounty_expire_executor._announce_expiry", new=mock_announce):
        result = await execute_bounty_expire_job(
            "job-skip",
            {"job_type": "bounty_expire", "bounty_id": 55},
        )

    assert result["status"] == "skipped"
    assert result["bounty_id"] == 55
    mock_announce.assert_not_awaited()


# ===========================================================================
# Tests: gateway announcement
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_expiry_http_error_is_non_fatal():
    """An HTTP error in _announce_expiry does not propagate to the caller."""
    import httpx
    from utils.executors.bounty_expire_executor import _announce_expiry

    bounty = _make_bounty(bounty_id=1)

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise.
        await _announce_expiry("parent-job", bounty)


@pytest.mark.asyncio
async def test_announce_expiry_posts_correct_message_type():
    """_announce_expiry posts a message with message_type='bounty_expire'."""
    from utils.executors.bounty_expire_executor import _announce_expiry

    bounty = _make_bounty(bounty_id=5, guild_id=200, division="silver")
    captured_body: list[dict] = []

    with patch("utils.executors.bounty_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def _post(url, json=None, timeout=None):
            captured_body.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.post = _post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_expiry("parent-job", bounty)

    assert len(captured_body) == 1
    body = captured_body[0]
    assert body["message_type"] == "bounty_expire"
    assert body["guild_id"] == 200
    assert body["content"]["bounty_id"] == 5
    assert body["content"]["division"] == "silver"


# ===========================================================================
# Tests: exception propagation
# ===========================================================================


@pytest.mark.asyncio
async def test_bounty_service_exception_propagates():
    """When BountyService.expire_bounty raises, the exception is re-raised."""
    from utils.executors.bounty_expire_executor import execute_bounty_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

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
