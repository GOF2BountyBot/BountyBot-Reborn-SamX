"""
Repository for Discord message data access.

This module provides data access methods for Discord message persistence
following the repository pattern with embed payload support.
"""

from datetime import UTC, datetime

from shared import bblogger
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.discord_message import DiscordMessage
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-discord-message-repository")


class DiscordMessageRepository(GenericRepository[DiscordMessage]):
    """Repository for Discord message operations with embed support."""

    def __init__(self):
        super().__init__(DiscordMessage)

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict,
    ) -> DiscordMessage:
        """
        Create or update a Discord message record.

        Args:
            db: Database session
            raw: Dictionary with message data including embed_payload

        Returns:
            DiscordMessage object
        """
        flogger.trace(
            f"Creating or updating Discord message: guild_id={raw.get('guild_id')}, "
            f"channel_id={raw.get('channel_id')}, message_id={raw.get('message_id')}"
        )

        try:
            guild_id = raw["guild_id"]
            channel_id = raw["channel_id"]
            message_id = raw["message_id"]

            existing = await self.get_by_composite_key(db, guild_id, channel_id, message_id)

            if existing:
                existing.embed_payload = raw["embed_payload"]
                existing.message_type = raw.get("message_type", "general")
                existing.updated_at = datetime.now(UTC)
                await db.commit()
                await db.refresh(existing)
                flogger.debug(
                    f"Updated Discord message {existing.id}: type='{existing.message_type}', "
                    f"guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}"
                )
                return existing

            message = DiscordMessage(
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                embed_payload=raw["embed_payload"],
                message_type=raw.get("message_type", "general"),
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)
            flogger.debug(
                f"Created Discord message {message.id}: type='{message.message_type}', "
                f"guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}"
            )
            return message
        except Exception as e:
            flogger.error(f"Error creating/updating Discord message: {e}")
            await db.rollback()
            raise

    async def get_by_composite_key(
        self, db: AsyncSession, guild_id: int, channel_id: int, message_id: int
    ) -> DiscordMessage | None:
        """
        Get message by composite key (guild_id, channel_id, message_id).
        """
        flogger.trace(f"Querying message with guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}")
        result = await db.execute(
            select(self._model).where(
                and_(
                    self._model.guild_id == guild_id,
                    self._model.channel_id == channel_id,
                    self._model.message_id == message_id,
                )
            )
        )
        message = result.scalars().one_or_none()
        flogger.trace(f"Found message: {message.id if message else 'None'}")
        return message

    async def get_by_type(
        self, db: AsyncSession, message_type: str, guild_id: int | None = None, channel_id: int | None = None
    ) -> list[DiscordMessage]:
        """
        Get messages by type, optionally filtered by guild and channel,
        ordered by creation date descending.
        """
        flogger.trace(f"Querying messages by type='{message_type}', guild_id={guild_id}, channel_id={channel_id}")
        conditions = [self._model.message_type == message_type]
        if guild_id is not None:
            conditions.append(self._model.guild_id == guild_id)
        if channel_id is not None:
            conditions.append(self._model.channel_id == channel_id)

        result = await db.execute(select(self._model).where(and_(*conditions)).order_by(desc(self._model.created_at)))
        messages = list(result.scalars().all())
        flogger.trace(f"Found {len(messages)} message(s) of type '{message_type}'")
        return messages

    async def list_by_guild(self, db: AsyncSession, guild_id: int) -> list[DiscordMessage]:
        """
        List all messages for a guild ordered by creation date.
        """
        flogger.trace(f"Listing all messages for guild_id={guild_id}")
        result = await db.execute(
            select(self._model).where(self._model.guild_id == guild_id).order_by(desc(self._model.created_at))
        )
        messages = list(result.scalars().all())
        flogger.trace(f"Found {len(messages)} message(s) for guild_id={guild_id}")
        return messages

    async def list_by_channel(self, db: AsyncSession, guild_id: int, channel_id: int) -> list[DiscordMessage]:
        """
        List all messages for a channel ordered by creation date.
        """
        flogger.trace(f"Listing all messages for guild_id={guild_id}, channel_id={channel_id}")
        result = await db.execute(
            select(self._model)
            .where(and_(self._model.guild_id == guild_id, self._model.channel_id == channel_id))
            .order_by(desc(self._model.created_at))
        )
        messages = list(result.scalars().all())
        flogger.trace(f"Found {len(messages)} message(s) for guild_id={guild_id}, channel_id={channel_id}")
        return messages

    async def list_by_guild_and_channel(self, db: AsyncSession, guild_id: int, channel_id: int) -> list[DiscordMessage]:
        """
        List all messages for a specific channel in a guild,
        ordered by creation date descending.
        """
        flogger.trace(f"Listing messages for guild_id={guild_id}, channel_id={channel_id}")
        result = await db.execute(
            select(self._model)
            .where(and_(self._model.guild_id == guild_id, self._model.channel_id == channel_id))
            .order_by(desc(self._model.created_at))
        )
        messages = list(result.scalars().all())
        flogger.trace(f"Found {len(messages)} message(s) for guild_id={guild_id}, channel_id={channel_id}")
        return messages

    async def list_by_guild_and_type(self, db: AsyncSession, guild_id: int, message_type: str) -> list[DiscordMessage]:
        """
        List all messages of a given type in a guild,
        ordered by creation date descending.
        """
        flogger.trace(f"Listing messages for guild_id={guild_id}, type='{message_type}'")
        # reuse the existing get_by_type for filtering
        messages = await self.get_by_type(db, message_type, guild_id=guild_id)
        flogger.trace(f"Found {len(messages)} message(s) of type '{message_type}' for guild_id={guild_id}")
        return messages

    async def delete_by_composite_key(self, db: AsyncSession, guild_id: int, channel_id: int, message_id: int) -> bool:
        """
        Delete message by composite key.
        """
        flogger.trace(
            f"Attempting to delete message with guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}"
        )
        message = await self.get_by_composite_key(db, guild_id, channel_id, message_id)
        if message:
            try:
                await db.delete(message)
                await db.commit()
                flogger.debug(
                    f"Deleted Discord message: {message.id} (guild_id={guild_id}, "
                    f"channel_id={channel_id}, message_id={message_id})"
                )
                return True
            except Exception as e:
                flogger.error(f"Error deleting Discord message {message.id}: {e}")
                await db.rollback()
                raise
        flogger.trace(
            f"No message found to delete for guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}"
        )
        return False
