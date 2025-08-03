"""
Pydantic schemas for Discord message API endpoints.

This module defines request/response models for Discord message operations
including create, update, and delete with proper embed payload structures.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

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

class MessageRequest(BaseModel):
    """Request schema for message operations."""
    guild_id: int = Field(..., description="Discord guild ID (required)")
    channel_id: int = Field(..., description="Discord channel ID (required)")
    content: EmbedPayload = Field(..., description="Message embed content")
    message_type: str = Field("general", description="Type of message")

class MessageUpdateRequest(BaseModel):
    """Request schema for message updates."""
    guild_id: int = Field(..., description="Discord guild ID (required)")
    channel_id: int = Field(..., description="Discord channel ID (required)")
    message_id: int = Field(..., description="Discord message ID (required)")
    content: EmbedPayload = Field(..., description="Updated message embed content")
    message_type: str = Field("general", description="Type of message")

class MessageDeleteRequest(BaseModel):
    """Request schema for message deletion."""
    guild_id: int = Field(..., description="Discord guild ID (required)")
    channel_id: int = Field(..., description="Discord channel ID (required)")
    message_id: int = Field(..., description="Discord message ID (required)")

class MessageResponse(BaseModel):
    """Response schema for message operations."""
    status: str = Field(..., description="Operation status")
    guild_id: Optional[int] = Field(None, description="Discord guild ID")
    channel_id: Optional[int] = Field(None, description="Discord channel ID")
    message_id: Optional[int] = Field(None, description="Discord message ID")
    content: Optional[EmbedPayload] = Field(None, description="Message content (for GET operations)")
    timestamp: Optional[datetime] = Field(None, description="Operation timestamp")
