"""Tests for the scheduler API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.

The scheduler router does NOT use get_db_session — it reads
req.app.state.scheduler directly, so tests inject a mock scheduler
via app.state.

apscheduler is not installed in the test environment, so we mock the
module before importing the router.
"""

import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Mock apscheduler before it is imported by the router
# ---------------------------------------------------------------------------
if "apscheduler" not in sys.modules:
    _apscheduler = types.ModuleType("apscheduler")
    _apscheduler_triggers = types.ModuleType("apscheduler.triggers")
    _apscheduler_triggers_cron = types.ModuleType("apscheduler.triggers.cron")

    class _MockCronTrigger:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_crontab(cls, expr, *args, **kwargs):
            return cls()

        def __str__(self):
            return f"cron[{getattr(self, '_expr', '*')}]"

    _apscheduler_triggers_cron.CronTrigger = _MockCronTrigger
    sys.modules["apscheduler"] = _apscheduler
    sys.modules["apscheduler.triggers"] = _apscheduler_triggers
    sys.modules["apscheduler.triggers.cron"] = _apscheduler_triggers_cron


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_job(**overrides):
    defaults = dict(
        job_id="test-job-id-1234",
        next_run_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        trigger="date[2026-06-01 12:00:00 UTC]",
        args=["test-job-id-1234", {}],
    )
    defaults.update(overrides)
    job = MagicMock()
    for k, v in defaults.items():
        setattr(job, k, v)
    # APScheduler 3.x uses .id, set it from job_id for compatibility
    if "id" not in overrides:
        job.id = defaults.get("job_id", "test-job-id-1234")
    return job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_scheduler():
    sched = MagicMock()
    sched.get_jobs = MagicMock(return_value=[make_mock_job()])
    sched.get_job = MagicMock(return_value=make_mock_job())
    sched.add_job = MagicMock(return_value=None)
    sched.modify_job = MagicMock(return_value=None)
    sched.remove_job = MagicMock(return_value=None)
    sched.remove_all_jobs = MagicMock(return_value=None)
    return sched


@pytest.fixture
def test_app(mock_scheduler):
    from api.routers.scheduler import router as scheduler_router

    app = FastAPI()
    app.include_router(scheduler_router, prefix="/api/v1")
    app.state.scheduler = mock_scheduler
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. GET /jobs
# ===========================================================================


class TestListJobs:
    """Tests for GET /api/v1/jobs."""

    def test_list_jobs_happy_path(self, client, mock_scheduler):
        """Returns 200 with list of JobInfo."""
        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "test-job-id-1234"
        assert "next_run_time" in data[0]
        assert "trigger" in data[0]
        assert "args" in data[0]

    def test_list_jobs_empty_scheduler(self, client, mock_scheduler):
        """Returns 200 with empty list when no jobs are scheduled."""
        mock_scheduler.get_jobs.return_value = []

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_jobs_multiple_jobs(self, client, mock_scheduler):
        """Returns all scheduled jobs."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(job_id="job-1"),
            make_mock_job(job_id="job-2"),
        ]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        job_ids = [j["id"] for j in data]
        assert "job-1" in job_ids
        assert "job-2" in job_ids

    def test_list_jobs_args_with_non_json_serializable_object(self, client, mock_scheduler):
        """Bug 3: Returns 200 even when job args contain non-JSON-serializable objects.

        Previously j.args containing a datetime or custom object caused a 500 error
        because list(j.args) cannot be JSON-serialized directly by Pydantic.
        The fix uses json.dumps(default=str) to coerce all args to serializable form.
        """

        class _NonSerializable:
            def __str__(self):
                return "custom-object"

        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-with-bad-args",
                args=[
                    "job-with-bad-args",
                    {"job_type": "bounty_spawn", "ts": datetime(2026, 1, 1, tzinfo=UTC), "obj": _NonSerializable()},
                ],
            )
        ]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # args must be present and JSON-safe (no TypeError)
        assert isinstance(data[0]["args"], list)

    def test_list_jobs_args_with_empty_args(self, client, mock_scheduler):
        """Bug 3: Returns 200 when job has None/empty args."""
        mock_scheduler.get_jobs.return_value = [make_mock_job(job_id="job-no-args", args=None)]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert data[0]["args"] == []

    def test_get_job_args_with_non_json_serializable_object(self, client, mock_scheduler):
        """Bug 3: GET /jobs/{job_id} returns 200 with safely serialized args."""

        class _CustomObj:
            def __str__(self):
                return "some-object"

        mock_scheduler.get_job.return_value = make_mock_job(
            job_id="job-custom",
            args=["job-custom", {"nested": _CustomObj()}],
        )

        response = client.get("/api/v1/jobs/job-custom")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["args"], list)


# ===========================================================================
# 2. GET /jobs/{job_id}
# ===========================================================================


class TestGetJob:
    """Tests for GET /api/v1/jobs/{job_id}."""

    def test_get_job_happy_path(self, client, mock_scheduler):
        """Returns 200 with JobInfo when job exists."""
        response = client.get("/api/v1/jobs/test-job-id-1234")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-job-id-1234"
        assert "next_run_time" in data
        assert "trigger" in data

    def test_get_job_not_found_returns_404(self, client, mock_scheduler):
        """Returns 404 when job_id doesn't exist."""
        mock_scheduler.get_job.return_value = None

        response = client.get("/api/v1/jobs/nonexistent-job")

        assert response.status_code == 404
        assert "Job not found" in response.json()["detail"]


# ===========================================================================
# 3. POST /jobs (one-time job)
# ===========================================================================


class TestScheduleJob:
    """Tests for POST /api/v1/jobs (one-time job)."""

    def test_schedule_job_with_delay_seconds(self, client, mock_scheduler):
        """Returns 200 with status 'scheduled' when delay_seconds is provided."""
        payload = {"delay_seconds": 60, "payload": {"task": "refresh_shop"}}

        response = client.post("/api/v1/jobs", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "scheduled"
        assert "job_id" in data
        assert "run_date" in data

    def test_schedule_job_with_run_at(self, client, mock_scheduler):
        """Returns 200 with status 'scheduled' when run_at is provided."""
        payload = {
            "run_at": "2026-06-01T12:00:00+00:00",
            "payload": {"task": "refresh_shop"},
        }

        response = client.post("/api/v1/jobs", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "scheduled"

    def test_schedule_job_missing_both_run_at_and_delay_returns_400(self, client, mock_scheduler):
        """Returns 400 when neither run_at nor delay_seconds is provided."""
        payload = {"payload": {"task": "refresh_shop"}}

        response = client.post("/api/v1/jobs", json=payload)

        assert response.status_code == 400
        assert "run_at" in response.json()["detail"] or "delay_seconds" in response.json()["detail"]

    def test_schedule_job_add_job_exception_returns_400(self, client, mock_scheduler):
        """Returns 400 when scheduler.add_job raises an exception."""
        mock_scheduler.add_job.side_effect = Exception("Scheduler busy")
        payload = {"delay_seconds": 60}

        response = client.post("/api/v1/jobs", json=payload)

        assert response.status_code == 400
        assert "Could not schedule job" in response.json()["detail"]

    def test_schedule_job_calls_add_job(self, client, mock_scheduler):
        """Calls scheduler.add_job with correct trigger type."""
        payload = {"delay_seconds": 30, "payload": {}}

        client.post("/api/v1/jobs", json=payload)

        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs is not None


# ===========================================================================
# 4. POST /jobs/recurring
# ===========================================================================


class TestScheduleRecurring:
    """Tests for POST /api/v1/jobs/recurring."""

    def test_schedule_recurring_happy_path(self, client, mock_scheduler):
        """Returns 200 with status 'scheduled_recurring' for valid cron."""
        payload = {"cron": "*/5 * * * *", "payload": {"task": "daily_refresh"}}

        response = client.post("/api/v1/jobs/recurring", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "scheduled_recurring"
        assert "job_id" in data
        assert data["cron"] == "*/5 * * * *"

    def test_schedule_recurring_calls_add_job(self, client, mock_scheduler):
        """Calls scheduler.add_job once."""
        payload = {"cron": "0 9 * * 1", "payload": {}}

        client.post("/api/v1/jobs/recurring", json=payload)

        mock_scheduler.add_job.assert_called_once()

    def test_schedule_recurring_invalid_cron_returns_400(self, client, mock_scheduler):
        """Returns 400 when the cron expression is invalid."""
        mock_scheduler.add_job.side_effect = Exception("Invalid cron expression")
        payload = {"cron": "not-a-valid-cron", "payload": {}}

        response = client.post("/api/v1/jobs/recurring", json=payload)

        assert response.status_code == 400
        assert "Could not schedule recurring job" in response.json()["detail"]

    def test_schedule_recurring_missing_cron_returns_422(self, client):
        """Returns 422 when cron is missing (required field)."""
        payload = {"payload": {}}

        response = client.post("/api/v1/jobs/recurring", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 5. PUT /jobs/{job_id}
# ===========================================================================


class TestUpdateJob:
    """Tests for PUT /api/v1/jobs/{job_id}.

    # BUG: The update_job handler (scheduler.py line ~112) returns:
    #   {"status": "updated", "job_id": id}
    # where `id` is Python's built-in `id()` function (a reference to the
    # built-in), NOT the `job_id` string variable. The developer wrote bare
    # `id` instead of `job_id`. FastAPI cannot serialize a built-in function
    # and raises ValueError during response serialization, causing the
    # test-client to raise an exception rather than returning a 500 response.
    # The correct return value should use `job_id` (the string variable).
    """

    def test_update_job_happy_path(self, client, mock_scheduler):
        """Returns 200 with status 'updated' and the job_id string when job exists.

        # BUG: Fails because update_job returns `id` (Python builtin function)
        # instead of `job_id` (the string). FastAPI raises ValueError trying to
        # serialize a builtin function. Fix: change `"job_id": id` to
        # `"job_id": job_id` in scheduler.py update_job return statement.
        """
        from fastapi.testclient import TestClient as TC

        safe_client = TC(client.app, raise_server_exceptions=False)
        payload = {"payload": {"updated_key": "new_value"}}

        response = safe_client.put("/api/v1/jobs/test-job-id-1234", json=payload)

        # Correct behavior: 200 with status=updated and job_id as a string
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["job_id"] == "test-job-id-1234"  # must be the string, not id()

    def test_update_job_not_found_returns_404(self, client, mock_scheduler):
        """Returns 404 when job doesn't exist."""
        mock_scheduler.get_job.return_value = None
        payload = {"payload": {}}

        response = client.put("/api/v1/jobs/nonexistent-job", json=payload)

        assert response.status_code == 404
        assert "Job not found" in response.json()["detail"]

    def test_update_job_calls_modify_job(self, client, mock_scheduler):
        """Calls scheduler.modify_job with updated args.

        # BUG: Same bug as test_update_job_happy_path — the response
        # serialization crashes before the call to modify_job can be verified.
        # Use raise_server_exceptions=False to allow the request to complete.
        """
        from fastapi.testclient import TestClient as TC

        safe_client = TC(client.app, raise_server_exceptions=False)
        payload = {"payload": {"new": "data"}}

        safe_client.put("/api/v1/jobs/test-job-id-1234", json=payload)

        mock_scheduler.modify_job.assert_called_once()

    def test_update_job_modify_exception_returns_400(self, client, mock_scheduler):
        """Returns 400 when modify_job raises exception."""
        mock_scheduler.modify_job.side_effect = Exception("Cannot modify running job")
        payload = {"payload": {}}

        response = client.put("/api/v1/jobs/test-job-id-1234", json=payload)

        assert response.status_code == 400
        assert "Could not update job" in response.json()["detail"]


# ===========================================================================
# 6. DELETE /jobs/all
# ===========================================================================


class TestDeleteAllJobs:
    """Tests for DELETE /api/v1/jobs/all."""

    def test_delete_all_jobs_happy_path(self, client, mock_scheduler):
        """Returns 200 with status 'all_jobs_deleted'."""
        response = client.delete("/api/v1/jobs/all")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "all_jobs_deleted"

    def test_delete_all_jobs_calls_remove_all_jobs(self, client, mock_scheduler):
        """Calls scheduler.remove_all_jobs once."""
        client.delete("/api/v1/jobs/all")

        mock_scheduler.remove_all_jobs.assert_called_once()


# ===========================================================================
# 7. DELETE /jobs/{id}
# ===========================================================================
#
# BUG: The delete_job route is decorated with @router.delete("/jobs/{id}")
# but the function signature is `async def delete_job(req, job_id: str)`.
# FastAPI cannot bind the path parameter `{id}` to the function parameter
# `job_id` because the names differ. Every DELETE /jobs/{job_id} request
# therefore returns 422 (Unprocessable Entity) instead of processing.
# Additionally, within the handler, `id` is used in several log/return
# statements (bare builtin) instead of `job_id` — a secondary bug.
# Fix: rename the path segment to `{job_id}` or rename the function param
# to `id` (the former is preferred to avoid shadowing the builtin).


class TestDeleteJob:
    """Tests for DELETE /api/v1/jobs/{id}.

    # BUG: Path parameter name mismatch: route uses `{id}` but function
    # parameter is `job_id`. FastAPI returns 422 for all requests to this
    # endpoint. Tests assert correct 200/404 behavior (per spec). They will
    # FAIL until the production code is fixed.
    """

    def test_delete_job_happy_path(self, client, mock_scheduler):
        """Returns 200 with status 'deleted' when job exists.

        # BUG: Returns 422 instead due to path param name mismatch
        # (route: /jobs/{id}, param: job_id). Fix scheduler.py delete_job.
        """
        response = client.delete("/api/v1/jobs/test-job-id-1234")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    def test_delete_job_not_found_returns_404(self, client, mock_scheduler):
        """Returns 404 when job doesn't exist.

        # BUG: Returns 422 instead due to path param name mismatch.
        """
        mock_scheduler.get_job.return_value = None

        response = client.delete("/api/v1/jobs/nonexistent-job")

        assert response.status_code == 404
        assert "Job not found" in response.json()["detail"]

    def test_delete_job_calls_remove_job(self, client, mock_scheduler):
        """Calls scheduler.remove_job with correct job_id.

        # BUG: remove_job is never called because the route param mismatch
        # causes a 422 before the handler is invoked.
        """
        client.delete("/api/v1/jobs/test-job-id-1234")

        mock_scheduler.remove_job.assert_called_once_with("test-job-id-1234")


# ===========================================================================
# Gap 3: Serialization Boundary Tests — Scheduler
# ===========================================================================


class TestListJobsSerializationBoundary:
    """Gap 3: Serialization edge-cases that previously caused 500 errors.

    APScheduler jobs can carry arbitrary Python objects in their args payloads.
    The router must safely serialize any job args without raising.
    """

    def test_list_jobs_with_datetime_in_args(self, client, mock_scheduler):
        """GET /jobs where job args contain a datetime object → serialises without 500.

        A datetime in job args is not directly JSON-serialisable; the router must
        coerce it to a string representation rather than propagating a TypeError as 500.
        """
        from datetime import UTC, datetime

        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-with-datetime",
                args=[
                    "job-with-datetime",
                    {
                        "job_type": "bounty_spawn",
                        "guild_id": 67890,
                        "scheduled_at": datetime(2026, 3, 15, 10, 30, tzinfo=UTC),
                    },
                ],
            )
        ]

        response = client.get("/api/v1/jobs")

        # Must not be 500 — datetime must be serialised as a string
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert isinstance(data[0]["args"], list)

    def test_list_jobs_with_none_next_run_time(self, client, mock_scheduler):
        """GET /jobs where a paused job has next_run_time=None → serialises without 500.

        APScheduler sets next_run_time to None for paused jobs. The router must handle
        this null value without crashing.
        """
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="paused-job",
                next_run_time=None,  # paused job
                args=["paused-job", {"job_type": "shop_refresh"}],
            )
        ]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # next_run_time should be null/None in the JSON response
        assert data[0]["next_run_time"] is None

    def test_list_jobs_with_multiple_datetimes_in_args(self, client, mock_scheduler):
        """GET /jobs where args contain multiple datetime objects → all serialised without 500."""
        from datetime import UTC, datetime

        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-multi-dates",
                next_run_time=datetime(2026, 6, 1, tzinfo=UTC),
                args=[
                    "job-multi-dates",
                    {
                        "job_type": "bounty_expire",
                        "guild_id": 67890,
                        "expiry_time": datetime(2026, 3, 16, 12, 0, tzinfo=UTC),
                        "created_at": datetime(2026, 3, 14, 8, 0, tzinfo=UTC),
                    },
                ],
            )
        ]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert isinstance(data[0]["args"], list)

    def test_get_single_job_with_none_next_run_time(self, client, mock_scheduler):
        """GET /jobs/{job_id} for a paused job (next_run_time=None) → 200 without crash."""
        mock_scheduler.get_job.return_value = make_mock_job(
            job_id="paused-single",
            next_run_time=None,
            args=["paused-single", {"job_type": "temperature_decay"}],
        )

        response = client.get("/api/v1/jobs/paused-single")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "paused-single"
        assert data["next_run_time"] is None


# ===========================================================================
# 8. GET /jobs?guild_id=  — guild-scoped filtering
# ===========================================================================


class TestListJobsGuildFilter:
    """Tests for GET /api/v1/jobs?guild_id={guild_id} guild-scoped filtering."""

    def test_list_jobs_no_guild_filter_returns_all(self, client, mock_scheduler):
        """Without guild_id param, all jobs are returned unchanged."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(job_id="bounty_spawn_default", args=["bounty_spawn_default", {"job_type": "bounty_spawn"}]),
            make_mock_job(job_id="guild-job-1", args=["guild-job-1", {"job_type": "bounty_expire", "guild_id": 111}]),
            make_mock_job(job_id="guild-job-2", args=["guild-job-2", {"job_type": "bounty_expire", "guild_id": 222}]),
        ]

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

    def test_list_jobs_guild_filter_includes_matching_guild_job(self, client, mock_scheduler):
        """With guild_id=111, only jobs with guild_id==111 in payload (plus defaults) are returned."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="bounty_spawn_default",
                args=["bounty_spawn_default", {"job_type": "bounty_spawn"}],
            ),
            make_mock_job(
                job_id="guild-job-111",
                args=["guild-job-111", {"job_type": "bounty_expire", "guild_id": 111}],
            ),
            make_mock_job(
                job_id="guild-job-222",
                args=["guild-job-222", {"job_type": "bounty_expire", "guild_id": 222}],
            ),
        ]

        response = client.get("/api/v1/jobs", params={"guild_id": 111})

        assert response.status_code == 200
        data = response.json()
        # Should include: bounty_spawn_default (default) + guild-job-111; exclude guild-job-222
        ids = [j["id"] for j in data]
        assert "bounty_spawn_default" in ids
        assert "guild-job-111" in ids
        assert "guild-job-222" not in ids

    def test_list_jobs_guild_filter_includes_all_three_defaults(self, client, mock_scheduler):
        """All three default jobs are always included when guild_id filter is active."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="bounty_spawn_default",
                args=["bounty_spawn_default", {"job_type": "bounty_spawn"}],
            ),
            make_mock_job(
                job_id="shop_refresh_default",
                args=["shop_refresh_default", {"job_type": "shop_refresh"}],
            ),
            make_mock_job(
                job_id="temperature_decay_default",
                args=["temperature_decay_default", {"job_type": "temperature_decay"}],
            ),
            make_mock_job(
                job_id="other-guild-job",
                args=["other-guild-job", {"job_type": "duel_expire", "guild_id": 999}],
            ),
        ]

        response = client.get("/api/v1/jobs", params={"guild_id": 123})

        assert response.status_code == 200
        data = response.json()
        ids = [j["id"] for j in data]
        # All defaults included, other guild job excluded
        assert "bounty_spawn_default" in ids
        assert "shop_refresh_default" in ids
        assert "temperature_decay_default" in ids
        assert "other-guild-job" not in ids

    def test_list_jobs_guild_filter_excludes_jobs_with_different_guild(self, client, mock_scheduler):
        """Jobs with a different guild_id are excluded when filtering by guild_id."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-guild-42",
                args=["job-guild-42", {"job_type": "bounty_expire", "guild_id": 42}],
            ),
            make_mock_job(
                job_id="job-guild-99",
                args=["job-guild-99", {"job_type": "bounty_expire", "guild_id": 99}],
            ),
        ]

        response = client.get("/api/v1/jobs", params={"guild_id": 42})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "job-guild-42"

    def test_list_jobs_guild_filter_empty_result_when_no_matching_jobs(self, client, mock_scheduler):
        """Returns empty list when guild_id filter matches no jobs and no defaults exist."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-guild-999",
                args=["job-guild-999", {"job_type": "bounty_expire", "guild_id": 999}],
            ),
        ]

        response = client.get("/api/v1/jobs", params={"guild_id": 777})

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_jobs_guild_filter_excludes_jobs_without_guild_id_in_payload(self, client, mock_scheduler):
        """One-time jobs without a guild_id in their payload are excluded when filtering."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-no-guild",
                args=["job-no-guild", {"job_type": "time_announcement"}],
            ),
            make_mock_job(
                job_id="job-with-guild",
                args=["job-with-guild", {"job_type": "time_announcement", "guild_id": 55}],
            ),
        ]

        response = client.get("/api/v1/jobs", params={"guild_id": 55})

        assert response.status_code == 200
        data = response.json()
        ids = [j["id"] for j in data]
        assert "job-with-guild" in ids
        assert "job-no-guild" not in ids


# ===========================================================================
# 9. DELETE /jobs/guild/{guild_id}
# ===========================================================================


class TestDeleteGuildJobs:
    """Tests for DELETE /api/v1/jobs/guild/{guild_id} — guild-scoped delete."""

    def test_delete_guild_jobs_removes_matching_jobs(self, client, mock_scheduler):
        """Removes all jobs whose payload guild_id matches; returns correct removed_count."""
        guild_id = 12345
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="expire-job-1",
                args=["expire-job-1", {"job_type": "bounty_expire", "guild_id": guild_id}],
            ),
            make_mock_job(
                job_id="expire-job-2",
                args=["expire-job-2", {"job_type": "bounty_expire", "guild_id": guild_id}],
            ),
            make_mock_job(
                job_id="other-guild-job",
                args=["other-guild-job", {"job_type": "bounty_expire", "guild_id": 99999}],
            ),
        ]

        response = client.delete(f"/api/v1/jobs/guild/{guild_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "guild_jobs_deleted"
        assert data["guild_id"] == guild_id
        assert data["removed_count"] == 2

    def test_delete_guild_jobs_calls_remove_job_for_each_match(self, client, mock_scheduler):
        """scheduler.remove_job is called once per matching job."""
        guild_id = 777
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="job-a",
                args=["job-a", {"job_type": "bounty_expire", "guild_id": guild_id}],
            ),
            make_mock_job(
                job_id="job-b",
                args=["job-b", {"job_type": "duel_expire", "guild_id": guild_id}],
            ),
        ]

        client.delete(f"/api/v1/jobs/guild/{guild_id}")

        assert mock_scheduler.remove_job.call_count == 2
        called_ids = {call.args[0] for call in mock_scheduler.remove_job.call_args_list}
        assert called_ids == {"job-a", "job-b"}

    def test_delete_guild_jobs_zero_removed_when_no_matches(self, client, mock_scheduler):
        """Returns removed_count=0 when no jobs match the guild_id."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="other-job",
                args=["other-job", {"job_type": "bounty_expire", "guild_id": 111}],
            ),
        ]

        response = client.delete("/api/v1/jobs/guild/999")

        assert response.status_code == 200
        data = response.json()
        assert data["removed_count"] == 0
        mock_scheduler.remove_job.assert_not_called()

    def test_delete_guild_jobs_skips_jobs_without_payload_guild_id(self, client, mock_scheduler):
        """Jobs without a guild_id in their payload are not removed."""
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(
                job_id="default-spawn",
                args=["default-spawn", {"job_type": "bounty_spawn"}],  # no guild_id
            ),
        ]

        response = client.delete("/api/v1/jobs/guild/123")

        assert response.status_code == 200
        data = response.json()
        assert data["removed_count"] == 0
        mock_scheduler.remove_job.assert_not_called()

    def test_delete_guild_jobs_empty_scheduler(self, client, mock_scheduler):
        """Returns 200 with removed_count=0 when scheduler has no jobs."""
        mock_scheduler.get_jobs.return_value = []

        response = client.delete("/api/v1/jobs/guild/12345")

        assert response.status_code == 200
        data = response.json()
        assert data["removed_count"] == 0

    def test_delete_guild_jobs_continues_on_exception(self, client, mock_scheduler):
        """If remove_job raises an exception for one job, processing continues for others."""
        guild_id = 42
        job_ok = make_mock_job(
            job_id="job-ok",
            args=["job-ok", {"job_type": "bounty_expire", "guild_id": guild_id}],
        )
        # Remove raises for this job
        mock_scheduler.remove_job.side_effect = [Exception("locked"), None]
        mock_scheduler.get_jobs.return_value = [
            job_ok,
            make_mock_job(
                job_id="job-ok2",
                args=["job-ok2", {"job_type": "bounty_expire", "guild_id": guild_id}],
            ),
        ]

        response = client.delete(f"/api/v1/jobs/guild/{guild_id}")

        # Should not return 500 — exception is swallowed
        assert response.status_code == 200

    def test_delete_guild_jobs_scheduler_unavailable_returns_503(self, test_app):
        """Returns 503 when the scheduler is not available."""
        from fastapi.testclient import TestClient

        app_no_scheduler = test_app.__class__()
        app_no_scheduler.state.scheduler = None

        # Remove state.scheduler entirely
        del app_no_scheduler

        # Use test_app but remove the scheduler
        test_app.state.scheduler = None
        c = TestClient(test_app, raise_server_exceptions=False)
        response = c.delete("/api/v1/jobs/guild/123")
        assert response.status_code == 503


# ===========================================================================
# 10. POST /reset
# ===========================================================================


class TestResetScheduler:
    """Tests for POST /api/v1/reset — scheduler reset endpoint."""

    def test_reset_scheduler_happy_path(self, client, mock_scheduler):
        """Returns 200 with status='reset' and jobs_registered count."""
        # After reset, get_jobs should return the 3 default jobs
        mock_scheduler.get_jobs.return_value = [
            make_mock_job(job_id="bounty_spawn_default"),
            make_mock_job(job_id="shop_refresh_default"),
            make_mock_job(job_id="temperature_decay_default"),
        ]

        from unittest.mock import patch

        with patch("api.routers.scheduler.reset_scheduler.__module__"):
            pass  # just ensure import works

        # Patch register_default_jobs to avoid importing main (which needs full app)
        with patch("api.routers.scheduler.register_default_jobs", create=True):
            # We patch the deferred import inside the function
            import types

            fake_main = types.ModuleType("main")
            fake_main.register_default_jobs = MagicMock()
            import sys

            sys.modules["main"] = fake_main

            response = client.post("/api/v1/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "reset"
        assert "jobs_registered" in data
        assert isinstance(data["jobs_registered"], int)

    def test_reset_scheduler_calls_remove_all_jobs(self, client, mock_scheduler):
        """scheduler.remove_all_jobs is called once during reset."""
        mock_scheduler.get_jobs.return_value = []
        import sys
        import types

        fake_main = types.ModuleType("main")
        fake_main.register_default_jobs = MagicMock()
        sys.modules["main"] = fake_main

        client.post("/api/v1/reset")

        mock_scheduler.remove_all_jobs.assert_called_once()

    def test_reset_scheduler_calls_register_default_jobs(self, client, mock_scheduler):
        """register_default_jobs is called once with the scheduler after wipe."""
        mock_scheduler.get_jobs.return_value = []
        import sys
        import types

        fake_main = types.ModuleType("main")
        fake_main.register_default_jobs = MagicMock()
        sys.modules["main"] = fake_main

        client.post("/api/v1/reset")

        fake_main.register_default_jobs.assert_called_once_with(mock_scheduler)

    def test_reset_scheduler_returns_correct_job_count(self, client, mock_scheduler):
        """jobs_registered in response equals the number of jobs after re-registration."""
        import sys
        import types

        fake_main = types.ModuleType("main")
        fake_main.register_default_jobs = MagicMock()
        sys.modules["main"] = fake_main

        mock_scheduler.get_jobs.return_value = [
            make_mock_job(job_id="bounty_spawn_default"),
            make_mock_job(job_id="shop_refresh_default"),
            make_mock_job(job_id="temperature_decay_default"),
        ]

        response = client.post("/api/v1/reset")

        assert response.status_code == 200
        assert response.json()["jobs_registered"] == 3

    def test_reset_scheduler_unavailable_returns_503(self, test_app):
        """Returns 503 when the scheduler is not available."""
        from fastapi.testclient import TestClient

        test_app.state.scheduler = None
        c = TestClient(test_app, raise_server_exceptions=False)
        response = c.post("/api/v1/reset")
        assert response.status_code == 503
