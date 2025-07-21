"""
SQLAlchemy model for Discord messages with embed support.

This module defines the DiscordMessage model for persisting Discord message
information including embed payloads with proper composite key constraints.
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, BigInteger, DateTime, Text, UniqueConstraint, Index
from datetime import datetime, timezone
from persist.models.base import Base
from persist.database.tablenames import TableNames

class DiscordMessage(Base):
    """Model for Discord message persistence with embed support."""
    __tablename__ = TableNames.DiscordMessage.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    
    # Store the complete embed payload as JSON
    embed_payload: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Composite unique constraint for (guild_id, channel_id, message_id)
    __table_args__ = (
        UniqueConstraint('guild_id', 'channel_id', 'message_id', name='uq_guild_channel_message'),
        # Index for efficient lookups
        Index('ix_discord_message_guild_channel', 'guild_id', 'channel_id'),
        Index('ix_discord_message_type_guild_channel', 'message_type', 'guild_id', 'channel_id'),
        Index('ix_discord_message_created_at', 'created_at'),
    )

    def __repr__(self) -> str:
        return (f"<DiscordMessage(id={self.id}, guild_id={self.guild_id}, "
                f"channel_id={self.channel_id}, message_id={self.message_id}, "
                f"type='{self.message_type}')>")
