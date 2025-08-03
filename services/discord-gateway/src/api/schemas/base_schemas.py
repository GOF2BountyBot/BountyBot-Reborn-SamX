"""
Base Pydantic schemas for Discord Gateway API.

This module defines base models and common response schemas used across
all Discord API endpoints. These provide consistent structure and typing
for all API operations.
"""

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

class BaseResponse(BaseModel):
    """Base response model for all API endpoints."""
    status: str = Field(..., description="Operation status")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")

class SuccessResponse(BaseResponse):
    """Generic success response for operations without specific return data."""
    message: str = Field(..., description="Success message")

class DeleteResponse(BaseResponse):
    """Response model for delete operations."""
    deleted: bool = Field(True, description="Whether deletion was successful")
    message: str = Field(..., description="Deletion confirmation message")

class BaseListResponse(BaseResponse):
    """Base response model for list endpoints."""
    pass

class BaseDetailResponse(BaseResponse):
    """Base response model for detail endpoints."""
    pass

class BaseCreateRequest(BaseModel):
    """Base request model for create operations."""
    pass

class BaseUpdateRequest(BaseModel):
    """Base request model for update operations."""
    pass
