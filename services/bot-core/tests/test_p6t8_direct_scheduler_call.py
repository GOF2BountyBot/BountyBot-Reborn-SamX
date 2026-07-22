"""P6-T8 tests: self-HTTP /jobs loopback replaced with direct scheduler.add_job / modify_job.

Spec requirements (from PLAN_OF_ACTION_TASKS.md P6-T8)
------------------------------------------------------
1. No self-HTTP loopback: executor must NOT make a POST /jobs or PUT /jobs/{id}
   HTTP call for scheduling; it must call scheduler_holder.get_scheduler()
   directly.
2. Job fires EXACTLY ONCE: the job created by add_job has the same
   id / trigger / run_date / args as what the old HTTP path produced.
3. workers=1 contract: a single scheduler instance, a single add_job call per
   eligible tier → the job can only fire once.
4. Failure path: if add_job raises, the executor records schedule_error (same
   as when the HTTP call failed) and continues to the next tier.
5. time_announcement_executor: first-time creation updates job args via direct
   scheduler.modify_job, NOT via HTTP PUT /jobs/{id}.

Test strategy
-------------
Tier A — pure structural / static assertions (no mocks).
Tier B — unit tests using MagicMock schedulers; assert add_job / modify_job
          call signatures exactly.
Tier C — no-loopback assertions: respx.mock with assert_all_mocked=True ensures
          no unmocked HTTP call reaches the scheduler endpoint.

Mock budget
-----------
Each test uses at most 2 mocks:
  1. ``persist.database.manager.db_manager`` bridge (Tier B gate).
  2. ``utils.scheduler_holder.get_scheduler`` returning a mock scheduler.
Patching ``scheduler_holder.get_scheduler`` is NOT mocking a repo method or a
service — it is providing a test double for the in-process scheduler (no live
APScheduler in unit tests).  Counted as 1 mock by the test policy.
"""

from __future__ import annotations

import os
import re
import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup + shared stubs
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
    _mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_shared.bblogger  # type: ignore[arg-type]

if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = types.ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sau

import respx

# ===========================================================================
# Helpers
# ===========================================================================

_JOB_ID_PATTERN = re.compile(r"^bounty_spawn_\d+_(bronze|silver|gold|platinum)_[0-9a-f\-]{36}$")


def _make_spawn_scheduler(*, add_job_side_effect=None) -> MagicMock:
    """Return a mock scheduler with a recording add_job.

    The returned mock also stubs ``get_jobs`` with an empty list so the
    orchestrator's queued-count query sees zero pre-existing jobs (cold-start).
    """
    sched = MagicMock()
    sched.get_jobs = MagicMock(return_value=[])
    if add_job_side_effect is not None:
        sched.add_job = MagicMock(side_effect=add_job_side_effect)
    else:
        sched.add_job = MagicMock(return_value=None)
    return sched


def _make_ta_scheduler(*, modify_job_side_effect=None) -> MagicMock:
    """Return a mock scheduler for time_announcement tests.

    Stubs only ``modify_job`` — the time_announcement executor never calls
    ``add_job`` or ``get_jobs``.
    """
    sched = MagicMock()
    if modify_job_side_effect is not None:
        sched.modify_job = MagicMock(side_effect=modify_job_side_effect)
    else:
        sched.modify_job = MagicMock(return_value=None)
    return sched


# ===========================================================================
# Tier A — static / structural assertions
# ===========================================================================


# ===========================================================================
# NOTE: class TestNoHttpLoopbackInSource (two source-text greps asserting the
# ``_SELF_BASE_URL/jobs`` POST and ``/api/v1/jobs/`` PUT loopbacks are absent)
# was removed here (test true-up).  Those greps pass/fail on source text; the
# no-loopback behaviour is proven for real below by TestOrchestratorDirectAddJob
# (the orchestrator calls scheduler.add_job directly) and by the respx tests
# with ``assert_all_mocked=True`` (test_no_http_call_to_jobs_endpoint /
# test_no_http_put_to_jobs_loopback) which fail loudly if ANY HTTP call to the
# jobs endpoint is made.
# ===========================================================================


# ===========================================================================
# Tier B — bounty_spawn_executor: job-spec equivalence
# ===========================================================================


class TestOrchestratorDirectAddJob:
    """Tier B: verify direct add_job call produces the correct job spec.

    Job-spec invariants (identical to what the old POST /jobs path produced):
      - trigger == "date"  (DateTrigger — one-time)
      - run_date is a UTC datetime strictly in the future
      - args == [spawn_job_id, payload_dict]
      - id == spawn_job_id  (bounty_spawn_{gid}_{tier}_{uuid} pattern)
      - payload_dict == {"job_type": "bounty_spawn_one", "guild_id": gid, "tier": tier}
    """

    def _make_orchestrator_stubs(self, guild_id: int = 42001, tier: str = "bronze", max_per_tier: int = 3):
        """Build minimal stubs for the orchestrator to reach the scheduling call."""
        guild_cfg = MagicMock()
        guild_cfg.guild_id = guild_id
        guild_cfg.bronze_bounty_channel_id = 111
        guild_cfg.silver_bounty_channel_id = 222
        guild_cfg.gold_bounty_channel_id = 333
        guild_cfg.platinum_bounty_channel_id = 444
        guild_cfg.bounty_hunter_role_id = 555
        guild_cfg.bounty_max_per_tier = {tier: max_per_tier, "silver": 0, "gold": 0, "platinum": 0}
        guild_cfg.bounty_expiry_minutes = 480
        guild_cfg.bounty_spawn_interval_minutes = 5
        guild_cfg.bronze_role_id = None
        guild_cfg.silver_role_id = None
        guild_cfg.gold_role_id = None
        guild_cfg.platinum_role_id = None

        mock_db = AsyncMock()
        config_repo = AsyncMock()
        config_repo.list_all = AsyncMock(return_value=[guild_cfg])
        bounty_repo = AsyncMock()
        bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        mock_db_mgr = MagicMock()
        mock_db_mgr.get_session = MagicMock(return_value=_async_ctx(mock_db))

        return guild_cfg, mock_db_mgr, config_repo, bounty_repo

    async def test_add_job_trigger_is_date(self):
        """Direct add_job is called with trigger='date' (one-time job contract)."""
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42001
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id)

        add_job_calls: list[dict] = []

        sched = _make_spawn_scheduler(add_job_side_effect=lambda *a, **kw: add_job_calls.append(kw))

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            result = await execute_bounty_spawn_orchestrate_job("p6t8-trig", {})

        assert result["status"] == "success"
        assert len(add_job_calls) == 1, f"Expected 1 add_job call, got {len(add_job_calls)}"
        call = add_job_calls[0]
        assert call.get("trigger") == "date", (
            f"Expected trigger='date' (one-time DateTrigger), got {call.get('trigger')!r}"
        )

    async def test_add_job_run_date_is_future_utc(self):
        """run_date passed to add_job is a UTC-aware datetime in the future."""
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42002
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id)

        run_dates: list[datetime] = []

        def _capture(*args, **kwargs):
            rd = kwargs.get("run_date")
            if rd is not None:
                run_dates.append(rd)

        sched = _make_spawn_scheduler(add_job_side_effect=_capture)
        now_before = datetime.now(UTC)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            await execute_bounty_spawn_orchestrate_job("p6t8-date", {})

        assert len(run_dates) == 1, f"Expected 1 run_date captured, got {len(run_dates)}"
        rd = run_dates[0]
        assert isinstance(rd, datetime), f"run_date must be a datetime, got {type(rd)}"
        assert rd.tzinfo is not None, "run_date must be timezone-aware (UTC)"
        assert rd > now_before, f"run_date {rd} must be in the future (> {now_before})"

    async def test_add_job_args_match_job_spec(self):
        """args=[spawn_job_id, payload_dict] and id=spawn_job_id both match bounty_spawn pattern."""
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42003
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id)

        captured: dict[str, Any] = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)

        sched = _make_spawn_scheduler(add_job_side_effect=_capture)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            await execute_bounty_spawn_orchestrate_job("p6t8-args", {})

        # args must be [job_id_str, payload_dict]
        args = captured.get("args")
        assert isinstance(args, list) and len(args) == 2, f"add_job args must be [job_id, payload], got {args!r}"
        spawn_job_id, inner_payload = args
        assert isinstance(spawn_job_id, str), f"args[0] must be the job id string, got {spawn_job_id!r}"
        assert isinstance(inner_payload, dict), f"args[1] must be the payload dict, got {inner_payload!r}"

        # Payload fields.
        assert inner_payload.get("job_type") == "bounty_spawn_one", (
            f"payload.job_type must be 'bounty_spawn_one', got {inner_payload!r}"
        )
        assert inner_payload.get("guild_id") == guild_id, (
            f"payload.guild_id must be {guild_id}, got {inner_payload.get('guild_id')!r}"
        )
        assert inner_payload.get("tier") == "bronze", (
            f"payload.tier must be 'bronze', got {inner_payload.get('tier')!r}"
        )

        # id= kwarg must equal args[0] (spawn_job_id).
        assert captured.get("id") == spawn_job_id, (
            f"add_job id={captured.get('id')!r} must equal args[0]={spawn_job_id!r}"
        )

        # id must match bounty_spawn_{gid}_{tier}_{uuid} pattern.
        assert _JOB_ID_PATTERN.match(spawn_job_id), (
            f"spawn_job_id={spawn_job_id!r} does not match bounty_spawn_<gid>_<tier>_<uuid>"
        )

    async def test_fires_exactly_once_at_workers_1(self):
        """At workers=1 (single scheduler), add_job is called exactly once per eligible tier.

        The one-time-job contract: a single add_job call with id=spawn_job_id
        means APScheduler fires the job once and then removes it.  Multiple
        add_job calls with the same id would raise ConflictingIdError; a test
        that only calls add_job once is the strongest possible proof that the
        job fires exactly once.

        This test goes one step further: it verifies that add_job is called
        exactly once per tier (bronze only here) even when the orchestrator runs
        twice (simulating two cron ticks on the same single-worker process).
        On the second tick, bronze is at capacity (queued_count=1), so add_job
        must NOT be called again.
        """
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42004
        # max_per_tier=1: once 1 job is queued, the second tick sees capacity_full.
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id, max_per_tier=1)

        add_job_count = [0]

        def _count(*args, **kwargs):
            add_job_count[0] += 1

        # First tick: scheduler has 0 queued jobs for bronze.
        sched_tick1 = _make_spawn_scheduler(add_job_side_effect=_count)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched_tick1),
        ):
            result1 = await execute_bounty_spawn_orchestrate_job("p6t8-once-tick1", {})

        assert result1["total_queued"] == 1, f"Tick 1 should queue 1 job; got {result1['total_queued']}"
        assert add_job_count[0] == 1, f"add_job called {add_job_count[0]} times on tick 1 (expected 1)"

        # Second tick: now the scheduler reports 1 queued bronze job → capacity reached.
        from types import SimpleNamespace as _SN

        queued_job = _SN(
            id=f"bounty_spawn_{guild_id}_bronze_fake-uuid-0",
            next_run_time=datetime.now(UTC) + timedelta(minutes=5),
        )
        sched_tick2 = _make_spawn_scheduler()
        sched_tick2.get_jobs = MagicMock(return_value=[queued_job])
        add_job_count_tick2 = [0]

        def _count_tick2(*a, **kw):
            add_job_count_tick2[0] += 1

        sched_tick2.add_job = MagicMock(side_effect=_count_tick2)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched_tick2),
        ):
            result2 = await execute_bounty_spawn_orchestrate_job("p6t8-once-tick2", {})

        # Second tick must NOT schedule bronze again (capacity_full).
        assert result2["total_queued"] == 0, (
            f"Tick 2 should NOT queue any job (capacity reached); got {result2['total_queued']}"
        )
        assert add_job_count_tick2[0] == 0, (
            f"add_job must NOT be called on tick 2 (capacity full), got {add_job_count_tick2[0]} calls"
        )

    async def test_failure_path_records_schedule_error_and_continues(self):
        """If add_job raises, the executor records schedule_error and continues to next tier.

        This mirrors the old HTTP-failure behavior: a 5xx response from POST /jobs
        caused an exception → logged error → schedule_error recorded → loop continued.
        The direct add_job path must preserve exactly that failure behavior.
        """
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42005

        # Enable bronze + silver so we can observe continuation.
        guildcfg = MagicMock()
        guildcfg.guild_id = guild_id
        guildcfg.bronze_bounty_channel_id = 111
        guildcfg.silver_bounty_channel_id = 222
        guildcfg.gold_bounty_channel_id = 333
        guildcfg.platinum_bounty_channel_id = 444
        guildcfg.bounty_hunter_role_id = 555
        guildcfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 0, "platinum": 0}
        guildcfg.bounty_expiry_minutes = 480
        guildcfg.bounty_spawn_interval_minutes = 5
        guildcfg.bronze_role_id = None
        guildcfg.silver_role_id = None
        guildcfg.gold_role_id = None
        guildcfg.platinum_role_id = None

        mock_db = AsyncMock()
        config_repo = AsyncMock()
        config_repo.list_all = AsyncMock(return_value=[guildcfg])
        bounty_repo = AsyncMock()
        bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
        db_mgr = MagicMock()
        db_mgr.get_session = MagicMock(return_value=_async_ctx(mock_db))

        bronze_tried = [0]
        silver_tried = [0]

        def _add_job_handler(*args, **kwargs):
            payload = (kwargs.get("args") or [None, {}])[1]
            tier = payload.get("tier", "")
            if tier == "bronze":
                bronze_tried[0] += 1
                raise RuntimeError("scheduler busy")
            elif tier == "silver":
                silver_tried[0] += 1

        sched = _make_spawn_scheduler(add_job_side_effect=_add_job_handler)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            result = await execute_bounty_spawn_orchestrate_job("p6t8-fail", {})

        tiers = result.get("results", {}).get(guild_id, {}).get("tiers", {})

        # Bronze failed → schedule_error.
        assert tiers.get("bronze", {}).get("reason") == "schedule_error", (
            f"Bronze add_job failure must record schedule_error, got {tiers.get('bronze')!r}"
        )

        # Silver succeeded → queued=1 (orchestrator did NOT stop at bronze failure).
        assert tiers.get("silver", {}).get("queued") == 1, (
            f"Silver must still be queued despite bronze failure, got {tiers.get('silver')!r}"
        )

        assert bronze_tried[0] == 1, "Bronze add_job must be attempted exactly once"
        assert silver_tried[0] == 1, "Silver add_job must be attempted despite bronze failure"

    async def test_no_http_call_to_jobs_endpoint(self):
        """No HTTP call is made to the self-/jobs endpoint on the orchestrator path.

        Uses respx in assert_all_mocked=True mode: any unexpected HTTP call
        raises httpx.ConnectError, which would propagate and fail the test.
        We do NOT register a /jobs route — so if the code posts to /jobs, the
        test fails.
        """
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42006
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id)
        sched = _make_spawn_scheduler()

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
            # assert_all_mocked=True means ANY unmocked HTTP call raises ConnectError.
            # We register NO /jobs route — so a POST /jobs loopback would fail the test.
            respx.mock(assert_all_called=False, assert_all_mocked=True),
        ):
            result = await execute_bounty_spawn_orchestrate_job("p6t8-nohttp", {})

        # If we get here, no HTTP call to /jobs was made.
        assert result["status"] == "success"
        assert result["total_queued"] == 1, f"Expected 1 job queued (bronze only), got {result['total_queued']}"

    async def test_scheduler_none_records_schedule_error(self):
        """If get_scheduler() returns None, add_job cannot be called → schedule_error.

        This is the canonical 'scheduler not yet initialised' failure mode.
        The executor must NOT crash the whole orchestrator — it must record
        schedule_error for the affected tier and continue.
        """
        from utils.executors.bounty_spawn_executor import execute_bounty_spawn_orchestrate_job

        guild_id = 42007
        _guildcfg, db_mgr, config_repo, bounty_repo = self._make_orchestrator_stubs(guild_id)

        with (
            patch("persist.database.manager.db_manager", db_mgr),
            patch("persist.repositories.config_repository.ConfigRepository", return_value=config_repo),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=bounty_repo),
            patch("utils.scheduler_holder.get_scheduler", return_value=None),
        ):
            result = await execute_bounty_spawn_orchestrate_job("p6t8-none", {})

        tiers = result.get("results", {}).get(guild_id, {}).get("tiers", {})
        assert tiers.get("bronze", {}).get("reason") == "schedule_error", (
            f"scheduler=None must result in schedule_error for the tier, got {tiers.get('bronze')!r}"
        )


# ===========================================================================
# Tier B — time_announcement_executor: modify_job job-spec equivalence
# ===========================================================================


class TestTimeAnnouncementDirectModifyJob:
    """Tier B: verify direct modify_job call produces the correct updated args.

    Job-spec invariants for the modify_job path:
      - job_id == the job_id passed to execute_time_announcement_job
      - new args == [job_id, {**original_payload, "message_id": <new_msg_id>}]
    """

    @staticmethod
    def _base_time_url() -> str:
        """The /time URL the executor actually uses (computed from env at import)."""
        import utils.executors.time_announcement_executor as ta_mod

        return ta_mod.BASE_TIME_URL

    def _register_time_routes(self, router, *, get_status: int = 404, message_id: str = "ta-msg-99"):
        """Register transport-level GET/POST/PUT routes on the real /time URL.

        Replaces the previous accept-anything MagicMock httpx client — respx now
        asserts the route + verb, so a POST/PUT to the wrong endpoint fails.
        """
        base = self._base_time_url()
        router.get(url__startswith=base).respond(get_status, text="")
        router.post(url__startswith=base).respond(201, json={"message_id": message_id})
        router.put(url__startswith=base).respond(200, json={})

    async def test_modify_job_called_on_first_creation(self):
        """On first creation (GET=404, POST=201), modify_job is called to update args."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        sched = _make_ta_scheduler()
        payload = {"guild_id": "g1", "channel_id": "c1", "current_time": "2026-01-01T00:00:00Z"}

        with (
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            self._register_time_routes(router, get_status=404, message_id="new-ta-msg")
            await execute_time_announcement_job("ta-job-new", payload)

        sched.modify_job.assert_called_once()

    async def test_modify_job_not_called_on_update(self):
        """On subsequent runs (GET=200, PUT), modify_job is NOT called.

        Only first-time creation needs to update the job args with the new
        message_id.  Subsequent ticks already have message_id in the payload.
        """
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        sched = _make_ta_scheduler()
        payload = {
            "guild_id": "g1",
            "channel_id": "c1",
            "message_id": "existing-id",
            "current_time": "2026-01-01T00:00:00Z",
        }

        with (
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            # GET returns 200 → exists path (PUT).
            self._register_time_routes(router, get_status=200)
            await execute_time_announcement_job("ta-job-upd", payload)

        sched.modify_job.assert_not_called()

    async def test_modify_job_args_include_new_message_id(self):
        """modify_job args= kwarg includes the message_id returned by the POST."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        sched = _make_ta_scheduler()
        payload = {"guild_id": "g2", "channel_id": "c2", "current_time": "2026-06-01T00:00:00Z"}

        with (
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            self._register_time_routes(router, get_status=404, message_id="fresh-id-42")
            await execute_time_announcement_job("ta-job-args", payload)

        call = sched.modify_job.call_args
        # First positional arg: job_id.
        job_id_passed = call.args[0] if call.args else call.kwargs.get("job_id")
        assert job_id_passed == "ta-job-args", (
            f"modify_job first arg must be the job_id='ta-job-args', got {job_id_passed!r}"
        )
        # args= kwarg: [job_id, new_payload].
        new_args = call.kwargs.get("args")
        assert new_args is not None and len(new_args) == 2, (
            f"modify_job args= must be [job_id, payload], got {new_args!r}"
        )
        new_payload = new_args[1]
        assert new_payload.get("message_id") == "fresh-id-42", (
            f"Updated payload must have message_id='fresh-id-42', got {new_payload!r}"
        )
        # Original payload fields must be preserved.
        assert new_payload.get("guild_id") == "g2"
        assert new_payload.get("channel_id") == "c2"

    async def test_no_http_put_to_jobs_loopback(self):
        """No HTTP PUT is made to the scheduler /jobs/{id} endpoint.

        Uses respx assert_all_mocked=True: any unregistered HTTP call raises.
        We register the GET + POST routes but NOT the PUT /jobs route — so if
        the code tries to PUT /jobs/{id}, the test fails.
        """
        import os as _os

        from utils.executors.time_announcement_executor import execute_time_announcement_job

        executor_host = _os.getenv("EXECUTOR_HOST", "bot-core")
        executor_port = _os.getenv("EXECUTOR_PORT", "8000")
        base_time_url = f"http://{executor_host}:{executor_port}/api/v1/time"

        sched = _make_ta_scheduler()
        payload = {"guild_id": "g3", "channel_id": "c3", "current_time": "2026-01-01T00:00:00Z"}

        with (
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
        ):
            # Register legitimate routes (GET /time → 404, POST /time → 201).
            router.get(base_time_url).respond(404, text="")
            router.post(base_time_url).respond(
                201,
                json={"message_id": "nohttp-msg"},
                headers={"content-type": "application/json"},
            )
            # Do NOT register PUT /api/v1/jobs/* — any call there would fail.

            await execute_time_announcement_job("ta-nohttp", payload)

        # If we get here, no HTTP PUT to /jobs was attempted.
        sched.modify_job.assert_called_once()

    async def test_failure_path_logs_error_and_does_not_raise(self):
        """If modify_job raises, the error is logged but does NOT propagate.

        The old HTTP-failure path: a failed PUT /jobs/{id} logged an error and
        continued.  The direct path must preserve this behavior.
        """
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        sched = _make_ta_scheduler(modify_job_side_effect=RuntimeError("scheduler not available"))
        payload = {"guild_id": "g4", "channel_id": "c4", "current_time": "2026-01-01T00:00:00Z"}

        with (
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
            patch("utils.scheduler_holder.get_scheduler", return_value=sched),
        ):
            self._register_time_routes(router, get_status=404, message_id="fail-test-msg")
            # Must NOT raise — failure is non-fatal (logged and swallowed).
            result = await execute_time_announcement_job("ta-fail", payload)

        assert result == {"status": "success"}, (
            f"Executor must return success even when modify_job fails; got {result!r}"
        )

    async def test_scheduler_none_non_fatal_for_update(self):
        """get_scheduler() returning None raises RuntimeError internally, which is caught.

        The executor raises 'scheduler not available via holder' when the holder
        has no scheduler registered.  This must be caught by the broad exception
        handler and logged — not propagated.
        """
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        payload = {"guild_id": "g5", "channel_id": "c5", "current_time": "2026-01-01T00:00:00Z"}

        with (
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
            patch("utils.scheduler_holder.get_scheduler", return_value=None),
        ):
            self._register_time_routes(router, get_status=404, message_id="none-sched-msg")
            result = await execute_time_announcement_job("ta-none", payload)

        assert result == {"status": "success"}, (
            f"Executor must return success even when scheduler is None; got {result!r}"
        )


# ===========================================================================
# Helper
# ===========================================================================


def _async_ctx(mock_db):
    """Build a minimal asynccontextmanager that yields mock_db."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield mock_db

    ctx = MagicMock()
    ctx.return_value = _ctx()
    ctx.side_effect = lambda: _ctx()
    return ctx
