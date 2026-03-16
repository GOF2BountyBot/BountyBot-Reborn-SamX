"""
Tests for discord_converters.py utilities.

This module provides comprehensive test coverage for the Discord object conversion utilities,
including all converter classes and their bidirectional conversion methods.
"""

import os
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = _make_mock_logger


sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger


# Hand-rolled fake discord module: keep plain Python classes (not MagicMock)
# for channel/role/user types so that ``isinstance(target, discord.Role)``
# checks inside discord_converters.py work correctly via __class__ spoofing.
_mock_discord = types.ModuleType("discord")

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThread = type("Thread", (), {})
_MockEmbed = type("Embed", (), {})
_MockPermissionOverwrite = type("PermissionOverwrite", (), {})
_MockGuild = type("Guild", (), {})
_MockUser = type("User", (), {})
_MockMember = type("Member", (), {})
_MockRole = type("Role", (), {})
_MockMessage = type("Message", (), {})

# Use real discord exception classes so that isinstance checks in production
# code always work regardless of test execution order.
import discord as _real_discord

_MockForbidden = _real_discord.Forbidden
_MockNotFound = _real_discord.NotFound
_MockHTTPException = _real_discord.HTTPException

_mock_discord.CategoryChannel = _MockCategoryChannel
_mock_discord.TextChannel = _MockTextChannel
_mock_discord.VoiceChannel = _MockVoiceChannel
_mock_discord.ForumChannel = _MockForumChannel
_mock_discord.Thread = _MockThread
_mock_discord.Embed = _MockEmbed
_mock_discord.PermissionOverwrite = _MockPermissionOverwrite
_mock_discord.Guild = _MockGuild
_mock_discord.User = _MockUser
_mock_discord.Member = _MockMember
_mock_discord.Role = _MockRole
_mock_discord.Message = _MockMessage
_mock_discord.Forbidden = _MockForbidden
_mock_discord.NotFound = _MockNotFound
_mock_discord.HTTPException = _MockHTTPException

_mock_discord_ext = types.ModuleType("discord.ext")
_MockBot = type("Bot", (), {})
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _MockBot

_MockPermissions = type("Permissions", (), {"value": 0})
_mock_discord.Permissions = _MockPermissions

_MockColor = type("Color", (), {"value": 0})
_mock_discord.Color = _MockColor


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_converter_discord():
    """
    Re-assert this file's fake discord module into sys.modules before each
    converter test, then reload utils.discord_converters so that its
    module-level ``discord`` reference (and thus ``discord.Role`` etc.) is
    rebound to our hand-rolled fake with plain Python types.

    Without the reload, discord_converters.py keeps whatever ``discord``
    reference it captured when first imported (typically the API-test fake
    that has ``discord.Role = MagicMock()``, which causes ``isinstance`` to
    return incorrect results or raise TypeError).

    After each test, restore the real discord module so that tests in other
    files (e.g. test_embed_converter.py, test_command_utils.py) are not
    affected by this file's sys.modules patch.
    """
    import importlib

    sys.modules["discord"] = _mock_discord
    sys.modules["discord.ext"] = _mock_discord_ext
    sys.modules["discord.ext.commands"] = _mock_discord_ext.commands
    import utils.discord_converters as _dc_mod

    importlib.reload(_dc_mod)
    yield

    # Restore real discord after each test to avoid polluting other test files.
    # Use conftest's saved references (captured before any test file ran).
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS


class TestGuildConverter:
    """Tests for GuildConverter class."""

    def test_guild_to_summary_returns_correct_data(self):
        """guild_to_summary should convert guild to summary correctly."""
        mock_guild = DiscordMockUtils.create_mock_guild(
            guild_id=987654321,
            name="Test Guild",
            icon_url="icon_url",
            member_count=100,
            owner_id=111111111,
            description="Test description",
            created_at=datetime(2024, 1, 1),
            features=["feature1", "feature2"],
            verification_level="high",
            default_notifications="all_messages",
            explicit_content_filter="none",
            mfa_level="elevated",
            premium_tier=2,
            premium_subscription_count=5,
            preferred_locale="en-US",
            nsfw_level="strict",
        )

        from utils.discord_converters import GuildConverter

        result = GuildConverter.guild_to_summary(mock_guild)

        assert result.id == 987654321
        assert result.name == "Test Guild"
        assert result.icon == "icon_url"
        assert result.member_count == 100
        assert result.owner_id == 111111111
        assert result.description == "Test description"
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.features == ["feature1", "feature2"]
        assert result.verification_level == "high"
        assert result.default_notifications == "all_messages"
        assert result.explicit_content_filter == "none"
        assert result.mfa_level == "elevated"
        assert result.premium_tier == 2
        assert result.premium_subscription_count == 5
        assert result.preferred_locale == "en-US"
        assert result.nsfw_level == "strict"

    def test_guild_to_summary_handles_missing_attributes(self):
        """guild_to_summary should handle missing guild attributes gracefully."""
        mock_guild = DiscordMockUtils.create_mock_guild(guild_id=987654321, name="Test Guild")
        # Override factory defaults with None to test graceful handling
        mock_guild.icon = None
        mock_guild.member_count = None
        mock_guild.owner_id = None
        mock_guild.description = None
        mock_guild.created_at = None
        mock_guild.features = None
        mock_guild.verification_level = None
        mock_guild.default_notifications = None
        mock_guild.explicit_content_filter = None
        mock_guild.mfa_level = None
        mock_guild.premium_tier = None
        mock_guild.premium_subscription_count = None
        mock_guild.preferred_locale = None
        mock_guild.nsfw_level = None

        from utils.discord_converters import GuildConverter

        result = GuildConverter.guild_to_summary(mock_guild)

        assert result.id == 987654321
        assert result.name == "Test Guild"
        assert result.icon is None
        assert result.member_count == 0
        assert result.owner_id == 0  # Schema requires int, defaults to 0
        assert result.description is None
        assert result.created_at == ""
        assert result.features == []
        assert result.verification_level == ""
        assert result.default_notifications == ""
        assert result.explicit_content_filter == ""
        assert result.mfa_level == ""
        assert result.premium_tier == 0
        assert result.premium_subscription_count is None
        assert result.preferred_locale == ""
        assert result.nsfw_level is None

    def test_guild_to_detail_is_alias_for_summary(self):
        """guild_to_detail should be alias for guild_to_summary."""
        from utils.discord_converters import GuildConverter

        assert GuildConverter.guild_to_detail == GuildConverter.guild_to_summary


class TestChannelConverter:
    """Tests for ChannelConverter class."""

    def test_channel_to_summary_handles_text_channel(self):
        """channel_to_summary should convert text channel correctly."""
        mock_channel = DiscordMockUtils.create_mock_text_channel(
            channel_id=1234567890,
            name="test-channel",
            position=5,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)

        assert result.id == 1234567890
        assert result.name == "test-channel"
        assert result.type == "text"
        assert result.position == 5
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"

    def test_channel_to_summary_handles_voice_channel(self):
        """channel_to_summary should convert voice channel correctly."""
        mock_channel = DiscordMockUtils.create_mock_voice_channel(
            channel_id=1234567890,
            name="test-voice",
            position=3,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)

        assert result.id == 1234567890
        assert result.name == "test-voice"
        assert result.type == "voice"
        assert result.position == 3
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"

    def test_channel_to_summary_handles_category_channel(self):
        """channel_to_summary should convert category channel correctly."""
        mock_channel = DiscordMockUtils.create_mock_category_channel(
            channel_id=1234567890,
            name="test-category",
            position=1,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)

        assert result.id == 1234567890
        assert result.name == "test-category"
        assert result.type == "category"
        assert result.position == 1
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"

    def test_channel_to_summary_handles_missing_position(self):
        """channel_to_summary should handle missing position gracefully."""
        mock_channel = DiscordMockUtils.create_mock_text_channel(
            channel_id=1234567890,
            name="test-channel",
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )
        mock_channel.position = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)

        assert result.position == 0

    def test_channel_to_summary_coerces_position_to_int(self):
        """channel_to_summary should coerce position to int."""
        mock_channel = DiscordMockUtils.create_mock_text_channel(
            channel_id=1234567890,
            name="test-channel",
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )
        mock_channel.position = "5"

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)

        assert result.position == 5

    def test_channel_to_detail_handles_text_channel(self):
        """channel_to_detail should convert text channel with extended fields."""
        mock_channel = DiscordMockUtils.create_mock_text_channel(
            channel_id=1234567890,
            name="test-channel",
            position=5,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
            topic="Test topic",
            nsfw=True,
            slowmode_delay=10,
            category_id=111111111,
            default_auto_archive_duration=1440,
        )
        mock_channel.bitrate = None
        mock_channel.user_limit = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_detail(mock_channel)

        assert result.id == 1234567890
        assert result.name == "test-channel"
        assert result.type == "text"
        assert result.position == 5
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.topic == "Test topic"
        assert result.nsfw is True
        assert result.slowmode_delay == 10
        assert result.bitrate is None
        assert result.user_limit is None
        assert result.category_id == 111111111
        assert result.default_auto_archive_duration == 1440

    def test_channel_to_detail_handles_voice_channel(self):
        """channel_to_detail should convert voice channel with extended fields."""
        mock_channel = DiscordMockUtils.create_mock_voice_channel(
            channel_id=1234567890,
            name="test-voice",
            position=3,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
            bitrate=64000,
            user_limit=10,
            category_id=111111111,
        )
        mock_channel.topic = None
        mock_channel.nsfw = False
        mock_channel.slowmode_delay = None
        mock_channel.default_auto_archive_duration = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_detail(mock_channel)

        assert result.id == 1234567890
        assert result.name == "test-voice"
        assert result.type == "voice"
        assert result.position == 3
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.topic is None
        assert result.nsfw is False
        assert result.slowmode_delay is None
        assert result.bitrate == 64000
        assert result.user_limit == 10
        assert result.category_id == 111111111
        assert result.default_auto_archive_duration is None

    def test_category_to_detail_converts_correctly(self):
        """category_to_detail should convert category channel correctly."""
        mock_category = DiscordMockUtils.create_mock_category_channel(
            channel_id=1234567890,
            name="test-category",
            position=1,
            guild_id=987654321,
            created_at=datetime(2024, 1, 1),
        )

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.category_to_detail(mock_category)

        assert result.id == 1234567890
        assert result.name == "test-category"
        assert result.position == 1
        assert result.guild_id == 987654321
        assert result.created_at == "2024-01-01T00:00:00"

    def test_thread_to_summary_converts_basic_thread(self):
        """thread_to_summary should convert basic thread correctly."""
        mock_thread = DiscordMockUtils.create_mock_thread(
            thread_id=1234567890,
            name="test-thread",
            parent_id=111111111,
            guild_id=987654321,
            owner_id=222222222,
            archived=False,
            locked=False,
            message_count=5,
            member_count=3,
            auto_archive_duration=1440,
            last_message_id=333333333,
            created_at=datetime(2024, 1, 1),
        )
        mock_thread.applied_tags = None
        mock_thread.applied_tag_ids = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)

        assert result.id == 1234567890
        assert result.name == "test-thread"
        assert result.channel_id == 111111111
        assert result.guild_id == 987654321
        assert result.owner_id == 222222222
        assert result.archived is False
        assert result.locked is False
        assert result.message_count == 5
        assert result.member_count == 3
        assert result.default_auto_archive_duration == 1440
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.last_message_id == 333333333
        assert result.applied_tag_ids is None
        assert result.applied_tags is None

    def test_thread_to_summary_handles_parent_as_object(self):
        """thread_to_summary should handle parent as object instead of parent_id."""
        mock_thread = DiscordMockUtils.create_mock_thread(
            thread_id=1234567890,
            name="test-thread",
            parent_id=111111111,
            guild_id=987654321,
            owner_id=222222222,
            archived=False,
            locked=False,
            message_count=5,
            member_count=3,
            auto_archive_duration=1440,
            last_message_id=333333333,
            created_at=datetime(2024, 1, 1),
        )
        mock_thread.parent_id = None  # Ensure parent_id is None so parent is used
        mock_thread.parent = MagicMock()
        mock_thread.parent.id = 111111111

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)

        assert result.channel_id == 111111111

    def test_thread_to_summary_handles_owner_as_object(self):
        """thread_to_summary should handle owner as object instead of owner_id."""
        mock_thread = DiscordMockUtils.create_mock_thread(
            thread_id=1234567890,
            name="test-thread",
            parent_id=111111111,
            guild_id=987654321,
            owner_id=222222222,
            archived=False,
            locked=False,
            message_count=5,
            member_count=3,
            auto_archive_duration=1440,
            last_message_id=333333333,
            created_at=datetime(2024, 1, 1),
        )
        mock_thread.owner_id = None  # Ensure owner_id is None so owner is used
        mock_thread.owner = MagicMock()
        mock_thread.owner.id = 222222222

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)

        assert result.owner_id == 222222222

    def test_thread_to_summary_handles_applied_tags(self):
        """thread_to_summary should handle applied tags correctly."""
        mock_tag1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", emoji="🏷️")
        mock_tag2 = DiscordMockUtils.create_mock_forum_tag(tag_id=222, name="tag2", emoji="📝")

        mock_thread = DiscordMockUtils.create_mock_thread(
            thread_id=1234567890,
            name="test-thread",
            parent_id=111111111,
            guild_id=987654321,
            owner_id=222222222,
            archived=False,
            locked=False,
            message_count=5,
            member_count=3,
            auto_archive_duration=1440,
            last_message_id=333333333,
            created_at=datetime(2024, 1, 1),
            applied_tags=[mock_tag1, mock_tag2],
        )
        mock_thread.applied_tag_ids = None  # Set to None so applied_tags is used

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)

        assert result.applied_tag_ids == [111, 222]
        assert len(result.applied_tags) == 2
        assert result.applied_tags[0].id == 111
        assert result.applied_tags[0].name == "tag1"
        assert result.applied_tags[0].emoji == "🏷️"
        assert result.applied_tags[1].id == 222
        assert result.applied_tags[1].name == "tag2"
        assert result.applied_tags[1].emoji == "📝"

    def test_thread_to_summary_handles_applied_tag_ids(self):
        """thread_to_summary should handle applied_tag_ids attribute."""
        mock_thread = DiscordMockUtils.create_mock_thread(
            thread_id=1234567890,
            name="test-thread",
            parent_id=111111111,
            guild_id=987654321,
            owner_id=222222222,
            archived=False,
            locked=False,
            message_count=5,
            member_count=3,
            auto_archive_duration=1440,
            last_message_id=333333333,
            created_at=datetime(2024, 1, 1),
        )
        mock_thread.applied_tags = None  # Must be None for applied_tag_ids to be used
        mock_thread.applied_tag_ids = [111, 222]

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)

        assert result.applied_tag_ids == [111, 222]
        assert result.applied_tags is None

    def test_thread_to_detail_delegates_to_summary(self):
        """thread_to_detail should delegate to thread_to_summary."""
        from utils.discord_converters import ChannelConverter

        assert ChannelConverter.thread_to_detail == ChannelConverter.thread_to_summary

    def test_forum_tag_to_payload_converts_correctly(self):
        """forum_tag_to_payload should convert forum tag correctly."""
        mock_tag = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="test-tag", emoji="🏷️", channel_id=1)

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.forum_tag_to_payload(mock_tag, channel_id=111111111)

        assert result["id"] == 111
        assert result["channel_id"] == 111111111
        assert result["name"] == "test-tag"
        assert result["emoji"] == "🏷️"


class TestPermissionConverter:
    """Tests for PermissionConverter class."""

    def test_overwrite_to_payload_converts_role_overwrite(self):
        """overwrite_to_payload should convert role permission overwrite correctly."""
        # Create a mock that passes isinstance(target, discord.Role) check
        # Use the mocked Role class that's already set up in the module
        mock_role = DiscordMockUtils.create_mock_role(role_id=111111111, name="test-role")
        # Make it an instance of the mocked discord.Role
        mock_role.__class__ = sys.modules["discord"].Role

        mock_overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=8, deny=4)

        from utils.discord_converters import PermissionConverter

        result = PermissionConverter.overwrite_to_payload(mock_role, mock_overwrite, channel_id=1234567890)

        assert result.id == "1234567890:111111111"
        assert result.channel_id == 1234567890
        assert result.target_id == 111111111
        assert result.type == "role"
        assert result.allow == 8
        assert result.deny == 4

    def test_overwrite_to_payload_converts_member_overwrite(self):
        """overwrite_to_payload should convert member permission overwrite correctly."""
        mock_member = DiscordMockUtils.create_mock_member(user_id=222222222, username="test-member")

        mock_overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=16, deny=2)

        from utils.discord_converters import PermissionConverter

        result = PermissionConverter.overwrite_to_payload(mock_member, mock_overwrite, channel_id=1234567890)

        assert result.id == "1234567890:222222222"
        assert result.channel_id == 1234567890
        assert result.target_id == 222222222
        assert result.type == "member"
        assert result.allow == 16
        assert result.deny == 2

    def test_overwrite_to_payload_handles_none_channel_id(self):
        """overwrite_to_payload should handle None channel_id gracefully."""
        # Create a mock that passes isinstance(target, discord.Role) check
        mock_role = DiscordMockUtils.create_mock_role(role_id=111111111, name="test-role")
        # Make it an instance of the mocked discord.Role
        mock_role.__class__ = sys.modules["discord"].Role

        mock_overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=8, deny=4)

        from utils.discord_converters import PermissionConverter

        result = PermissionConverter.overwrite_to_payload(mock_role, mock_overwrite)

        assert result.id is None
        assert result.channel_id is None
        assert result.target_id == 111111111
        assert result.type == "role"
        assert result.allow == 8
        assert result.deny == 4


class TestRoleConverter:
    """Tests for RoleConverter class."""

    def test_role_to_payload_converts_basic_role(self):
        """role_to_payload should convert basic role correctly."""
        mock_role = DiscordMockUtils.create_mock_role(
            role_id=111111111,
            name="test-role",
            color_value=0xFF0000,
            hoist=True,
            position=5,
            permissions=104324673,
            managed=False,
            mentionable=True,
            guild_id=987654321,
        )
        mock_role.created_at = datetime(2024, 1, 1)
        mock_role.tags = None

        from utils.discord_converters import RoleConverter

        result = RoleConverter.role_to_payload(mock_role)

        assert result.id == 111111111
        assert result.guild_id == 987654321
        assert result.name == "test-role"
        assert result.color == 0xFF0000
        assert result.hoist is True
        assert result.position == 5
        assert result.permissions == 104324673
        assert result.managed is False
        assert result.mentionable is True
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.tags is None

    def test_role_to_payload_converts_role_with_tags(self):
        """role_to_payload should convert role with tags correctly."""
        mock_role = DiscordMockUtils.create_mock_role(
            role_id=111111111,
            name="test-role",
            color_value=0xFF0000,
            hoist=True,
            position=5,
            permissions=104324673,
            managed=False,
            mentionable=True,
            guild_id=987654321,
        )
        mock_role.created_at = datetime(2024, 1, 1)

        mock_tags = MagicMock()
        mock_tags.bot_id = 111
        mock_tags.integration_id = 222
        mock_tags._premium_subscriber = True
        mock_role.tags = mock_tags

        from utils.discord_converters import RoleConverter

        result = RoleConverter.role_to_payload(mock_role)

        assert result.tags["bot_id"] == 111
        assert result.tags["integration_id"] == 222
        assert result.tags["premium_subscriber"] is True


class TestUserConverter:
    """Tests for UserConverter class."""

    def test_user_to_payload_converts_basic_user(self):
        """user_to_payload should convert basic user correctly."""
        mock_user = DiscordMockUtils.create_mock_user(
            user_id=111111111,
            username="test-user",
            discriminator="1234",
            bot=False,
            system=False,
            public_flags=0,
        )
        mock_user.avatar = MagicMock()
        mock_user.avatar.url = "avatar_url"
        mock_user.created_at = datetime(2024, 1, 1)

        from utils.discord_converters import UserConverter

        result = UserConverter.user_to_payload(mock_user)

        assert result.id == 111111111
        assert result.username == "test-user"
        assert result.discriminator == "1234"
        assert result.avatar == "avatar_url"
        assert result.bot is False
        assert result.system is False
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.public_flags == 0

    def test_user_to_payload_converts_bot_user(self):
        """user_to_payload should convert bot user correctly."""
        mock_user = DiscordMockUtils.create_mock_user(
            user_id=111111111,
            username="test-bot",
            discriminator="5678",
            bot=True,
            system=True,
            public_flags=1,
        )
        mock_user.avatar = None
        mock_user.created_at = datetime(2024, 1, 1)

        from utils.discord_converters import UserConverter

        result = UserConverter.user_to_payload(mock_user)

        assert result.bot is True
        assert result.system is True
        assert result.public_flags == 1

    def test_member_to_payload_converts_basic_member(self):
        """member_to_payload should convert basic member correctly."""
        # Member is also a User in discord.py, so mock needs User attributes
        mock_member = DiscordMockUtils.create_mock_member(
            user_id=111111111,
            username="test-user",
            discriminator="1234",
            nickname="test-nick",
            roles=[MagicMock(id=111), MagicMock(id=222)],
            joined_at=datetime(2024, 1, 2),
            premium_since=datetime(2024, 1, 3),
            pending=False,
            guild_id=987654321,
        )
        mock_member.avatar = MagicMock()
        mock_member.avatar.url = "avatar_url"
        mock_member.bot = False
        mock_member.system = False
        mock_member.created_at = datetime(2024, 1, 1)
        mock_member.public_flags = MagicMock()
        mock_member.public_flags.value = 0
        mock_member.voice = MagicMock()
        mock_member.voice.deaf = False
        mock_member.voice.mute = False
        mock_member.guild_permissions = MagicMock()
        mock_member.guild_permissions.value = 104324673

        from utils.discord_converters import UserConverter

        result = UserConverter.member_to_payload(mock_member)

        assert result.user.id == 111111111
        assert result.user.username == "test-user"
        assert result.guild_id == 987654321
        assert result.nick == "test-nick"
        assert result.roles == [111, 222]
        assert result.joined_at == "2024-01-02T00:00:00"
        assert result.premium_since == "2024-01-03T00:00:00"
        assert result.deaf is False
        assert result.mute is False
        assert result.pending is False
        assert result.permissions == 104324673

    def test_member_to_payload_handles_missing_voice(self):
        """member_to_payload should handle missing voice object gracefully."""
        # Member is also a User in discord.py, so mock needs User attributes
        mock_member = DiscordMockUtils.create_mock_member(
            user_id=111111111,
            username="test-user",
            discriminator="1234",
            nickname="test-nick",
            roles=[MagicMock(id=111), MagicMock(id=222)],
            joined_at=datetime(2024, 1, 2),
            premium_since=None,
            pending=False,
            guild_id=987654321,
        )
        mock_member.avatar = MagicMock()
        mock_member.avatar.url = "avatar_url"
        mock_member.bot = False
        mock_member.system = False
        mock_member.created_at = datetime(2024, 1, 1)
        mock_member.public_flags = MagicMock()
        mock_member.public_flags.value = 0
        mock_member.voice = None
        mock_member.guild_permissions = MagicMock()
        mock_member.guild_permissions.value = 104324673

        from utils.discord_converters import UserConverter

        result = UserConverter.member_to_payload(mock_member)

        assert result.deaf is False
        assert result.mute is False


class TestMessageConverter:
    """Tests for MessageConverter class."""

    def test_message_to_payload_converts_basic_message(self):
        """message_to_payload should convert basic message correctly."""
        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1234567890,
            channel_id=1234567890,
            guild_id=987654321,
            author_id=111111111,
            content="Hello, world!",
            created_at=datetime(2024, 1, 1),
            edited_at=None,
            embeds=[],
        )
        mock_message.author.name = "TestUser"
        mock_message.type = MagicMock()
        mock_message.type.name = "default"

        from utils.discord_converters import MessageConverter

        result = MessageConverter.message_to_payload(mock_message)

        assert result.id == 1234567890
        assert result.channel_id == 1234567890
        assert result.guild_id == 987654321
        assert result.author_id == 111111111
        assert result.content is None
        assert result.timestamp == datetime(2024, 1, 1)
        assert result.edited_timestamp is None
        assert result.message_type == "default"

    def test_message_to_payload_converts_message_with_embed(self):
        """message_to_payload should convert message with embed correctly."""
        # Create a mock embed that EmbedConverter can process
        mock_field = MagicMock()
        mock_field.name = "Field Name"
        mock_field.value = "Field Value"
        mock_field.inline = False

        mock_embed = MagicMock()
        mock_embed.title = "Test Embed"
        mock_embed.description = "Test description"
        mock_embed.color = None
        mock_embed.fields = [mock_field]
        mock_embed.footer = None
        mock_embed.timestamp = None
        mock_embed.thumbnail = None
        mock_embed.image = None

        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1234567890,
            channel_id=1234567890,
            guild_id=987654321,
            author_id=111111111,
            content="Hello, world!",
            created_at=datetime(2024, 1, 1),
            edited_at=None,
            embeds=[mock_embed],
        )
        mock_message.author.name = "TestUser"
        mock_message.type = MagicMock()
        mock_message.type.name = "default"

        from utils.discord_converters import MessageConverter

        result = MessageConverter.message_to_payload(mock_message)

        assert result.content is not None
        assert result.content.title == "Test Embed"
        assert result.content.description == "Test description"

    def test_message_to_summary_converts_basic_message(self):
        """message_to_summary should convert basic message correctly."""
        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1234567890,
            author_id=111111111,
            content="Hello, world!",
            created_at=datetime(2024, 1, 1),
        )
        mock_message.author.name = "TestUser"

        from utils.discord_converters import MessageConverter

        result = MessageConverter.message_to_summary(mock_message)

        assert result.id == 1234567890
        assert result.author_id == 111111111
        assert result.content == "Hello, world!"
        assert result.timestamp == datetime(2024, 1, 1)

    def test_message_to_summary_converts_message_with_embed(self):
        """message_to_summary should convert message with embed correctly."""
        mock_embed = MagicMock()
        mock_embed.description = "Test description"
        mock_embed.title = "Test title"
        mock_embed.fields = [MagicMock(name="Field1", value="Value1")]
        mock_embed.footer = MagicMock()
        mock_embed.footer.text = "Footer text"

        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1234567890,
            author_id=111111111,
            content="",
            created_at=datetime(2024, 1, 1),
            embeds=[mock_embed],
        )
        mock_message.author.name = "TestUser"

        from utils.discord_converters import MessageConverter

        result = MessageConverter.message_to_summary(mock_message)

        assert result.content == "Test description"

    def test_message_to_summary_falls_back_to_embed_fields(self):
        """message_to_summary should fall back to embed fields if no description/title."""
        # Create field mock with proper name and value attributes
        mock_field = MagicMock()
        mock_field.name = "Field1"
        mock_field.value = "Value1"

        mock_embed = MagicMock()
        mock_embed.description = None
        mock_embed.title = None
        mock_embed.fields = [mock_field]
        mock_embed.footer = None

        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1234567890,
            author_id=111111111,
            content="",
            created_at=datetime(2024, 1, 1),
            embeds=[mock_embed],
        )
        mock_message.author.name = "TestUser"

        from utils.discord_converters import MessageConverter

        result = MessageConverter.message_to_summary(mock_message)

        assert result.content == "Field1: Value1"


# ---------------------------------------------------------------------------
# GuildConverter — exception path
# ---------------------------------------------------------------------------


class TestGuildConverterExceptionPath:
    def test_guild_to_summary_raises_on_critical_attribute_error(self):
        """guild_to_summary should propagate exception when guild.id raises."""
        mock_guild = MagicMock()
        type(mock_guild).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_guild.name = "Test"

        from utils.discord_converters import GuildConverter

        with pytest.raises(Exception):
            GuildConverter.guild_to_summary(mock_guild)


# ---------------------------------------------------------------------------
# ChannelConverter._coerce_position — exception path
# ---------------------------------------------------------------------------


class TestCoercePositionExceptionPath:
    def test_coerce_position_returns_zero_on_non_numeric_string(self):
        """_coerce_position should return 0 for non-numeric values."""
        mock_channel = DiscordMockUtils.create_mock_text_channel(channel_id=1, name="ch", guild_id=1)
        mock_channel.position = "not-a-number"  # int("not-a-number") raises ValueError

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.channel_to_summary(mock_channel)
        assert result.position == 0


# ---------------------------------------------------------------------------
# ChannelConverter — exception paths
# ---------------------------------------------------------------------------


class TestChannelConverterExceptionPaths:
    def test_channel_to_summary_raises_on_missing_id(self):
        """channel_to_summary should propagate exception when channel.id raises."""
        mock_channel = MagicMock()
        type(mock_channel).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_channel.name = "ch"

        from utils.discord_converters import ChannelConverter

        with pytest.raises(Exception):
            ChannelConverter.channel_to_summary(mock_channel)

    def test_channel_to_detail_raises_on_missing_id(self):
        """channel_to_detail should propagate exception when channel.id raises."""
        mock_channel = MagicMock()
        type(mock_channel).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_channel.name = "ch"

        from utils.discord_converters import ChannelConverter

        with pytest.raises(Exception):
            ChannelConverter.channel_to_detail(mock_channel)

    def test_category_to_detail_raises_on_missing_id(self):
        """category_to_detail should propagate exception when category.id raises."""
        mock_category = MagicMock()
        type(mock_category).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_category.name = "cat"

        from utils.discord_converters import ChannelConverter

        with pytest.raises(Exception):
            ChannelConverter.category_to_detail(mock_category)


# ---------------------------------------------------------------------------
# ChannelConverter.thread_to_summary — additional edge cases
# ---------------------------------------------------------------------------


class TestThreadToSummaryEdgeCases:
    def test_thread_to_summary_owner_id_non_integer_falls_back_to_zero(self):
        """thread_to_summary should set owner_id=0 when owner_id can't be cast to int."""
        mock_thread = DiscordMockUtils.create_mock_thread(thread_id=1, name="t", parent_id=10, guild_id=1, owner_id=1)
        mock_thread.owner_id = "not-an-int"
        mock_thread.owner = None
        mock_thread.creator = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)
        assert result.owner_id == 0

    def test_thread_to_summary_applied_tags_with_integer_elements(self):
        """thread_to_summary should handle applied_tags as list of integers."""
        mock_thread = DiscordMockUtils.create_mock_thread(thread_id=1, name="t", parent_id=10, guild_id=1, owner_id=1)
        # Plain integers: no .id attribute, no .name/.emoji
        mock_thread.applied_tags = [111, 222, 333]
        mock_thread.applied_tag_ids = None

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)
        assert result.applied_tag_ids == [111, 222, 333]

    def test_thread_to_summary_applied_tag_ids_with_exception_is_none(self):
        """thread_to_summary should set applied_tag_ids=None when applied_tags is None and applied_tag_ids raises."""
        mock_thread = DiscordMockUtils.create_mock_thread(thread_id=1, name="t", parent_id=10, guild_id=1, owner_id=1)
        mock_thread.applied_tags = None
        # applied_tag_ids will raise TypeError when iterated
        mock_thread.applied_tag_ids = ["not-int", None]

        from utils.discord_converters import ChannelConverter

        result = ChannelConverter.thread_to_summary(mock_thread)
        # int("not-int") raises, so applied_tag_ids becomes None
        assert result.applied_tag_ids is None

    def test_thread_to_summary_raises_on_critical_error(self):
        """thread_to_summary should propagate exception when thread.id raises."""
        mock_thread = MagicMock()
        type(mock_thread).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))
        mock_thread.name = "t"

        from utils.discord_converters import ChannelConverter

        with pytest.raises(Exception):
            ChannelConverter.thread_to_summary(mock_thread)

    def test_forum_tag_to_payload_raises_on_tag_error(self):
        """forum_tag_to_payload should propagate exception when tag_to_dict raises."""
        # Passing None as tag causes tag_to_dict to fail with an exception path
        mock_tag = MagicMock()

        # Make tag_to_dict fail by making the tag itself raise
        def boom():
            raise RuntimeError("explode")

        mock_tag.__class__ = type("BadTag", (), {"id": property(lambda self: boom())})

        from utils.discord_converters import ChannelConverter

        # tag_to_dict is resilient; instead test the re-raise path via a tag that
        # causes forum_tag_to_payload's own try-block to fail
        with (
            patch("utils.discord_converters.tag_to_dict", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError),
        ):
            ChannelConverter.forum_tag_to_payload(mock_tag, channel_id=1)


# ---------------------------------------------------------------------------
# PermissionConverter — exception path
# ---------------------------------------------------------------------------


class TestPermissionConverterExceptionPath:
    def test_overwrite_to_payload_raises_on_pair_failure(self):
        """overwrite_to_payload should propagate exception when overwrite.pair() raises."""
        mock_role = DiscordMockUtils.create_mock_role(role_id=1)
        mock_role.__class__ = sys.modules["discord"].Role

        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(side_effect=AttributeError("no pair"))

        from utils.discord_converters import PermissionConverter

        with pytest.raises(Exception):
            PermissionConverter.overwrite_to_payload(mock_role, mock_overwrite, channel_id=1)


# ---------------------------------------------------------------------------
# RoleConverter — exception path
# ---------------------------------------------------------------------------


class TestRoleConverterExceptionPath:
    def test_role_to_payload_raises_on_missing_id(self):
        """role_to_payload should propagate exception when role.id raises."""
        mock_role = MagicMock()
        type(mock_role).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_role.name = "TestRole"

        from utils.discord_converters import RoleConverter

        with pytest.raises(Exception):
            RoleConverter.role_to_payload(mock_role)


# ---------------------------------------------------------------------------
# UserConverter — exception paths
# ---------------------------------------------------------------------------


class TestUserConverterExceptionPaths:
    def test_user_to_payload_raises_on_missing_id(self):
        """user_to_payload should propagate exception when user.id raises."""
        mock_user = MagicMock()
        type(mock_user).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_user.name = "TestUser"
        mock_user.discriminator = "0001"

        from utils.discord_converters import UserConverter

        with pytest.raises(Exception):
            UserConverter.user_to_payload(mock_user)

    def test_member_to_payload_raises_on_missing_id(self):
        """member_to_payload should propagate exception when member.id raises."""
        mock_member = MagicMock()
        type(mock_member).id = property(lambda self: (_ for _ in ()).throw(AttributeError("no id")))
        mock_member.display_name = "TestMember"
        mock_member.name = "TestMember"

        from utils.discord_converters import UserConverter

        with pytest.raises(Exception):
            UserConverter.member_to_payload(mock_member)


# ---------------------------------------------------------------------------
# MessageConverter — embed conversion failure and summary fallback paths
# ---------------------------------------------------------------------------


class TestMessageConverterAdditionalPaths:
    def test_message_to_payload_handles_embed_conversion_failure(self):
        """message_to_payload should leave content=None when embed conversion fails."""
        mock_embed = MagicMock()
        # EmbedConverter.embed_to_payload will fail on this embed
        mock_message = DiscordMockUtils.create_mock_message(
            message_id=1, channel_id=2, guild_id=3, author_id=4, content="", embeds=[mock_embed]
        )
        mock_message.type = MagicMock()
        mock_message.type.name = "default"

        with patch("utils.discord_converters.EmbedConverter.embed_to_payload", side_effect=Exception("embed failed")):
            from utils.discord_converters import MessageConverter

            result = MessageConverter.message_to_payload(mock_message)
            assert result.content is None

    def test_message_to_payload_raises_on_critical_error(self):
        """message_to_payload should propagate exception on critical failure."""
        mock_message = MagicMock()
        type(mock_message).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))

        from utils.discord_converters import MessageConverter

        with pytest.raises(Exception):
            MessageConverter.message_to_payload(mock_message)

    def test_message_to_summary_uses_embed_title_when_no_description(self):
        """message_to_summary should use embed title when description is absent."""
        mock_embed = MagicMock()
        mock_embed.description = None
        mock_embed.title = None
        mock_embed.fields = []
        mock_embed.footer = None

        mock_message = DiscordMockUtils.create_mock_message(message_id=1, author_id=2, content="", embeds=[mock_embed])
        mock_message.author.name = "TestUser"

        # Make embed_to_payload return an EmbedPayload with title only
        mock_ep = MagicMock()
        mock_ep.description = None
        mock_ep.title = "The Embed Title"
        mock_ep.fields = []
        mock_ep.footer_text = None

        with patch("utils.discord_converters.EmbedConverter.embed_to_payload", return_value=mock_ep):
            from utils.discord_converters import MessageConverter

            result = MessageConverter.message_to_summary(mock_message)
            assert result.content == "The Embed Title"

    def test_message_to_summary_uses_embed_fields_when_no_title(self):
        """message_to_summary should join embed fields when title and description are absent."""
        mock_field = MagicMock()
        mock_field.name = "Key"
        mock_field.value = "Val"

        mock_embed = MagicMock()
        mock_embed.description = None
        mock_embed.title = None
        mock_embed.fields = [mock_field]

        mock_message = DiscordMockUtils.create_mock_message(message_id=1, author_id=2, content="", embeds=[mock_embed])
        mock_message.author.name = "TestUser"

        mock_ep = MagicMock()
        mock_ep.description = None
        mock_ep.title = None
        mock_ep.fields = [mock_field]
        mock_ep.footer_text = None

        with patch("utils.discord_converters.EmbedConverter.embed_to_payload", return_value=mock_ep):
            from utils.discord_converters import MessageConverter

            result = MessageConverter.message_to_summary(mock_message)
            assert result.content == "Key: Val"

    def test_message_to_summary_uses_footer_when_nothing_else(self):
        """message_to_summary should use footer text as last resort."""
        mock_embed = MagicMock()

        mock_message = DiscordMockUtils.create_mock_message(message_id=1, author_id=2, content="", embeds=[mock_embed])
        mock_message.author.name = "TestUser"

        mock_ep = MagicMock()
        mock_ep.description = None
        mock_ep.title = None
        mock_ep.fields = []
        mock_ep.footer_text = "Footer content"

        with patch("utils.discord_converters.EmbedConverter.embed_to_payload", return_value=mock_ep):
            from utils.discord_converters import MessageConverter

            result = MessageConverter.message_to_summary(mock_message)
            assert result.content == "Footer content"

    def test_message_to_summary_fallback_raw_embed_on_converter_failure(self):
        """message_to_summary should fall back to raw embed attrs when converter fails."""
        mock_embed = MagicMock()
        mock_embed.description = "Raw description"
        mock_embed.title = None
        mock_embed.fields = []

        mock_message = DiscordMockUtils.create_mock_message(message_id=1, author_id=2, content="", embeds=[mock_embed])
        mock_message.author.name = "TestUser"

        with patch(
            "utils.discord_converters.EmbedConverter.embed_to_payload", side_effect=Exception("converter failed")
        ):
            from utils.discord_converters import MessageConverter

            result = MessageConverter.message_to_summary(mock_message)
            assert result.content == "Raw description"

    def test_message_to_summary_raises_on_critical_error(self):
        """message_to_summary should propagate critical exceptions."""
        mock_message = MagicMock()
        type(mock_message).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))

        from utils.discord_converters import MessageConverter

        with pytest.raises(Exception):
            MessageConverter.message_to_summary(mock_message)
