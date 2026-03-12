"""
Discord object conversion utilities for Discord Gateway service.

This module provides bidirectional conversion between JSON payloads
and Discord objects, ensuring 100% consistency and round-trip accuracy.
All converters are completely generic and contain no business logic.
"""

from contextlib import suppress
from typing import Any

import discord
from api.schemas.channel_schemas import Category, Channel, Thread
from api.schemas.guild_schemas import Guild
from api.schemas.message_schemas import Message, MessageSummary
from api.schemas.permission_schemas import PermissionOverwrite
from api.schemas.role_schemas import Role
from api.schemas.user_schemas import Member, User
from shared import bblogger

from utils.discord_helpers import tag_to_dict
from utils.embed_converter import EmbedConverter

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
            # Handle required fields with proper defaults
            features = getattr(guild, "features", None) or []
            premium_tier = getattr(guild, "premium_tier", None)
            premium_tier = 0 if premium_tier is None else premium_tier
            return Guild(
                id=guild.id,
                name=guild.name,
                icon=icon_url,
                member_count=getattr(guild, "member_count", 0) or 0,
                owner_id=getattr(guild, "owner_id", 0) or 0,
                description=getattr(guild, "description", None),
                created_at=getattr(getattr(guild, "created_at", None), "isoformat", lambda: "")(),
                features=features,
                verification_level=getattr(getattr(guild, "verification_level", None), "name", "") or "",
                default_notifications=getattr(getattr(guild, "default_notifications", None), "name", "") or "",
                explicit_content_filter=getattr(getattr(guild, "explicit_content_filter", None), "name", "") or "",
                mfa_level=getattr(getattr(guild, "mfa_level", None), "name", "") or "",
                premium_tier=premium_tier,
                premium_subscription_count=getattr(guild, "premium_subscription_count", None),
                preferred_locale=getattr(getattr(guild, "preferred_locale", None), "value", "") or "",
                nsfw_level=getattr(getattr(guild, "nsfw_level", None), "name", None),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting guild to summary")
            raise

    # alias detail to summary since single Guild model covers both
    guild_to_detail = guild_to_summary


class ChannelConverter:
    @staticmethod
    def _coerce_position(pos: Any) -> int:
        try:
            return 0 if pos is None else int(pos)
        except Exception:  # pylint: disable=broad-exception-caught
            return 0

    @staticmethod
    def channel_to_summary(channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel) -> Channel:
        """
        Convert a Discord channel to a summary payload.
        """
        flogger.debug(
            f"channel_to_summary called for channel: {getattr(channel, 'name', None)} ({getattr(channel, 'id', None)})"
        )
        try:
            position = ChannelConverter._coerce_position(getattr(channel, "position", None))
            return Channel(
                id=channel.id,
                name=channel.name,
                type=getattr(getattr(channel, "type", None), "name", None),
                position=position,
                guild_id=getattr(getattr(channel, "guild", None), "id", None),
                created_at=getattr(getattr(channel, "created_at", None), "isoformat", lambda: "")(),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting channel to summary")
            raise

    @staticmethod
    def channel_to_detail(
        channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel | discord.ForumChannel,
    ) -> Channel:
        """
        Convert a Discord channel to a full detail payload.
        """
        flogger.debug(
            f"channel_to_detail called for channel: {getattr(channel, 'name', None)} ({getattr(channel, 'id', None)})"
        )
        try:
            position = ChannelConverter._coerce_position(getattr(channel, "position", None))
            data: dict[str, Any] = {
                "id": channel.id,
                "name": channel.name,
                "type": getattr(getattr(channel, "type", None), "name", None),
                "position": position,
                "guild_id": getattr(getattr(channel, "guild", None), "id", None),
                "created_at": getattr(getattr(channel, "created_at", None), "isoformat", lambda: "")(),
            }

            # common extended fields
            data.update(
                {
                    "topic": getattr(channel, "topic", None),
                    "nsfw": getattr(channel, "nsfw", False),
                    "slowmode_delay": getattr(channel, "slowmode_delay", None),
                    "bitrate": getattr(channel, "bitrate", None),
                    "user_limit": getattr(channel, "user_limit", None),
                    "category_id": getattr(channel, "category_id", None),
                    "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", None),
                }
            )

            return Channel(**data)
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting channel to detail")
            raise

    @staticmethod
    def category_to_detail(category: discord.CategoryChannel) -> Category:
        """
        Convert a Discord category to a payload.
        """
        flogger.debug(
            f"category_to_detail called for category: "
            f"{getattr(category, 'name', None)} ({getattr(category, 'id', None)})"
        )
        try:
            position = ChannelConverter._coerce_position(getattr(category, "position", None))
            return Category(
                id=category.id,
                name=category.name,
                position=position,
                guild_id=getattr(getattr(category, "guild", None), "id", None),
                created_at=getattr(getattr(category, "created_at", None), "isoformat", lambda: "")(),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting category to detail")
            raise

    @staticmethod
    def thread_to_summary(thread: discord.Thread | discord.TextChannel | discord.Thread) -> Thread:
        """
        Convert a Discord Thread (or thread-like channel) to a Thread payload.

        Defensive: uses getattr with sensible defaults to avoid attribute errors
        across discord.py versions and different runtime objects.
        """
        flogger.debug(
            f"thread_to_summary called for thread: {getattr(thread, 'name', None)} ({getattr(thread, 'id', None)})"
        )
        try:
            # parent/channel id: some versions expose parent_id, some expose parent object
            parent_id = getattr(thread, "parent_id", None)
            if parent_id is None:
                parent = getattr(thread, "parent", None) or getattr(thread, "channel", None)
                parent_id = getattr(parent, "id", None) if parent is not None else None

            guild_obj = getattr(thread, "guild", None)
            guild_id = getattr(guild_obj, "id", None) if guild_obj is not None else None

            # owner info: prefer owner_id then owner attribute then fallbacks
            owner_id = getattr(thread, "owner_id", None)
            if owner_id is None:
                owner = getattr(thread, "owner", None) or getattr(thread, "creator", None)
                owner_id = getattr(owner, "id", None) if owner is not None else None
            try:
                owner_id_int = int(owner_id) if owner_id is not None else 0
            except Exception:  # pylint: disable=broad-exception-caught
                owner_id_int = 0

            # numeric optional fields
            def _int_or_none(v):
                try:
                    return int(v) if v is not None else None
                except Exception:  # pylint: disable=broad-exception-caught
                    return None

            msg_count = _int_or_none(getattr(thread, "message_count", None))
            mem_count = _int_or_none(getattr(thread, "member_count", None))
            default_auto = _int_or_none(
                getattr(thread, "auto_archive_duration", None) or getattr(thread, "default_auto_archive_duration", None)
            )
            last_msg = _int_or_none(getattr(thread, "last_message_id", None))

            created_at = getattr(getattr(thread, "created_at", None), "isoformat", lambda: "")()

            # --- New: extract applied tag ids and (when available) full tag payloads ---
            applied_tag_ids = None
            applied_tags_payload = None
            try:
                # Many discord.py variants expose thread.applied_tags as a list of Tag objects or ints
                applied = getattr(thread, "applied_tags", None)
                if applied is not None:
                    # normalize to list of ids when possible, and also produce tag payloads when Tag objects present
                    ids = []
                    payloads = []
                    for at in applied:
                        # if element is an object with id attribute
                        tid = getattr(at, "id", None)
                        if tid is None:
                            # might be an int
                            try:
                                tid = int(at)
                            except Exception:  # pylint: disable=broad-exception-caught
                                tid = None
                        if tid is not None:
                            ids.append(int(tid))
                        # try to build full tag payload if the object looks like a Tag
                        if hasattr(at, "name") or hasattr(at, "emoji"):
                            with suppress(Exception):
                                payloads.append(ChannelConverter.forum_tag_to_payload(at, channel_id=parent_id))
                    applied_tag_ids = ids if ids else None
                    applied_tags_payload = payloads if payloads else None
                else:
                    # fallback to attribute applied_tag_ids (some libs expose this)
                    atids = getattr(thread, "applied_tag_ids", None)
                    if atids:
                        try:
                            applied_tag_ids = [int(x) for x in atids]
                        except Exception:  # pylint: disable=broad-exception-caught
                            applied_tag_ids = None
            except Exception:  # pylint: disable=broad-exception-caught
                # non-fatal; leave tag info as None if anything goes wrong
                applied_tag_ids = None
                applied_tags_payload = None

            return Thread(
                id=int(getattr(thread, "id", 0)),
                name=getattr(thread, "name", "") or "",
                channel_id=int(parent_id) if parent_id is not None else 0,
                guild_id=int(guild_id) if guild_id is not None else None,
                owner_id=owner_id_int,
                archived=bool(getattr(thread, "archived", False)),
                locked=bool(getattr(thread, "locked", False)),
                message_count=msg_count,
                member_count=mem_count,
                default_auto_archive_duration=default_auto,
                created_at=created_at,
                last_message_id=last_msg,
                # attach new tag fields
                applied_tag_ids=applied_tag_ids,
                applied_tags=applied_tags_payload,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting thread to summary")
            raise

    # alias detail to summary since single Thread model covers both
    thread_to_detail = thread_to_summary

    @staticmethod
    def forum_tag_to_payload(tag, channel_id: int | None = None) -> dict:
        """
        Convert a discord ForumTag (or similar object) to the API payload:
          { "id": int, "channel_id": int, "name": str, "emoji": Optional[str] }

        Delegates to utils.discord_helpers.tag_to_dict(...) for consistent behavior.
        """
        try:
            payload = tag_to_dict(tag, channel_id=channel_id)
            # Ensure fields conform to expected types (id -> int or None, channel_id -> int or None)
            return {
                "id": payload.get("id"),
                "channel_id": payload.get("channel_id"),
                "name": payload.get("name"),
                "emoji": payload.get("emoji"),
            }
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting forum tag to payload")
            raise


class PermissionConverter:
    @staticmethod
    def overwrite_to_payload(
        target: discord.Role | discord.Member, overwrite: discord.PermissionOverwrite, channel_id: int | None = None
    ) -> PermissionOverwrite:
        """
        Convert a Discord permission overwrite to a PermissionOverwrite payload.
        channel_id is optional (None for non-channel-scoped contexts).
        """
        flogger.debug(
            f"overwrite_to_payload for target: {getattr(target, 'name', None)} "
            f"({getattr(target, 'id', None)}) on channel {channel_id}"
        )
        try:
            allow, deny = overwrite.pair()
            target_type = "role" if isinstance(target, discord.Role) else "member"
            return PermissionOverwrite(
                id=f"{int(channel_id)}:{int(target.id)}" if channel_id is not None else None,
                channel_id=channel_id,
                target_id=int(target.id),
                type=target_type,
                allow=getattr(allow, "value", 0),
                deny=getattr(deny, "value", 0),
            )
        except Exception:  # pylint: disable=broad-exception-caught
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
            tags_data: dict[str, Any] | None = None
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
                tags=tags_data,
            )
        except Exception:  # pylint: disable=broad-exception-caught
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
                public_flags=getattr(getattr(user, "public_flags", None), "value", 0),
            )
        except Exception:  # pylint: disable=broad-exception-caught
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
                permissions=getattr(getattr(member, "guild_permissions", None), "value", 0),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting member to payload")
            raise


class MessageConverter:
    @staticmethod
    def message_to_payload(message: discord.Message) -> Message:
        """
        Convert a discord.Message into a full Message Pydantic model.

        Behavior:
        - If the Discord message has embeds, prefer the first embed and convert it
            into an EmbedPayload via EmbedConverter.embed_to_payload().
        - If embed conversion fails, fall back to leaving content as None.
        - Populate required Message fields (id, channel_id, author_id, timestamp).
        - Returns an instance of api.schemas.message_schemas.Message.

        Note: This implementation assumes Message.content expects an EmbedPayload (the
        full Message schema). If you need text-only fallback, see the
        message_to_summary variant below.
        """
        try:
            content_payload = None

            # Prefer first embed as stored structured content when present
            embeds = getattr(message, "embeds", None)
            if embeds:
                try:
                    content_payload = EmbedConverter.embed_to_payload(embeds[0])
                except Exception:  # pylint: disable=broad-exception-caught
                    # best-effort: convert may fail for odd embed shapes; log and continue
                    flogger.exception("embed_to_payload failed while converting message; content left as None")
                    content_payload = None

            # Compose the full message payload that matches the Message schema
            msg = Message(
                id=int(message.id),
                channel_id=int(getattr(message.channel, "id", 0)),
                guild_id=(int(getattr(message, "guild", None).id) if getattr(message, "guild", None) else None),
                author_id=int(getattr(getattr(message, "author", None), "id", 0)),
                content=(content_payload if content_payload is not None else None),
                timestamp=getattr(message, "created_at", None),
                edited_timestamp=getattr(message, "edited_at", None),
                message_type=(
                    getattr(getattr(message, "type", None), "name", "default")
                    if getattr(message, "type", None) is not None
                    else "default"
                ),
            )
            return msg
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting message to full Message payload")
            raise

    @staticmethod
    def message_to_summary(message: discord.Message) -> MessageSummary:
        """
        Convert a discord.Message into a MessageSummary (text-focused).

        Behavior:
        - Prefer plain text message.content if non-empty.
        - Otherwise prefer the first embed: use EmbedConverter.embed_to_payload()
            to extract a structured EmbedPayload and then choose a best textual
            representation (description, then title, then joined fields, then footer).
        - If EmbedConverter fails, fall back to raw embed attributes (description/title/fields).
        - Returns an instance of api.schemas.message_schemas.MessageSummary.
        """
        try:
            # 1) Prefer plain text content when present
            text_content = None
            raw_text = getattr(message, "content", None)
            if raw_text:
                s = raw_text.strip()
                if s:
                    text_content = s

            # 2) If no plain text, try to extract a texty representation from the first embed
            if not text_content:
                embeds = getattr(message, "embeds", None) or []
                if embeds:
                    e = embeds[0]
                    try:
                        # Use the canonical converter to get an EmbedPayload
                        ep = EmbedConverter.embed_to_payload(e)
                        # Prefer description, then title, then fields, then footer
                        if getattr(ep, "description", None):
                            text_content = ep.description
                        elif getattr(ep, "title", None):
                            text_content = ep.title
                        elif getattr(ep, "fields", None):
                            # join fields as "name: value" pairs
                            fld_texts = []
                            for f in ep.fields:
                                try:
                                    fld_texts.append(f"{f.name}: {f.value}")
                                except Exception:  # pylint: disable=broad-exception-caught
                                    # defensive: skip malformed field
                                    continue
                            text_content = " | ".join(fld_texts) if fld_texts else None
                        elif getattr(ep, "footer_text", None):
                            text_content = ep.footer_text
                        else:
                            text_content = None
                    except Exception:  # pylint: disable=broad-exception-caught
                        # best-effort fallback to raw embed attributes if converter fails
                        flogger.exception("EmbedConverter.embed_to_payload failed; falling back to raw embed attrs")
                        desc = getattr(e, "description", None)
                        title = getattr(e, "title", None)
                        fields = getattr(e, "fields", None)
                        if desc:
                            text_content = desc
                        elif title:
                            text_content = title
                        elif fields:
                            try:
                                text_content = " | ".join(f"{f.name}: {f.value}" for f in fields)
                            except Exception:  # pylint: disable=broad-exception-caught
                                text_content = None

            # 3) Build and return the MessageSummary
            summary = MessageSummary(
                id=int(message.id),
                author_id=int(getattr(getattr(message, "author", None), "id", 0)),
                content=text_content,
                timestamp=getattr(message, "created_at", None),
            )
            return summary
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.exception("Error converting message to MessageSummary")
            raise
