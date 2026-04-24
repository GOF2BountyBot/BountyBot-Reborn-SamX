"""
Tests for the tags API endpoints.

This module provides comprehensive test coverage for the tags router,
including tag retrieval, creation, updates, and deletion.
Actual routes:
  GET    /tags/{tag_id}
  POST   /channels/{channel_id}/tags
  PUT    /tags/{tag_id}
  DELETE /tags/{tag_id}
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

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

# Setup mock discord module with discord_mock_utils
# create_mock_discord_module() already wires real discord exception classes
# (NotFound, Forbidden, HTTPException) so except clauses work correctly.
_mock_discord = _mock_utils.create_mock_discord_module_with_factories()

# Mock discord.ext with MagicMock Bot for module-level compatibility
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = MagicMock

_mock_discord.ext = _mock_discord_ext

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Override ForumChannel in the mock discord module with a real type so that
# isinstance(channel, discord.ForumChannel) works correctly in the router.
_MockForumChannel = type("ForumChannel", (), {})
_mock_discord.ForumChannel = _MockForumChannel


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """
    Re-assert the real discord module into sys.modules before each test
    and reload api.routers.tags so its ``discord`` reference is fresh.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    # Force the tags router to re-bind its 'discord' global to real discord
    from api.routers import tags as _tags_mod

    importlib.reload(_tags_mod)
    yield


def create_mock_tag(tag_id=1234567890, channel_id=555555555, name="Test Tag", emoji=None):
    """Create a mock Discord forum tag using DiscordMockUtils."""
    tag = DiscordMockUtils.create_mock_forum_tag(
        tag_id=tag_id,
        name=name,
        emoji=emoji,
        channel_id=channel_id,
    )
    tag.moderated = False
    tag.edit = AsyncMock()
    tag.delete = AsyncMock()
    return tag


def create_mock_channel(channel_id=555555555, guild_id=987654321):
    """Create a mock Discord forum channel.

    Uses ``MagicMock(spec=_MockForumChannel)`` so that:
    - isinstance(channel, discord.ForumChannel) passes when discord is
      patched to _mock_discord (which has ForumChannel = _MockForumChannel).
    - hasattr() checks only return True for attributes explicitly set,
      preventing MagicMock's auto-attribute generation from confusing the
      router's branching logic (e.g. ``delete_tag`` check).
    """
    channel = MagicMock(spec=_MockForumChannel)
    # Make isinstance(channel, discord.ForumChannel) work when discord is patched
    channel.__class__ = _MockForumChannel
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    channel.available_tags = [create_mock_tag(1234567890, channel_id)]
    channel.edit = AsyncMock()
    channel.create_tag = AsyncMock(return_value=create_mock_tag(1234567890, channel_id))
    return channel


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    mock_channel = create_mock_channel(555555555, 987654321)
    mock_guild = MagicMock()
    mock_guild.id = 987654321
    mock_guild.channels = [mock_channel]
    bot.guilds = [mock_guild]
    bot.get_channel = MagicMock(return_value=mock_channel)
    bot.fetch_channel = AsyncMock(return_value=mock_channel)

    return bot


@pytest.fixture
def tags_test_app(mock_bot):
    """Create a test FastAPI app with the tags router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    with (
        patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.tags.ChannelConverter") as mock_converter,
        patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
        patch("api.routers.tags.discord", _mock_discord),
    ):

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        # forum_tag_to_payload returns a ForumTag-like dict
        from api.schemas.channel_schemas import ForumTag

        _mock_tag_payload = ForumTag(
            id=1234567890,
            channel_id=555555555,
            name="Test Tag",
            emoji=None,
        )
        mock_converter.forum_tag_to_payload.return_value = _mock_tag_payload

        # get_entity_or_404 returns the mock channel for known ids
        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            if entity_id == 555555555:
                return mock_bot.get_channel(entity_id)
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"{entity_type} not found")

        mock_get_entity.side_effect = _get_entity

        # Set up discord.utils.get to find tag within available_tags
        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

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
        """GET /tags/{tag_id} should return 200 with tag details."""
        response = tags_client.get("/api/v1/tags/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 1234567890

    def test_get_tag_not_found_returns_404(self, tags_client):
        """GET /tags/{tag_id} should return 404 for non-existent tag."""
        response = tags_client.get("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_get_tag_invalid_id_returns_422(self, tags_client):
        """GET /tags/{tag_id} should return 422 for invalid tag ID."""
        response = tags_client.get("/api/v1/tags/invalid")
        assert response.status_code == 422


class TestCreateForumTag:
    """Tests for POST /channels/{channel_id}/tags endpoint."""

    def test_create_forum_tag_success(self, tags_client):
        """POST /channels/{channel_id}/tags should create tag successfully."""
        tag_data = {
            "name": "New Tag",
            "emoji": None,
        }
        response = tags_client.post("/api/v1/channels/555555555/tags", json=tag_data)
        assert response.status_code == 201

        data = response.json()
        assert data["status"] == "created"
        assert "data" in data
        assert data["data"]["id"] == 1234567890

    def test_create_forum_tag_missing_name_returns_422(self, tags_client):
        """POST /channels/{channel_id}/tags should return 422 for missing name."""
        tag_data = {
            "emoji": None,
        }
        response = tags_client.post("/api/v1/channels/555555555/tags", json=tag_data)
        assert response.status_code == 422

    def test_create_forum_tag_channel_not_found(self, tags_client):
        """POST /channels/{channel_id}/tags should return 404 for non-existent channel."""
        tag_data = {"name": "New Tag"}
        response = tags_client.post("/api/v1/channels/999999999/tags", json=tag_data)
        assert response.status_code == 404


class TestUpdateTag:
    """Tests for PUT /tags/{tag_id} endpoint."""

    def test_update_tag_success(self, tags_client):
        """PUT /tags/{tag_id} should update tag successfully."""
        update_data = {
            "name": "Updated Tag Name",
        }
        response = tags_client.put("/api/v1/tags/1234567890", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data

    def test_update_tag_not_found(self, tags_client):
        """PUT /tags/{tag_id} should return 404 for non-existent tag."""
        update_data = {"name": "Updated Tag Name"}
        response = tags_client.put("/api/v1/tags/9999999999", json=update_data)
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()


class TestDeleteTag:
    """Tests for DELETE /tags/{tag_id} endpoint."""

    def test_delete_tag_success(self, tags_client):
        """DELETE /tags/{tag_id} should delete tag successfully."""
        response = tags_client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True

    def test_delete_tag_not_found(self, tags_client):
        """DELETE /tags/{tag_id} should return 404 for non-existent tag."""
        response = tags_client.delete("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in tags endpoints."""

    def test_handle_discord_exception(self, tags_client):
        """Tags endpoints should handle Discord exceptions gracefully."""
        from fastapi import HTTPException as FastAPIHTTPException

        with (
            patch("api.routers.tags.resolve_bot", side_effect=Exception("Test Discord error")),
            patch(
                "api.routers.tags.handle_discord_exception",
                side_effect=FastAPIHTTPException(status_code=500, detail="Internal server error"),
            ),
        ):
            response = tags_client.get("/api/v1/tags/1234567890")
            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()
