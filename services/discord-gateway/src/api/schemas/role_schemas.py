"""
Role-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord role operations
including role creation, updates, and member management.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from api.schemas.base_schemas import (
    BaseListResponse, BaseDetailResponse,
    BaseCreateRequest, BaseUpdateRequest
)
from .user_schemas import Member

class Role(BaseModel):
    """Role information model."""
    id: int = Field(..., description="Role ID")
    name: str = Field(..., description="Role name")
    color: int = Field(..., description="Role color")
    hoist: bool = Field(..., description="Whether role is hoisted")
    position: int = Field(..., description="Role position")
    permissions: int = Field(..., description="Role permissions")
    managed: bool = Field(..., description="Whether role is managed")
    mentionable: bool = Field(..., description="Whether role is mentionable")
    created_at: str = Field(..., description="Role creation timestamp")
    tags: Optional[Dict[str, Any]] = Field(None, description="Role tags")

class RoleListResponse(BaseListResponse):
    """Response model for role list endpoint."""
    roles: List[Role] = Field(..., description="List of roles")

class RoleDetailResponse(BaseDetailResponse):
    """Response model for role detail endpoint."""
    role: Role = Field(..., description="Role details")

class RoleMemberListResponse(BaseListResponse):
    """Response model for role member list endpoint."""
    members: List[Member] = Field(..., description="List of members with role")

class RoleCreateRequest(BaseCreateRequest):
    """Request model for creating a role."""
    name: Optional[str] = Field("new role", description="Role name")
    permissions: Optional[int] = Field(None, description="Role permissions")
    color: Optional[int] = Field(0, description="Role color")
    hoist: Optional[bool] = Field(False, description="Whether role is hoisted")
    mentionable: Optional[bool] = Field(False, description="Whether role is mentionable")

class RoleUpdateRequest(BaseUpdateRequest):
    """Request model for updating a role."""
    name: Optional[str] = Field(None, description="Role name")
    permissions: Optional[int] = Field(None, description="Role permissions")
    color: Optional[int] = Field(None, description="Role color")
    hoist: Optional[bool] = Field(None, description="Whether role is hoisted")
    position: Optional[int] = Field(None, description="Role position")
    mentionable: Optional[bool] = Field(None, description="Whether role is mentionable")
