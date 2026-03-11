from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    service: str
    environment: dict[str, Any]
    checks: dict[str, bool]
    database_check: dict[str, Any] | None = None
    schema_check: dict[str, Any] | None = None

class SimpleHealthResponse(BaseModel):
    status: str
    timestamp: datetime
