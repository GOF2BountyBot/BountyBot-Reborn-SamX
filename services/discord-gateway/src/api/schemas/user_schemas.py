"""
User and member-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord user and member
operations including user details and member management.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from api.schemas.base_schemas import BaseResponse, BaseUpdateRequest, PaginatedResponse


class User(BaseModel):
    """User information model."""
    id: int = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    discriminator: str = Field(..., description="User discriminator")
    avatar: Optional[str] = Field(None, description="Avatar URL")
    bot: bool = Field(False, description="Whether user is a bot")
    system: bool = Field(False, description="Whether user is a system user")
    created_at: str = Field(..., description="User creation timestamp")
    public_flags: int = Field(0, description="Public user flags")

class Member(BaseModel):
    """Guild member information model."""
    user: User = Field(..., description="User information")
    guild_id: int = Field(..., description="Guild ID")
    nick: Optional[str] = Field(None, description="Nickname in guild")
    roles: List[int] = Field(default_factory=list, description="Role IDs")
    joined_at: Optional[str] = Field(None, description="When user joined guild")
    premium_since: Optional[str] = Field(None, description="When user started boosting")
    deaf: bool = Field(False, description="Whether user is deafened")
    mute: bool = Field(False, description="Whether user is muted")
    pending: bool = Field(False, description="Whether user is pending verification")
    permissions: int = Field(..., description="User permissions in guild")

class UserResponse(BaseResponse):
    """Response model for user detail endpoint."""
    data: User = Field(..., description="User data")

class MemberResponse(BaseResponse):
    """Response model for member detail endpoint."""
    data: Member = Field(..., description="Member data")

class MemberListResponse(PaginatedResponse):
    """Response model for member list endpoint."""
    data: List[Member] = Field(..., description="List of guild members")

class MemberUpdateRequest(BaseUpdateRequest):
    """Request model for updating member properties."""
    nick: Optional[str] = Field(None, description="New nickname")
    roles: Optional[List[int]] = Field(None, description="Role IDs to assign")
    mute: Optional[bool] = Field(None, description="Whether to mute user")
    deaf: Optional[bool] = Field(None, description="Whether to deafen user")
    channel_id: Optional[int] = Field(None, description="Voice channel to move to")
