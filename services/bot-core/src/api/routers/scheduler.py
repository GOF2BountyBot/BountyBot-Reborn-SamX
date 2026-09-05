import json
import uuid
from datetime import UTC, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request
from shared.bblogger import get_logger
from utils.job_executor import run_job  # ← external executor

from api.schemas.scheduler_schema import JobInfo, OneTimeJob, RecurringJob, UpdateJob

flogger = get_logger("scheduler-router")
router = APIRouter(tags=["job-scheduler"])


def _get_scheduler(req: Request):
    """Return the scheduler or raise 503 if unavailable."""
    scheduler = getattr(req.app.state, "scheduler", None)
    if scheduler is None:
        flogger.warning("Scheduler requested but not available")
        raise HTTPException(status_code=503, detail="Scheduler is not available. The service may still be starting up.")
    return scheduler


def _safe_serialize_args(args) -> list:
    """Safely serialize job args to a JSON-compatible list.

    APScheduler job args may contain non-JSON-serializable objects (datetime,
    custom dataclasses, etc.).  We use json.dumps with default=str to coerce
    everything to a serializable form, then round-trip back to a Python list.

    Args:
        args: The raw args tuple/list from an APScheduler job.

    Returns:
        A JSON-safe list suitable for inclusion in a Pydantic response model.
    """
    try:
        raw = list(args) if args is not None else []
        return json.loads(json.dumps(raw, default=str))
    except Exception:
        # Last-resort: stringify every element individually
        try:
            return [str(a) for a in (args or [])]
        except Exception:
            return []


# temperature_decay_default removed from DEFAULT_SCHEDULER_JOBS (rev 0031; temperature subsystem retired).
# Stale job rows may still exist in older deployments — they are handled as no-ops by the executor.
_DEFAULT_JOB_IDS = frozenset({"bounty_spawn_default", "shop_refresh_default"})


@router.get("/jobs", response_model=list[JobInfo])
async def list_jobs(req: Request, guild_id: int | None = None):
    flogger.info("List scheduled jobs endpoint: starting")
    flogger.debug(f"Listing scheduled jobs guild_id={guild_id}")
    jobs = _get_scheduler(req).get_jobs()

    result = []
    for j in jobs:
        jid = getattr(j, "id", None) or getattr(j, "job_id", "unknown")

        if guild_id is not None:
            # Always include the default recurring jobs (they serve all guilds)
            if jid in _DEFAULT_JOB_IDS:
                pass  # include unconditionally
            else:
                # Include only if the payload's guild_id matches
                args = list(j.args) if j.args else []
                if not (len(args) >= 2 and isinstance(args[1], dict) and args[1].get("guild_id") == guild_id):
                    continue  # skip this job

        result.append(
            JobInfo(
                id=jid,
                next_run_time=j.next_run_time,
                trigger=str(j.trigger),
                args=_safe_serialize_args(j.args),
            )
        )

    flogger.info(f"Found {len(result)} scheduled job(s)")
    return result


@router.get("/jobs/{job_id}", response_model=JobInfo)
async def get_job(req: Request, job_id: str):
    flogger.info(f"Get job endpoint: starting job_id={job_id}")
    flogger.debug(f"Fetching job '{job_id}'")
    job = _get_scheduler(req).get_job(job_id)
    if not job:
        flogger.warning(f"Job '{job_id}' not found")
        raise HTTPException(404, "Job not found")
    info = JobInfo(
        id=getattr(job, "id", None) or getattr(job, "job_id", "unknown"),
        next_run_time=job.next_run_time,
        trigger=str(job.trigger),
        args=_safe_serialize_args(job.args),
    )
    flogger.info(f"Retrieved job '{job_id}': next_run_time={info.next_run_time}")
    return info


@router.post("/jobs")
async def schedule_job(req: Request, job: OneTimeJob):
    # Honor caller-supplied job_id when provided so that callers (e.g. the
    # bounty-spawn orchestrator) can correlate scheduled jobs via indexed
    # LIKE queries on apscheduler_jobs.id.  Format validated by the
    # OneTimeJob schema pattern.  Also guard against clobbering the three
    # default recurring job IDs.
    if job.job_id is not None:
        if job.job_id in _DEFAULT_JOB_IDS:
            flogger.warning(f"Refusing to schedule job with reserved default ID '{job.job_id}'")
            raise HTTPException(400, f"job_id '{job.job_id}' is reserved for default recurring jobs")
        job_id = job.job_id
    else:
        job_id = str(uuid.uuid4())
    flogger.info(f"Schedule one-time job endpoint: starting job_id={job_id}")
    flogger.debug(f"Using one-time job id={job_id} payload={job}")
    if not job.run_at and job.delay_seconds is None:
        flogger.warning("One-time job request missing both run_at and delay_seconds")
        raise HTTPException(400, "Provide either run_at or delay_seconds")
    run_date = job.run_at or (datetime.now(UTC) + timedelta(seconds=job.delay_seconds))

    try:
        _get_scheduler(req).add_job(
            run_job,
            trigger="date",
            run_date=run_date,
            args=[job_id, job.payload],
            id=job_id,
        )
        flogger.info(f"Scheduled one-time job '{job_id}' at {run_date.isoformat()}")
    except Exception as e:
        flogger.error(f"Failed to schedule one-time job '{job_id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not schedule job: {e}") from e

    return {"status": "scheduled", "job_id": job_id, "run_date": run_date}


@router.post("/jobs/recurring")
async def schedule_recurring(req: Request, job: RecurringJob):
    job_id = str(uuid.uuid4())
    flogger.info(f"Schedule recurring job endpoint: starting job_id={job_id}")
    flogger.debug(f"Generated recurring job id={job_id} cron={job.cron}")
    try:
        trigger = CronTrigger.from_crontab(job.cron)
        _get_scheduler(req).add_job(
            run_job,
            trigger=trigger,
            args=[job_id, job.payload],
            id=job_id,
        )
        flogger.info(f"Scheduled recurring job '{job_id}' with CRON '{job.cron}'")
    except Exception as e:
        flogger.error(f"Failed to schedule recurring job '{job_id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not schedule recurring job: {e}") from e

    return {"status": "scheduled_recurring", "job_id": job_id, "cron": job.cron}


@router.put("/jobs/{job_id}")
async def update_job(req: Request, job_id: str, update: UpdateJob):
    """
    Update the payload args for an existing job.
    This will replace the original payload passed at scheduling time.
    """
    flogger.info(f"Update job endpoint: starting job_id={job_id}")
    flogger.debug(f"Updating job '{job_id}' with new payload: {update.payload}")
    sched = _get_scheduler(req)
    job = sched.get_job(job_id)
    if not job:
        flogger.warning(f"Cannot update job '{job_id}': not found")
        raise HTTPException(404, "Job not found")

    new_args = [job_id, update.payload]
    try:
        sched.modify_job(job_id, args=new_args)
        flogger.info(f"Updated job '{job_id}' args successfully")
    except Exception as e:
        flogger.error(f"Failed to update job '{job_id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not update job: {e}") from e

    return {"status": "updated", "job_id": job_id}


@router.delete("/jobs/all")
async def delete_all_jobs(req: Request):
    flogger.info("Delete all jobs endpoint: starting")
    flogger.debug("Deleting all jobs")
    _get_scheduler(req).remove_all_jobs()
    flogger.info("All jobs have been removed")
    return {"status": "all_jobs_deleted"}


@router.delete("/jobs/guild/{guild_id}")
async def delete_guild_jobs(req: Request, guild_id: int):
    """Delete all one-time jobs scoped to a specific guild.

    Iterates all scheduled jobs and removes those whose payload (args[1]) contains
    a ``guild_id`` matching the path parameter.  Default recurring jobs that serve
    all guilds are never removed by this endpoint.
    """
    flogger.info(f"Delete guild jobs endpoint: starting guild_id={guild_id}")
    scheduler = _get_scheduler(req)
    removed = 0
    for job in scheduler.get_jobs():
        try:
            args = list(job.args) if job.args else []
            if len(args) >= 2 and isinstance(args[1], dict) and args[1].get("guild_id") == guild_id:
                scheduler.remove_job(job.id)
                removed += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    flogger.info(f"Removed {removed} guild job(s) for guild_id={guild_id}")
    return {"status": "guild_jobs_deleted", "guild_id": guild_id, "removed_count": removed}


@router.delete("/jobs/{job_id}")
async def delete_job(req: Request, job_id: str):
    flogger.info(f"Delete job endpoint: starting job_id={job_id}")
    flogger.debug(f"Deleting job '{job_id}'")
    sched = _get_scheduler(req)
    if not sched.get_job(job_id):
        flogger.warning(f"Cannot delete job '{job_id}': not found")
        raise HTTPException(404, "Job not found")
    sched.remove_job(job_id)
    flogger.info(f"Deleted job '{job_id}'")
    return {"status": "deleted", "job_id": job_id}


@router.post("/reset")
async def reset_scheduler(req: Request):
    """Remove all jobs and re-register the default recurring jobs.

    This is an admin-level operation that wipes the entire job queue and then
    calls ``register_default_jobs`` to recreate the standard recurring jobs
    (bounty_spawn_default, shop_refresh_default, bounty_failsafe_cleanup_default,
    pg_backup_default, db_retention_default, event_tick_default).  Note:
    temperature_decay_default is NOT re-seeded (temperature subsystem retired, rev 0031).
    """
    flogger.info("Reset scheduler endpoint: starting")
    scheduler = _get_scheduler(req)
    scheduler.remove_all_jobs()
    flogger.info("All jobs removed; re-registering default jobs")

    # Deferred import to avoid circular import at module load time
    from main import register_default_jobs

    register_default_jobs(scheduler)
    jobs = scheduler.get_jobs()
    flogger.info(f"Scheduler reset complete: {len(jobs)} default job(s) registered")
    return {"status": "reset", "jobs_registered": len(jobs)}
