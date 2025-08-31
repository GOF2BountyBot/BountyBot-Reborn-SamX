"""
Pydantic schemas for Discord message API endpoints.

This module defines request/response models for Discord message operations
including create, update, and delete with proper embed payload structures.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from api.schemas.base_schemas import BaseResponse, PaginatedResponse

class EmbedField(BaseModel):
    """Embed field structure."""
    name: str = Field(..., description="Field name")
    value: str = Field(..., description="Field value")
    inline: bool = Field(False, description="Whether field should be inline")

class EmbedPayload(BaseModel):
    """Standard embed payload structure for message content."""
    title: Optional[str] = Field(None, description="Embed title")
    description: Optional[str] = Field(None, description="Embed description")
    color: Optional[int] = Field(None, description="Embed color as integer")
    fields: List[EmbedField] = Field(default_factory=list, description="Embed fields")
    footer_text: Optional[str] = Field(None, description="Footer text")
    footer_icon_url: Optional[str] = Field(None, description="Footer icon URL")
    timestamp: Optional[datetime] = Field(None, description="Embed timestamp")
    thumbnail_url: Optional[str] = Field(None, description="Thumbnail image URL")
    image_url: Optional[str] = Field(None, description="Main image URL")

class Message(BaseModel):
    """Consolidated message information model."""
    id: int = Field(..., description="Message ID")
    channel_id: int = Field(..., description="Channel ID")
    guild_id: Optional[int] = Field(None, description="Guild ID")
    author_id: int = Field(..., description="Author (user) ID")
    content: Optional[str] = Field(None, description="Message text content")
    embed_content: Optional[EmbedPayload] = Field(None, description="Message embed content")
    timestamp: datetime = Field(..., description="When the message was created")
    edited_timestamp: Optional[datetime] = Field(None, description="When the message was last edited")
    message_type: str = Field("general", description="Type of message")

class MessageSummary(BaseModel):
    """Minimal message summary for Discord Gateway conversions."""
    id: int = Field(..., description="Message ID")
    author_id: int = Field(..., description="Author (user) ID")
    content: Optional[str] = Field(None, description="Message text content")
    timestamp: datetime = Field(..., description="When the message was created")

class MessageCreateRequest(BaseModel):
    """Request schema for creating messages. Context inferred from URI."""
    content: EmbedPayload = Field(..., description="Message embed content")
    message_type: str = Field("general", description="Type of message")

class MessageUpdateRequest(BaseModel):
    """Request schema for updating messages. Context inferred from URI."""
    content: EmbedPayload = Field(..., description="Updated message embed content")
    message_type: str = Field("general", description="Type of message")

class MessageResponse(BaseResponse):
    """Response model for single message endpoint."""
    data: Message = Field(..., description="Message data")

class MessageListResponse(PaginatedResponse):
    """Response model for message list endpoint."""
    data: List[Message] = Field(..., description="List of messages")