from datetime import datetime, timezone
from shared.bblogger import get_logger

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
        flogger.info(f"[{start_ts.isoformat()}] Starting job '{job_id}' with payload: {payload}")

        try:
            # ── Place your real task/logic here ──
            flogger.debug(f"Job '{job_id}': executing payload handler")
            # e.g. result = await some_service.process(payload)
            # ─────────────────────────────────────

            end_ts = datetime.now(timezone.utc)
            duration = (end_ts - start_ts).total_seconds()
            flogger.info(f"[{end_ts.isoformat()}] Completed job '{job_id}' in {duration:.2f}s")

        except Exception as e:
            flogger.error(
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"Job '{job_id}' failed: {e}",
                exc_info=True
            )


# A single, picklable executor instance for APScheduler
_executor = JobExecutor()


async def run_job(job_id: str, payload: dict):
    """
    Thin wrapper that APScheduler can serialize/pickle.
    Delegates to the JobExecutor.
    """
    await _executor.execute(job_id, payload)