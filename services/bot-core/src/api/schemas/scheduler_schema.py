from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Any

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