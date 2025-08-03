"""
Permission-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord permission operations
including permission overwrites and permission reference data.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from .base_schemas import BaseListResponse, BaseDetailResponse, BaseUpdateRequest

class PermissionOverwrite(BaseModel):
    """Permission overwrite information model."""
    id: int = Field(..., description="Target ID (role or user)")
    type: str = Field(..., description="Target type (role or member)")
    allow: int = Field(..., description="Allowed permissions")
    deny: int = Field(..., description="Denied permissions")

class PermissionFlag(BaseModel):
    """Permission flag information model."""
    name: str = Field(..., description="Permission name")
    value: int = Field(..., description="Permission bit value")
    description: str = Field(..., description="Permission description")
    channel_types: List[str] = Field(default_factory=list, description="Applicable channel types")

class PermissionOverwriteListResponse(BaseListResponse):
    """Response model for permission overwrite list endpoint."""
    overwrites: List[PermissionOverwrite] = Field(..., description="List of permission overwrites")

class PermissionOverwriteDetailResponse(BaseDetailResponse):
    """Response model for permission overwrite detail endpoint."""
    overwrite: PermissionOverwrite = Field(..., description="Permission overwrite details")

class PermissionFlagListResponse(BaseListResponse):
    """Response model for permission flag list endpoint."""
    permissions: List[PermissionFlag] = Field(..., description="List of permission flags")

class PermissionOverwriteRequest(BaseUpdateRequest):
    """Request model for setting a permission overwrite."""
    allow: Optional[int] = Field(None, description="Permissions to allow")
    deny: Optional[int] = Field(None, description="Permissions to deny")

class PermissionOverwriteListRequest(BaseModel):
    """Request model for setting multiple permission overwrites."""
    overwrites: List[Dict[str, Any]] = Field(..., description="List of overwrites to set")
