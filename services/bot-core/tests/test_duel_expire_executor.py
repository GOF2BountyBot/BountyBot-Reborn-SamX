"""
Unit tests for utils.executors.duel_expire_executor.

Tests verify:
 - Returns error dict when duel_id is missing from payload
 - Calls DuelService.expire_duel() with the correct duel_id
 - Returns 'skipped' when expire_duel raises ValueError (not found / wrong status)
 - Returns 'success' dict with duel_id on successful expiry
 - Calls _notify_expiry after successful expiry
 - Does NOT call _notify_expiry when expire_duel raises ValueError
 - HTTP errors in gateway notification are non-fatal
 - DuelService exceptions (non-ValueError) propagate (re-raised)
 - job_executor.py dispatches duel_expire job_type
 - bounty_expire payloads do NOT trigger execute_duel_expire_job

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because duel_expire_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.duel_service.DuelService"
We pre-register stub modules in sys.modules so deferred imports inside
execute_duel_expire_job resolve without pulling in real ORM code.
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
# Pre-register stub modules so deferred imports in duel_expire_executor work
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

# Stub for services.duel_service — DuelService class.
_MockDuelService = MagicMock()
_ensure_stub("services.duel_service", DuelService=_MockDuelService)

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("services")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_duel(
    duel_id: int = 1,
    guild_id: int = 100,
    challenger_id: int = 111,
    target_id: int = 222,
    stakes: int = 500,
    status: str = "expired",
) -> MagicMock:
    """Build a mock DuelRequest-like object."""
    d = MagicMock()
    d.id = duel_id
    d.guild_id = guild_id
    d.challenger_id = challenger_id
    d.target_id = target_id
    d.stakes = stakes
    d.status = status
    return d


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


def _configure_duel_service(expire_return=None, expire_side_effect=None) -> AsyncMock:
    """Configure DuelService.expire_duel to return *expire_return* or raise *expire_side_effect*."""
    mock_svc = AsyncMock()
    if expire_side_effect is not None:
        mock_svc.expire_duel = AsyncMock(side_effect=expire_side_effect)
    else:
        mock_svc.expire_duel = AsyncMock(return_value=expire_return)
    sys.modules["services.duel_service"].DuelService = MagicMock(return_value=mock_svc)
    return mock_svc


# ===========================================================================
# Tests: payload validation
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_duel_id_returns_error():
    """When duel_id is absent from the payload, return an error dict immediately."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    result = await execute_duel_expire_job("job-no-id", {"job_type": "duel_expire"})

    assert result["status"] == "error"
    assert result["duel_id"] is None
    assert "missing duel_id" in result["reason"]


# ===========================================================================
# Tests: successful expiry
# ===========================================================================


@pytest.mark.asyncio
async def test_expire_duel_called_with_correct_id():
    """DuelService.expire_duel is called with the duel_id from the payload."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    duel = _make_duel(duel_id=42)
    mock_svc = _configure_duel_service(expire_return=duel)

    with patch("utils.executors.duel_expire_executor._notify_expiry", new=AsyncMock()):
        await execute_duel_expire_job(
            "job-expire-42",
            {"job_type": "duel_expire", "duel_id": 42},
        )

    mock_svc.expire_duel.assert_awaited_once_with(mock_db, 42)


@pytest.mark.asyncio
async def test_successful_expiry_returns_success_dict():
    """A successful expiry returns status='success' with the correct duel_id."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    duel = _make_duel(duel_id=7)
    _configure_duel_service(expire_return=duel)

    with patch("utils.executors.duel_expire_executor._notify_expiry", new=AsyncMock()):
        result = await execute_duel_expire_job(
            "job-ok",
            {"job_type": "duel_expire", "duel_id": 7},
        )

    assert result["status"] == "success"
    assert result["duel_id"] == 7


@pytest.mark.asyncio
async def test_notify_called_after_successful_expiry():
    """_notify_expiry is called with the job_id and expired duel on success."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    duel = _make_duel(duel_id=99, challenger_id=111, target_id=222)
    _configure_duel_service(expire_return=duel)

    mock_notify = AsyncMock()
    with patch("utils.executors.duel_expire_executor._notify_expiry", new=mock_notify):
        await execute_duel_expire_job(
            "job-notify",
            {"job_type": "duel_expire", "duel_id": 99},
        )

    mock_notify.assert_awaited_once_with("job-notify", duel)


# ===========================================================================
# Tests: expire_duel raises ValueError (not found / wrong status)
# ===========================================================================


@pytest.mark.asyncio
async def test_value_error_gives_skipped():
    """When expire_duel raises ValueError, the result is status='skipped'."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_duel_service(
        expire_side_effect=ValueError("Duel request with ID 55 not found.")
    )

    mock_notify = AsyncMock()
    with patch("utils.executors.duel_expire_executor._notify_expiry", new=mock_notify):
        result = await execute_duel_expire_job(
            "job-skip",
            {"job_type": "duel_expire", "duel_id": 55},
        )

    assert result["status"] == "skipped"
    assert result["duel_id"] == 55
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_status_gives_skipped():
    """When expire_duel raises ValueError due to wrong status, result is 'skipped'."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_duel_service(
        expire_side_effect=ValueError("Duel 10 cannot be expired — current status is 'accepted'.")
    )

    mock_notify = AsyncMock()
    with patch("utils.executors.duel_expire_executor._notify_expiry", new=mock_notify):
        result = await execute_duel_expire_job(
            "job-wrong-status",
            {"job_type": "duel_expire", "duel_id": 10},
        )

    assert result["status"] == "skipped"
    assert result["duel_id"] == 10
    mock_notify.assert_not_awaited()


# ===========================================================================
# Tests: gateway notification
# ===========================================================================


@pytest.mark.asyncio
async def test_notify_expiry_http_error_is_non_fatal():
    """An HTTP error in _notify_expiry does not propagate to the caller."""
    import httpx
    from utils.executors.duel_expire_executor import _notify_expiry

    duel = _make_duel(duel_id=1)

    with patch("utils.executors.duel_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise.
        await _notify_expiry("parent-job", duel)


@pytest.mark.asyncio
async def test_notify_expiry_posts_correct_message_type():
    """_notify_expiry posts a message with message_type='duel_expire' and both player IDs."""
    from utils.executors.duel_expire_executor import _notify_expiry

    duel = _make_duel(
        duel_id=5,
        guild_id=200,
        challenger_id=111,
        target_id=222,
        stakes=1000,
    )
    captured_body: list[dict] = []

    with patch("utils.executors.duel_expire_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def _post(url, json=None, timeout=None):
            captured_body.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.post = _post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _notify_expiry("parent-job", duel)

    assert len(captured_body) == 1
    body = captured_body[0]
    assert body["message_type"] == "duel_expire"
    assert body["guild_id"] == 200
    assert body["content"]["duel_id"] == 5
    assert body["content"]["challenger_id"] == 111
    assert body["content"]["target_id"] == 222
    assert body["content"]["stakes"] == 1000


# ===========================================================================
# Tests: exception propagation
# ===========================================================================


@pytest.mark.asyncio
async def test_duel_service_runtime_exception_propagates():
    """When DuelService.expire_duel raises a non-ValueError exception, it is re-raised."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_duel_service(expire_side_effect=RuntimeError("DB gone"))

    with pytest.raises(RuntimeError, match="DB gone"):
        await execute_duel_expire_job(
            "job-err",
            {"job_type": "duel_expire", "duel_id": 1},
        )


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_job_executor_dispatches_duel_expire():
    """JobExecutor.execute routes duel_expire payload to execute_duel_expire_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "duel_expire", "duel_id": 42}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_duel_expire_job", mock_fn):
        await executor.execute("job-dispatch-expire", payload)

    mock_fn.assert_awaited_once_with("job-dispatch-expire", payload)


@pytest.mark.asyncio
async def test_job_executor_does_not_dispatch_duel_expire_for_bounty_expire():
    """bounty_expire payloads do NOT trigger execute_duel_expire_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_expire", "bounty_id": 99}

    mock_duel_expire_fn = AsyncMock()
    mock_bounty_expire_fn = AsyncMock(return_value={"status": "success"})

    with (
        patch("utils.job_executor.execute_duel_expire_job", mock_duel_expire_fn),
        patch("utils.job_executor.execute_bounty_expire_job", mock_bounty_expire_fn),
    ):
        await executor.execute("job-no-duel-expire", payload)

    mock_duel_expire_fn.assert_not_awaited()
    mock_bounty_expire_fn.assert_awaited_once()


# ===========================================================================
# Tests: timeout handling (DUEL_REQUEST_EXPIRY constant)
# ===========================================================================


@pytest.mark.asyncio
async def test_timeout_causes_skipped_when_duel_already_resolved():
    """When a duel is already accepted/rejected before the timeout fires,
    expire_duel raises ValueError and the executor returns 'skipped' — this
    is the normal timeout race-condition path."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    # Simulate duel already accepted before the timeout job fires
    _configure_duel_service(
        expire_side_effect=ValueError(
            "Duel 77 cannot be expired — current status is 'accepted'."
        )
    )

    mock_notify = AsyncMock()
    with patch("utils.executors.duel_expire_executor._notify_expiry", new=mock_notify):
        result = await execute_duel_expire_job(
            "job-timeout-race",
            {"job_type": "duel_expire", "duel_id": 77},
        )

    assert result["status"] == "skipped"
    assert result["duel_id"] == 77
    # No notification should be sent when the duel was already resolved
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_causes_skipped_when_duel_not_found():
    """When a duel_id is no longer in the database when the timeout fires,
    the executor returns 'skipped' gracefully."""
    from utils.executors.duel_expire_executor import execute_duel_expire_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_duel_service(
        expire_side_effect=ValueError("Duel request with ID 999 not found.")
    )

    mock_notify = AsyncMock()
    with patch("utils.executors.duel_expire_executor._notify_expiry", new=mock_notify):
        result = await execute_duel_expire_job(
            "job-not-found",
            {"job_type": "duel_expire", "duel_id": 999},
        )

    assert result["status"] == "skipped"
    assert result["duel_id"] == 999
    mock_notify.assert_not_awaited()
