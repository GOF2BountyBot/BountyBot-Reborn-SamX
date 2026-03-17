"""Async render job queue with in-memory state tracking.

Manages render jobs as background tasks with status polling.  Jobs are
stored in an in-memory dict (no persistence — jobs lost on restart).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from shared import bblogger

flogger = bblogger.get_logger("blender-job-queue-service")


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class RenderJob:
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_path: str | None = None
    error_message: str | None = None
    # Render parameters (stored for reference)
    model_path: str = ""
    res_x: int = 1920
    res_y: int = 1080
    num_samples: int = 64

    @property
    def is_expired(self) -> bool:
        """Jobs expire after TTL (1 hour after completion, or 30 min if stuck processing)."""
        now = datetime.now(UTC)
        if self.completed_at:
            return now - self.completed_at > timedelta(hours=1)
        # Stuck-job detection: processing jobs that exceed 30 minutes are considered failed
        if self.status == JobStatus.PROCESSING and self.started_at and now - self.started_at > timedelta(minutes=30):
            old_status = self.status
            self.status = JobStatus.FAILED
            self.error_message = "Job timed out (exceeded 30 minute processing limit)"
            self.completed_at = now
            flogger.debug(f"Job {self.job_id} transitioned from {old_status} to FAILED (timeout)")
            return False  # Don't expire yet — mark failed and let next cycle clean up
        return False

    def to_dict(self) -> dict:
        """Serialize to API response dict."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result_path": self.result_path,
            "error_message": self.error_message,
            "model_path": self.model_path,
            "res_x": self.res_x,
            "res_y": self.res_y,
            "num_samples": self.num_samples,
        }


class JobQueueService:
    """In-memory async job queue for render tasks."""

    def __init__(self, max_concurrent: int = 2, max_queue_size: int = 100):
        self._jobs: dict[str, RenderJob] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._cleanup_task: asyncio.Task | None = None
        self._active_tasks: set[asyncio.Task] = set()
        flogger.info(f"JobQueueService initialized (max_concurrent={max_concurrent}, "
                     f"max_queue_size={max_queue_size})")

    def create_job(self, model_path: str, res_x: int, res_y: int, num_samples: int) -> RenderJob:
        """Create a new job and return it. Does NOT start processing.

        Raises ValueError if the queue is full (max_queue_size exceeded).
        """
        active_count = sum(1 for j in self._jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.PROCESSING))
        if active_count >= self._max_queue_size:
            raise ValueError(f"Job queue full ({self._max_queue_size} active jobs). Try again later.")
        job_id = str(uuid.uuid4())[:8]
        job = RenderJob(
            job_id=job_id,
            model_path=model_path,
            res_x=res_x,
            res_y=res_y,
            num_samples=num_samples,
        )
        self._jobs[job_id] = job
        flogger.info(f"Job {job_id} created: model={model_path}, res={res_x}x{res_y}, samples={num_samples}")
        return job

    def get_job(self, job_id: str) -> RenderJob | None:
        """Get job by ID. Returns None if not found or expired."""
        flogger.trace(f"get_job() called for job_id={job_id}")
        job = self._jobs.get(job_id)
        if job and job.is_expired:
            flogger.debug(f"Job {job_id} has expired, removing it")
            self._cleanup_job(job_id)
            return None
        return job

    def list_jobs(self) -> list[dict]:
        """List all non-expired jobs."""
        flogger.trace(f"list_jobs() called, {len(self._jobs)} total jobs in queue")
        self._cleanup_expired()
        jobs_list = [job.to_dict() for job in self._jobs.values()]
        flogger.debug(f"Returning {len(jobs_list)} non-expired jobs")
        return jobs_list

    async def submit_job(self, job: RenderJob, render_coro) -> None:
        """Submit a job for processing. Runs in background with semaphore control."""
        flogger.debug(f"Submitting job {job.job_id} for async processing")
        task = asyncio.create_task(self._process_job(job, render_coro))
        # Store reference to prevent garbage collection of the background task.
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        flogger.debug(f"Job {job.job_id} submitted as background task, active tasks: {len(self._active_tasks)}")

    async def _process_job(self, job: RenderJob, render_coro) -> None:
        """Process a single render job with concurrency limiting."""
        async with self._semaphore:
            old_status = job.status
            job.status = JobStatus.PROCESSING
            job.started_at = datetime.now(UTC)
            flogger.debug(f"Job {job.job_id} transitioned from {old_status} to PROCESSING")
            flogger.info(f"Job {job.job_id} started processing")
            try:
                result_path = await render_coro
                job.status = JobStatus.COMPLETE
                job.result_path = str(result_path)
                flogger.debug(f"Job {job.job_id} transitioned to COMPLETE")
                flogger.info(f"Job {job.job_id} completed successfully: {result_path}")
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                flogger.debug(f"Job {job.job_id} transitioned to FAILED")
                flogger.error(f"Job {job.job_id} failed: {e}")
            finally:
                job.completed_at = datetime.now(UTC)

    def _cleanup_job(self, job_id: str) -> None:
        """Remove a job and its result file."""
        job = self._jobs.pop(job_id, None)
        if job:
            if job.result_path:
                try:
                    Path(job.result_path).unlink(missing_ok=True)
                    flogger.debug(f"Cleaned up result file for job {job_id}: {job.result_path}")
                except Exception as e:
                    flogger.error(f"Failed to delete result file for job {job_id}: {e}")
            flogger.debug(f"Job {job_id} removed from queue (status was {job.status})")

    def _cleanup_expired(self) -> None:
        """Remove all expired jobs."""
        flogger.trace(f"_cleanup_expired() called with {len(self._jobs)} jobs in queue")
        expired = [jid for jid, j in self._jobs.items() if j.is_expired]
        if expired:
            flogger.debug(f"Cleaning up {len(expired)} expired jobs: {expired}")
            for jid in expired:
                self._cleanup_job(jid)
        else:
            flogger.trace("No expired jobs to clean up")

    async def start_cleanup_loop(self, interval_seconds: int = 300) -> None:
        """Start periodic cleanup of expired jobs."""
        flogger.info(f"Cleanup loop started (interval: {interval_seconds} seconds)")
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                flogger.trace("Cleanup cycle executing")
                self._cleanup_expired()
            except asyncio.CancelledError:
                flogger.info("Cleanup loop cancelled, shutting down")
                raise

    def shutdown(self) -> None:
        """Cancel cleanup task and all active render tasks."""
        flogger.info(f"Shutting down job queue (cleanup_task set: {self._cleanup_task is not None}, "
                     f"active tasks: {len(self._active_tasks)})")
        if self._cleanup_task:
            self._cleanup_task.cancel()
            flogger.debug("Cleanup task cancelled")
        for task in self._active_tasks:
            task.cancel()
            flogger.debug("Cancelled active render task")
        self._active_tasks.clear()
        flogger.debug("Job queue shutdown complete")
