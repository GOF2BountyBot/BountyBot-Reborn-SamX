"""
Discord mock utilities for testing - comprehensive factory functions for all Discord objects.

This module provides factory functions for creating mock Discord objects used in testing.
It includes:
- Mock factory functions for all Discord objects
- Async-aware mocks using AsyncMock
- Helper functions for common mock patterns
- Comprehensive coverage of Discord.py objects used in the codebase

Design notes
------------
Bot mocks
~~~~~~~~~
``create_mock_bot`` uses ``MagicMock(spec=commands.Bot)`` so that
``isinstance(bot, commands.Bot)`` passes.  This is required by
``resolve_bot`` in ``utils/discord_helpers.py`` which does a hard
isinstance check before returning the bot.

Discord exceptions
~~~~~~~~~~~~~~~~~~
``create_discord_not_found``, ``create_discord_forbidden`` and
``create_discord_http_exception`` return **real** ``discord.NotFound``,
``discord.Forbidden`` and ``discord.HTTPException`` instances (not
MagicMock objects).  Using real instances is the only way to:

1. Pass ``isinstance(exc, discord.NotFound)`` checks used by
   ``handle_discord_exception`` and ``get_entity_or_404``.
2. Be catchable via ``except discord.NotFound`` / ``except
   discord.Forbidden`` / ``except discord.HTTPException`` – Python's
   ``raise``/``except`` mechanism requires that the raised object is an
   actual subclass of ``BaseException``.

The legacy ``create_mock_discord_exception`` helper is retained for
backward compatibility but is **deprecated**; prefer the typed
factories above.

EmbedProxy
~~~~~~~~~~
``create_mock_embed_proxy`` returns a real ``discord.embeds.EmbedProxy``
instance.  Discord never returns ``None`` for ``Embed.footer``,
``Embed.thumbnail``, ``Embed.image``, ``Embed.video``, ``Embed.author``
or ``Embed.provider`` – it returns an ``EmbedProxy`` object that
evaluates to ``False`` when empty (``len == 0``).  Tests should assert
``bool(proxy)`` rather than ``proxy is None``.
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Any, Dict, List, Optional, Union, Type, Callable
import discord
from discord.embeds import EmbedProxy
from discord.ext import commands


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    """
    Minimal stand-in for an aiohttp.ClientResponse used when constructing
    real discord.HTTPException instances in tests.

    discord.HTTPException.__init__ reads:
        self.status = response.status
        self.response = response

    We don't need a full aiohttp response – just these two fields plus a
    ``reason`` attribute for the str() representation.
    """

    def __init__(self, status: int, reason: str = "Test"):
        self.status: int = status
        self.reason: str = reason


# ---------------------------------------------------------------------------
# Exception factories (return REAL discord exception instances)
# ---------------------------------------------------------------------------

def create_discord_not_found(
    text: str = "Not found",
    code: int = 0,
) -> discord.NotFound:
    """
    Return a real ``discord.NotFound`` instance.

    The instance:
    - passes ``isinstance(exc, discord.NotFound)``
    - passes ``isinstance(exc, discord.HTTPException)``
    - is catchable via ``except discord.NotFound``
    - has ``.status == 404``, ``.text``, ``.code``, ``.response``
    """
    resp = _FakeHTTPResponse(404, "Not Found")
    message: Any = {"message": text, "code": code} if code else text
    return discord.NotFound(resp, message)  # type: ignore[arg-type]


def create_discord_forbidden(
    text: str = "Forbidden",
    code: int = 0,
) -> discord.Forbidden:
    """
    Return a real ``discord.Forbidden`` instance.

    - passes ``isinstance(exc, discord.Forbidden)``
    - passes ``isinstance(exc, discord.HTTPException)``
    - is catchable via ``except discord.Forbidden``
    - has ``.status == 403``
    """
    resp = _FakeHTTPResponse(403, "Forbidden")
    message: Any = {"message": text, "code": code} if code else text
    return discord.Forbidden(resp, message)  # type: ignore[arg-type]


def create_discord_http_exception(
    status: int = 400,
    text: str = "Bad request",
    code: int = 0,
) -> discord.HTTPException:
    """
    Return a real ``discord.HTTPException`` instance with an arbitrary status.

    Useful for testing the status-based branching in
    ``handle_discord_exception``:
    - 400-range → HTTP 400 Bad Request (or mapped 403/404)
    - 500-range → HTTP 502 Bad Gateway

    - passes ``isinstance(exc, discord.HTTPException)``
    - is catchable via ``except discord.HTTPException``
    - has ``.status``, ``.text``, ``.code``, ``.response``
    """
    resp = _FakeHTTPResponse(status, f"HTTP {status}")
    message: Any = {"message": text, "code": code} if code else text
    return discord.HTTPException(resp, message)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EmbedProxy factory
# ---------------------------------------------------------------------------

def create_mock_embed_proxy(
    **fields: Any,
) -> EmbedProxy:
    """
    Return a real ``discord.embeds.EmbedProxy`` populated with *fields*.

    Background
    ----------
    ``discord.Embed.footer``, ``.thumbnail``, ``.image``, ``.video``,
    ``.author``, and ``.provider`` never return ``None``; they return an
    ``EmbedProxy`` object that is falsy (``bool(proxy) == False``) when
    empty and truthy when populated.

    Tests that check embed sub-objects should NOT do::

        assert embed.footer is None   # WRONG – will always fail

    Instead do::

        assert not embed.footer          # empty/unset
        assert embed.footer.text == ...  # specific field

    Examples
    --------
    >>> proxy = create_mock_embed_proxy()          # empty, falsy
    >>> bool(proxy)
    False
    >>> proxy.text   # returns None via __getattr__
    None

    >>> proxy = create_mock_embed_proxy(text="hello", icon_url="http://x.com")
    >>> bool(proxy)
    True
    >>> proxy.text
    'hello'
    """
    return EmbedProxy(fields)


# ---------------------------------------------------------------------------
# Main utility class
# ---------------------------------------------------------------------------

class DiscordMockUtils:
    """Comprehensive Discord mock utilities for testing."""

    @staticmethod
    def create_mock_user(
        user_id: int = 1,
        username: str = "test_user",
        discriminator: str = "0001",
        avatar: Optional[str] = None,
        bot: bool = False,
        system: bool = False,
        mfa_enabled: bool = False,
        locale: str = "en-US",
        verified: bool = True,
        email: Optional[str] = None,
        flags: int = 0,
        premium_type: int = 0,
        public_flags: int = 0,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord User object."""
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.name = username
        mock_user.username = username
        mock_user.discriminator = discriminator
        mock_user.avatar = avatar
        mock_user.bot = bot
        mock_user.system = system
        mock_user.mfa_enabled = mfa_enabled
        mock_user.locale = locale
        mock_user.verified = verified
        mock_user.email = email
        mock_user.flags = mock_user._flags = MagicMock(value=flags)
        mock_user.premium_type = premium_type
        mock_user.public_flags = mock_user._public_flags = MagicMock(value=public_flags)
        mock_user.mention = f"<@!{user_id}>"
        mock_user.display_name = username
        mock_user.created_at = datetime(2020, 1, 1)

        for key, value in kwargs.items():
            setattr(mock_user, key, value)

        return mock_user

    @staticmethod
    def create_mock_member(
        user_id: int = 1,
        guild_id: int = 1,
        username: str = "test_user",
        discriminator: str = "0001",
        nickname: Optional[str] = None,
        roles: Optional[List[MagicMock]] = None,
        joined_at: Optional[datetime] = None,
        premium_since: Optional[datetime] = None,
        pending: bool = False,
        permissions: Optional[MagicMock] = None,
        guild: Optional[MagicMock] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Member object."""
        if roles is None:
            roles = []

        mock_user = DiscordMockUtils.create_mock_user(
            user_id=user_id,
            username=username,
            discriminator=discriminator,
        )

        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id)

        mock_member = MagicMock()
        mock_member.user = mock_user
        mock_member.guild = guild
        mock_member.id = user_id
        mock_member.name = username
        mock_member.username = username
        mock_member.discriminator = discriminator
        mock_member.nick = nickname
        mock_member.roles = roles
        mock_member.joined_at = joined_at or datetime(2020, 1, 1)
        mock_member.premium_since = premium_since
        mock_member.pending = pending
        mock_member.permissions = permissions or MagicMock()
        mock_member.mention = f"<@!{user_id}>"
        mock_member.display_name = nickname or username
        mock_member.guild_permissions = permissions or MagicMock()
        mock_member.colour = MagicMock()
        mock_member.color = mock_member.colour
        mock_member.voice = None

        for key, value in kwargs.items():
            setattr(mock_member, key, value)

        return mock_member

    @staticmethod
    def create_mock_guild(
        guild_id: int = 1,
        name: str = "test_guild",
        icon: Optional[str] = None,
        icon_url: Optional[str] = None,
        member_count: int = 10,
        owner_id: int = 1,
        description: Optional[str] = None,
        created_at: Optional[datetime] = None,
        features: Optional[List[str]] = None,
        verification_level: str = "none",
        default_notifications: str = "all_messages",
        explicit_content_filter: str = "none",
        mfa_level: str = "none",
        premium_tier: int = 0,
        premium_subscription_count: Optional[int] = None,
        preferred_locale: str = "en-US",
        nsfw_level: Optional[str] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Guild object."""
        if features is None:
            features = []

        mock_guild = MagicMock()
        mock_guild.id = guild_id
        mock_guild.name = name
        mock_guild.icon = MagicMock()
        mock_guild.icon.url = icon_url or f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png"
        mock_guild.member_count = member_count
        mock_guild.owner_id = owner_id
        mock_guild.description = description
        mock_guild.created_at = created_at or datetime(2020, 1, 1)
        mock_guild.features = features

        mock_verification_level = MagicMock()
        mock_verification_level.name = verification_level
        mock_guild.verification_level = mock_verification_level

        mock_default_notifications = MagicMock()
        mock_default_notifications.name = default_notifications
        mock_guild.default_notifications = mock_default_notifications

        mock_explicit_content_filter = MagicMock()
        mock_explicit_content_filter.name = explicit_content_filter
        mock_guild.explicit_content_filter = mock_explicit_content_filter

        mock_mfa_level = MagicMock()
        mock_mfa_level.name = mfa_level
        mock_guild.mfa_level = mock_mfa_level

        mock_guild.premium_tier = premium_tier
        mock_guild.premium_subscription_count = premium_subscription_count

        mock_preferred_locale = MagicMock()
        mock_preferred_locale.value = preferred_locale
        mock_guild.preferred_locale = mock_preferred_locale

        if nsfw_level:
            mock_nsfw_level = MagicMock()
            mock_nsfw_level.name = nsfw_level
            mock_guild.nsfw_level = mock_nsfw_level

        for key, value in kwargs.items():
            setattr(mock_guild, key, value)

        return mock_guild

    @staticmethod
    def create_mock_role(
        role_id: int = 1,
        guild_id: int = 1,
        name: str = "test_role",
        color: int = 0,
        color_value: int = 0,
        hoist: bool = False,
        position: int = 0,
        permissions: int = 0,
        managed: bool = False,
        mentionable: bool = False,
        guild: Optional[MagicMock] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Role object."""
        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id)

        mock_role = MagicMock()
        mock_role.id = role_id
        mock_role.name = name
        mock_role.guild = guild

        mock_color = MagicMock()
        mock_color.value = color_value
        mock_role.color = mock_color
        mock_role.colour = mock_color

        mock_role.colour.value = color_value
        mock_role.color.value = color_value

        mock_role.hoist = hoist
        mock_role.position = position

        mock_permissions = MagicMock()
        mock_permissions.value = permissions
        mock_role.permissions = mock_permissions

        mock_role.managed = managed
        mock_role.mentionable = mentionable
        mock_role.mention = f"@&{role_id}"
        mock_role.created_at = datetime(2020, 1, 1)
        mock_role.tags = None

        for key, value in kwargs.items():
            setattr(mock_role, key, value)

        return mock_role

    @staticmethod
    def create_mock_channel(
        channel_id: int = 1,
        name: str = "test_channel",
        channel_type: str = "text",
        position: int = 0,
        guild_id: int = 1,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Channel object."""
        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id)

        mock_channel = MagicMock()
        mock_channel.id = channel_id
        mock_channel.name = name

        mock_type = MagicMock()
        mock_type.name = channel_type
        mock_channel.type = mock_type

        mock_channel.position = position
        mock_channel.guild = guild
        mock_channel.guild_id = guild_id
        mock_channel.created_at = created_at or datetime(2020, 1, 1)

        # Common optional channel attributes
        mock_channel.topic = None
        mock_channel.nsfw = False
        mock_channel.slowmode_delay = None
        mock_channel.bitrate = None
        mock_channel.user_limit = None
        mock_channel.category_id = None
        mock_channel.default_auto_archive_duration = None

        for key, value in kwargs.items():
            setattr(mock_channel, key, value)

        return mock_channel

    @staticmethod
    def create_mock_text_channel(
        channel_id: int = 1,
        name: str = "test_text_channel",
        position: int = 0,
        guild_id: int = 1,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord TextChannel object."""
        return DiscordMockUtils.create_mock_channel(
            channel_id=channel_id,
            name=name,
            channel_type="text",
            position=position,
            guild_id=guild_id,
            guild=guild,
            created_at=created_at,
            **kwargs
        )

    @staticmethod
    def create_mock_voice_channel(
        channel_id: int = 1,
        name: str = "test_voice_channel",
        position: int = 0,
        guild_id: int = 1,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        bitrate: int = 64000,
        user_limit: int = 0,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord VoiceChannel object."""
        mock_channel = DiscordMockUtils.create_mock_channel(
            channel_id=channel_id,
            name=name,
            channel_type="voice",
            position=position,
            guild_id=guild_id,
            guild=guild,
            created_at=created_at,
            **kwargs
        )

        mock_channel.bitrate = bitrate
        mock_channel.user_limit = user_limit

        return mock_channel

    @staticmethod
    def create_mock_category_channel(
        channel_id: int = 1,
        name: str = "test_category",
        position: int = 0,
        guild_id: int = 1,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord CategoryChannel object."""
        return DiscordMockUtils.create_mock_channel(
            channel_id=channel_id,
            name=name,
            channel_type="category",
            position=position,
            guild_id=guild_id,
            guild=guild,
            created_at=created_at,
            **kwargs
        )

    @staticmethod
    def create_mock_forum_channel(
        channel_id: int = 1,
        name: str = "test_forum",
        position: int = 0,
        guild_id: int = 1,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        available_tags: Optional[List[MagicMock]] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord ForumChannel object."""
        mock_channel = DiscordMockUtils.create_mock_channel(
            channel_id=channel_id,
            name=name,
            channel_type="forum",
            position=position,
            guild_id=guild_id,
            guild=guild,
            created_at=created_at,
            **kwargs
        )
        mock_channel.available_tags = available_tags or []
        return mock_channel

    @staticmethod
    def create_mock_thread(
        thread_id: int = 1,
        name: str = "test_thread",
        guild_id: int = 1,
        parent_id: int = 1,
        owner_id: int = 1,
        archived: bool = False,
        locked: bool = False,
        message_count: int = 0,
        member_count: int = 0,
        auto_archive_duration: int = 1440,
        guild: Optional[MagicMock] = None,
        parent: Optional[MagicMock] = None,
        applied_tags: Optional[List[MagicMock]] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Thread object."""
        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id)

        if parent is None:
            parent = DiscordMockUtils.create_mock_forum_channel(
                channel_id=parent_id, guild_id=guild_id, guild=guild
            )

        mock_thread = MagicMock()
        mock_thread.id = thread_id
        mock_thread.name = name
        mock_thread.guild = guild
        mock_thread.guild_id = guild_id
        mock_thread.parent_id = parent_id
        mock_thread.parent = parent
        mock_thread.owner_id = owner_id
        mock_thread.archived = archived
        mock_thread.locked = locked
        mock_thread.message_count = message_count
        mock_thread.member_count = member_count
        mock_thread.auto_archive_duration = auto_archive_duration
        mock_thread.default_auto_archive_duration = auto_archive_duration
        mock_thread.created_at = datetime(2020, 1, 1)
        mock_thread.last_message_id = None
        mock_thread.applied_tags = applied_tags or []

        mock_type = MagicMock()
        mock_type.name = "public_thread"
        mock_thread.type = mock_type

        for key, value in kwargs.items():
            setattr(mock_thread, key, value)

        return mock_thread

    @staticmethod
    def create_mock_forum_tag(
        tag_id: int = 1,
        name: str = "test_tag",
        emoji: Optional[str] = None,
        channel_id: int = 1,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord ForumTag object."""
        mock_tag = MagicMock()
        mock_tag.id = tag_id
        mock_tag.name = name
        mock_tag.channel_id = channel_id
        # emoji is None (no emoji) or a PartialEmoji-like mock
        mock_tag.emoji = emoji
        for key, value in kwargs.items():
            setattr(mock_tag, key, value)
        return mock_tag

    @staticmethod
    def create_mock_message(
        message_id: int = 1,
        channel_id: int = 1,
        author_id: int = 1,
        content: str = "test message",
        guild_id: int = 1,
        channel: Optional[MagicMock] = None,
        author: Optional[MagicMock] = None,
        guild: Optional[MagicMock] = None,
        created_at: Optional[datetime] = None,
        edited_at: Optional[datetime] = None,
        tts: bool = False,
        mention_everyone: bool = False,
        mentions: Optional[List[MagicMock]] = None,
        mention_roles: Optional[List[MagicMock]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        reactions: Optional[List[Dict[str, Any]]] = None,
        pinned: bool = False,
        type: int = 0,
        activity: Optional[Dict[str, Any]] = None,
        application: Optional[Dict[str, Any]] = None,
        message_reference: Optional[Dict[str, Any]] = None,
        flags: int = 0,
        sticky: bool = False,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Message object."""
        if mentions is None:
            mentions = []
        if mention_roles is None:
            mention_roles = []
        if attachments is None:
            attachments = []
        if embeds is None:
            embeds = []
        if reactions is None:
            reactions = []

        if author is None:
            author = DiscordMockUtils.create_mock_user(author_id)

        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id)

        if channel is None:
            channel = DiscordMockUtils.create_mock_text_channel(
                channel_id=channel_id,
                guild_id=guild_id,
                guild=guild
            )

        mock_message = MagicMock()
        mock_message.id = message_id
        mock_message.channel = channel
        mock_message.author = author
        mock_message.guild = guild
        mock_message.content = content
        mock_message.created_at = created_at or datetime(2020, 1, 1)
        mock_message.edited_at = edited_at
        mock_message.tts = tts
        mock_message.mention_everyone = mention_everyone
        mock_message.mentions = mentions
        mock_message.mention_roles = mention_roles
        mock_message.attachments = attachments
        mock_message.embeds = embeds
        mock_message.reactions = reactions
        mock_message.pinned = pinned
        mock_message.type = type
        mock_message.activity = activity
        mock_message.application = application
        mock_message.message_reference = message_reference
        mock_message.flags = flags
        mock_message.sticky = sticky
        mock_message.mention = author.mention

        for key, value in kwargs.items():
            setattr(mock_message, key, value)

        return mock_message

    @staticmethod
    def create_mock_embed(
        title: Optional[str] = None,
        description: Optional[str] = None,
        url: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        color: Optional[int] = None,
        color_value: Optional[int] = None,
        footer: Optional[Dict[str, Any]] = None,
        image: Optional[Dict[str, Any]] = None,
        thumbnail: Optional[Dict[str, Any]] = None,
        video: Optional[Dict[str, Any]] = None,
        provider: Optional[Dict[str, Any]] = None,
        author: Optional[Dict[str, Any]] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> MagicMock:
        """
        Create a mock Discord Embed object.

        Sub-object fields (footer, image, thumbnail, video, provider, author)
        are stored as real ``EmbedProxy`` instances matching discord.py
        behaviour.  An empty/unset field is a falsy ``EmbedProxy({})``,
        NOT ``None``.

        Example::

            embed = DiscordMockUtils.create_mock_embed(footer={"text": "hi"})
            assert bool(embed.footer)          # truthy when populated
            assert embed.footer.text == "hi"

            embed2 = DiscordMockUtils.create_mock_embed()
            assert not embed2.footer           # falsy when empty
            assert embed2.footer is not None   # but never None
        """
        if fields is None:
            fields = []

        mock_embed = MagicMock()
        mock_embed.title = title
        mock_embed.description = description
        mock_embed.url = url
        mock_embed.timestamp = timestamp

        mock_color = MagicMock()
        if color_value is not None:
            mock_color.value = color_value
        elif color is not None:
            mock_color.value = color
        else:
            mock_color.value = 0
        mock_embed.color = mock_color
        mock_embed.colour = mock_color

        # Use real EmbedProxy objects so tests behave like real discord.py
        mock_embed.footer = EmbedProxy(footer or {})
        mock_embed.image = EmbedProxy(image or {})
        mock_embed.thumbnail = EmbedProxy(thumbnail or {})
        mock_embed.video = EmbedProxy(video or {})
        mock_embed.provider = EmbedProxy(provider or {})
        mock_embed.author = EmbedProxy(author or {})
        mock_embed.fields = fields

        mock_embed.to_dict = MagicMock(return_value={
            'title': title,
            'description': description,
            'url': url,
            'timestamp': timestamp.isoformat() if timestamp else None,
            'color': mock_color.value,
            'footer': footer,
            'image': image,
            'thumbnail': thumbnail,
            'video': video,
            'provider': provider,
            'author': author,
            'fields': fields
        })

        for key, value in kwargs.items():
            setattr(mock_embed, key, value)

        return mock_embed

    @staticmethod
    def create_mock_permission_overwrite(
        allow: int = 0,
        deny: int = 0,
        target: Optional[Union[MagicMock, Type[MagicMock]]] = None,
        target_type: str = "role",
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord PermissionOverwrite object."""
        if target is None:
            if target_type == "role":
                target = DiscordMockUtils.create_mock_role()
            else:
                target = DiscordMockUtils.create_mock_member()

        mock_permission = MagicMock()
        mock_permission.allow = MagicMock(value=allow)
        mock_permission.deny = MagicMock(value=deny)
        mock_permission.target = target
        mock_permission.target_id = target.id
        mock_permission.type = target_type

        allow_obj = MagicMock()
        allow_obj.value = allow
        deny_obj = MagicMock()
        deny_obj.value = deny
        mock_permission.pair = MagicMock(return_value=(allow_obj, deny_obj))

        for key, value in kwargs.items():
            setattr(mock_permission, key, value)

        return mock_permission

    @staticmethod
    def create_mock_bot(
        user_id: int = 1,
        username: str = "test_bot",
        is_ready: bool = True,
        guilds: Optional[List[MagicMock]] = None,
        **kwargs
    ) -> MagicMock:
        """
        Create a mock Discord Bot object.

        Uses ``MagicMock(spec=commands.Bot)`` so that
        ``isinstance(bot, commands.Bot)`` returns ``True``.

        This is required by ``resolve_bot`` in
        ``utils/discord_helpers.py`` which performs::

            if not isinstance(bot, commands.Bot):
                raise HTTPException(...)

        A plain ``MagicMock()`` without a spec would fail that check.

        ``is_ready`` is configured as a regular (non-async) callable
        returning the *is_ready* argument (default ``True``).
        ``wait_until_ready`` is configured as an ``AsyncMock`` that
        returns immediately.
        """
        mock_user = DiscordMockUtils.create_mock_user(
            user_id=user_id,
            username=username,
            bot=True
        )

        # spec=commands.Bot makes isinstance(bot, commands.Bot) == True
        mock_bot = MagicMock(spec=commands.Bot)
        mock_bot.user = mock_user
        mock_bot.guilds = guilds or []

        # is_ready() is synchronous in discord.py (returns bool)
        mock_bot.is_ready = MagicMock(return_value=is_ready)
        # wait_until_ready() is a coroutine
        mock_bot.wait_until_ready = AsyncMock()

        # Common async methods
        mock_bot.fetch_guild = AsyncMock()
        mock_bot.fetch_channel = AsyncMock()
        mock_bot.fetch_user = AsyncMock()

        # Sync cache-lookup methods
        mock_bot.get_guild = MagicMock(return_value=None)
        mock_bot.get_channel = MagicMock(return_value=None)
        mock_bot.get_user = MagicMock(return_value=None)

        for key, value in kwargs.items():
            setattr(mock_bot, key, value)

        return mock_bot

    @staticmethod
    def create_mock_context(
        message: Optional[MagicMock] = None,
        channel: Optional[MagicMock] = None,
        guild: Optional[MagicMock] = None,
        author: Optional[MagicMock] = None,
        bot: Optional[MagicMock] = None,
        prefix: str = "!",
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Context object."""
        if message is None:
            message = DiscordMockUtils.create_mock_message()

        if channel is None:
            channel = message.channel

        if guild is None:
            guild = message.guild

        if author is None:
            author = message.author

        if bot is None:
            bot = DiscordMockUtils.create_mock_bot()

        mock_ctx = MagicMock()
        mock_ctx.message = message
        mock_ctx.channel = channel
        mock_ctx.guild = guild
        mock_ctx.author = author
        mock_ctx.bot = bot
        mock_ctx.prefix = prefix
        mock_ctx.args = ()
        mock_ctx.kwargs = {}

        mock_ctx.send = AsyncMock()
        mock_ctx.reply = AsyncMock()
        mock_ctx.trigger_typing = AsyncMock()
        mock_ctx.author.voice = None

        for key, value in kwargs.items():
            setattr(mock_ctx, key, value)

        return mock_ctx

    @staticmethod
    def create_mock_interaction(
        interaction_id: int = 1,
        guild_id: int = 1,
        channel_id: int = 1,
        user_id: int = 1,
        token: str = "test_token",
        version: int = 1,
        data: Optional[Dict[str, Any]] = None,
        guild: Optional[MagicMock] = None,
        channel: Optional[MagicMock] = None,
        user: Optional[MagicMock] = None,
        member: Optional[MagicMock] = None,
        **kwargs
    ) -> MagicMock:
        """Create a mock Discord Interaction object."""
        if data is None:
            data = {}

        if guild is None:
            guild = DiscordMockUtils.create_mock_guild(guild_id)

        if channel is None:
            channel = DiscordMockUtils.create_mock_text_channel(channel_id, guild_id=guild_id, guild=guild)

        if user is None:
            user = DiscordMockUtils.create_mock_user(user_id)

        if member is None:
            member = DiscordMockUtils.create_mock_member(
                user_id=user_id,
                guild_id=guild_id,
                guild=guild
            )

        mock_interaction = MagicMock()
        mock_interaction.id = interaction_id
        mock_interaction.token = token
        mock_interaction.version = version
        mock_interaction.data = data
        mock_interaction.guild = guild
        mock_interaction.channel = channel
        mock_interaction.user = user
        mock_interaction.member = member

        mock_interaction.response = MagicMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup = MagicMock()
        mock_interaction.followup.send = AsyncMock()

        for key, value in kwargs.items():
            setattr(mock_interaction, key, value)

        return mock_interaction

    # -----------------------------------------------------------------------
    # Exception factories (delegating to module-level typed factories)
    # -----------------------------------------------------------------------

    @staticmethod
    def create_discord_not_found(
        text: str = "Not found",
        code: int = 0,
    ) -> discord.NotFound:
        """Return a real ``discord.NotFound`` (404) exception instance."""
        return create_discord_not_found(text=text, code=code)

    @staticmethod
    def create_discord_forbidden(
        text: str = "Forbidden",
        code: int = 0,
    ) -> discord.Forbidden:
        """Return a real ``discord.Forbidden`` (403) exception instance."""
        return create_discord_forbidden(text=text, code=code)

    @staticmethod
    def create_discord_http_exception(
        status: int = 400,
        text: str = "Bad request",
        code: int = 0,
    ) -> discord.HTTPException:
        """Return a real ``discord.HTTPException`` with an arbitrary status."""
        return create_discord_http_exception(status=status, text=text, code=code)

    @staticmethod
    def create_mock_discord_exception(
        exc_type: Type[Exception],
        message: str = "test exception",
        **kwargs
    ) -> MagicMock:
        """
        .. deprecated::
            Use the typed factories ``create_discord_not_found``,
            ``create_discord_forbidden``, or ``create_discord_http_exception``
            instead.  This method returns a MagicMock whose ``__class__``
            is spoofed, which means:

            - ``isinstance`` checks will pass (Python honours ``__class__``)
            - But the mock **cannot be raised/caught** via ``except
              discord.NotFound`` because Python's exception machinery
              requires a real BaseException subclass instance.

        Retained for backward compatibility only.
        """
        mock_exception = MagicMock()
        mock_exception.__class__ = exc_type  # type: ignore[assignment]
        mock_exception.__str__ = MagicMock(return_value=message)

        if issubclass(exc_type, discord.HTTPException):
            status_map = {
                discord.NotFound: 404,
                discord.Forbidden: 403,
            }
            mock_exception.status = status_map.get(exc_type, 400)
            mock_exception.code = 0
            mock_exception.text = message
            # Provide a minimal response-like object so code that reads
            # exc.response.status doesn't crash.
            mock_response = MagicMock()
            mock_response.status = mock_exception.status
            mock_response.reason = "Test"
            mock_exception.response = mock_response

        for key, value in kwargs.items():
            setattr(mock_exception, key, value)

        return mock_exception

    # -----------------------------------------------------------------------
    # EmbedProxy factory (delegating to module-level factory)
    # -----------------------------------------------------------------------

    @staticmethod
    def create_mock_embed_proxy(**fields: Any) -> EmbedProxy:
        """
        Return a real ``discord.embeds.EmbedProxy``.

        See module-level ``create_mock_embed_proxy`` for full docs.
        """
        return create_mock_embed_proxy(**fields)

    # -----------------------------------------------------------------------
    # Discord module mock helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def create_mock_discord_module() -> MagicMock:
        """Create a mock discord module with all necessary objects."""
        mock_discord = MagicMock()

        mock_discord.User = MagicMock()
        mock_discord.Member = MagicMock()
        mock_discord.Guild = MagicMock()
        mock_discord.Role = MagicMock()
        mock_discord.TextChannel = MagicMock()
        mock_discord.VoiceChannel = MagicMock()
        mock_discord.CategoryChannel = MagicMock()
        mock_discord.ForumChannel = MagicMock()
        mock_discord.Thread = MagicMock()
        mock_discord.Message = MagicMock()
        mock_discord.Embed = MagicMock()
        mock_discord.PermissionOverwrite = MagicMock()
        mock_discord.Color = MagicMock()
        mock_discord.Colour = MagicMock()
        mock_discord.Permissions = MagicMock()
        mock_discord.HTTPException = discord.HTTPException
        mock_discord.Forbidden = discord.Forbidden
        mock_discord.NotFound = discord.NotFound
        mock_discord.InvalidData = MagicMock()
        mock_discord.PrivacyError = MagicMock()
        mock_discord.ServerError = MagicMock()
        mock_discord.InvalidMessage = MagicMock()
        mock_discord.NoMoreItems = MagicMock()
        mock_discord.ConnectionClosed = MagicMock()

        mock_discord_ext = MagicMock()
        mock_discord_ext.commands = MagicMock()
        mock_discord_ext.commands.Bot = commands.Bot
        mock_discord_ext.commands.Context = MagicMock()
        mock_discord_ext.commands.Command = MagicMock()

        mock_discord_ext.commands.app_commands = MagicMock()
        mock_discord_ext.commands.app_commands.Command = MagicMock()

        mock_discord.ext = mock_discord_ext

        return mock_discord

    @staticmethod
    def create_mock_discord_module_with_factories() -> MagicMock:
        """Create a mock discord module with factory functions for testing."""
        mock_discord = DiscordMockUtils.create_mock_discord_module()

        mock_discord.utils = MagicMock()
        mock_discord.utils.get = MagicMock()
        mock_discord.utils.find = MagicMock()

        return mock_discord

    @staticmethod
    def patch_discord_module():  # type: ignore[return]  # returns (MagicMock, patcher) tuple
        """Patch the discord module with mock implementations.

        Returns a ``(mock_discord, patcher)`` tuple.  Call
        ``patcher.stop()`` in teardown to undo the patch.
        """
        mock_discord = DiscordMockUtils.create_mock_discord_module()

        patcher = patch.dict('sys.modules', {'discord': mock_discord})
        patcher.start()

        return mock_discord, patcher  # type: ignore[return-value]

    @staticmethod
    def patch_discord_ext_module():  # type: ignore[return]  # returns (MagicMock, patcher) tuple
        """Patch the discord.ext module with mock implementations.

        Returns a ``(mock_discord_ext, patcher)`` tuple.  Call
        ``patcher.stop()`` in teardown to undo the patch.
        """
        mock_discord_ext = MagicMock()
        mock_discord_ext.commands = MagicMock()
        mock_discord_ext.commands.Bot = commands.Bot
        mock_discord_ext.commands.Context = MagicMock()
        mock_discord_ext.commands.Command = MagicMock()
        mock_discord_ext.commands.app_commands = MagicMock()
        mock_discord_ext.commands.app_commands.Command = MagicMock()

        patcher = patch.dict('sys.modules', {'discord.ext': mock_discord_ext})
        patcher.start()

        return mock_discord_ext, patcher  # type: ignore[return-value]

    @staticmethod
    def create_mock_test_environment() -> Dict[str, Any]:
        """Create a comprehensive test environment with all mock objects."""
        test_env: Dict[str, Any] = {}

        test_env['guild'] = DiscordMockUtils.create_mock_guild()
        test_env['channel'] = DiscordMockUtils.create_mock_text_channel(
            guild_id=test_env['guild'].id, guild=test_env['guild']
        )
        test_env['user'] = DiscordMockUtils.create_mock_user()
        test_env['member'] = DiscordMockUtils.create_mock_member(
            user_id=test_env['user'].id,
            guild_id=test_env['guild'].id,
            guild=test_env['guild']
        )
        test_env['role'] = DiscordMockUtils.create_mock_role(
            guild_id=test_env['guild'].id, guild=test_env['guild']
        )
        test_env['message'] = DiscordMockUtils.create_mock_message(
            channel_id=test_env['channel'].id,
            author_id=test_env['user'].id,
            guild_id=test_env['guild'].id,
            channel=test_env['channel'],
            author=test_env['user'],
            guild=test_env['guild']
        )
        test_env['embed'] = DiscordMockUtils.create_mock_embed()
        test_env['bot'] = DiscordMockUtils.create_mock_bot()
        test_env['ctx'] = DiscordMockUtils.create_mock_context(
            message=test_env['message'],
            channel=test_env['channel'],
            guild=test_env['guild'],
            author=test_env['user'],
            bot=test_env['bot']
        )
        test_env['interaction'] = DiscordMockUtils.create_mock_interaction(
            guild_id=test_env['guild'].id,
            channel_id=test_env['channel'].id,
            user_id=test_env['user'].id,
            guild=test_env['guild'],
            channel=test_env['channel'],
            user=test_env['user'],
            member=test_env['member']
        )
        # Convenience exception instances for common test scenarios
        test_env['exc_not_found'] = create_discord_not_found()
        test_env['exc_forbidden'] = create_discord_forbidden()
        test_env['exc_http_400'] = create_discord_http_exception(400)
        test_env['exc_http_502'] = create_discord_http_exception(503)

        return test_env


# ---------------------------------------------------------------------------
# Module-level instance for convenience (import and use directly)
# ---------------------------------------------------------------------------
discord_mock_utils = DiscordMockUtils()
