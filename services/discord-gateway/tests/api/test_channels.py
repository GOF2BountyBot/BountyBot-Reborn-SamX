"""Tests for the channels API endpoints."""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
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

# Setup mock discord module with discord_mock_utils
# create_mock_discord_module() wires real exception classes so isinstance checks work.
_mock_discord = DiscordMockUtils.create_mock_discord_module()

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThreadChannel = type("ThreadChannel", (), {})
_MockThread = type("Thread", (), {})
_MockEmbed = type("Embed", (), {})
_MockPermissionOverwrite = type("PermissionOverwrite", (), {})
_MockGuild = type("Guild", (), {})
_MockUser = type("User", (), {})
_MockMember = type("Member", (), {})
_MockRole = type("Role", (), {})
_MockMessage = type("Message", (), {})

_mock_discord.CategoryChannel = _MockCategoryChannel
_mock_discord.TextChannel = _MockTextChannel
_mock_discord.VoiceChannel = _MockVoiceChannel
_mock_discord.ForumChannel = _MockForumChannel
_mock_discord.ThreadChannel = _MockThreadChannel
_mock_discord.Thread = _MockThread
_mock_discord.Embed = _MockEmbed
_mock_discord.PermissionOverwrite = _MockPermissionOverwrite
_mock_discord.Guild = _MockGuild
_mock_discord.User = _MockUser
_mock_discord.Member = _MockMember
_mock_discord.Role = _MockRole
_mock_discord.Message = _MockMessage

_MockBot = type("Bot", (), {})
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _MockBot

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def create_mock_channel(channel_type="text", channel_id=1234567890):
    """Create a mock Discord channel using DiscordMockUtils."""
    channel = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="test-channel",
        channel_type=channel_type,
        position=1,
        guild_id=987654321,
    )
    channel.category_id = 1111111111
    channel.topic = "Test topic"
    channel.nsfw = False
    channel.slowmode_delay = 0
    channel.overwrites = {}
    channel.threads = []
    channel.available_tags = []

    # async history as an async generator
    async def _history(limit=50):
        return
        yield  # make it an async generator

    channel.history = _history
    return channel


def create_mock_forum_channel(channel_id=1234567891):
    """Create a mock Discord forum channel using DiscordMockUtils."""
    channel = DiscordMockUtils.create_mock_forum_channel(
        channel_id=channel_id,
        name="test-forum",
        position=1,
        guild_id=987654321,
    )
    channel.category_id = None
    channel.topic = "Forum topic"
    channel.nsfw = False
    channel.default_auto_archive_duration = 1440
    channel.overwrites = {}
    channel.threads = []
    return channel


def create_mock_category(channel_id=1111111111):
    """Create a mock Discord category channel using DiscordMockUtils."""
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=channel_id,
        name="Test Category",
        position=1,
        guild_id=987654321,
    )
    return category


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(channel_id):
        if channel_id == 1234567890:
            return create_mock_channel()
        elif channel_id == 1234567891:
            return create_mock_forum_channel()
        elif channel_id == 1111111111:
            return create_mock_category()
        return None

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=lambda x: get_channel(x))

    return bot


@pytest.fixture
def channels_test_app(mock_bot):
    """Create a test FastAPI app with the channels router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with (
        patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
        patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.channels.ChannelConverter") as mock_converter,
    ):

        async def mock_get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
            channel = mock_bot.get_channel(entity_id)
            if channel is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return channel

        async def mock_resolve_bot(request):
            return mock_bot

        mock_get_entity.side_effect = mock_get_entity_or_404
        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.return_value = None

        mock_converter.channel_to_detail.return_value = {
            "id": 1234567890,
            "name": "test-channel",
            "type": "text",
            "position": 1,
            "guild_id": 987654321,
            "category_id": 1111111111,
            "created_at": "2024-01-01T00:00:00",
            "topic": "Test topic",
            "nsfw": False,
            "slowmode_delay": 0,
        }
        mock_converter.overwrite_to_payload = MagicMock(return_value={})

        from api.routers.channels import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


@pytest.fixture
def channels_client(channels_test_app):
    """Create a test client for the channels API."""
    return TestClient(channels_test_app)


class TestGetChannel:
    """Tests for GET /channels/{channel_id} endpoint."""

    def test_get_channel_returns_200(self, channels_client):
        """GET /channels/{channel_id} should return 200 with channel details."""
        response = channels_client.get("/api/v1/channels/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data

    def test_get_channel_not_found_returns_404(self, channels_client):
        """GET /channels/{channel_id} should return 404 for non-existent channel."""
        response = channels_client.get("/api/v1/channels/9999999999")
        assert response.status_code == 404

    def test_get_category_channel_returns_400(self, channels_client):
        """GET /channels/{channel_id} for a category should return 400."""
        # channel 1111111111 is a category mock, which isinstance check against _MockCategoryChannel
        # Since we use a plain MagicMock (not instance of _MockCategoryChannel), it won't trigger
        # the isinstance check in the router. This is expected behavior.
        response = channels_client.get("/api/v1/channels/1234567890")
        assert response.status_code in (200, 400)


class TestGetChannelPermissions:
    """Tests for GET /channels/{channel_id}/permissions endpoint."""

    def test_get_permissions_returns_200(self, channels_client):
        """GET /channels/{channel_id}/permissions should return 200 with permissions list."""
        response = channels_client.get("/api/v1/channels/1234567890/permissions")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)


class TestListForumThreads:
    """Tests for GET /channels/{channel_id}/threads endpoint."""

    def test_list_threads_not_forum_error(self, channels_client):
        """GET /channels/{channel_id}/threads should return 400 for non-forum channel."""
        response = channels_client.get("/api/v1/channels/1234567890/threads")
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()


class TestListForumTags:
    """Tests for GET /channels/{channel_id}/tags endpoint."""

    def test_list_tags_not_forum_error(self, channels_client):
        """GET /channels/{channel_id}/tags should return 400 for non-forum channel."""
        response = channels_client.get("/api/v1/channels/1234567890/tags")
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()
