"""
Tests for the tags API endpoints.

This module provides comprehensive test coverage for the tags router,
including tag retrieval, creation, updates, and deletion.
Actual routes:
  GET    /tags/{tag_id}
  POST   /channels/{channel_id}/tags
  PUT    /tags/{tag_id}
  DELETE /tags/{tag_id}

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception``, ``get_entity_or_404`` or ``ChannelConverter``.
The mock channel/tag objects are ``spec=discord.ForumChannel`` /
``spec=discord.ForumTag`` so ``isinstance`` checks in the router pass for
real, and — since the installed discord.py (2.7.1) exposes neither
``ForumTag.edit``/``ForumTag.delete`` nor
``ForumChannel.edit_tag``/``ForumChannel.delete_tag`` — ``hasattr()`` on the
spec'd mocks is faithfully ``False`` for those, so ``update_tag``/
``delete_tag`` genuinely fall through to their real last-resort
``channel.edit(available_tags=...)`` path, exactly as production does.
``channel.edit``/``channel.create_tag`` are the true outbound-Discord-API
boundary; their mocks simulate the edit in-memory (faithful shape/behavior)
rather than returning a canned response.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

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


def create_mock_tag(tag_id=1234567890, name="Test Tag", emoji=None):
    """Create a real-spec'd mock Discord ForumTag."""
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def create_mock_channel(channel_id=555555555, guild_id=987654321, tags=None):
    """Create a mock Discord ForumChannel.

    ``spec=discord.ForumChannel`` makes ``isinstance(channel,
    discord.ForumChannel)`` pass, and makes ``hasattr(channel, "edit_tag")``
    / ``hasattr(channel, "delete_tag")`` faithfully ``False`` (this
    discord.py version doesn't implement them), so the router's real
    fallback branches run.
    """
    channel = MagicMock(spec=discord.ForumChannel)
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    channel.available_tags = tags if tags is not None else [create_mock_tag(1234567890)]

    async def _create_tag(name, emoji=None, **_kwargs):
        new_id = max((t.id for t in channel.available_tags), default=0) + 1
        new_tag = create_mock_tag(tag_id=new_id, name=name, emoji=emoji)
        channel.available_tags.append(new_tag)
        return new_tag

    async def _edit(**kwargs):
        """Simulate the real ``ForumChannel.edit(available_tags=...)`` call.

        Accepts either dict payloads (``tags_to_edit_payload`` output, used
        by ``update_tag``'s fallback) or ForumTag-like objects passed
        directly (used by ``delete_tag``'s ``remaining`` list).
        """
        if "available_tags" not in kwargs:
            return
        new_list = []
        next_id = max((t.id for t in channel.available_tags if isinstance(t.id, int)), default=0) + 1
        for item in kwargs["available_tags"]:
            if hasattr(item, "moderated"):
                # Already a ForumTag-like object (delete_tag's remaining list).
                new_list.append(item)
                continue
            data = item if isinstance(item, dict) else item.to_dict()
            tid = data.get("id")
            existing = discord.utils.get(channel.available_tags, id=tid) if tid is not None else None
            if existing is not None:
                existing.name = data.get("name")
                existing.emoji = data.get("emoji")
                new_list.append(existing)
            else:
                # Discord assigns a real numeric id to newly-created tags; synthesize one.
                if tid is None:
                    tid, next_id = next_id, next_id + 1
                new_list.append(create_mock_tag(tag_id=tid, name=data.get("name"), emoji=data.get("emoji")))
        channel.available_tags = new_list

    channel.edit = AsyncMock(side_effect=_edit)
    channel.create_tag = AsyncMock(side_effect=_create_tag)
    return channel


@pytest.fixture
def mock_channel():
    return create_mock_channel(555555555, 987654321)


@pytest.fixture
def mock_bot(mock_channel):
    """Create a mock Discord bot using DiscordMockUtils.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so
    ``create_forum_tag``'s real ``get_entity_or_404`` -> ``handle_discord_exception``
    chain produces a genuine 404.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(channel_id):
        if channel_id == mock_channel.id:
            return mock_channel
        return None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)

    mock_guild = MagicMock()
    mock_guild.id = mock_channel.guild.id
    mock_guild.channels = [mock_channel]
    bot.guilds = [mock_guild]

    return bot


@pytest.fixture
def tags_test_app(mock_bot):
    """Create a test FastAPI app with the tags router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.tags import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def tags_client(tags_test_app):
    """Create a test client for the tags API."""
    return TestClient(tags_test_app)


class TestGetTag:
    """Tests for GET /tags/{tag_id} endpoint."""

    def test_get_tag_returns_200(self, tags_client):
        """GET /tags/{tag_id} should return 200 with real serialized tag details."""
        response = tags_client.get("/api/v1/tags/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 1234567890
        assert data["data"]["name"] == "Test Tag"
        assert data["data"]["channel_id"] == 555555555

    def test_get_tag_not_found_returns_404(self, tags_client):
        """GET /tags/{tag_id} should return 404 for non-existent tag (real search-miss branch)."""
        response = tags_client.get("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_get_tag_invalid_id_returns_422(self, tags_client):
        """GET /tags/{tag_id} should return 422 for invalid tag ID."""
        response = tags_client.get("/api/v1/tags/invalid")
        assert response.status_code == 422


class TestCreateForumTag:
    """Tests for POST /channels/{channel_id}/tags endpoint."""

    def test_create_forum_tag_success(self, tags_client, mock_channel):
        """POST /channels/{channel_id}/tags should create tag via the real channel.create_tag call."""
        tag_data = {
            "name": "New Tag",
            "emoji": None,
        }
        response = tags_client.post("/api/v1/channels/555555555/tags", json=tag_data)
        assert response.status_code == 201

        data = response.json()
        assert data["status"] == "created"
        assert "data" in data
        assert data["data"]["name"] == "New Tag"
        assert data["data"]["channel_id"] == 555555555
        # the id must be a newly-minted one from the real create_tag call, not the seed tag's id
        assert data["data"]["id"] != 1234567890
        assert any(t.id == data["data"]["id"] for t in mock_channel.available_tags)

    def test_create_forum_tag_missing_name_returns_422(self, tags_client):
        """POST /channels/{channel_id}/tags should return 422 for missing name."""
        tag_data = {
            "emoji": None,
        }
        response = tags_client.post("/api/v1/channels/555555555/tags", json=tag_data)
        assert response.status_code == 422

    def test_create_forum_tag_channel_not_found(self, tags_client):
        """POST /channels/{channel_id}/tags should return 404 (real discord.NotFound) for non-existent channel."""
        tag_data = {"name": "New Tag"}
        response = tags_client.post("/api/v1/channels/999999999/tags", json=tag_data)
        assert response.status_code == 404


class TestUpdateTag:
    """Tests for PUT /tags/{tag_id} endpoint."""

    def test_update_tag_success(self, tags_client):
        """PUT /tags/{tag_id} should update tag via the real fallback channel.edit(available_tags=...) call."""
        update_data = {
            "name": "Updated Tag Name",
        }
        response = tags_client.put("/api/v1/tags/1234567890", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["id"] == 1234567890
        assert data["data"]["name"] == "Updated Tag Name"

    def test_update_tag_not_found(self, tags_client):
        """PUT /tags/{tag_id} should return 404 for non-existent tag."""
        update_data = {"name": "Updated Tag Name"}
        response = tags_client.put("/api/v1/tags/9999999999", json=update_data)
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()


class TestDeleteTag:
    """Tests for DELETE /tags/{tag_id} endpoint."""

    def test_delete_tag_success(self, tags_client, mock_channel):
        """DELETE /tags/{tag_id} should delete tag via the real fallback channel.edit(available_tags=...) call."""
        response = tags_client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert not any(t.id == 1234567890 for t in mock_channel.available_tags)

    def test_delete_tag_not_found(self, tags_client):
        """DELETE /tags/{tag_id} should return 404 for non-existent tag."""
        response = tags_client.delete("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in tags endpoints.

    ``resolve_bot`` (a network/readiness boundary) is patched to raise a
    generic error so the real, unpatched ``handle_discord_exception`` mapping
    of an unrecognized exception to HTTP 500 is exercised end-to-end.
    """

    def test_handle_discord_exception(self, tags_client):
        """Tags endpoints should map an unexpected error to a real 500 via handle_discord_exception."""
        with patch("api.routers.tags.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = tags_client.get("/api/v1/tags/1234567890")
            assert response.status_code == 500
            assert "test discord error" in response.json()["detail"].lower()
