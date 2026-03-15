"""
Pydantic schemas for Discord message operations.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class EmbedPayloadDict(BaseModel):
    """Embed payload as dictionary for storage."""
    title: str | None = None
    description: str | None = None
    color: int | None = None
    fields: list[dict] = []
    footer_text: str | None = None
    footer_icon_url: str | None = None
    timestamp: str | None = None
    thumbnail_url: str | None = None
    image_url: str | None = None


class DiscordMessageRequest(BaseModel):
    """Request model for generic Discord message operations."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    message_id: int | None = Field(None, description="Discord message ID (use for update/get/delete operations)")
    embed_payload: EmbedPayloadDict = Field(..., description="Embed payload for the message")
    message_type: str = Field("general", description="Type of message")


class DiscordMessageResponse(BaseModel):
    """Response model for Discord message operations."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID

    @field_serializer("id")
    @classmethod
    def serialize_uuid(cls, v: UUID) -> str:
        return str(v)
    guild_id: int
    channel_id: int
    message_id: int
    embed_payload: str
    message_type: str
    created_at: datetime
    updated_at: datetime
