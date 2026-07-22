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

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``get_entity_or_404``, ``handle_discord_exception``, ``validate_channel_type``,
``ChannelConverter``/``PermissionConverter`` or ``create_permission_overwrite``:
the mock bot is ``spec=commands.Bot`` (``is_ready()==True``) so the real
helpers run end-to-end, and mock categories/channels/roles/members carry
real-typed attributes so the real converters produce genuine serialized
bodies. "Unexpected exception" paths are exercised by making ``bot.get_channel``
itself raise (a real boundary), not by re-implementing the router's own
exception handling.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

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


def _make_guild(guild_id=987654321, roles_by_id=None, members_by_id=None, fetch_member_result=None):
    """Build a real-attribute mock guild with configurable role/member lookups."""
    guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id)
    roles_by_id = roles_by_id or {}
    members_by_id = members_by_id or {}
    guild.get_role = MagicMock(side_effect=lambda rid: roles_by_id.get(rid))
    guild.get_member = MagicMock(side_effect=lambda mid: members_by_id.get(mid))

    async def _fetch_member(mid):
        if fetch_member_result is not None:
            return fetch_member_result
        raise create_discord_not_found(f"Member {mid} not found")

    guild.fetch_member = AsyncMock(side_effect=_fetch_member)
    return guild


def create_mock_category(category_id=1111111111, name="Test Category", guild=None):
    """Create a mock Discord category with real-typed attributes."""
    guild = guild or _make_guild()
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=category_id, name=name, position=1, guild=guild, guild_id=guild.id
    )
    category.nsfw = False
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
    category.set_permissions = AsyncMock()
    return category


@pytest.fixture
def mock_bot():
    """Create a mock bot whose ``get_channel``/``fetch_channel`` back a single category.

    Tests reassign ``mock_bot.get_channel``/``.channel`` (a mutable holder) to
    swap in a differently-configured category or to simulate a lookup
    failure — see ``_set_category`` / ``_raise_on_lookup`` helpers below.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    holder = {"category": create_mock_category()}

    def get_channel(channel_id):
        cat = holder["category"]
        if channel_id == cat.id:
            return cat
        return None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot._category_holder = holder  # test-only escape hatch to swap the backing category
    return bot


def _set_category(mock_bot, category):
    """Swap the category returned by ``mock_bot.get_channel``/``fetch_channel``."""
    mock_bot._category_holder["category"] = category


def _raise_on_lookup(mock_bot, exc):
    """Make ``mock_bot.get_channel`` raise *exc* — exercises the router's real
    generic-exception -> ``handle_discord_exception`` -> 500 mapping without
    reimplementing it in the test."""

    def _raise(_channel_id):
        raise exc

    mock_bot.get_channel = _raise


@pytest.fixture
def categories_app(mock_bot):
    """Create test app with a real bot state (no router patches)."""
    app = FastAPI(title="Test")
    app.state.bot = mock_bot

    from api.routers.categories import router

    app.include_router(router, prefix="/api/v1")

    yield app, mock_bot


@pytest.fixture
def ext_client(categories_app):
    """Test client for extended categories tests."""
    app, _mock_bot = categories_app
    return TestClient(app)


class TestUpdateCategoryErrors:
    """Test update_category error paths."""

    def test_update_category_exception_raises_500(self, categories_app):
        """update_category should return 500 when bot.get_channel raises unexpectedly."""
        app, mock_bot = categories_app
        _raise_on_lookup(mock_bot, RuntimeError("Unexpected"))

        client = TestClient(app)
        response = client.put("/api/v1/categories/1111111111", json={"name": "New Name"})
        assert response.status_code == 500
        assert "Unexpected" in response.json()["detail"]


class TestDeleteCategoryWithCascade:
    """Test delete_category with cascade=True path."""

    def test_delete_category_with_cascade_deletes_children(self, categories_app):
        """DELETE /categories/{id}?cascade=true should delete real child channels."""
        app, mock_bot = categories_app

        child1 = DiscordMockUtils.create_mock_text_channel(channel_id=201, name="c1", position=1)
        child1.delete = AsyncMock()
        child2 = DiscordMockUtils.create_mock_text_channel(channel_id=202, name="c2", position=2)
        child2.delete = AsyncMock()

        cat = create_mock_category()
        cat.channels = [child1, child2]
        _set_category(mock_bot, cat)

        client = TestClient(app)
        response = client.delete("/api/v1/categories/1111111111?cascade=true")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert "child channel" in data["message"].lower()
        child1.delete.assert_awaited_once()
        child2.delete.assert_awaited_once()

    def test_delete_category_without_cascade(self, ext_client, mock_bot):
        """DELETE /categories/{id} without cascade should not delete children."""
        child = DiscordMockUtils.create_mock_text_channel(channel_id=201, name="c1")
        child.delete = AsyncMock()
        cat = mock_bot._category_holder["category"]
        cat.channels = [child]

        response = ext_client.delete("/api/v1/categories/1111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        child.delete.assert_not_called()

    def test_delete_category_exception_raises_500(self, categories_app):
        """delete_category should return 500 when bot.get_channel raises unexpectedly."""
        app, mock_bot = categories_app
        _raise_on_lookup(mock_bot, RuntimeError("Unexpected error"))

        client = TestClient(app)
        response = client.delete("/api/v1/categories/1111111111")
        assert response.status_code == 500


class TestListCategoryChannelsExtended:
    """Extended tests for list_category_channels."""

    def test_list_category_channels_with_children(self, categories_app):
        """list_category_channels should return real-serialized channels sorted by position."""
        app, mock_bot = categories_app

        child_high = DiscordMockUtils.create_mock_text_channel(channel_id=301, name="second", position=2)
        child_low = DiscordMockUtils.create_mock_text_channel(channel_id=302, name="first", position=1)

        cat = create_mock_category()
        cat.channels = [child_high, child_low]
        _set_category(mock_bot, cat)

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 200
        data = response.json()["data"]
        # Sorted by position: child_low (1) before child_high (2) — real router sort, real converter output.
        assert [c["id"] for c in data] == [302, 301]
        assert [c["name"] for c in data] == ["first", "second"]

    def test_list_category_channels_exception_raises_500(self, categories_app):
        """list_category_channels should return 500 when bot.get_channel raises unexpectedly."""
        app, mock_bot = categories_app
        _raise_on_lookup(mock_bot, RuntimeError("Unexpected error"))

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/channels")
        assert response.status_code == 500


class TestGetCategoryPermissionsExtended:
    """Extended tests for get_category_permissions."""

    def test_get_category_permissions_with_overwrites(self, categories_app):
        """get_category_permissions should return a real-serialized overwrite."""
        app, mock_bot = categories_app

        role = DiscordMockUtils.create_mock_role(role_id=222222222, name="mods")
        role.__class__ = discord.Role
        overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=8, deny=0)
        cat = create_mock_category()
        cat.overwrites = {role: overwrite}
        _set_category(mock_bot, cat)

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 200
        data = response.json()
        assert data["data"] == [
            {
                "id": "1111111111:222222222",
                "channel_id": 1111111111,
                "target_id": 222222222,
                "type": "role",
                "allow": 8,
                "deny": 0,
            }
        ]

    def test_get_category_permissions_empty(self, categories_app):
        """get_category_permissions should return an empty list when there are no overwrites."""
        app, _mock_bot = categories_app
        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_get_category_permissions_exception_raises_500(self, categories_app):
        """get_category_permissions should return 500 when bot.get_channel raises unexpectedly."""
        app, mock_bot = categories_app
        _raise_on_lookup(mock_bot, RuntimeError("Unexpected error"))

        client = TestClient(app)
        response = client.get("/api/v1/categories/1111111111/permissions")
        assert response.status_code == 500


class TestUpdateCategoryPermissions:
    """Tests for update_category_permissions endpoint."""

    def test_update_category_permissions_with_role(self, categories_app):
        """update_category_permissions should apply real permission math to a resolved role."""
        app, mock_bot = categories_app

        role = DiscordMockUtils.create_mock_role(role_id=333333333, name="mods")
        role.__class__ = discord.Role
        guild = _make_guild(roles_by_id={333333333: role})
        cat = create_mock_category(guild=guild)
        _set_category(mock_bot, cat)

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 333333333, "type": "role", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        # Real create_permission_overwrite() + real channel.set_permissions() call.
        cat.set_permissions.assert_awaited_once()
        called_target, kwargs = cat.set_permissions.call_args.args[0], cat.set_permissions.call_args.kwargs
        assert called_target is role
        assert isinstance(kwargs["overwrite"], discord.PermissionOverwrite)
        allow, _deny = kwargs["overwrite"].pair()
        # allow=8 == PERMISSION_FLAGS["ADMINISTRATOR"]["value"] (0x8)
        assert allow.administrator is True

    def test_update_category_permissions_role_not_found_skipped(self, categories_app):
        """update_category_permissions should skip missing roles."""
        app, mock_bot = categories_app

        guild = _make_guild(roles_by_id={})  # no roles resolve
        cat = create_mock_category(guild=guild)
        _set_category(mock_bot, cat)

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 999888777, "type": "role", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200  # Skips missing roles but succeeds
        cat.set_permissions.assert_not_called()

    def test_update_category_permissions_member_from_cache(self, categories_app):
        """update_category_permissions should use the cached member for member overwrites."""
        app, mock_bot = categories_app

        member = DiscordMockUtils.create_mock_member(user_id=111222333)
        member.__class__ = discord.Member
        guild = _make_guild(members_by_id={111222333: member})
        cat = create_mock_category(guild=guild)
        _set_category(mock_bot, cat)

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 111222333, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200
        cat.set_permissions.assert_awaited_once()
        assert cat.set_permissions.call_args.args[0] is member
        guild.fetch_member.assert_not_awaited()

    def test_update_category_permissions_member_fetch_fallback(self, categories_app):
        """update_category_permissions should fetch the member from the API when not cached."""
        app, mock_bot = categories_app

        member = DiscordMockUtils.create_mock_member(user_id=111222333)
        member.__class__ = discord.Member
        guild = _make_guild(members_by_id={}, fetch_member_result=member)
        cat = create_mock_category(guild=guild)
        _set_category(mock_bot, cat)

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 111222333, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200
        guild.fetch_member.assert_awaited_once_with(111222333)
        cat.set_permissions.assert_awaited_once()

    def test_update_category_permissions_member_not_found_skipped(self, categories_app):
        """update_category_permissions should skip if the member cannot be resolved or fetched."""
        app, mock_bot = categories_app

        guild = _make_guild(members_by_id={})  # get_member -> None, fetch_member -> real NotFound
        cat = create_mock_category(guild=guild)
        _set_category(mock_bot, cat)

        client = TestClient(app)
        payload = {"overwrites": [{"target_id": 999999999, "type": "member", "allow": 8, "deny": 0}]}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 200  # Skips missing members but succeeds
        cat.set_permissions.assert_not_called()

    def test_update_category_permissions_exception_raises_500(self, categories_app):
        """update_category_permissions should return 500 when bot.get_channel raises unexpectedly."""
        app, mock_bot = categories_app
        _raise_on_lookup(mock_bot, RuntimeError("Unexpected error"))

        client = TestClient(app)
        payload = {"overwrites": []}
        response = client.put("/api/v1/categories/1111111111/permissions", json=payload)
        assert response.status_code == 500

    def test_update_category_permissions_not_found(self, ext_client):
        """update_category_permissions should return 404 for non-existent category."""
        payload = {"overwrites": []}
        response = ext_client.put("/api/v1/categories/9999999999/permissions", json=payload)
        assert response.status_code == 404
