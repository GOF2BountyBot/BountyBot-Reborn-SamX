"""
Pydantic schemas for Discord message operations.
"""

from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from datetime import datetime


class EmbedPayloadDict(BaseModel):
    """Embed payload as dictionary for storage."""
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    fields: List[dict] = []
    footer_text: Optional[str] = None
    footer_icon_url: Optional[str] = None
    timestamp: Optional[str] = None
    thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None


class DiscordMessageRequest(BaseModel):
    """Request model for generic Discord message operations."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    message_id: Optional[int] = Field(None, description="Discord message ID (use for update/get/delete operations)")
    embed_payload: EmbedPayloadDict = Field(..., description="Embed payload for the message")
    message_type: str = Field("general", description="Type of message")


class DiscordMessageResponse(BaseModel):
    """Response model for Discord message operations."""
    id: UUID
    guild_id: int
    channel_id: int
    message_id: int
    embed_payload: str
    message_type: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {UUID: lambda u: str(u)}
