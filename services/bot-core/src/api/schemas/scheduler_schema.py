from datetime import datetime
from typing import Any

from pydantic import BaseModel


# —— Pydantic models ——
class OneTimeJob(BaseModel):
    payload: dict | None = {}
    run_at: datetime | None = None
    delay_seconds: int | None = None


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

    payload: dict | None = {}
