"""Temperature decay executor — DEPRECATED (rev 0031).

The temperature subsystem was never fully wired and has been retired (owner-approved).
This module is a no-op shim: existing deployments may still have a
``temperature_decay_default`` job row in the ``apscheduler_jobs`` table.
An unknown job_type must not error-spam, so the executor logs a one-line
deprecation warning and returns a no-op result.

To remove the stale job row from the scheduler store, call:
    DELETE /jobs/temperature_decay_default
or
    POST /scheduler/reset

(POST /scheduler/reset also removes the job from DEFAULT_SCHEDULER_JOBS,
which no longer seeds temperature_decay_default as of rev 0031.)
"""

from shared.bblogger import get_logger

flogger = get_logger("temperature-decay-executor")


async def execute_temperature_decay_job(job_id: str, payload: dict) -> dict:
    """No-op handler for the retired temperature_decay job type.

    Logs a deprecation warning and returns immediately.  This handler exists
    so that stale ``apscheduler_jobs`` rows with job_type='temperature_decay'
    do not produce unhandled-job-type errors in the scheduler log.

    Remove the stale row via DELETE /jobs/temperature_decay_default or
    POST /scheduler/reset.
    """
    flogger.warning(
        f"TemperatureDecayJob[{job_id}] — DEPRECATED: temperature subsystem retired in rev 0031. "
        "Remove this job row via DELETE /jobs/temperature_decay_default or POST /scheduler/reset."
    )
    return {"status": "deprecated", "job_id": job_id, "message": "temperature subsystem retired rev 0031"}
