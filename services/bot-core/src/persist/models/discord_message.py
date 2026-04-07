"""
SQLAlchemy model for Discord messages with embed support.

This module defines the DiscordMessage model for persisting Discord message
information including embed payloads with proper composite key constraints.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy_utils import UUIDType

from persist.database.tablenames import TableNames
from persist.models.base import Base


class DiscordMessage(Base):
    """Model for Discord message persistence with embed support."""

    __tablename__ = TableNames.DiscordMessage.value

    # cross-dialect UUID column: native on Postgres, CHAR(36) elsewhere
    id: Mapped[uuid.UUID] = mapped_column(UUIDType(binary=False), primary_key=True, default=uuid.uuid4, nullable=False)

    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    embed_payload: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    reference_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        # ensure one record per guild/channel/message-ID triple
        UniqueConstraint("guild_id", "channel_id", "message_id", name="uq_guild_channel_message"),
        # useful lookup indices
        Index("ix_discord_message_guild_channel", "guild_id", "channel_id"),
        Index("ix_discord_message_type_guild_channel", "message_type", "guild_id", "channel_id"),
        Index("ix_discord_message_created_at", "created_at"),
        # composite index for reference-based lookups (e.g. bounty announcements)
        Index("ix_discord_message_reference", "guild_id", "message_type", "reference_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<DiscordMessage(id={self.id}, guild_id={self.guild_id}, "
            f"channel_id={self.channel_id}, message_id={self.message_id}, "
            f"type='{self.message_type}')>"
        )
