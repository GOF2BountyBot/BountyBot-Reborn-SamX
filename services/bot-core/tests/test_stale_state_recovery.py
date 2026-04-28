"""Unit tests for run_stale_state_recovery_sweep() in main.py.

Tests verify (B.14 — Layer 2):
  - Stale active bounties (status='active', end_time < NOW()) are bulk-updated to 'expired'.
  - Stale pending duels (status='pending', expires_at < NOW()) are bulk-updated to 'expired'.
  - The sweep emits two UPDATE statements (one per entity type).
  - A single commit is made on success.
  - DB errors during sweep are caught, rollback is called, and the exception does not propagate
    (sweep is non-fatal — startup must continue even if sweep fails).

Isolation strategy:
  - Sets up sys.path and mocks so ``from main import run_stale_state_recovery_sweep`` works.
  - Patches ``main.db_manager`` (module-level attribute) so no real DB is needed.
  - Uses an AsyncMock session whose execute() returns pre-configured rowcount results.
  - Max 2 mocks per test (db_manager session + execute side_effect).

Setup mirrors test_main_coverage.py, which already handles the apscheduler / api.routers
import chain in main.py.
"""

import os
import sys
import types
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger BEFORE any source imports.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for method in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, method, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Ensure src/ is at the front of sys.path (mirrors test_main_coverage.py).
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)


# ---------------------------------------------------------------------------
# Mock the full apscheduler stack + other deps required by main.py.
# ---------------------------------------------------------------------------


def _ensure_apscheduler_mocked() -> None:
    """Install complete apscheduler mock covering all submodules used by main.py."""
    if "apscheduler" not in sys.modules or not hasattr(sys.modules["apscheduler"], "_is_full_mock"):
        _apscheduler = ModuleType("apscheduler")
        _apscheduler._is_full_mock = True  # type: ignore[attr-defined]
        sys.modules["apscheduler"] = _apscheduler

    if "apscheduler.triggers" not in sys.modules:
        sys.modules["apscheduler.triggers"] = ModuleType("apscheduler.triggers")
    if "apscheduler.triggers.cron" not in sys.modules:
        _mod = ModuleType("apscheduler.triggers.cron")

        class _CronTrigger:
            jitter = None

            def __init__(self, *a, **kw):
                pass

            @classmethod
            def from_crontab(cls, expr, *a, **kw):
                obj = cls()
                obj._expr = expr
                return obj

        _mod.CronTrigger = _CronTrigger
        sys.modules["apscheduler.triggers.cron"] = _mod

    if "apscheduler.schedulers" not in sys.modules:
        sys.modules["apscheduler.schedulers"] = ModuleType("apscheduler.schedulers")
    if "apscheduler.schedulers.asyncio" not in sys.modules:
        _mod2 = ModuleType("apscheduler.schedulers.asyncio")
        _mod2.AsyncIOScheduler = MagicMock  # type: ignore[attr-defined]
        sys.modules["apscheduler.schedulers.asyncio"] = _mod2

    if "apscheduler.jobstores" not in sys.modules:
        sys.modules["apscheduler.jobstores"] = ModuleType("apscheduler.jobstores")
    if "apscheduler.jobstores.sqlalchemy" not in sys.modules:
        _mod3 = ModuleType("apscheduler.jobstores.sqlalchemy")
        _mod3.SQLAlchemyJobStore = MagicMock  # type: ignore[attr-defined]
        sys.modules["apscheduler.jobstores.sqlalchemy"] = _mod3


_ensure_apscheduler_mocked()


@pytest.fixture(autouse=True)
def _repair_module_env():
    """Ensure sys.modules is clean before each test (mirrors test_main_coverage.py)."""
    if _SRC_DIR not in sys.path:
        sys.path.insert(0, _SRC_DIR)
    elif sys.path[0] != _SRC_DIR:
        sys.path.remove(_SRC_DIR)
        sys.path.insert(0, _SRC_DIR)

    _ensure_apscheduler_mocked()

    # Purge stale 'main' that doesn't come from src/
    for _k in list(sys.modules):
        if _k == "main" or _k.startswith("main."):
            _f = getattr(sys.modules[_k], "__file__", "") or ""
            if _SRC_DIR not in _f:
                del sys.modules[_k]

    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_execute_result(rowcount: int) -> MagicMock:
    """Return a mock execute result with the given rowcount."""
    result = MagicMock()
    result.rowcount = rowcount
    return result


def _make_session_ctx(session: AsyncMock) -> MagicMock:
    """Return an async context manager that yields *session*."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStaleBountyRecovery:
    """Recovery sweep correctly transitions stale active bounties to 'expired'."""

    @pytest.mark.asyncio
    async def test_stale_bounties_are_marked_expired(self):
        """3 stale active bounties → both UPDATEs executed, single commit."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        # First execute = bounty UPDATE (3 rows), second = duel UPDATE (0 rows)
        mock_db.execute = AsyncMock(side_effect=[_make_execute_result(3), _make_execute_result(0)])

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        # Two UPDATEs executed (bounties + duels)
        assert mock_db.execute.await_count == 2
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_stale_bounties_no_op(self):
        """0 stale bounties → commit still called once, no error."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[_make_execute_result(0), _make_execute_result(0)])

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sweep_bounty_statement_targets_bounty_table_with_time_filter(self):
        """The first UPDATE statement targets the bounty table and includes a now() time filter."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[_make_execute_result(3), _make_execute_result(0)])

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        # Inspect the first UPDATE statement (bounty)
        first_stmt = mock_db.execute.call_args_list[0][0][0]
        stmt_str = str(first_stmt.compile(compile_kwargs={"literal_binds": False}))
        # Must target the bounty table and include a now() time guard
        assert "bounty" in stmt_str.lower(), f"Expected 'bounty' table in SQL, got: {stmt_str}"
        assert "now" in stmt_str.lower(), f"Expected now() time filter in SQL, got: {stmt_str}"


class TestStaleDuelRecovery:
    """Recovery sweep correctly transitions stale pending duels to 'expired'."""

    @pytest.mark.asyncio
    async def test_stale_duels_are_marked_expired(self):
        """2 stale pending duels → both UPDATEs executed, single commit."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        # First execute = bounty UPDATE (0), second = duel UPDATE (2)
        mock_db.execute = AsyncMock(side_effect=[_make_execute_result(0), _make_execute_result(2)])

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        assert mock_db.execute.await_count == 2
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duel_update_statement_uses_time_filter(self):
        """The second UPDATE statement targets duel_requests with a now() time filter."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[_make_execute_result(0), _make_execute_result(1)])

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        # Second statement = duel UPDATE
        second_stmt = mock_db.execute.call_args_list[1][0][0]
        stmt_str = str(second_stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "duel_requests" in stmt_str.lower(), f"Expected 'duel_requests' table in SQL, got: {stmt_str}"
        assert "now" in stmt_str.lower(), f"Expected now() time filter in duel UPDATE SQL, got: {stmt_str}"


class TestRecoverySweepErrorHandling:
    """Recovery sweep is non-fatal — DB errors are logged and startup continues."""

    @pytest.mark.asyncio
    async def test_db_error_is_caught_and_does_not_propagate(self):
        """When the DB raises during sweep, the exception is swallowed (non-fatal)."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("Connection lost"))

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            # Must NOT raise — sweep is non-fatal
            await run_stale_state_recovery_sweep()

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_error_causes_rollback(self):
        """On sweep failure, rollback is called and commit is NOT called."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("Timeout"))

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_state_recovery_sweep

            await run_stale_state_recovery_sweep()

        mock_db.rollback.assert_awaited_once()
        mock_db.commit.assert_not_awaited()


class TestStaleRespawnRecovery:
    """run_stale_respawn_recovery() re-fires bounty respawns missed during downtime."""

    @pytest.mark.asyncio
    async def test_no_stale_escaped_bounties_no_op(self):
        """Empty result set → executor never invoked."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        mock_db.execute = AsyncMock(return_value=result)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        executor = AsyncMock()
        with (
            patch("main.db_manager", mock_db_manager),
            patch("utils.executors.bounty_respawn_executor.execute_bounty_respawn_job", executor),
        ):
            from main import run_stale_respawn_recovery

            await run_stale_respawn_recovery()

        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_escaped_bounties_invoke_executor(self):
        """Each stale escaped bounty triggers an execute_bounty_respawn_job call."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.all = MagicMock(return_value=[(101,), (202,), (303,)])
        mock_db.execute = AsyncMock(return_value=result)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        executor = AsyncMock(return_value={"status": "success", "bounty_id": 0})
        with (
            patch("main.db_manager", mock_db_manager),
            patch("utils.executors.bounty_respawn_executor.execute_bounty_respawn_job", executor),
        ):
            from main import run_stale_respawn_recovery

            await run_stale_respawn_recovery()

        assert executor.await_count == 3
        # Verify payload shape on the first call
        first_kwargs = executor.await_args_list[0].kwargs
        assert first_kwargs["payload"]["job_type"] == "bounty_respawn"
        assert first_kwargs["payload"]["bounty_id"] in (101, 202, 303)

    @pytest.mark.asyncio
    async def test_executor_failure_is_swallowed(self):
        """Exception from executor → other bounties still processed; sweep does not raise."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.all = MagicMock(return_value=[(1,), (2,)])
        mock_db.execute = AsyncMock(return_value=result)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        executor = AsyncMock(side_effect=[RuntimeError("boom"), {"status": "success"}])
        with (
            patch("main.db_manager", mock_db_manager),
            patch("utils.executors.bounty_respawn_executor.execute_bounty_respawn_job", executor),
        ):
            from main import run_stale_respawn_recovery

            # Must not raise
            await run_stale_respawn_recovery()

        assert executor.await_count == 2

    @pytest.mark.asyncio
    async def test_db_error_during_query_is_swallowed(self):
        """DB error during the SELECT → sweep returns without invoking executor."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("Connection lost"))

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        executor = AsyncMock()
        with (
            patch("main.db_manager", mock_db_manager),
            patch("utils.executors.bounty_respawn_executor.execute_bounty_respawn_job", executor),
        ):
            from main import run_stale_respawn_recovery

            await run_stale_respawn_recovery()

        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_query_filters_status_escaped_with_time_guard(self):
        """SELECT statement filters status='escaped' AND respawn_time guard."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        mock_db.execute = AsyncMock(return_value=result)

        mock_db_manager = MagicMock()
        mock_db_manager.get_session = MagicMock(return_value=_make_session_ctx(mock_db))

        with patch("main.db_manager", mock_db_manager):
            from main import run_stale_respawn_recovery

            await run_stale_respawn_recovery()

        stmt = mock_db.execute.call_args_list[0][0][0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "bounty" in stmt_str.lower()
        assert "escaped" in stmt_str.lower()
        assert "now" in stmt_str.lower()
        assert "respawn_time" in stmt_str.lower()
