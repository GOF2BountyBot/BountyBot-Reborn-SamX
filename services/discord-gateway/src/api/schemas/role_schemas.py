"""
Role-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord role operations
including role creation, updates, and member management.
"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from api.schemas.base_schemas import BaseCreateRequest, BaseResponse, BaseUpdateRequest, PaginatedResponse


def _generate_role_name() -> str:
    return f"unk-role-{uuid4().hex[:8]}"


class Role(BaseModel):
    """Consolidated role information model."""

    id: int = Field(..., description="Role ID")
    guild_id: int = Field(..., description="Guild ID")
    name: str = Field(..., description="Role name")
    color: int = Field(..., description="Role color")
    hoist: bool = Field(..., description="Whether role is hoisted")
    position: int = Field(..., description="Role position")
    permissions: int = Field(..., description="Role permissions")
    managed: bool = Field(..., description="Whether role is managed")
    mentionable: bool = Field(..., description="Whether role is mentionable")
    created_at: str = Field(..., description="Role creation timestamp")
    tags: dict[str, Any] | None = Field(None, description="Role tags")


class RoleResponse(BaseResponse):
    """Response model for single role endpoint."""

    data: Role = Field(..., description="Role data")


class RoleListResponse(PaginatedResponse):
    """Response model for role list endpoint."""

    data: list[Role] = Field(..., description="List of roles")


class RoleCreateRequest(BaseCreateRequest):
    """Request model for creating a role."""

    name: str | None = Field(default_factory=_generate_role_name, description="Role name")
    permissions: int | None = Field(None, description="Role permissions")
    color: int | None = Field(0, description="Role color")
    hoist: bool | None = Field(False, description="Whether role is hoisted")
    position: int | None = Field(None, description="Role position")
    mentionable: bool | None = Field(False, description="Whether role is mentionable")


class RoleUpdateRequest(BaseUpdateRequest):
    """Request model for updating a role."""

    name: str | None = Field(None, description="Role name")
    permissions: int | None = Field(None, description="Role permissions")
    color: int | None = Field(None, description="Role color")
    hoist: bool | None = Field(None, description="Whether role is hoisted")
    position: int | None = Field(None, description="Role position")
    mentionable: bool | None = Field(None, description="Whether role is mentionable")
