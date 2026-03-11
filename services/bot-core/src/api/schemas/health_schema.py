from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    service: str
    environment: Dict[str, Any]
    checks: Dict[str, bool]
    database_check: Optional[Dict[str, Any]] = None
    schema_check: Optional[Dict[str, Any]] = None

class SimpleHealthResponse(BaseModel):
    status: str
    timestamp: datetime
