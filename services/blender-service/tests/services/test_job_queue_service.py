"""
Unit tests for JobQueueService.

Tests the async job queue, job lifecycle, concurrency limiting, expiry,
and cleanup behaviours.  Each test uses at most 2 mocks.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from services.job_queue_service import JobQueueService, JobStatus, RenderJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_coro() -> str:
    """A coroutine that succeeds and returns a dummy path."""
    return "/tmp/render_output.png"


async def _failing_coro() -> str:
    """A coroutine that always raises."""
    raise RuntimeError("simulated render failure")


# ---------------------------------------------------------------------------
# RenderJob dataclass tests
# ---------------------------------------------------------------------------


def test_create_job_returns_job() -> None:
    """create_job() returns a RenderJob with the correct attributes."""
    svc = JobQueueService()
    job = svc.create_job(model_path="/models/ship.obj", res_x=1920, res_y=1080, num_samples=64)

    assert isinstance(job, RenderJob)
    assert job.model_path == "/models/ship.obj"
    assert job.res_x == 1920
    assert job.res_y == 1080
    assert job.num_samples == 64
    assert job.status == JobStatus.QUEUED
    assert job.job_id  # non-empty string


def test_create_job_unique_ids() -> None:
    """Multiple create_job() calls produce different job IDs."""
    svc = JobQueueService()
    ids = {svc.create_job("/m.obj", 1920, 1080, 64).job_id for _ in range(10)}
    assert len(ids) == 10


def test_get_job_found() -> None:
    """get_job() returns the job when it exists and has not expired."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)
    found = svc.get_job(job.job_id)
    assert found is job


def test_get_job_not_found() -> None:
    """get_job() returns None for an unknown job_id."""
    svc = JobQueueService()
    assert svc.get_job("nonexistent") is None


def test_job_to_dict_complete() -> None:
    """to_dict() includes all expected keys with correct types."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1280, 720, 32)
    d = job.to_dict()

    expected_keys = {
        "job_id", "status", "created_at", "started_at", "completed_at",
        "result_path", "error_message", "model_path", "res_x", "res_y", "num_samples",
    }
    assert expected_keys == set(d.keys())
    assert d["status"] == "queued"
    assert d["model_path"] == "/m.obj"
    assert d["res_x"] == 1280
    assert d["res_y"] == 720
    assert d["num_samples"] == 32
    assert d["started_at"] is None
    assert d["completed_at"] is None
    assert d["result_path"] is None
    assert d["error_message"] is None


def test_job_status_enum() -> None:
    """JobStatus enum has QUEUED, PROCESSING, COMPLETE, FAILED with string values."""
    assert JobStatus.QUEUED.value == "queued"
    assert JobStatus.PROCESSING.value == "processing"
    assert JobStatus.COMPLETE.value == "complete"
    assert JobStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Async processing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_job_processes() -> None:
    """submit_job() with a successful coroutine transitions job to COMPLETE."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)

    mock_coro = AsyncMock(return_value="/tmp/result.png")
    await svc.submit_job(job, mock_coro())

    # Allow the background task to run
    await asyncio.sleep(0.1)

    assert job.status == JobStatus.COMPLETE
    assert job.result_path == "/tmp/result.png"
    assert job.started_at is not None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_submit_job_handles_failure() -> None:
    """submit_job() with a failing coroutine transitions job to FAILED."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)

    mock_coro = AsyncMock(side_effect=RuntimeError("blender exploded"))
    await svc.submit_job(job, mock_coro())

    await asyncio.sleep(0.1)

    assert job.status == JobStatus.FAILED
    assert "blender exploded" in job.error_message
    assert job.completed_at is not None


# ---------------------------------------------------------------------------
# Expiry and cleanup tests
# ---------------------------------------------------------------------------


def test_expired_job_cleaned_up() -> None:
    """A job with completed_at > 1 hour ago is treated as expired and removed."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)
    job.status = JobStatus.COMPLETE
    # Force the job to be expired by backdating completed_at
    job.completed_at = datetime.now(UTC) - timedelta(hours=2)

    assert job.is_expired is True

    # get_job() should treat it as not found and clean up
    result = svc.get_job(job.job_id)
    assert result is None
    # Job should have been removed from internal dict
    assert job.job_id not in svc._jobs


def test_list_jobs_excludes_expired() -> None:
    """list_jobs() filters out expired jobs."""
    svc = JobQueueService()

    active_job = svc.create_job("/active.obj", 1920, 1080, 64)
    expired_job = svc.create_job("/expired.obj", 1920, 1080, 64)
    expired_job.status = JobStatus.COMPLETE
    expired_job.completed_at = datetime.now(UTC) - timedelta(hours=2)

    jobs = svc.list_jobs()
    job_ids = [j["job_id"] for j in jobs]

    assert active_job.job_id in job_ids
    assert expired_job.job_id not in job_ids


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency() -> None:
    """Only max_concurrent jobs run at the same time."""
    max_concurrent = 2
    svc = JobQueueService(max_concurrent=max_concurrent)

    # Track how many tasks are running simultaneously
    concurrent_count = 0
    max_seen = 0

    async def slow_render():
        nonlocal concurrent_count, max_seen
        concurrent_count += 1
        max_seen = max(max_seen, concurrent_count)
        await asyncio.sleep(0.05)
        concurrent_count -= 1
        return "/tmp/out.png"

    jobs = []
    for _ in range(5):
        job = svc.create_job("/m.obj", 1920, 1080, 64)
        jobs.append(job)
        await svc.submit_job(job, slow_render())

    # Wait for all to finish
    await asyncio.sleep(0.5)

    # All jobs should be complete
    for job in jobs:
        assert job.status == JobStatus.COMPLETE

    # The semaphore should have prevented more than max_concurrent from running at once
    assert max_seen <= max_concurrent


# ---------------------------------------------------------------------------
# P0 fix (J1): Task reference stored — GC prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_job_stores_task_reference() -> None:
    """submit_job() keeps the asyncio.Task in _active_tasks until done (no GC)."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)

    # Use a slow coroutine so we can inspect _active_tasks before it finishes.
    async def slow_render():
        await asyncio.sleep(0.05)
        return "/tmp/result.png"

    await svc.submit_job(job, slow_render())

    # Task must be tracked immediately after submission.
    assert len(svc._active_tasks) == 1

    # After completion the done-callback should remove it.
    await asyncio.sleep(0.2)
    assert len(svc._active_tasks) == 0


# ---------------------------------------------------------------------------
# P1 fix (J2): Queue depth limit
# ---------------------------------------------------------------------------


def test_create_job_queue_full_raises() -> None:
    """create_job() raises ValueError when max_queue_size active jobs exist."""
    svc = JobQueueService(max_queue_size=2)

    # Fill the queue with QUEUED jobs (they count toward active).
    svc.create_job("/m.obj", 1920, 1080, 64)
    svc.create_job("/m.obj", 1920, 1080, 64)

    with pytest.raises(ValueError, match="queue full"):
        svc.create_job("/m.obj", 1920, 1080, 64)


def test_create_job_completed_jobs_dont_count_toward_limit() -> None:
    """Completed/failed jobs don't count toward the active-job limit."""
    svc = JobQueueService(max_queue_size=1)

    # Create one job and mark it complete.
    job = svc.create_job("/m.obj", 1920, 1080, 64)
    job.status = JobStatus.COMPLETE
    job.completed_at = datetime.now(UTC)

    # Queue should now accept another job because no active jobs remain.
    new_job = svc.create_job("/m.obj", 1920, 1080, 64)
    assert new_job.status == JobStatus.QUEUED


# ---------------------------------------------------------------------------
# P1 fix (J3): Stuck-job detection
# ---------------------------------------------------------------------------


def test_stuck_processing_job_marked_failed() -> None:
    """A PROCESSING job started more than 30 minutes ago is marked FAILED by is_expired."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)
    job.status = JobStatus.PROCESSING
    # Backdate started_at to simulate a stuck job.
    job.started_at = datetime.now(UTC) - timedelta(minutes=31)

    # Accessing is_expired triggers the stuck-job detection.
    result = job.is_expired
    assert result is False  # not expired yet — just marked failed
    assert job.status == JobStatus.FAILED
    assert job.completed_at is not None
    assert "timed out" in (job.error_message or "").lower()


def test_recent_processing_job_not_marked_failed() -> None:
    """A PROCESSING job within the 30-minute window is left untouched."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)
    job.status = JobStatus.PROCESSING
    job.started_at = datetime.now(UTC) - timedelta(minutes=5)

    result = job.is_expired
    assert result is False
    assert job.status == JobStatus.PROCESSING  # not changed


# ---------------------------------------------------------------------------
# Shutdown cancels active tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_cancels_active_tasks() -> None:
    """shutdown() cancels all tasks currently tracked in _active_tasks."""
    svc = JobQueueService()
    job = svc.create_job("/m.obj", 1920, 1080, 64)

    render_started = asyncio.Event()

    async def long_render():
        render_started.set()
        await asyncio.sleep(10)  # will be cancelled
        return "/tmp/result.png"

    await svc.submit_job(job, long_render())
    # Wait until the render coroutine has actually started so the task is in-flight.
    await render_started.wait()
    assert len(svc._active_tasks) == 1

    # Capture the task before shutdown clears the set.
    tasks_before = list(svc._active_tasks)
    svc.shutdown()

    # Allow cancellation to propagate; suppress CancelledError.
    await asyncio.gather(*tasks_before, return_exceptions=True)
    assert len(svc._active_tasks) == 0
