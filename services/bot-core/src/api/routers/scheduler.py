import uuid
from datetime import UTC, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request
from shared.bblogger import get_logger
from utils.job_executor import run_job  # ← external executor

from api.schemas.scheduler_schema import JobInfo, OneTimeJob, RecurringJob, UpdateJob

flogger = get_logger("bot-router-scheduler")
router = APIRouter(tags=["job-scheduler"])


@router.get("/jobs", response_model=list[JobInfo])
async def list_jobs(req: Request):
    flogger.debug("Listing all scheduled jobs")
    jobs = req.app.state.scheduler.get_jobs()
    result = [
        JobInfo(
            id=j.job_id,
            next_run_time=j.next_run_time,
            trigger=str(j.trigger),
            args=j.args,
        )
        for j in jobs
    ]
    flogger.info(f"Found {len(result)} scheduled job(s)")
    return result

@router.get("/jobs/{job_id}", response_model=JobInfo)
async def get_job(req: Request, job_id: str):
    flogger.debug(f"Fetching job '{job_id}'")
    job = req.app.state.scheduler.get_job(job_id)
    if not job:
        flogger.warning(f"Job '{job_id}' not found")
        raise HTTPException(404, "Job not found")
    info = JobInfo(
        id=job.job_id,
        next_run_time=job.next_run_time,
        trigger=str(job.trigger),
        args=job.args,
    )
    flogger.info(f"Retrieved job '{job_id}': next_run_time={info.next_run_time}")
    return info

@router.post("/jobs")
async def schedule_job(req: Request, job: OneTimeJob):
    job_id = str(uuid.uuid4())
    flogger.debug(f"Generated one-time job id={job_id} payload={job}")
    if not job.run_at and job.delay_seconds is None:
        flogger.warning("One-time job request missing both run_at and delay_seconds")
        raise HTTPException(400, "Provide either run_at or delay_seconds")
    run_date = job.run_at or (datetime.now(UTC) + timedelta(seconds=job.delay_seconds))

    try:
        req.app.state.scheduler.add_job(
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
    flogger.debug(f"Generated recurring job id={job_id} cron={job.cron}")
    try:
        trigger = CronTrigger.from_crontab(job.cron)
        req.app.state.scheduler.add_job(
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
    flogger.debug(f"Updating job '{job_id}' with new payload: {update.payload}")
    sched = req.app.state.scheduler
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
    flogger.debug("Deleting all jobs")
    req.app.state.scheduler.remove_all_jobs()
    flogger.info("All jobs have been removed")
    return {"status": "all_jobs_deleted"}


@router.delete("/jobs/{job_id}")
async def delete_job(req: Request, job_id: str):
    flogger.debug(f"Deleting job '{job_id}'")
    sched = req.app.state.scheduler
    if not sched.get_job(job_id):
        flogger.warning(f"Cannot delete job '{job_id}': not found")
        raise HTTPException(404, "Job not found")
    sched.remove_job(job_id)
    flogger.info(f"Deleted job '{job_id}'")
    return {"status": "deleted", "job_id": job_id}
