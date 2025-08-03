"""
Guild-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord guild operations
including guild listings, details, and member management.
"""

from typing import List
from pydantic import Field
from .base_schemas import BaseListResponse, BaseDetailResponse

class GuildSummary(BaseModel):
    """Summary guild information for list responses."""
    id: int = Field(..., description="Guild ID")
    name: str = Field(..., description="Guild name")
    icon: Optional[str] = Field(None, description="Guild icon URL")
    member_count: Optional[int] = Field(None, description="Number of members")
    owner_id: int = Field(..., description="Guild owner ID")

class GuildDetail(GuildSummary):
    """Detailed guild information for detail responses."""
    description: Optional[str] = Field(None, description="Guild description")
    created_at: str = Field(..., description="Guild creation timestamp")
    features: List[str] = Field(default_factory=list, description="Guild features")
    verification_level: str = Field(..., description="Verification level")
    default_notifications: str = Field(..., description="Default notification level")
    explicit_content_filter: str = Field(..., description="Explicit content filter level")
    mfa_level: str = Field(..., description="MFA level")
    premium_tier: int = Field(..., description="Nitro boost tier")
    premium_subscription_count: Optional[int] = Field(None, description="Number of boosts")
    preferred_locale: str = Field(..., description="Preferred locale")
    nsfw_level: Optional[str] = Field(None, description="NSFW level")

class GuildListResponse(BaseListResponse):
    """Response model for guild list endpoint."""
    guilds: List[GuildSummary] = Field(..., description="List of guilds")

class GuildDetailResponse(BaseDetailResponse):
    """Response model for guild detail endpoint."""
    guild: GuildDetail = Field(..., description="Guild details")
