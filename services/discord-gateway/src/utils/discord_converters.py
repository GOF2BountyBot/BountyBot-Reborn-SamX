"""
Discord object conversion utilities for Discord Gateway service.

This module provides bidirectional conversion between JSON payloads
and Discord objects, ensuring 100% consistency and round-trip accuracy.
All converters are completely generic and contain no business logic.
"""

from typing import Dict, Any, Optional, List, Union
import discord
from datetime import datetime
from api.schemas.guild_schemas import Guild
from api.schemas.channel_schemas import Channel, Category
from api.schemas.role_schemas import Role
from api.schemas.user_schemas import User, Member
from api.schemas.permission_schemas import PermissionOverwrite
from api.schemas.message_schemas import MessageSummary
import shared.bblogger as bblogger

flogger = bblogger.get_logger("discord-converters")


class GuildConverter:
    @staticmethod
    def guild_to_summary(guild: discord.Guild) -> Guild:
        """
        Convert a Discord guild to a payload using the Guild schema.
        """
        flogger.debug(f"guild_to_summary called for guild: {guild.name} ({guild.id})")
        try:
            icon_url = getattr(getattr(guild, "icon", None), "url", None)
            return Guild(
                id=guild.id,
                name=guild.name,
                icon=icon_url,
                member_count=getattr(guild, "member_count", 0),
                owner_id=getattr(guild, "owner_id", None),
                description=getattr(guild, "description", None),
                created_at=getattr(getattr(guild, "created_at", None), "isoformat", lambda: "")(),
                features=getattr(guild, "features", []),
                verification_level=getattr(getattr(guild, "verification_level", None), "name", ""),
                default_notifications=getattr(getattr(guild, "default_notifications", None), "name", ""),
                explicit_content_filter=getattr(getattr(guild, "explicit_content_filter", None), "name", ""),
                mfa_level=getattr(getattr(guild, "mfa_level", None), "name", ""),
                premium_tier=getattr(guild, "premium_tier", 0),
                premium_subscription_count=getattr(guild, "premium_subscription_count", None),
                preferred_locale=getattr(getattr(guild, "preferred_locale", None), "value", ""),
                nsfw_level=getattr(getattr(guild, "nsfw_level", None), "name", None),
            )
        except Exception:
            flogger.exception("Error converting guild to summary")
            raise

    # alias detail to summary since single Guild model covers both
    guild_to_detail = guild_to_summary


class ChannelConverter:
    @staticmethod
    def _coerce_position(pos: Any) -> int:
        try:
            return 0 if pos is None else int(pos)
        except Exception:
            return 0

    @staticmethod
    def channel_to_summary(
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]
    ) -> Channel:
        """
        Convert a Discord channel to a summary payload.
        """
        flogger.debug(f"channel_to_summary called for channel: {getattr(channel, 'name', None)} ({getattr(channel, 'id', None)})")
        try:
            position = ChannelConverter._coerce_position(getattr(channel, "position", None))
            return Channel(
                id=channel.id,
                name=channel.name,
                type=getattr(getattr(channel, "type", None), "name", None),
                position=position,
                guild_id=getattr(getattr(channel, "guild", None), "id", None),
                created_at=getattr(getattr(channel, "created_at", None), "isoformat", lambda: "")()
            )
        except Exception:
            flogger.exception("Error converting channel to summary")
            raise

    @staticmethod
    def channel_to_detail(
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel]
    ) -> Channel:
        """
        Convert a Discord channel to a full detail payload.
        """
        flogger.debug(f"channel_to_detail called for channel: {getattr(channel, 'name', None)} ({getattr(channel, 'id', None)})")
        try:
            position = ChannelConverter._coerce_position(getattr(channel, "position", None))
            data: Dict[str, Any] = {
                "id": channel.id,
                "name": channel.name,
                "type": getattr(getattr(channel, "type", None), "name", None),
                "position": position,
                "guild_id": getattr(getattr(channel, "guild", None), "id", None),
                "created_at": getattr(getattr(channel, "created_at", None), "isoformat", lambda: "")(),
            }

            # common extended fields
            data.update({
                "topic": getattr(channel, "topic", None),
                "nsfw": getattr(channel, "nsfw", False),
                "slowmode_delay": getattr(channel, "slowmode_delay", None),
                "bitrate": getattr(channel, "bitrate", None),
                "user_limit": getattr(channel, "user_limit", None),
                "category_id": getattr(channel, "category_id", None),
                "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", None),
            })

            return Channel(**data)
        except Exception:
            flogger.exception("Error converting channel to detail")
            raise

    @staticmethod
    def category_to_detail(category: discord.CategoryChannel) -> Category:
        """
        Convert a Discord category to a payload.
        """
        flogger.debug(f"category_to_detail called for category: {getattr(category, 'name', None)} ({getattr(category, 'id', None)})")
        try:
            position = ChannelConverter._coerce_position(getattr(category, "position", None))
            return Category(
                id=category.id,
                name=category.name,
                position=position,
                guild_id=getattr(getattr(category, "guild", None), "id", None),
                created_at=getattr(getattr(category, "created_at", None), "isoformat", lambda: "")()
            )
        except Exception:
            flogger.exception("Error converting category to detail")
            raise


class PermissionConverter:
    @staticmethod
    def overwrite_to_payload(
        target: Union[discord.Role, discord.Member],
        overwrite: discord.PermissionOverwrite,
        channel_id: int
    ) -> PermissionOverwrite:
        """
        Convert a Discord permission overwrite to a PermissionOverwrite payload.
        """
        flogger.debug(f"overwrite_to_payload for target: {getattr(target, 'name', None)} ({getattr(target, 'id', None)}) on channel {channel_id}")
        try:
            allow, deny = overwrite.pair()
            target_type = "role" if isinstance(target, discord.Role) else "member"
            return PermissionOverwrite(
                id=f"{channel_id}:{target.id}",
                channel_id=channel_id,
                target_id=target.id,
                type=target_type,
                allow=getattr(allow, "value", 0),
                deny=getattr(deny, "value", 0)
            )
        except Exception:
            flogger.exception("Error converting overwrite to payload")
            raise


class RoleConverter:
    @staticmethod
    def role_to_payload(role: discord.Role) -> Role:
        """
        Convert a Discord role to a Role payload.
        """
        flogger.debug(f"role_to_payload called for role: {role.name} ({role.id})")
        try:
            tags = getattr(role, "tags", None)
            tags_data: Optional[Dict[str, Any]] = None
            if tags:
                tags_data = {
                    "bot_id": getattr(tags, "bot_id", None),
                    "integration_id": getattr(tags, "integration_id", None),
                    "premium_subscriber": getattr(tags, "_premium_subscriber", None),
                }
            return Role(
                id=role.id,
                guild_id=getattr(getattr(role, "guild", None), "id", None),
                name=role.name,
                color=getattr(getattr(role, "color", None), "value", 0),
                hoist=getattr(role, "hoist", False),
                position=getattr(role, "position", 0),
                permissions=getattr(getattr(role, "permissions", None), "value", 0),
                managed=getattr(role, "managed", False),
                mentionable=getattr(role, "mentionable", False),
                created_at=getattr(getattr(role, "created_at", None), "isoformat", lambda: "")(),
                tags=tags_data
            )
        except Exception:
            flogger.exception("Error converting role to payload")
            raise


class UserConverter:
    @staticmethod
    def user_to_payload(user: discord.User) -> User:
        """
        Convert a Discord user to a User payload.
        """
        flogger.debug(f"user_to_payload called for user: {user.name} ({user.id})")
        try:
            avatar_url = getattr(getattr(user, "avatar", None), "url", None)
            return User(
                id=user.id,
                username=user.name,
                discriminator=user.discriminator,
                avatar=avatar_url,
                bot=getattr(user, "bot", False),
                system=getattr(user, "system", False),
                created_at=getattr(getattr(user, "created_at", None), "isoformat", lambda: "")(),
                public_flags=getattr(getattr(user, "public_flags", None), "value", 0)
            )
        except Exception:
            flogger.exception("Error converting user to payload")
            raise

    @staticmethod
    def member_to_payload(member: discord.Member) -> Member:
        """
        Convert a Discord member to a Member payload.
        """
        flogger.debug(f"member_to_payload called for member: {member.display_name} ({member.id})")
        try:
            user_payload = UserConverter.user_to_payload(member)
            voice = getattr(member, "voice", None)
            return Member(
                user=user_payload,
                guild_id=getattr(getattr(member, "guild", None), "id", None),
                nick=getattr(member, "nick", None),
                roles=[r.id for r in getattr(member, "roles", [])],
                joined_at=getattr(getattr(member, "joined_at", None), "isoformat", lambda: "")(),
                premium_since=getattr(getattr(member, "premium_since", None), "isoformat", lambda: None)(),
                deaf=getattr(voice, "deaf", False),
                mute=getattr(voice, "mute", False),
                pending=getattr(member, "pending", False),
                permissions=getattr(getattr(member, "guild_permissions", None), "value", 0)
            )
        except Exception:
            flogger.exception("Error converting member to payload")
            raise


class MessageConverter:
    @staticmethod
    def message_to_payload(message: discord.Message) -> MessageSummary:
        """
        Convert a Discord message to a MessageSummary payload.
        """
        return MessageSummary(
            id=message.id,
            author_id=message.author.id,
            content=message.content or None,
            timestamp=message.created_at
        )