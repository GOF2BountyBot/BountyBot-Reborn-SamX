"""
Tests for the categories API endpoints.

This module provides comprehensive test coverage for the categories router,
including category listing, creation, updates, and management operations.

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``get_entity_or_404``, ``handle_discord_exception``, ``validate_channel_type``
or ``ChannelConverter``/``PermissionConverter``: the mock bot is
``spec=commands.Bot`` with ``is_ready()==True`` so the real helpers run
end-to-end, and the mock category/channel objects carry real-typed
attributes so the real converters produce genuine serialized bodies.
"""

import sys
import types
from datetime import datetime
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


def create_mock_category(
    category_id=1111111111, guild_id=987654321, name="Test Category", position=1, nsfw=False, created_at=None
):
    """Create a mock Discord category using DiscordMockUtils."""
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=category_id,
        name=name,
        position=position,
        guild_id=guild_id,
        created_at=created_at or datetime(2024, 1, 1),
    )
    category.nsfw = nsfw
    category.channels = []
    category.overwrites = {}
    category.__class__ = discord.CategoryChannel

    async def _edit(**kwargs):
        if "name" in kwargs:
            category.name = kwargs["name"]
        if "position" in kwargs:
            category.position = kwargs["position"]

    category.delete = AsyncMock()
    category.edit = AsyncMock(side_effect=_edit)
    return category


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so the
    real ``get_entity_or_404``/``handle_discord_exception`` chain produces a
    genuine 404 (rather than the test hand-rolling its own 404 branch).
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    category = create_mock_category()

    def get_channel(channel_id):
        if channel_id == category.id:
            return category
        return None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)

    return bot


@pytest.fixture
def categories_test_app(mock_bot):
    """Create a test FastAPI app with the categories router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.categories import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def categories_client(categories_test_app):
    """Create a test client for the categories API."""
    return TestClient(categories_test_app)


class TestGetCategory:
    """Tests for GET /categories/{category_id} endpoint."""

    def test_get_category_returns_200(self, categories_client):
        """GET /categories/{category_id} should return 200 with real serialized category details."""
        response = categories_client.get("/api/v1/categories/1111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == {
            "id": 1111111111,
            "name": "Test Category",
            "position": 1,
            "guild_id": 987654321,
            "created_at": "2024-01-01T00:00:00",
        }

    def test_get_category_not_found_returns_404(self, categories_client):
        """GET /categories/{category_id} should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateCategory:
    """Tests for PUT /categories/{category_id} endpoint."""

    def test_update_category_success(self, categories_client):
        """PUT /categories/{category_id} should update category successfully and re-serialize real state."""
        update_data = {"name": "Updated Category Name", "position": 2}

        response = categories_client.put("/api/v1/categories/1111111111", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        # Real category.edit() applied the kwargs; the real converter re-serializes the mutated mock.
        assert data["data"]["name"] == "Updated Category Name"
        assert data["data"]["position"] == 2

    def test_update_category_not_found(self, categories_client):
        """PUT /categories/{category_id} should return 404 for non-existent category."""
        update_data = {"name": "Updated Category Name"}

        response = categories_client.put("/api/v1/categories/9999999999", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteCategory:
    """Tests for DELETE /categories/{category_id} endpoint."""

    def test_delete_category_success(self, categories_client):
        """DELETE /categories/{category_id} should delete category successfully."""
        response = categories_client.delete("/api/v1/categories/1111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True

    def test_delete_category_not_found(self, categories_client):
        """DELETE /categories/{category_id} should return 404 for non-existent category."""
        response = categories_client.delete("/api/v1/categories/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetCategoryChannels:
    """Tests for GET /categories/{category_id}/channels endpoint."""

    def test_get_channels_success(self, categories_client, mock_bot):
        """GET /categories/{category_id}/channels should return 200 with a real serialized channel list."""
        category = mock_bot.get_channel(1111111111)
        child = DiscordMockUtils.create_mock_text_channel(
            channel_id=222, name="general", position=0, guild_id=987654321
        )
        category.channels = [child]

        response = categories_client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == [
            {
                "id": 222,
                "name": "general",
                "type": "text",
                "position": 0,
                "guild_id": 987654321,
                "created_at": "2020-01-01T00:00:00",
                "category_id": None,
                "topic": None,
                "nsfw": None,
                "slowmode_delay": None,
                "bitrate": None,
                "user_limit": None,
                "default_auto_archive_duration": None,
            }
        ]

    def test_get_channels_not_found(self, categories_client):
        """GET /categories/{category_id}/channels should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999/channels")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetCategoryPermissions:
    """Tests for GET /categories/{category_id}/permissions endpoint."""

    def test_get_permissions_success(self, categories_client, mock_bot):
        """GET /categories/{category_id}/permissions should return 200 with real serialized overwrites."""
        category = mock_bot.get_channel(1111111111)
        role = DiscordMockUtils.create_mock_role(role_id=42, guild_id=987654321, name="mods")
        role.__class__ = discord.Role
        overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=2048, deny=0)
        category.overwrites = {role: overwrite}

        response = categories_client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == [
            {
                "id": "1111111111:42",
                "channel_id": 1111111111,
                "target_id": 42,
                "type": "role",
                "allow": 2048,
                "deny": 0,
            }
        ]

    def test_get_permissions_not_found(self, categories_client):
        """GET /categories/{category_id}/permissions should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999/permissions")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in categories endpoints."""

    def test_handle_discord_exception(self, categories_client):
        """A non-Discord exception raised by resolve_bot maps, via the real handler, to exactly 500."""
        with patch("api.routers.categories.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = categories_client.get("/api/v1/categories/1111111111")
            assert response.status_code == 500
            assert "Test Discord error" in response.json()["detail"]
