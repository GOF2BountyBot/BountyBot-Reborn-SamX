"""
Pydantic schemas for Discord message API endpoints.

This module defines request/response models for Discord message operations
including create, update, and delete with proper embed payload structures.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from api.schemas.base_schemas import BaseResponse, PaginatedResponse


class EmbedField(BaseModel):
    """Embed field structure."""

    name: str = Field(..., description="Field name")
    value: str = Field(..., description="Field value")
    inline: bool = Field(False, description="Whether field should be inline")


class EmbedPayload(BaseModel):
    """Standard embed payload structure for message content."""

    title: str | None = Field(None, description="Embed title")
    description: str | None = Field(None, description="Embed description")
    color: int | None = Field(None, description="Embed color as integer")
    fields: list[EmbedField] = Field(default_factory=list, description="Embed fields")
    footer_text: str | None = Field(None, description="Footer text")
    footer_icon_url: str | None = Field(None, description="Footer icon URL")
    timestamp: datetime | None = Field(None, description="Embed timestamp")
    thumbnail_url: str | None = Field(None, description="Thumbnail image URL")
    image_url: str | None = Field(None, description="Main image URL")


class Message(BaseModel):
    """Consolidated message information model."""

    id: int = Field(..., description="Message ID")
    channel_id: int = Field(..., description="Channel ID")
    guild_id: int | None = Field(None, description="Guild ID")
    author_id: int = Field(..., description="Author (user) ID")
    # content is an embed payload (matches MessageCreateRequest/UpdateRequest)
    content: EmbedPayload | None = Field(None, description="Message embed/content payload")
    timestamp: datetime | None = Field(..., description="When the message was created")
    edited_timestamp: datetime | None = Field(None, description="When the message was last edited")
    message_type: str = Field("default", description="Type of message")


class MessageSummary(BaseModel):
    """Minimal message summary for Discord Gateway conversions."""

    id: int = Field(..., description="Message ID")
    author_id: int = Field(..., description="Author (user) ID")
    content: str | None = Field(None, description="Message text content")
    timestamp: datetime | None = Field(..., description="When the message was created")


class MessageCreateRequest(BaseModel):
    """Request schema for creating messages. Context inferred from URI."""

    content: EmbedPayload = Field(..., description="Message embed content")
    text_content: str | None = Field(None, description="Plain text content (e.g. role mentions) sent alongside embed")
    message_type: str = Field("default", description="Type of message")


class MessageUpdateRequest(BaseModel):
    """Request schema for updating messages. Context inferred from URI."""

    content: EmbedPayload = Field(..., description="Updated message embed content")
    message_type: str = Field("default", description="Type of message")


class MessageResponse(BaseResponse):
    """Response model for single message endpoint."""

    data: Message | MessageSummary = Field(..., description="Message data")


class MessageListResponse(PaginatedResponse):
    """Response model for message list endpoint."""

    data: list[Message | MessageSummary] = Field(..., description="List of messages")


class FileUploadData(BaseModel):
    """Data returned after a successful file upload to a Discord channel."""

    message_id: int = Field(..., description="ID of the Discord message containing the attachment")
    attachment_url: str = Field(..., description="CDN URL of the uploaded attachment")
    filename: str = Field(..., description="Filename of the uploaded attachment")
    size: int = Field(..., description="Size of the uploaded attachment in bytes")


class FileUploadResponse(BaseResponse):
    """Response model for the file upload endpoint."""

    data: FileUploadData = Field(..., description="Upload result data")


class BatchFileUploadData(BaseModel):
    """Data for a single file in a batch-upload response."""

    attachment_url: str = Field(..., description="CDN URL of the uploaded attachment")
    filename: str = Field(..., description="Filename of the uploaded attachment")
    size: int = Field(..., description="Size of the uploaded attachment in bytes")


class BatchFileUploadResponse(BaseResponse):
    """Response model for the batch file upload endpoint.

    All files in a single batch are uploaded as attachments on ONE Discord
    message. Discord limits this to 10 attachments per message.
    """

    message_id: int = Field(..., description="ID of the Discord message containing all attachments")
    data: list[BatchFileUploadData] = Field(..., description="Per-file upload result, indexed by filename")
