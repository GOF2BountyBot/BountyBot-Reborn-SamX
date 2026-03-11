"""
Tests for the categories API endpoints.

This module provides comprehensive test coverage for the categories router,
including category listing, creation, updates, and management operations.
"""

import pytest
import importlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.testclient import TestClient
import sys
import os
import types
from datetime import datetime

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


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_discord():
    """
    Re-assert the real discord module into sys.modules before each test
    and reload api.routers.categories so its ``discord`` reference is fresh.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force the categories router to re-bind its 'discord' global to real discord
    from api.routers import categories as _categories_mod
    importlib.reload(_categories_mod)
    yield


def create_mock_category(
    category_id=1111111111,
    guild_id=987654321,
    name="Test Category",
    position=1,
    nsfw=False,
    created_at=None
):
    """Create a mock Discord category using DiscordMockUtils."""
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=category_id,
        name=name,
        position=position,
        guild_id=guild_id,
        created_at=created_at or datetime.now(),
    )
    category.nsfw = nsfw
    category.channels = []
    category.type = MagicMock()
    category.type.name = "category"
    category.overwrites = {}
    category.delete = AsyncMock()
    category.edit = AsyncMock()
    return category


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(channel_id):
        if channel_id == 1111111111:
            return create_mock_category(channel_id)
        return None

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=lambda x: get_channel(x))

    return bot


@pytest.fixture
def categories_test_app(mock_bot):
    """Create a test FastAPI app with the categories router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with patch("api.routers.categories.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
         patch("api.routers.categories.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.categories.validate_channel_type") as mock_validate, \
         patch("api.routers.categories.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.categories.ChannelConverter") as mock_converter:

        async def mock_get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
            category = mock_bot.get_channel(entity_id)
            if category is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"Channel {entity_id} not found")
            return category

        async def mock_resolve_bot(request):
            return mock_bot

        mock_get_entity.side_effect = mock_get_entity_or_404
        mock_resolve.side_effect = mock_resolve_bot
        # handle_discord_exception raises HTTPException, never returns a value
        async def mock_handle_discord_exception(operation, exc):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to {operation}: {exc}"
            )
        mock_handle.side_effect = mock_handle_discord_exception
        mock_validate.return_value = None  # don't raise, pass validation

        mock_converter.category_to_detail.return_value = {
            "id": 1111111111,
            "guild_id": 987654321,
            "name": "Test Category",
            "position": 1,
            "nsfw": False,
            "created_at": "2024-01-01T00:00:00"
        }
        mock_converter.channel_to_summary.return_value = {
            "id": 1111111111,
            "name": "test-channel",
            "type": "text",
        }

        from api.routers.categories import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


@pytest.fixture
def categories_client(categories_test_app):
    """Create a test client for the categories API."""
    return TestClient(categories_test_app)


class TestGetCategory:
    """Tests for GET /categories/{category_id} endpoint."""

    def test_get_category_returns_200(self, categories_client, mock_bot):
        """GET /categories/{category_id} should return 200 with category details."""
        response = categories_client.get("/api/v1/categories/1111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data

    def test_get_category_not_found_returns_404(self, categories_client):
        """GET /categories/{category_id} should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateCategory:
    """Tests for PUT /categories/{category_id} endpoint."""

    def test_update_category_success(self, categories_client):
        """PUT /categories/{category_id} should update category successfully."""
        update_data = {
            "name": "Updated Category Name",
            "position": 2
        }

        response = categories_client.put("/api/v1/categories/1111111111", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data

    def test_update_category_not_found(self, categories_client):
        """PUT /categories/{category_id} should return 404 for non-existent category."""
        update_data = {
            "name": "Updated Category Name"
        }

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

    def test_get_channels_success(self, categories_client):
        """GET /categories/{category_id}/channels should return 200 with channel list."""
        response = categories_client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_channels_not_found(self, categories_client):
        """GET /categories/{category_id}/channels should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999/channels")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetCategoryPermissions:
    """Tests for GET /categories/{category_id}/permissions endpoint."""

    def test_get_permissions_success(self, categories_client):
        """GET /categories/{category_id}/permissions should return 200 with permissions."""
        response = categories_client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_permissions_not_found(self, categories_client):
        """GET /categories/{category_id}/permissions should return 404 for non-existent category."""
        response = categories_client.get("/api/v1/categories/9999999999/permissions")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in categories endpoints."""

    def test_handle_discord_exception(self, categories_client):
        """Categories endpoints should handle Discord exceptions gracefully."""
        with patch("api.routers.categories.resolve_bot", side_effect=Exception("Test Discord error")):
            response = categories_client.get("/api/v1/categories/1111111111")
            assert response.status_code in (500, 503)
