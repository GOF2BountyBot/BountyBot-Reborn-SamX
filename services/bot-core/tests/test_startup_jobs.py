"""
Unit tests for default scheduler job registration on startup.

Tests cover ``main.register_default_jobs`` and ``main.DEFAULT_SCHEDULER_JOBS``
which are invoked from the lifespan context manager.

Strategy
--------
* Import only ``register_default_jobs`` and ``DEFAULT_SCHEDULER_JOBS`` from
  ``main`` — this requires pre-mocking all the heavy imports that ``main.py``
  pulls in at module level (apscheduler, sqlalchemy, shared, etc.).
* All scheduler interactions are performed on a MagicMock — no real scheduler
  or database is needed.
* The ``CronTrigger.from_crontab`` call is kept real (via our mock
  _MockCronTrigger) so we can inspect which cron expression was used.
* ``shared.bblogger`` is already patched by conftest.py; an additional
  belt-and-suspenders guard is included for direct pytest invocations.
"""

import os as _os
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Ensure src/ is at the front of sys.path, and that the shadow
# tests/api/__init__.py doesn't steal the name "api" from src/api.
# (Same approach used by tests/api/conftest.py)
# ---------------------------------------------------------------------------
_SRC_DIR = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "..", "src")
)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# Purge any stale api.* modules loaded from tests/api/
for _key in list(sys.modules):
    if _key == "api" or _key.startswith("api."):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger before importing any source module.
# conftest.py handles this at collection time; guard is here for standalone runs.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for method in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, method, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Mock apscheduler before main.py is imported.
#
# We always register the submodule stubs because another test file
# (e.g. tests/api/test_scheduler_router.py) may have already inserted a
# plain ModuleType for "apscheduler" without the subpackages — in that case
# "apscheduler" is not a real package and importing
# ``apscheduler.jobstores.sqlalchemy`` would fail with
# "ModuleNotFoundError: 'apscheduler' is not a package".
# We force-register each submodule individually so the import succeeds
# regardless of collection order.
# ---------------------------------------------------------------------------


class _MockCronTrigger:
    """Minimal CronTrigger replacement that records the cron expression."""

    def __init__(self, *args, **kwargs):
        self._expr = None

    @classmethod
    def from_crontab(cls, expr, timezone=None):
        instance = cls()
        instance._expr = expr
        return instance

    def __str__(self):
        return f"cron[{self._expr}]"


def _ensure_apscheduler_stubs():
    """Guarantee all required apscheduler submodules exist in sys.modules."""
    if "apscheduler" not in sys.modules:
        sys.modules["apscheduler"] = types.ModuleType("apscheduler")

    for path in ("apscheduler.jobstores", "apscheduler.schedulers", "apscheduler.triggers"):
        if path not in sys.modules:
            sys.modules[path] = types.ModuleType(path)

    # apscheduler.jobstores.sqlalchemy
    sqla_mod = sys.modules.get("apscheduler.jobstores.sqlalchemy")
    if sqla_mod is None or not hasattr(sqla_mod, "SQLAlchemyJobStore"):
        sqla_mod = types.ModuleType("apscheduler.jobstores.sqlalchemy")
        sqla_mod.SQLAlchemyJobStore = MagicMock()
        sys.modules["apscheduler.jobstores.sqlalchemy"] = sqla_mod

    # apscheduler.schedulers.asyncio
    asyncio_mod = sys.modules.get("apscheduler.schedulers.asyncio")
    if asyncio_mod is None or not hasattr(asyncio_mod, "AsyncIOScheduler"):
        asyncio_mod = types.ModuleType("apscheduler.schedulers.asyncio")
        asyncio_mod.AsyncIOScheduler = MagicMock()
        sys.modules["apscheduler.schedulers.asyncio"] = asyncio_mod

    # apscheduler.triggers.cron  — must use _MockCronTrigger so tests can
    # inspect the cron expression on trigger._expr
    cron_mod = sys.modules.get("apscheduler.triggers.cron")
    if cron_mod is None or not hasattr(cron_mod, "CronTrigger"):
        cron_mod = types.ModuleType("apscheduler.triggers.cron")
        sys.modules["apscheduler.triggers.cron"] = cron_mod
    # Always (re-)set CronTrigger to our mock so cron assertions work.
    cron_mod.CronTrigger = _MockCronTrigger


_ensure_apscheduler_stubs()

# ---------------------------------------------------------------------------
# SQLAlchemy is installed in the container venv — no stubbing needed.
# We import it here just to ensure it is in sys.modules before main.py runs,
# so that main.py's `from sqlalchemy import create_engine` resolves correctly.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Import main.DEFAULT_SCHEDULER_JOBS and main.register_default_jobs.
#
# main.py has many module-level imports (persist, utils.auto_seeder, etc.)
# that are not installed in the test environment.  We stub them temporarily
# in sys.modules for the duration of the import, then — crucially — remove
# the stubs so later test files can import the real src/ implementations.
#
# This "borrow-and-restore" pattern avoids polluting sys.modules for the
# entire test session.
# ---------------------------------------------------------------------------

_MOCK_RUN_JOB = MagicMock()


def _make_stub(module_path: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(module_path)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Modules we need to inject — only if they are not already present (i.e.,
# the real version has already been loaded by a different test file).
_TEMP_STUBS: dict[str, types.ModuleType] = {}

_STUB_SPECS: dict[str, dict] = {
    "persist": {},
    "persist.database": {},
    "persist.database.manager": {"db_manager": MagicMock()},
    "persist.schemas": {},
    "persist.schemas.schema_manager": {"initialize_schema": MagicMock()},
    "utils.auto_seeder": {"auto_seed_data": MagicMock()},
    "utils.job_executor": {"run_job": _MOCK_RUN_JOB},
}

for _mod_path, _attrs in _STUB_SPECS.items():
    if _mod_path not in sys.modules:
        _stub = _make_stub(_mod_path, **_attrs)
        sys.modules[_mod_path] = _stub
        _TEMP_STUBS[_mod_path] = _stub  # record that WE added this

# ---------------------------------------------------------------------------
# Now it is safe to import from main (only the symbols we need).
# ---------------------------------------------------------------------------
from main import DEFAULT_SCHEDULER_JOBS, register_default_jobs

# ---------------------------------------------------------------------------
# Restore: selectively remove temporary stubs that could interfere with other
# test files that import the real implementations.
#
# Rules:
# - utils.auto_seeder: restore — test_auto_seed.py imports the real module
# - utils.job_executor: restore — executor tests import the real module
# - persist.*: keep — they are lightweight stubs and other tests that need the
#   real persist code set up their own mocks via @patch
# - main: restore — no other test file needs it, safe to clean up
# ---------------------------------------------------------------------------
_RESTORE_PATHS = ("utils.auto_seeder", "utils.job_executor", "main")
for _mod_path in _RESTORE_PATHS:
    if _mod_path in _TEMP_STUBS and sys.modules.get(_mod_path) is _TEMP_STUBS[_mod_path]:
        del sys.modules[_mod_path]
sys.modules.pop("main", None)


# ---------------------------------------------------------------------------
# Helper: build a mock scheduler instance
# ---------------------------------------------------------------------------


def _make_mock_scheduler(existing_job_ids=None):
    """Return a MagicMock that quacks like AsyncIOScheduler."""
    sched = MagicMock()
    sched.start = MagicMock()
    sched.shutdown = MagicMock()
    sched.add_job = MagicMock()

    existing = existing_job_ids or []
    mock_jobs = []
    for jid in existing:
        j = MagicMock()
        j.id = jid
        mock_jobs.append(j)
    sched.get_jobs = MagicMock(return_value=mock_jobs)

    return sched


# ===========================================================================
# Tests for DEFAULT_SCHEDULER_JOBS constant
# ===========================================================================


class TestDefaultSchedulerJobsConstant:
    """Validate the structure of DEFAULT_SCHEDULER_JOBS."""

    def test_three_jobs_defined(self):
        """DEFAULT_SCHEDULER_JOBS contains exactly three job definitions."""
        assert len(DEFAULT_SCHEDULER_JOBS) == 3

    def test_job_ids_are_unique(self):
        """Each job definition has a unique job_id."""
        ids = [j["job_id"] for j in DEFAULT_SCHEDULER_JOBS]
        assert len(ids) == len(set(ids))

    def test_expected_job_ids_present(self):
        """The three expected default jobs are defined."""
        ids = {j["job_id"] for j in DEFAULT_SCHEDULER_JOBS}
        assert "bounty_spawn_default" in ids
        assert "shop_refresh_default" in ids
        assert "temperature_decay_default" in ids

    def test_each_job_has_required_keys(self):
        """Every job definition has job_id, cron, and payload keys."""
        for job_def in DEFAULT_SCHEDULER_JOBS:
            assert "job_id" in job_def, f"Missing 'job_id' in {job_def}"
            assert "cron" in job_def, f"Missing 'cron' in {job_def}"
            assert "payload" in job_def, f"Missing 'payload' in {job_def}"

    def test_each_payload_has_job_type(self):
        """Every job payload contains a 'job_type' key."""
        for job_def in DEFAULT_SCHEDULER_JOBS:
            assert "job_type" in job_def["payload"], (
                f"payload missing 'job_type' in {job_def}"
            )

    def test_bounty_spawn_payload(self):
        """bounty_spawn_default payload has job_type='bounty_spawn'."""
        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "bounty_spawn_default")
        assert job["payload"]["job_type"] == "bounty_spawn"

    def test_shop_refresh_payload(self):
        """shop_refresh_default payload has job_type='shop_refresh'."""
        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "shop_refresh_default")
        assert job["payload"]["job_type"] == "shop_refresh"

    def test_temperature_decay_payload(self):
        """temperature_decay_default payload has job_type='temperature_decay'."""
        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "temperature_decay_default")
        assert job["payload"]["job_type"] == "temperature_decay"

    def test_bounty_spawn_cron_is_5_minute_interval(self):
        """bounty_spawn_default cron encodes a 5-minute interval (BOUNTY_DELAY_RANDOM_MIN=5)."""
        from services.game_constants import GameConstants

        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "bounty_spawn_default")
        expected = f"*/{GameConstants.BOUNTY_DELAY_RANDOM_MIN} * * * *"
        assert job["cron"] == expected

    def test_shop_refresh_cron(self):
        """shop_refresh_default cron is '0 */6 * * *' (every 6 hours)."""
        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "shop_refresh_default")
        assert job["cron"] == "0 */6 * * *"

    def test_temperature_decay_cron(self):
        """temperature_decay_default cron is '0 * * * *' (every hour)."""
        job = next(j for j in DEFAULT_SCHEDULER_JOBS if j["job_id"] == "temperature_decay_default")
        assert job["cron"] == "0 * * * *"


# ===========================================================================
# Tests for register_default_jobs()
# ===========================================================================


class TestRegisterDefaultJobs:
    """Tests for the register_default_jobs() function."""

    # ------------------------------------------------------------------
    # Happy-path: fresh scheduler with no pre-existing jobs
    # ------------------------------------------------------------------

    def test_three_jobs_added_on_clean_scheduler(self):
        """All three default jobs are added when the scheduler has no prior jobs."""
        scheduler = _make_mock_scheduler()

        register_default_jobs(scheduler)

        assert scheduler.add_job.call_count == 3

    def test_bounty_spawn_job_added(self):
        """bounty_spawn_default is added with correct id and payload."""
        scheduler = _make_mock_scheduler()

        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        assert "bounty_spawn_default" in calls_by_id
        call = calls_by_id["bounty_spawn_default"]
        assert call.kwargs["args"] == [
            "bounty_spawn_default",
            {"job_type": "bounty_spawn"},
        ]

    def test_shop_refresh_job_added(self):
        """shop_refresh_default is added with correct id and payload."""
        scheduler = _make_mock_scheduler()

        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        assert "shop_refresh_default" in calls_by_id
        call = calls_by_id["shop_refresh_default"]
        assert call.kwargs["args"] == [
            "shop_refresh_default",
            {"job_type": "shop_refresh"},
        ]

    def test_temperature_decay_job_added(self):
        """temperature_decay_default is added with correct id and payload."""
        scheduler = _make_mock_scheduler()

        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        assert "temperature_decay_default" in calls_by_id
        call = calls_by_id["temperature_decay_default"]
        assert call.kwargs["args"] == [
            "temperature_decay_default",
            {"job_type": "temperature_decay"},
        ]

    # ------------------------------------------------------------------
    # Idempotency: no duplicate registration when jobs already exist
    # ------------------------------------------------------------------

    def test_no_jobs_added_when_all_already_exist(self):
        """add_job is never called when all three default jobs already exist."""
        scheduler = _make_mock_scheduler(
            existing_job_ids=[
                "bounty_spawn_default",
                "shop_refresh_default",
                "temperature_decay_default",
            ]
        )

        register_default_jobs(scheduler)

        scheduler.add_job.assert_not_called()

    def test_only_missing_jobs_are_added_partial_existing(self):
        """Only the two missing jobs are added when one already exists."""
        scheduler = _make_mock_scheduler(
            existing_job_ids=["bounty_spawn_default"]
        )

        register_default_jobs(scheduler)

        assert scheduler.add_job.call_count == 2
        registered_ids = {
            call.kwargs["id"]
            for call in scheduler.add_job.call_args_list
        }
        assert "shop_refresh_default" in registered_ids
        assert "temperature_decay_default" in registered_ids
        assert "bounty_spawn_default" not in registered_ids

    def test_single_missing_job_added(self):
        """Only the one missing job is added when two already exist."""
        scheduler = _make_mock_scheduler(
            existing_job_ids=["bounty_spawn_default", "shop_refresh_default"]
        )

        register_default_jobs(scheduler)

        assert scheduler.add_job.call_count == 1
        call = scheduler.add_job.call_args_list[0]
        assert call.kwargs["id"] == "temperature_decay_default"

    # ------------------------------------------------------------------
    # Cron expression verification
    # ------------------------------------------------------------------

    def test_bounty_spawn_trigger_uses_correct_cron(self):
        """bounty_spawn_default trigger encodes the 5-minute cron expression."""
        from services.game_constants import GameConstants

        scheduler = _make_mock_scheduler()
        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        trigger = calls_by_id["bounty_spawn_default"].kwargs["trigger"]
        expected_cron = f"*/{GameConstants.BOUNTY_DELAY_RANDOM_MIN} * * * *"
        assert trigger._expr == expected_cron

    def test_shop_refresh_trigger_uses_correct_cron(self):
        """shop_refresh_default trigger encodes the every-6-hours cron expression."""
        scheduler = _make_mock_scheduler()
        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        trigger = calls_by_id["shop_refresh_default"].kwargs["trigger"]
        assert trigger._expr == "0 */6 * * *"

    def test_temperature_decay_trigger_uses_correct_cron(self):
        """temperature_decay_default trigger encodes the hourly cron expression."""
        scheduler = _make_mock_scheduler()
        register_default_jobs(scheduler)

        calls_by_id = {
            call.kwargs["id"]: call
            for call in scheduler.add_job.call_args_list
        }
        trigger = calls_by_id["temperature_decay_default"].kwargs["trigger"]
        assert trigger._expr == "0 * * * *"

    # ------------------------------------------------------------------
    # run_job function is passed as the callable
    # ------------------------------------------------------------------

    def test_run_job_is_the_callable_for_all_jobs(self):
        """All registered jobs use run_job as their callable (first positional arg)."""
        scheduler = _make_mock_scheduler()
        register_default_jobs(scheduler)

        for call in scheduler.add_job.call_args_list:
            # First positional argument to add_job should be run_job
            assert call.args[0] is _MOCK_RUN_JOB, (
                f"Expected run_job callable but got {call.args[0]} "
                f"for job '{call.kwargs.get('id')}'"
            )
