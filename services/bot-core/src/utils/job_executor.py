from datetime import datetime, timezone
import traceback

from shared.bblogger import get_logger

# dispatch job-type specific executor module
from utils.executors.time_announcement_executor import execute_time_announcement_job

flogger = get_logger("bot-job-executor")

class JobExecutor:
    """
    Handles the actual work of running a scheduled job.
    Extend this class with your real business logic,
    helper methods, service injections, etc.
    """

    async def execute(self, job_id: str, payload: dict):
        """
        Perform the work for a job.

        :param job_id:   Unique identifier of the job
        :param payload:  Arbitrary dict passed in when the job was scheduled
        """
        start_ts = datetime.now(timezone.utc)
        flogger.info(f"[{start_ts.isoformat()}] Starting job '{job_id}' with payload")
        flogger.trace(f"JobExecutor payload: job_id={job_id}, payload={payload}")

        try:
            # 1) time-announcement jobs go to our executor
            if payload.get("job_type") == "time_announcement":
                flogger.debug(f"Dispatching time_announcement for job {job_id}")
                return await execute_time_announcement_job(job_id, payload)

            # 2) fallback for other payloads
            flogger.debug(f"Job '{job_id}': executing generic payload handler")
            # … your existing task/logic here …

            end_ts = datetime.now(timezone.utc)
            duration = (end_ts - start_ts).total_seconds()
            flogger.info(f"[{end_ts.isoformat()}] Completed job '{job_id}' in {duration:.2f}s")

        except Exception as e:
            flogger.error(
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"Job '{job_id}' failed: {e}", 
                exc_info=True
            )
            flogger.trace(traceback.format_exc())


# A single, picklable executor instance for APScheduler
_executor = JobExecutor()


async def run_job(job_id: str, payload: dict):
    """
    Thin wrapper that APScheduler can serialize/pickle.
    Delegates to the JobExecutor.
    """
    await _executor.execute(job_id, payload)