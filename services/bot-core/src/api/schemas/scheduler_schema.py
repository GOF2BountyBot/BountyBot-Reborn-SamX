from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# —— Pydantic models ——
class OneTimeJob(BaseModel):
    payload: dict | None = {}
    run_at: datetime | None = None
    delay_seconds: int | None = None
    # Optional caller-supplied job ID. Must match the safe identifier pattern
    # (letters, digits, underscore, hyphen; 1–128 chars). When omitted, the
    # router generates a UUID. Required for callers that need to correlate
    # scheduled jobs via indexed LIKE queries on the ``apscheduler_jobs.id``
    # column (see bounty_spawn_executor orchestrator). Defense-in-depth —
    # the endpoint is internal-only, but validation prevents SQL wildcard
    # injection (``%``) and excessive length.
    job_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_\-]{1,128}$",
        description="Optional caller-supplied job ID (letters, digits, underscore, hyphen; max 128).",
    )


class RecurringJob(BaseModel):
    payload: dict | None = {}
    cron: str  # e.g. "*/5 * * * *"


class JobInfo(BaseModel):
    id: str
    next_run_time: datetime | None
    trigger: str
    args: list[Any]


class UpdateJob(BaseModel):
    """
    Model for updating the 'payload' of an existing job.
    Matches the shape of the original payload passed at scheduling time.
    """

    # B.30: forbid extra fields so that a wrong-field body (e.g. {"args": [...]})
    # returns HTTP 422 instead of silently wiping the existing payload to {}.
    # A.1: payload is non-nullable; {"payload": null} returns 422 instead of
    # corrupting the live job args with None (which would break job_executor.py).
    model_config = ConfigDict(extra="forbid")

    payload: dict = Field(default_factory=dict)
