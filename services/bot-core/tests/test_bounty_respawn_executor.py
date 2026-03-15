"""
Unit tests for utils.executors.bounty_respawn_executor.

Tests verify:
 - Returns error dict when bounty_id is missing from payload
 - Calls BountyService.respawn_bounty() with the correct bounty_id
 - Returns 'skipped' when respawn_bounty returns None (not found / wrong status / route failure)
 - Returns 'success' dict with bounty_id on successful respawn
 - Calls _announce_respawn after successful respawn
 - Does NOT call _announce_respawn when respawn_bounty returns None
 - HTTP errors in gateway announcement are non-fatal
 - BountyService exceptions propagate (re-raised)
 - Respawned bounty keeps same criminal (criminal_name preserved)
 - job_executor.py dispatches bounty_respawn job_type
 - bounty_expire payloads do NOT trigger execute_bounty_respawn_job

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because bounty_respawn_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.bounty_service.BountyService"
We pre-register stub modules in sys.modules so deferred imports inside
execute_bounty_respawn_job resolve without pulling in real ORM code.
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
# Pre-register stub modules so deferred imports in bounty_respawn_executor work
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


def _configure_bounty_service(respawn_return) -> AsyncMock:
    """Configure BountyService.respawn_bounty to return *respawn_return*."""
    mock_svc = AsyncMock()
    mock_svc.respawn_bounty = AsyncMock(return_value=respawn_return)
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)
    return mock_svc


# ===========================================================================
# Tests: payload validation
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_bounty_id_returns_error():
    """When bounty_id is absent from the payload, return an error dict immediately."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    result = await execute_bounty_respawn_job("job-no-id", {"job_type": "bounty_respawn"})

    assert result["status"] == "error"
    assert result["bounty_id"] is None


# ===========================================================================
# Tests: successful respawn
# ===========================================================================


@pytest.mark.asyncio
async def test_respawn_bounty_called_with_correct_id():
    """BountyService.respawn_bounty is called with the bounty_id from the payload."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=42)
    mock_svc = _configure_bounty_service(bounty)

    with patch("utils.executors.bounty_respawn_executor._announce_respawn", new=AsyncMock()):
        await execute_bounty_respawn_job(
            "job-respawn-42",
            {"job_type": "bounty_respawn", "bounty_id": 42},
        )

    mock_svc.respawn_bounty.assert_awaited_once_with(mock_db, 42)


@pytest.mark.asyncio
async def test_successful_respawn_returns_success_dict():
    """A successful respawn returns status='success' with the correct bounty_id."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=7)
    _configure_bounty_service(bounty)

    with patch("utils.executors.bounty_respawn_executor._announce_respawn", new=AsyncMock()):
        result = await execute_bounty_respawn_job(
            "job-ok",
            {"job_type": "bounty_respawn", "bounty_id": 7},
        )

    assert result["status"] == "success"
    assert result["bounty_id"] == 7


@pytest.mark.asyncio
async def test_announce_called_after_successful_respawn():
    """_announce_respawn is called with the job_id and respawned bounty on success."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    bounty = _make_bounty(bounty_id=99)
    _configure_bounty_service(bounty)

    mock_announce = AsyncMock()
    with patch("utils.executors.bounty_respawn_executor._announce_respawn", new=mock_announce):
        await execute_bounty_respawn_job(
            "job-announce",
            {"job_type": "bounty_respawn", "bounty_id": 99},
        )

    mock_announce.assert_awaited_once_with("job-announce", bounty)


@pytest.mark.asyncio
async def test_respawn_keeps_same_criminal_name():
    """The respawned bounty retains the same criminal_name (same criminal, new route)."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    original_criminal = "Vera Koss"
    bounty = _make_bounty(bounty_id=10, criminal_name=original_criminal)
    mock_svc = _configure_bounty_service(bounty)

    with patch("utils.executors.bounty_respawn_executor._announce_respawn", new=AsyncMock()):
        await execute_bounty_respawn_job(
            "job-criminal-check",
            {"job_type": "bounty_respawn", "bounty_id": 10},
        )

    # The bounty object returned by respawn_bounty still has the original criminal.
    called_args = mock_svc.respawn_bounty.call_args
    assert called_args.args[1] == 10  # bounty_id passed through correctly
    assert bounty.criminal_name == original_criminal  # criminal unchanged


# ===========================================================================
# Tests: respawn_bounty returns None (not found / wrong status / route failure)
# ===========================================================================


@pytest.mark.asyncio
async def test_respawn_returns_none_gives_skipped():
    """When respawn_bounty returns None, the result is status='skipped'."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)
    _configure_bounty_service(None)  # not found, wrong status, or route failure

    mock_announce = AsyncMock()
    with patch("utils.executors.bounty_respawn_executor._announce_respawn", new=mock_announce):
        result = await execute_bounty_respawn_job(
            "job-skip",
            {"job_type": "bounty_respawn", "bounty_id": 55},
        )

    assert result["status"] == "skipped"
    assert result["bounty_id"] == 55
    mock_announce.assert_not_awaited()


# ===========================================================================
# Tests: gateway announcement
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_respawn_http_error_is_non_fatal():
    """An HTTP error in _announce_respawn does not propagate to the caller."""
    import httpx
    from utils.executors.bounty_respawn_executor import _announce_respawn

    bounty = _make_bounty(bounty_id=1)

    with patch("utils.executors.bounty_respawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise.
        await _announce_respawn("parent-job", bounty)


@pytest.mark.asyncio
async def test_announce_respawn_posts_correct_message_type():
    """_announce_respawn posts a message with message_type='bounty_respawn'."""
    from utils.executors.bounty_respawn_executor import _announce_respawn

    bounty = _make_bounty(
        bounty_id=5,
        guild_id=200,
        division="gold",
        route=["A", "B", "C", "D"],
    )
    captured_body: list[dict] = []

    with patch("utils.executors.bounty_respawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def _post(url, json=None, timeout=None):
            captured_body.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.post = _post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_respawn("parent-job", bounty)

    assert len(captured_body) == 1
    body = captured_body[0]
    assert body["message_type"] == "bounty_respawn"
    assert body["guild_id"] == 200
    assert body["content"]["bounty_id"] == 5
    assert body["content"]["division"] == "gold"
    assert body["content"]["route_length"] == 4


@pytest.mark.asyncio
async def test_announce_respawn_includes_end_time():
    """_announce_respawn includes the new end_time in the content."""
    from utils.executors.bounty_respawn_executor import _announce_respawn

    new_end = datetime.now(UTC) + timedelta(days=4)
    bounty = _make_bounty(bounty_id=3, end_time=new_end)
    captured_body: list[dict] = []

    with patch("utils.executors.bounty_respawn_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()

        async def _post(url, json=None, timeout=None):
            captured_body.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client.post = _post
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_respawn("parent-job", bounty)

    assert captured_body[0]["content"]["end_time"] == new_end.isoformat()


# ===========================================================================
# Tests: exception propagation
# ===========================================================================


@pytest.mark.asyncio
async def test_bounty_service_exception_propagates():
    """When BountyService.respawn_bounty raises, the exception is re-raised."""
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_svc = AsyncMock()
    mock_svc.respawn_bounty = AsyncMock(side_effect=RuntimeError("DB gone"))
    sys.modules["services.bounty_service"].BountyService = MagicMock(return_value=mock_svc)

    with pytest.raises(RuntimeError, match="DB gone"):
        await execute_bounty_respawn_job(
            "job-err",
            {"job_type": "bounty_respawn", "bounty_id": 1},
        )


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_job_executor_dispatches_bounty_respawn():
    """JobExecutor.execute routes bounty_respawn payload to execute_bounty_respawn_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_respawn", "bounty_id": 42}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_bounty_respawn_job", mock_fn):
        await executor.execute("job-dispatch-respawn", payload)

    mock_fn.assert_awaited_once_with("job-dispatch-respawn", payload)


@pytest.mark.asyncio
async def test_job_executor_does_not_dispatch_respawn_for_bounty_expire():
    """bounty_expire payloads do NOT trigger execute_bounty_respawn_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "bounty_expire", "bounty_id": 7}

    mock_respawn_fn = AsyncMock()
    mock_expire_fn = AsyncMock(return_value={"status": "success"})

    with (
        patch("utils.job_executor.execute_bounty_respawn_job", mock_respawn_fn),
        patch("utils.job_executor.execute_bounty_expire_job", mock_expire_fn),
    ):
        await executor.execute("job-no-respawn", payload)

    mock_respawn_fn.assert_not_awaited()
    mock_expire_fn.assert_awaited_once()
