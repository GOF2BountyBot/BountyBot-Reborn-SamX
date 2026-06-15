"""
Extended tests for categories API router — covering uncovered error paths.
Target: push categories.py from 63% to 85%+.

Uncovered lines:
  104-106  (update_category — exception path)
  135-137  (delete_category — cascade child delete path)
  144      (delete_category — cascade message path)
  154-156  (delete_category — exception path)
  180-181  (list_category_channels — exception path)
  190-192  (list_category_channels — exception path)
  214-215  (get_category_permissions — empty overwrites)
  224-226  (get_category_permissions — exception path)
  241-285  (update_category_permissions — all paths)
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils

_mock_utils = DiscordMockUtils()

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = MagicMock

sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Restore real discord and reload categories router before each test."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import categories as _categories_mod

    importlib.reload(_categories_mod)
    yield


def create_mock_category(category_id=1111111111, name="Test Category"):
    """Create a mock Discord category."""
    category = MagicMock()
    category.id = category_id
    category.name = name
    category.position = 1
    category.nsfw = False
    category.channels = []
    category.overwrites = {}
    category.type = MagicMock()
    category.type.name = "category"
    category.delete = AsyncMock()
    category.edit = AsyncMock()
    category.set_permissions = AsyncMock()
    category.guild = MagicMock()
    category.guild.id = 987654321
    category.guild.get_role = MagicMock(return_value=None)
    category.guild.get_member = MagicMock(return_value=None)
    category.guild.fetch_member = AsyncMock()
    return category


@pytest.fixture
def mock_bot():
    """Create a mock bot."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    cat = create_mock_category()
    bot.get_channel = MagicMock(side_effect=lambda x: cat if x == 1111111111 else None)
    bot.fetch_channel = AsyncMock(side_effect=lambda x: cat if x == 1111111111 else None)
    return bot


@pytest.fixture
def patched_categories_app(mock_bot):
    """Create test app with patched categories router dependencies."""
    app = FastAPI(title="Test")
    app.state.bot = mock_bot

    with (
        patch("api.routers.categories.get_entity_or_404", new_callable=AsyncMock) as mock_get,
        patch("api.routers.categories.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.categories.validate_channel_type") as mock_validate,
        patch("api.routers.categories.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.categories.ChannelConverter") as mock_converter,
        patch("api.routers.categories.PermissionConverter") as mock_perm_converter,
        patch("api.routers.categories.create_permission_overwrite") as mock_create_overwrite,
    ):

        async def _resolve(request):
            return mock_bot

        mock_resolve.side_effect = _resolve

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            result = mock_bot.get_channel(entity_id)
            if result is None:
                raise HTTPException(status_code=404, detail=f"Channel {entity_id} not found")
            return result

        mock_get.side_effect = _get_entity

        async def _handle_exc(op, exc):
            raise HTTPException(status_code=500, detail=f"Failed to {op}: {exc}")

        mock_handle.side_effect = _handle_exc
        mock_validate.return_value = None

        from api.schemas.channel_schemas import Category, Channel

        _mock_cat_detail = Category(
            id=1111111111,
            guild_id=987654321,
            name="Test Category",
            position=1,
            created_at="2024-01-01T00:00:00",
        )
        mock_converter.category_to_detail.return_value = _mock_cat_detail

        _mock_channel = Channel(
            id=999,
            name="child-channel",
            type="text",
            position=1,
            guild_id=987654321,
            created_at="2024-01-01T00:00:00",
        )
        mock_converter.channel_to_summary.return_value = _mock_channel

        from api.schemas.permission_schemas import PermissionOverwrite as PermSchema

        _mock_perm_payload = PermSchema(
            id="1111111111:222222222",
            channel_id=1111111111,
            target_id=222222222,
            type="role",
            allow=8,
            deny=0,
        )
        mock_perm_converter.overwrite_to_payload.return_value = _mock_perm_payload

        overwrite_obj = MagicMock()
        mock_create_overwrite.return_value = overwrite_obj

        from api.routers.categories import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot, mock_get, mock_handle, mock_validate, mock_converter, mock_perm_converter


@pytest.fixture
def ext_client(patched_categories_app):
    """Test client for extended categories tests."""
    app, *_ = patched_categories_app
    return TestClient(app)


class TestUpdateCategoryErrors:
    """Test update_category error paths."""

    def test_update_category_exception_raises_500(self, patched_categories_app):
        """update_category should return 500 when unexpected exception occurs."""
        app, _mock_bot, mock_get, _mock_handle, *_ = patched_categories_app

        async def _fail(get_fn, fetch_fn, entity_id, entity_type):
            raise RuntimeError("Unexpected")

        mock_get.side_effect = _fail

        client = TestClient(app)
        response = client.put("/api/v1/categories/1111111111", json={"name": "New Name"})
        assert response.status_code == 500


class TestDeleteCategoryWithCascade:
    """Test delete_category with cascade=True path."""

    def test_delete_category_with_cascade_deletes_children(self, patched_categories_app):
        """DELETE /categories/{id}?cascade=true should delete child channels."""
        app, _mock_bot, *_ = patched_categories_app

        # Create category with child channels
        child1 = MagicMock()
        child1.position = 1
        child1.delete = AsyncMock()
        child2 = MagicMock()
        child2.position = 2
        child2.delete = AsyncMock()

        cat = create_mock_category()
        cat.channels = [child1, child2]
        _mock_bot.get_channel = MagicMock(side_effect=lambda x: cat if x == 1111111111 else None)

        client = TestClient(app)
        response = client.delete("/api/v1/categories/1111111111?cascade=true")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        # Message should mention child channels
        assert "child channel" in data["message"].lower()

    def test_delete_category_without_cascade(self, ext_client):
        """DELETE /categories/{id} without cascade should not delete children."""
        response = ext_client.delete("/api/v1/categories/1111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    def test_delete_category_exception_raises_500(self, patched_categories_app):
        """delete_category should return 500 when unexpected exception occurs."""
        app, _mock_bot, mock_get, _mock_handle, *_ = patched_categories_app

        async def _fail(get_fn, fetch_fn, entity_id, entity_type):
            raise RuntimeError("Unexpected error")

        mock_get.side_effect = _fail

        client = TestClient(app)
        response = client.delete("/api/v1/categories/1111111111")
        assert response.status_code == 500


class TestListCategoryChannelsExtended:
    """Extended tests for list_category_channels."""

    def test_list_category_channels_with_children(self, patched_categories_app):
        """list_category_channels should return channels sorted by position."""
        app, _mock_bot, mock_get, _mock_handle, _mock_validate, _mock_converter, *_ = patched_categories_app

        child1 = MagicMock()
        child1.position = 2
        child2 = MagicMock()
        child2.position = 1

        cat = create_mock_category()
        cat.channels = [child1, child2]

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity
        # channel_to_summary already returns a proper Channel schema object from fixture

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 200

    def test_list_category_channels_exception_raises_500(self, patched_categories_app):
        """list_category_channels should return 500 on unexpected exception."""
        app, _mock_bot, mock_get, _mock_handle, *_ = patched_categories_app

        async def _fail(get_fn, fetch_fn, entity_id, entity_type):
            raise RuntimeError("Unexpected error")

        mock_get.side_effect = _fail

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 500


class TestGetCategoryPermissionsExtended:
    """Extended tests for get_category_permissions."""

    def test_get_category_permissions_with_overwrites(self, patched_categories_app):
        """get_category_permissions should return overwrites."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        target = MagicMock()
        overwrite = MagicMock()
        cat = create_mock_category()
        cat.overwrites = {target: overwrite}

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

    def test_get_category_permissions_exception_raises_500(self, patched_categories_app):
        """get_category_permissions should return 500 on unexpected exception."""
        app, _mock_bot, mock_get, _mock_handle, *_ = patched_categories_app

        async def _fail(get_fn, fetch_fn, entity_id, entity_type):
            raise RuntimeError("Unexpected error")

        mock_get.side_effect = _fail

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 500


class TestUpdateCategoryPermissions:
    """Tests for update_category_permissions endpoint."""

    def test_update_category_permissions_with_role(self, patched_categories_app):
        """update_category_permissions should update role permissions."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        role = MagicMock()
        role.id = 333333333
        cat = create_mock_category()
        target = MagicMock()
        target.id = 333333333
        cat.overwrites = {target: MagicMock()}
        cat.guild.get_role = MagicMock(return_value=role)
        cat.set_permissions = AsyncMock()

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 333333333, "type": "role", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"

    def test_update_category_permissions_role_not_found_skipped(self, patched_categories_app):
        """update_category_permissions should skip missing roles."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        cat = create_mock_category()
        cat.overwrites = {}
        cat.guild.get_role = MagicMock(return_value=None)  # Role not found
        cat.set_permissions = AsyncMock()

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 999888777, "type": "role", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200  # Skips missing roles but succeeds

    def test_update_category_permissions_member_from_cache(self, patched_categories_app):
        """update_category_permissions should use cached member for member overwrites."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        member = MagicMock()
        member.id = 111222333
        cat = create_mock_category()
        cat.overwrites = {}
        cat.guild.get_member = MagicMock(return_value=member)  # Found in cache
        cat.set_permissions = AsyncMock()

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 111222333, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200

    def test_update_category_permissions_member_fetch_fallback(self, patched_categories_app):
        """update_category_permissions should fetch member if not in cache."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        member = MagicMock()
        member.id = 111222333
        cat = create_mock_category()
        cat.overwrites = {}
        cat.guild.get_member = MagicMock(return_value=None)  # Not in cache
        cat.guild.fetch_member = AsyncMock(return_value=member)
        cat.set_permissions = AsyncMock()

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 111222333, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200

    def test_update_category_permissions_member_not_found_skipped(self, patched_categories_app):
        """update_category_permissions should skip if member not found."""
        app, _mock_bot, mock_get, *_ = patched_categories_app

        cat = create_mock_category()
        cat.overwrites = {}
        cat.guild.get_member = MagicMock(return_value=None)
        cat.guild.fetch_member = AsyncMock(side_effect=Exception("Member not found"))
        cat.set_permissions = AsyncMock()

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            return cat

        mock_get.side_effect = _get_entity

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 999999999, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200  # Skips missing members but succeeds

    def test_update_category_permissions_exception_raises_500(self, patched_categories_app):
        """update_category_permissions should return 500 on unexpected exception."""
        app, _mock_bot, mock_get, _mock_handle, *_ = patched_categories_app

        async def _fail(get_fn, fetch_fn, entity_id, entity_type):
            raise RuntimeError("Unexpected error")

        mock_get.side_effect = _fail

        client = TestClient(app)
        payload = {"overwrites": []}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 500

    def test_update_category_permissions_not_found(self, ext_client):
        """update_category_permissions should return 404 for non-existent category."""
        payload = {"overwrites": []}
        response = ext_client.put("/api/v1/categories/9999999999/permissions", json=payload)
        assert response.status_code == 404
