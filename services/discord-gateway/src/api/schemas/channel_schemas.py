"""
Channel and category-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord channel and category
operations including creation, updates, and management.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from api.schemas.base_schemas import (
    BaseListResponse, BaseDetailResponse, 
    BaseCreateRequest, BaseUpdateRequest
)

class ChannelSummary(BaseModel):
    """Summary channel information for list responses."""
    id: int = Field(..., description="Channel ID")
    name: str = Field(..., description="Channel name")
    type: str = Field(..., description="Channel type")
    position: int = Field(..., description="Channel position")
    guild_id: Optional[int] = Field(None, description="Guild ID")
    created_at: str = Field(..., description="Channel creation timestamp")

class ChannelDetail(ChannelSummary):
    """Detailed channel information for detail responses."""
    topic: Optional[str] = Field(None, description="Channel topic")
    nsfw: Optional[bool] = Field(None, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, description="Slowmode delay in seconds")
    bitrate: Optional[int] = Field(None, description="Voice channel bitrate")
    user_limit: Optional[int] = Field(None, description="Voice channel user limit")
    category_id: Optional[int] = Field(None, description="Parent category ID")

class CategoryDetail(BaseModel):
    """Category channel information model."""
    id: int = Field(..., description="Category ID")
    name: str = Field(..., description="Category name")
    position: int = Field(..., description="Category position")
    guild_id: int = Field(..., description="Guild ID")
    nsfw: bool = Field(False, description="Whether category is NSFW")
    created_at: str = Field(..., description="Category creation timestamp")

class ChannelListResponse(BaseListResponse):
    """Response model for channel list endpoint."""
    channels: List[ChannelSummary] = Field(..., description="List of channels")

class ChannelDetailResponse(BaseDetailResponse):
    """Response model for channel detail endpoint."""
    channel: ChannelDetail = Field(..., description="Channel details")

class CategoryListResponse(BaseListResponse):
    """Response model for category list endpoint."""
    categories: List[CategoryDetail] = Field(..., description="List of categories")

class CategoryDetailResponse(BaseDetailResponse):
    """Response model for category detail endpoint."""
    category: CategoryDetail = Field(..., description="Category details")

class ChannelCreateRequest(BaseCreateRequest):
    """Request model for creating a channel."""
    name: str = Field(..., description="Channel name")
    type: str = Field("text", description="Channel type (text, voice)")
    topic: Optional[str] = Field(None, description="Channel topic")
    bitrate: Optional[int] = Field(None, description="Voice channel bitrate")
    user_limit: Optional[int] = Field(None, description="Voice channel user limit")
    position: Optional[int] = Field(None, description="Channel position")
    category_id: Optional[int] = Field(None, description="Parent category ID")
    nsfw: Optional[bool] = Field(False, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, description="Slowmode delay in seconds")

class ChannelUpdateRequest(BaseUpdateRequest):
    """Request model for updating a channel."""
    name: Optional[str] = Field(None, description="Channel name")
    topic: Optional[str] = Field(None, description="Channel topic")
    bitrate: Optional[int] = Field(None, description="Voice channel bitrate")
    user_limit: Optional[int] = Field(None, description="Voice channel user limit")
    position: Optional[int] = Field(None, description="Channel position")
    category_id: Optional[int] = Field(None, description="Parent category ID")
    nsfw: Optional[bool] = Field(None, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, description="Slowmode delay in seconds")

class CategoryCreateRequest(BaseCreateRequest):
    """Request model for creating a category."""
    name: str = Field(..., description="Category name")
    position: Optional[int] = Field(None, description="Category position")
    nsfw: Optional[bool] = Field(False, description="Whether category is NSFW")

class CategoryUpdateRequest(BaseUpdateRequest):
    """Request model for updating a category."""
    name: Optional[str] = Field(None, description="Category name")
    position: Optional[int] = Field(None, description="Category position")
    nsfw: Optional[bool] = Field(None, description="Whether category is NSFW")
