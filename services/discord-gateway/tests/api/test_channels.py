"""Tests for the channels API endpoints.

Fidelity notes
--------------
This file intentionally does NOT patch ``resolve_bot``, ``get_entity_or_404``,
``handle_discord_exception`` or ``ChannelConverter``/``PermissionConverter``.
``DiscordMockUtils.create_mock_bot`` returns a ``MagicMock(spec=commands.Bot)``
with ``is_ready()==True``, so the real ``resolve_bot``/``get_entity_or_404``
helpers run end-to-end against it (cache lookup -> fetch fallback -> real
404/500 mapping via ``handle_discord_exception``, which always raises).  The
real ``ChannelConverter``/``PermissionConverter`` run against mock channels
that carry real-typed attributes, so the response bodies asserted below are
genuine serialization output rather than test-fabricated dicts (this is the
class of prod defect this suite exists to catch).

Channel mocks use the REAL ``discord`` module (no ``sys.modules`` swap) and
set ``__class__`` to the real ``discord.TextChannel``/``CategoryChannel``/
``ForumChannel`` classes so the router's ``isinstance`` type-dispatch checks
exercise their real branches.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

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


def create_mock_channel(channel_type="text", channel_id=1234567890):
    """Create a mock Discord TextChannel/VoiceChannel using DiscordMockUtils.

    ``__class__`` is set to the real discord type so router ``isinstance``
    dispatch (e.g. ``get_channel``'s category rejection) exercises its real
    branch.
    """
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
    channel.__class__ = discord.VoiceChannel if channel_type == "voice" else discord.TextChannel

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
    channel.__class__ = discord.ForumChannel
    return channel


def create_mock_category(channel_id=1111111111):
    """Create a mock Discord category channel using DiscordMockUtils."""
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=channel_id,
        name="Test Category",
        position=1,
        guild_id=987654321,
    )
    category.__class__ = discord.CategoryChannel
    return category


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils.

    ``fetch_channel`` raises a real ``discord.NotFound`` for unknown ids
    (matching production's cache-miss -> fetch -> 404 behaviour) instead of
    silently returning ``None``, so the real ``get_entity_or_404`` /
    ``handle_discord_exception`` helpers produce a genuine 404.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    # Build channels once so tests that mutate a channel returned by
    # ``get_channel`` (e.g. setting ``.overwrites``) see that mutation
    # reflected on subsequent lookups by the router — a fresh mock per call
    # would silently discard the test's setup.
    channels = {
        1234567890: create_mock_channel(),
        1234567891: create_mock_forum_channel(),
        1111111111: create_mock_category(),
    }

    def get_channel(channel_id):
        return channels.get(channel_id)

    async def fetch_channel(channel_id):
        channel = get_channel(channel_id)
        if channel is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return channel

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)

    return bot


@pytest.fixture
def channels_test_app(mock_bot):
    """Create a test FastAPI app with the channels router and a real bot state.

    No helpers/converters are patched — the real ``resolve_bot``,
    ``get_entity_or_404``, ``handle_discord_exception`` and
    ``ChannelConverter``/``PermissionConverter`` all run against ``mock_bot``.
    """
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.channels import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def channels_client(channels_test_app):
    """Create a test client for the channels API."""
    return TestClient(channels_test_app)


class TestGetChannel:
    """Tests for GET /channels/{channel_id} endpoint."""

    def test_get_channel_returns_200(self, channels_client):
        """GET /channels/{channel_id} should return 200 with real serialized channel details."""
        response = channels_client.get("/api/v1/channels/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        # Real ChannelConverter.channel_to_detail output — not a canned dict.
        assert data["data"] == {
            "id": 1234567890,
            "name": "test-channel",
            "type": "text",
            "position": 1,
            "guild_id": 987654321,
            "created_at": "2020-01-01T00:00:00",
            "topic": "Test topic",
            "nsfw": False,
            "slowmode_delay": 0,
            "bitrate": None,
            "user_limit": None,
            "category_id": 1111111111,
            "default_auto_archive_duration": None,
        }

    def test_get_channel_not_found_returns_404(self, channels_client):
        """GET /channels/{channel_id} should return 404 for non-existent channel.

        Exercises the real cache-miss -> fetch -> discord.NotFound ->
        handle_discord_exception -> HTTP 404 mapping end to end.
        """
        response = channels_client.get("/api/v1/channels/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_category_channel_returns_400(self, channels_client):
        """GET /channels/{channel_id} for a category should return 400.

        channel 1111111111 has ``__class__`` set to the real
        ``discord.CategoryChannel``, so the router's real
        ``isinstance(channel, discord.CategoryChannel)`` branch fires.
        """
        response = channels_client.get("/api/v1/channels/1111111111")
        assert response.status_code == 400
        assert "category" in response.json()["detail"].lower()


class TestGetChannelPermissions:
    """Tests for GET /channels/{channel_id}/permissions endpoint."""

    def test_get_permissions_returns_200(self, channels_client, mock_bot):
        """GET /channels/{channel_id}/permissions should return 200 with real serialized overwrites."""
        role = DiscordMockUtils.create_mock_role(role_id=555, guild_id=987654321, name="mods")
        role.__class__ = discord.Role
        overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=1024, deny=8)
        channel = mock_bot.get_channel(1234567890)
        channel.overwrites = {role: overwrite}

        response = channels_client.get("/api/v1/channels/1234567890/permissions")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == [
            {
                "id": "1234567890:555",
                "channel_id": 1234567890,
                "target_id": 555,
                "type": "role",
                "allow": 1024,
                "deny": 8,
            }
        ]

    def test_get_permissions_empty_returns_empty_list(self, channels_client):
        """A channel with no overwrites yields an empty (not fabricated) list."""
        response = channels_client.get("/api/v1/channels/1234567890/permissions")
        assert response.status_code == 200
        assert response.json()["data"] == []


class TestListForumThreads:
    """Tests for GET /channels/{channel_id}/threads endpoint."""

    def test_list_threads_not_forum_error(self, channels_client):
        """GET /channels/{channel_id}/threads should return 400 for non-forum channel."""
        response = channels_client.get("/api/v1/channels/1234567890/threads")
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()

    def test_list_threads_forum_returns_200(self, channels_client, mock_bot):
        """A real ForumChannel (channel_id=1234567891) lists its threads via the real converter."""
        response = channels_client.get("/api/v1/channels/1234567891/threads")
        assert response.status_code == 200
        assert response.json()["data"] == []


class TestListForumTags:
    """Tests for GET /channels/{channel_id}/tags endpoint."""

    def test_list_tags_not_forum_error(self, channels_client):
        """GET /channels/{channel_id}/tags should return 400 for non-forum channel."""
        response = channels_client.get("/api/v1/channels/1234567890/tags")
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()

    def test_list_tags_forum_returns_200(self, channels_client, mock_bot):
        """A real ForumChannel lists its tags via the real converter (real forum_tag_to_payload)."""
        forum = mock_bot.get_channel(1234567891)
        forum.available_tags = [DiscordMockUtils.create_mock_forum_tag(tag_id=7, name="bug", channel_id=1234567891)]

        response = channels_client.get("/api/v1/channels/1234567891/tags")
        assert response.status_code == 200
        assert response.json()["data"] == [{"id": 7, "channel_id": 1234567891, "name": "bug", "emoji": None}]
