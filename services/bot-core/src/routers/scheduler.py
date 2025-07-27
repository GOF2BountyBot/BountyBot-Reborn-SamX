from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from apscheduler.triggers.cron import CronTrigger
from typing import Optional, List, Any
import uuid

from shared.bblogger import get_logger
from utils.job_executor import run_job   # ← external executor

flogger = get_logger("bot-router-scheduler")
router = APIRouter(tags=["job-scheduler"])


# —— Pydantic models —— 
class OneTimeJob(BaseModel):
    payload: Optional[dict] = {}
    run_at: Optional[datetime] = None
    delay_seconds: Optional[int] = None


class RecurringJob(BaseModel):
    payload: Optional[dict] = {}
    cron: str  # e.g. "*/5 * * * *"


class JobInfo(BaseModel):
    id: str
    next_run_time: Optional[datetime]
    trigger: str
    args: List[Any]


class UpdateJob(BaseModel):
    """
    Model for updating the 'payload' of an existing job.
    Matches the shape of the original payload passed at scheduling time.
    """
    payload: Optional[dict] = {}


# —— End models ——

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

@router.post("/jobs")
async def schedule_job(req: Request, job: OneTimeJob):
    job_id = str(uuid.uuid4())
    flogger.debug(f"Generated one-time job id={job_id} payload={job}")
    if not job.run_at and job.delay_seconds is None:
        flogger.warning("One-time job request missing both run_at and delay_seconds")
        raise HTTPException(400, "Provide either run_at or delay_seconds")
    run_date = job.run_at or (datetime.now(timezone.utc) + timedelta(seconds=job.delay_seconds))

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
        raise HTTPException(400, f"Could not schedule job: {e}")

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
        raise HTTPException(400, f"Could not schedule recurring job: {e}")

    return {"status": "scheduled_recurring", "job_id": job_id, "cron": job.cron}

@router.put("/jobs/{id}")
async def update_job(req: Request, id: str, update: UpdateJob):
    """
    Update the payload args for an existing job.
    This will replace the original payload passed at scheduling time.
    """
    flogger.debug(f"Updating job '{id}' with new payload: {update.payload}")
    sched = req.app.state.scheduler
    job = sched.get_job(id)
    if not job:
        flogger.warning(f"Cannot update job '{id}': not found")
        raise HTTPException(404, "Job not found")

    new_args = [id, update.payload]
    try:
        sched.modify_job(id, args=new_args)
        flogger.info(f"Updated job '{id}' args successfully")
    except Exception as e:
        flogger.error(f"Failed to update job '{id}': {e}", exc_info=True)
        raise HTTPException(400, f"Could not update job: {e}")

    return {"status": "updated", "job_id": id}

@router.delete("/jobs/all")
async def delete_all_jobs(req: Request):
    flogger.debug("Deleting all jobs")
    req.app.state.scheduler.remove_all_jobs()
    flogger.info("All jobs have been removed")
    return {"status": "all_jobs_deleted"}


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


