import traceback
from datetime import UTC, datetime

from shared.bblogger import get_logger

# dispatch job-type specific executor module
from utils.executors.bounty_expire_executor import execute_bounty_expire_job
from utils.executors.bounty_failsafe_cleanup_executor import execute_bounty_failsafe_cleanup_job
from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job
from utils.executors.bounty_spawn_executor import (
    execute_bounty_spawn_one_job,
    execute_bounty_spawn_orchestrate_job,
)
from utils.executors.db_retention_executor import execute_db_retention_job
from utils.executors.duel_expire_executor import execute_duel_expire_job
from utils.executors.pg_backup_executor import execute_pg_backup_job
from utils.executors.shop_refresh_executor import execute_shop_refresh_job
from utils.executors.temperature_decay_executor import execute_temperature_decay_job
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
        start_ts = datetime.now(UTC)
        flogger.info(f"[{start_ts.isoformat()}] Starting job '{job_id}' with payload")
        flogger.trace(f"JobExecutor payload: job_id={job_id}, payload={payload}")

        try:
            # 1) time-announcement jobs go to our executor
            if payload.get("job_type") == "time_announcement":
                flogger.debug(f"Dispatching time_announcement for job {job_id}")
                return await execute_time_announcement_job(job_id, payload)

            # 2) shop-refresh jobs
            if payload.get("job_type") == "shop_refresh":
                flogger.debug(f"Dispatching shop_refresh for job {job_id}")
                return await execute_shop_refresh_job(job_id, payload)

            # 3) bounty-spawn orchestrate jobs (new per-tier staggered flow)
            if payload.get("job_type") == "bounty_spawn_orchestrate":
                flogger.debug(f"Dispatching bounty_spawn_orchestrate for job {job_id}")
                return await execute_bounty_spawn_orchestrate_job(job_id, payload)

            # 3a) bounty-spawn one-time per-tier jobs
            if payload.get("job_type") == "bounty_spawn_one":
                flogger.debug(f"Dispatching bounty_spawn_one for job {job_id}")
                return await execute_bounty_spawn_one_job(job_id, payload)

            # 4) bounty-expire jobs
            if payload.get("job_type") == "bounty_expire":
                flogger.debug(f"Dispatching bounty_expire for job {job_id}")
                return await execute_bounty_expire_job(job_id, payload)

            # 5) bounty-respawn jobs
            if payload.get("job_type") == "bounty_respawn":
                flogger.debug(f"Dispatching bounty_respawn for job {job_id}")
                return await execute_bounty_respawn_job(job_id, payload)

            # 6) bounty failsafe cleanup (hourly Discord-driven sweep)
            if payload.get("job_type") == "bounty_failsafe_cleanup":
                flogger.debug(f"Dispatching bounty_failsafe_cleanup for job {job_id}")
                return await execute_bounty_failsafe_cleanup_job(job_id, payload)

            # 7) duel-expire jobs
            if payload.get("job_type") == "duel_expire":
                flogger.debug(f"Dispatching duel_expire for job {job_id}")
                return await execute_duel_expire_job(job_id, payload)

            # 8) temperature-decay jobs
            if payload.get("job_type") == "temperature_decay":
                flogger.debug(f"Dispatching temperature_decay for job {job_id}")
                return await execute_temperature_decay_job(job_id, payload)

            # 9) pg-backup jobs
            if payload.get("job_type") == "pg_backup":
                flogger.debug(f"Dispatching pg_backup for job {job_id}")
                return await execute_pg_backup_job(job_id, payload)

            # 10) db-retention jobs
            if payload.get("job_type") == "db_retention":
                flogger.debug(f"Dispatching db_retention for job {job_id}")
                return await execute_db_retention_job(job_id, payload)

            # 10) fallback for other payloads
            flogger.debug(f"Job '{job_id}': executing generic payload handler")
            # … your existing task/logic here …

            end_ts = datetime.now(UTC)
            duration = (end_ts - start_ts).total_seconds()
            flogger.info(f"[{end_ts.isoformat()}] Completed job '{job_id}' in {duration:.2f}s")

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"[{datetime.now(UTC).isoformat()}] Job '{job_id}' failed: {e}", exc_info=True)
            flogger.trace(traceback.format_exc())


# A single, picklable executor instance for APScheduler
_executor = JobExecutor()


async def run_job(job_id: str, payload: dict):
    """
    Thin wrapper that APScheduler can serialize/pickle.
    Delegates to the JobExecutor.
    """
    await _executor.execute(job_id, payload)
