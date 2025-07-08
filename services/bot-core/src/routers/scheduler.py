from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from apscheduler.triggers.cron import CronTrigger
from typing import Optional, List, Any

from shared.bblogger import get_logger
from utils.job_executor import run_job   # ← pull in the external executor

flogger = get_logger("bot-router-scheduler")
router = APIRouter()

# —— Pydantic models —— 
class OneTimeJob(BaseModel):
    id: str
    payload: Optional[dict] = {}
    run_at: Optional[datetime] = None
    delay_seconds: Optional[int] = None

class RecurringJob(BaseModel):
    id: str
    payload: Optional[dict] = {}
    cron: str  # e.g. "*/5 * * * *"

class JobInfo(BaseModel):
    id: str
    next_run_time: Optional[datetime]
    trigger: str
    args: List[Any]

@router.post("/jobs")
async def schedule_job(req: Request, job: OneTimeJob):
    flogger.debug(f"Received one‐time schedule request: {job}")
    if not job.run_at and job.delay_seconds is None:
        flogger.warning("One‐time job request missing both run_at and delay_seconds")
        raise HTTPException(400, "Provide either run_at or delay_seconds")
    run_date = job.run_at or (datetime.now(timezone.utc) + timedelta(seconds=job.delay_seconds))

    try:
        req.app.state.scheduler.add_job(
            run_job,               # ← delegate to utils/job_executor.py
            trigger="date",
            run_date=run_date,
            args=[job.id, job.payload],
            id=job.id,
        )
        flogger.info(f"Scheduled one‐time job '{job.id}' at {run_date.isoformat()}")
    except Exception as e:
        flogger.error(f"Failed to schedule one‐time job '{job.id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not schedule job: {e}")

    return {"status": "scheduled", "job_id": job.id, "run_date": run_date}

@router.post("/jobs/recurring")
async def schedule_recurring(req: Request, job: RecurringJob):
    flogger.debug(f"Received recurring schedule request: {job}")
    try:
        trigger = CronTrigger.from_crontab(job.cron)
        req.app.state.scheduler.add_job(
            run_job,               # ← same here
            trigger=trigger,
            args=[job.id, job.payload],
            id=job.id,
        )
        flogger.info(f"Scheduled recurring job '{job.id}' with CRON '{job.cron}'")
    except Exception as e:
        flogger.error(f"Failed to schedule recurring job '{job.id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not schedule recurring job: {e}")

    return {"status": "scheduled_recurring", "job_id": job.id, "cron": job.cron}

@router.get("/jobs", response_model=List[JobInfo])
async def list_jobs(req: Request):
    flogger.debug("Listing all scheduled jobs")
    jobs = req.app.state.scheduler.get_jobs()
    result = [
        JobInfo(
            id=j.id,
            next_run_time=j.next_run_time,
            trigger=str(j.trigger),
            args=j.args,
        )
        for j in jobs
    ]
    flogger.info(f"Found {len(result)} scheduled job(s)")
    return result

@router.get("/jobs/{id}", response_model=JobInfo)
async def get_job(req: Request, id: str):
    flogger.debug(f"Fetching job '{id}'")
    job = req.app.state.scheduler.get_job(id)
    if not job:
        flogger.warning(f"Job '{id}' not found")
        raise HTTPException(404, "Job not found")
    info = JobInfo(
        id=job.id,
        next_run_time=job.next_run_time,
        trigger=str(job.trigger),
        args=job.args,
    )
    flogger.info(f"Retrieved job '{id}': next_run_time={info.next_run_time}")
    return info

@router.delete("/jobs/{id}")
async def delete_job(req: Request, id: str):
    flogger.debug(f"Deleting job '{id}'")
    sched = req.app.state.scheduler
    if not sched.get_job(id):
        flogger.warning(f"Cannot delete job '{id}': not found")
        raise HTTPException(404, "Job not found")
    sched.remove_job(id)
    flogger.info(f"Deleted job '{id}'")
    return {"status": "deleted", "job_id": id}

@router.delete("/jobs/all")
async def delete_all_jobs(req: Request):
    flogger.debug("Deleting all jobs")
    req.app.state.scheduler.remove_all_jobs()
    flogger.info("All jobs have been removed")
    return {"status": "all_jobs_deleted"}