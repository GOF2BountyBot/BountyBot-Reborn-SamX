"""
Channel and category-/forum-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord channel, category,
forum-tag and forum-thread operations.
"""

from typing import List, Optional, Union

from pydantic import BaseModel, Field

from api.schemas.base_schemas import BaseCreateRequest, BaseResponse, BaseUpdateRequest, PaginatedResponse
from api.schemas.message_schemas import EmbedPayload

# -------------------------------------------------------------------
# Channel / Category core schemas

class Channel(BaseModel):
    """Consolidated channel information model."""
    id: int = Field(..., description="Channel ID")
    name: str = Field(..., description="Channel name")
    type: str = Field(..., description="Channel type")
    position: int = Field(..., description="Channel position")
    guild_id: Optional[int] = Field(None, description="Guild ID")
    category_id: Optional[int] = Field(None, description="Parent category ID")
    created_at: str = Field(..., description="Channel creation timestamp")
    topic: Optional[str] = Field(None, description="Channel topic")
    nsfw: Optional[bool] = Field(None, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, description="Slowmode delay in seconds")
    bitrate: Optional[int] = Field(None, description="Voice channel bitrate")
    user_limit: Optional[int] = Field(None, description="Voice channel user limit")
    default_auto_archive_duration: Optional[int] = Field(
        None, description="Forum auto-archive duration in minutes"
    )

class Category(BaseModel):
    """Category information model."""
    id: int = Field(..., description="Category ID")
    name: str = Field(..., description="Category name")
    position: int = Field(..., description="Category position")
    guild_id: int = Field(..., description="Guild ID")
    created_at: str = Field(..., description="Category creation timestamp")

class ChannelResponse(BaseResponse):
    """Response model for single channel endpoint."""
    data: Channel = Field(..., description="Channel data")

class ChannelListResponse(PaginatedResponse):
    """Response model for channel list endpoint."""
    data: List[Channel] = Field(..., description="List of channels")

class CategoryResponse(BaseResponse):
    """Response model for single category endpoint."""
    data: Category = Field(..., description="Category data")

class CategoryListResponse(PaginatedResponse):
    """Response model for category list endpoint."""
    data: List[Category] = Field(..., description="List of categories")

class CategoryCreateRequest(BaseCreateRequest):
    """Request model for creating a category."""
    name: str = Field(..., description="Category name")
    position: Optional[int] = Field(None, ge=0, description="Category position (≥0)")

class CategoryUpdateRequest(BaseUpdateRequest):
    """Request model for updating a category."""
    name: Optional[str] = Field(None, description="Category name")
    position: Optional[int] = Field(None, ge=0, description="Category position (≥0)")

# -------------------------------------------------------------------
# Create / Update requests for Guild Channels (text/voice/forum)

class ChannelCreateRequest(BaseCreateRequest):
    """Request model for creating a channel."""
    name: str = Field(..., description="Channel name")
    type: str = Field("text", description="Channel type (text, voice, forum)")
    topic: Optional[str] = Field(None, description="Channel topic")
    bitrate: Optional[int] = Field(None, ge=0, description="Voice channel bitrate (≥0)")
    user_limit: Optional[int] = Field(None, ge=0, description="Voice channel user limit (≥0)")
    position: Optional[int] = Field(None, ge=0, description="Channel position (≥0)")
    category_id: Optional[int] = Field(None, description="Parent category ID")
    nsfw: Optional[bool] = Field(False, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, ge=0, description="Slowmode delay in seconds (≥0)")
    default_auto_archive_duration: Optional[int] = Field(
        None, ge=0, description="Forum auto-archive duration in minutes (≥0)"
    )


class ChannelUpdateRequest(BaseUpdateRequest):
    """Request model for updating a channel."""
    name: Optional[str] = Field(None, description="Channel name")
    topic: Optional[str] = Field(None, description="Channel topic")
    bitrate: Optional[int] = Field(None, ge=0, description="Voice channel bitrate (≥0)")
    user_limit: Optional[int] = Field(None, ge=0, description="Voice channel user limit (≥0)")
    position: Optional[int] = Field(None, ge=0, description="Channel position (≥0)")
    category_id: Optional[int] = Field(None, description="Parent category ID")
    nsfw: Optional[bool] = Field(None, description="Whether channel is NSFW")
    slowmode_delay: Optional[int] = Field(None, ge=0, description="Slowmode delay in seconds (≥0)")
    default_auto_archive_duration: Optional[int] = Field(
        None, ge=0, description="Forum auto-archive duration in minutes (≥0)"
    )


# -------------------------------------------------------------------
# Forum Tag schemas

class ForumTag(BaseModel):
    """Payload model for a forum tag."""
    id: int = Field(..., description="Tag ID")
    channel_id: int = Field(..., description="Channel ID")
    name: str = Field(..., description="Tag name")
    emoji: Optional[str] = Field(None, description="Emoji identifier for tag")

class ForumTagCreateRequest(BaseModel):
    """Request to create a forum tag."""
    name: str = Field(..., description="Tag name")
    emoji: Optional[str] = Field(None, description="Emoji identifier for tag")

class ForumTagUpdateRequest(BaseModel):
    """Request to update a forum tag."""
    name: Optional[str] = Field(None, description="New tag name")
    emoji: Optional[str] = Field(None, description="New emoji for tag")

class ForumTagResponse(BaseResponse):
    """Single-tag response model."""
    data: ForumTag = Field(..., description="Tag data")

class ForumTagListResponse(PaginatedResponse):
    """List-tags response model."""
    data: List[ForumTag] = Field(..., description="List of forum tags")

class ForumTagListRequest(BaseModel):
    """Request model for updating tags on a thread/channel."""
    tags: List[Union[int, ForumTag]] = Field(
        ..., description="List of forum tag IDs or tag objects"
    )

# -------------------------------------------------------------------
# Forum-Thread schemas

class Thread(BaseModel):
    """Consolidated thread information model."""
    id: int = Field(..., description="Thread channel ID")
    name: str = Field(..., description="Thread name/title")
    channel_id: int = Field(..., description="Parent channel ID")
    guild_id: Optional[int] = Field(None, description="Guild ID")
    owner_id: int = Field(..., description="User ID of thread creator")
    archived: bool = Field(..., description="Whether the thread is archived")
    locked: bool = Field(..., description="Whether the thread is locked")
    message_count: Optional[int] = Field(None, description="Number of messages in the thread")
    member_count: Optional[int] = Field(None, description="Number of members in the thread")
    default_auto_archive_duration: Optional[int] = Field(
        None, description="Auto-archive duration in minutes"
    )
    created_at: str = Field(..., description="Thread creation timestamp")
    last_message_id: Optional[int] = Field(None, description="Last message ID in the thread")

    # Newly added fields to surface forum tag information on thread detail responses
    applied_tag_ids: Optional[List[int]] = Field(
        None, description="List of applied forum tag IDs on this thread"
    )
    applied_tags: Optional[List[ForumTag]] = Field(
        None, description="List of applied forum tag objects (when available)"
    )

class ThreadCreateRequest(BaseModel):
    """Request model for creating a forum thread."""
    name: str = Field(..., description="Thread name/title")
    auto_archive_duration: Optional[int] = Field(
        None,
        description=(
            "Auto-archive duration in minutes. Allowed values: "
            "60, 1440, 4320, or 10080"
        ),
    )
    type: Optional[str] = Field("public_thread", description="Thread type (public_thread, private_thread)")
    initial_message: Optional[EmbedPayload] = Field(
        None, description="Initial message embed payload posted when thread is created"
    )

class ThreadUpdateRequest(BaseModel):
    """Request model for updating a forum thread."""
    name: Optional[str] = Field(None, description="New thread name/title")
    archived: Optional[bool] = Field(None, description="Archive (close) the thread if True")
    locked: Optional[bool] = Field(None, description="Lock/unlock the thread")

class ThreadResponse(BaseResponse):
    """Response model for single thread endpoint."""
    data: Thread = Field(..., description="Thread data")

class ThreadListResponse(PaginatedResponse):
    """Response model for listing threads."""
    data: List[Thread] = Field(..., description="List of forum threads")
