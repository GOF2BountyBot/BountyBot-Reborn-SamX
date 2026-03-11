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
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
        next_run_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        trigger="date[2026-06-01 12:00:00 UTC]",
        args=["test-job-id-1234", {}],
    )
    defaults.update(overrides)
    job = MagicMock()
    for k, v in defaults.items():
        setattr(job, k, v)
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
