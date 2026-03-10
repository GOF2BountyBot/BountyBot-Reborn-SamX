"""
Base Pydantic schemas for Discord Gateway API.

This module defines base models and common response schemas used across
all Discord API endpoints. These provide consistent structure and typing
for all API operations.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, UTC

class BaseResponse(BaseModel):
    """Base response model for all API endpoints."""
    status: str = Field(..., description="Operation status")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")
    message: Optional[str] = Field(None, description="Optional response message")

class PaginatedResponse(BaseResponse):
    """Base response model for paginated endpoints."""
    total_count: Optional[int] = Field(None, description="Total number of items")
    page: Optional[int] = Field(None, description="Current page number")
    page_size: Optional[int] = Field(None, description="Items per page")
    has_more: Optional[bool] = Field(None, description="Whether there are more items")

class SuccessResponse(BaseResponse):
    """Generic success response for operations without specific return data."""
    message: str = Field(..., description="Success message")

class DeleteResponse(BaseResponse):
    """Response model for delete operations."""
    deleted: bool = Field(True, description="Whether deletion was successful")
    message: str = Field(..., description="Deletion confirmation message")

class BaseCreateRequest(BaseModel):
    """Base request model for create operations."""
    pass

class BaseUpdateRequest(BaseModel):
    """Base request model for update operations."""
    pass

# Generic response patterns
def create_resource_response(resource_name: str, resource_model):
    """Factory function to create standardized resource responses."""
    return type(
        f"{resource_name.title()}Response",
        (BaseResponse,),
        {
            "data": (resource_model, Field(..., description=f"{resource_name} data")),
            "__annotations__": {"data": resource_model}
        }
    )

def create_resource_list_response(resource_name: str, resource_model):
    """Factory function to create standardized resource list responses."""
    return type(
        f"{resource_name.title()}ListResponse", 
        (PaginatedResponse,),
        {
            "data": (List[resource_model], Field(..., description=f"List of {resource_name} items")),
            "__annotations__": {"data": List[resource_model]}
        }
    )