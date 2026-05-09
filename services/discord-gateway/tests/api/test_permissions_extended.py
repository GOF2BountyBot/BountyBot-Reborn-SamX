"""
Extended tests for permissions API router — covering uncovered error paths.
Target: push permissions.py from 68% to 85%+.

Uncovered lines:
  88-90   (list_all_permissions — exception path)
  119-121 (list_role_permissions — exception path)
  150-152 (list_user_permissions — exception path)
  181-183 (list_channel_permissions — exception path)
  212-214 (list_category_permissions — exception path)
  266     (get_permission_overwrite — fetch channel fallback)
  327-332 (update_permission_overwrite — target not found, fetch member)
  354-356 (update_permission_overwrite — exception path)
  401     (remove_permission_overwrite — fetch channel fallback)
  419-421 (remove_permission_overwrite — exception path)
  552-553 (check_comprehensive_permissions — channel-like target, role subject)
  557-598 (check_comprehensive_permissions — channel effective value computation)
  618-628 (check_comprehensive_permissions — channel vs guild evaluation branches)
  660-662 (check_comprehensive_permissions — exception path)
  676-679 (resolve target — guild not found, fetch fallback)
  685-705 (resolve target — channel target, channel not found)
  719-722 (resolve subject — member not in guild)
  729-735 (resolve subject — role not found)
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_utils = DiscordMockUtils()

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Restore real discord and reload permissions router before each test."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import permissions as _perm_mod

    importlib.reload(_perm_mod)
    yield


def _make_perm_payload():
    from api.schemas.permission_schemas import PermissionOverwrite as PermSchema

    return PermSchema(
        id="1234567890:111111111",
        channel_id=1234567890,
        target_id=111111111,
        type="role",
        allow=8,
        deny=0,
    )


def create_mock_channel(channel_id=1234567890):
    """Create mock channel with overwrites."""
    channel = MagicMock()
    channel.id = channel_id
    channel.name = "test-channel"
    channel.overwrites = {}
    channel.set_permissions = AsyncMock()
    channel.permissions_for = MagicMock(return_value=MagicMock(value=8))
    channel.guild = MagicMock()
    channel.guild.id = 987654321
    channel.guild.get_role = MagicMock(return_value=None)
    channel.guild.get_member = MagicMock(return_value=None)
    channel.guild.fetch_member = AsyncMock()
    return channel


@pytest.fixture
def mock_bot():
    """Mock bot for permissions tests."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    _mock_target = MagicMock()
    _mock_target.id = 111111111
    _mock_target.name = "test-role"
    _mock_target.permissions = MagicMock(value=0)

    _mock_overwrite = MagicMock()
    _mock_overwrite.pair.return_value = (MagicMock(value=8), MagicMock(value=4))

    def get_channel(channel_id):
        if channel_id not in (1234567890, 111111111):
            return None
        channel = create_mock_channel(channel_id)
        channel.overwrites = {_mock_target: _mock_overwrite}
        channel.set_permissions = AsyncMock()
        return channel

    _mock_guild = MagicMock()
    _mock_guild.id = 987654321
    _mock_role = MagicMock()
    _mock_role.id = 123456789
    _mock_role.name = "test-role"
    _mock_role.permissions = MagicMock(value=8)
    _mock_member = MagicMock()
    _mock_member.id = 111111111
    _mock_member.display_name = "test-member"
    _mock_member.guild = _mock_guild
    _mock_member.guild_permissions = MagicMock(value=8)
    _mock_member.roles = [_mock_role]
    _mock_guild.get_role = MagicMock(side_effect=lambda x: _mock_role if x == 123456789 else None)
    _mock_guild.get_member = MagicMock(side_effect=lambda x: _mock_member if x == 111111111 else None)
    _mock_guild.fetch_member = AsyncMock(return_value=_mock_member)
    _mock_guild.me = _mock_member
    _mock_guild.roles = [_mock_role]
    _mock_guild.members = [_mock_member]
    _mock_member.guild = _mock_guild

    async def fetch_channel_impl(channel_id):
        ch = get_channel(channel_id)
        if ch is None:
            raise Exception(f"Channel {channel_id} not found")
        return ch

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel_impl)
    bot.guilds = [_mock_guild]
    bot.get_guild = MagicMock(return_value=_mock_guild)
    bot.fetch_guild = AsyncMock(return_value=_mock_guild)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=Exception("User not found"))

    return bot


@pytest.fixture
def perm_ext_app(mock_bot):
    """Create test app with patched permissions router dependencies."""
    app = FastAPI(title="Test")
    app.state.bot = mock_bot

    with (
        patch("api.routers.permissions.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.permissions.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.permissions.PermissionConverter") as mock_converter,
    ):

        async def _resolve(request):
            return mock_bot

        mock_resolve.side_effect = _resolve

        async def _handle(op, exc):
            raise HTTPException(status_code=500, detail=f"Failed to {op}: {exc}")

        mock_handle.side_effect = _handle

        mock_converter.overwrite_to_payload.return_value = _make_perm_payload()

        from api.routers.permissions import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot, mock_resolve, mock_handle, mock_converter


@pytest.fixture
def ext_perm_client(perm_ext_app):
    """Test client for extended permissions tests."""
    app, *_ = perm_ext_app
    return TestClient(app)


class TestListPermissionsErrors:
    """Test error paths for list permissions endpoints."""

    def test_list_all_permissions_exception(self, perm_ext_app):
        """list_all_permissions should return 500 on exception."""
        app, _mock_bot, _mock_resolve, _mock_handle, *_ = perm_ext_app

        with patch("api.routers.permissions.get_all_permissions", side_effect=RuntimeError("fail")):
            client = TestClient(app)
            response = client.get("/api/v1/permissions")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_list_role_permissions_exception(self, perm_ext_app):
        """list_role_permissions should return 500 on exception."""
        app, *_ = perm_ext_app

        with patch("api.routers.permissions.get_role_permissions", side_effect=RuntimeError("fail")):
            client = TestClient(app)
            response = client.get("/api/v1/permissions/roles")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_list_user_permissions_exception(self, perm_ext_app):
        """list_user_permissions should return 500 on exception."""
        app, *_ = perm_ext_app

        with patch("api.routers.permissions.get_user_permissions", side_effect=RuntimeError("fail")):
            client = TestClient(app)
            response = client.get("/api/v1/permissions/users")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_list_channel_permissions_exception(self, perm_ext_app):
        """list_channel_permissions should return 500 on exception."""
        app, *_ = perm_ext_app

        with patch("api.routers.permissions.get_channel_permissions", side_effect=RuntimeError("fail")):
            client = TestClient(app)
            response = client.get("/api/v1/permissions/channels")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_list_category_permissions_exception(self, perm_ext_app):
        """list_category_permissions should return 500 on exception."""
        app, *_ = perm_ext_app

        with patch("api.routers.permissions.get_category_permissions", side_effect=RuntimeError("fail")):
            client = TestClient(app)
            response = client.get("/api/v1/permissions/categories")
            assert response.status_code == 500
            assert "detail" in response.json()


class TestGetPermissionOverwriteExtended:
    """Extended tests for GET /permissions/{permission_id}."""

    def test_get_overwrite_channel_from_fetch(self, perm_ext_app):
        """get_permission_overwrite should fetch channel when not in cache."""
        app, mock_bot, _mock_resolve, *_ = perm_ext_app

        _mock_target = MagicMock()
        _mock_target.id = 111111111
        _mock_overwrite = MagicMock()
        _mock_overwrite.pair.return_value = (MagicMock(value=8), MagicMock(value=0))

        channel = create_mock_channel(5555555555)
        channel.overwrites = {_mock_target: _mock_overwrite}

        # get_channel returns None (not cached), fetch_channel returns it
        mock_bot.get_channel = MagicMock(return_value=None)
        mock_bot.fetch_channel = AsyncMock(return_value=channel)

        client = TestClient(app)
        response = client.get("/api/v1/permissions/5555555555:111111111")
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_get_overwrite_channel_fetch_fails_404(self, perm_ext_app):
        """get_permission_overwrite should return 404 when channel fetch fails."""
        app, mock_bot, *_ = perm_ext_app

        mock_bot.get_channel = MagicMock(return_value=None)
        mock_bot.fetch_channel = AsyncMock(side_effect=Exception("Channel not found"))

        client = TestClient(app)
        response = client.get("/api/v1/permissions/9999999999:111111111")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_get_overwrite_exception_path(self, perm_ext_app):
        """get_permission_overwrite should return 500 on unexpected exception."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = perm_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.get("/api/v1/permissions/1234567890:111111111")
        assert response.status_code == 500
        assert "detail" in response.json()


class TestUpdatePermissionOverwriteExtended:
    """Extended tests for PUT /permissions/{permission_id}."""

    def test_update_overwrite_target_from_fetch_member(self, perm_ext_app):
        """update_permission_overwrite should fetch member when role and member not cached."""
        app, _mock_bot, _mock_resolve, _mock_handle, *_ = perm_ext_app

        fetched_member = MagicMock()
        fetched_member.id = 222222222

        channel = create_mock_channel(1234567890)
        channel.guild.get_role = MagicMock(return_value=None)
        channel.guild.get_member = MagicMock(return_value=None)
        channel.guild.fetch_member = AsyncMock(return_value=fetched_member)

        mock_bot.get_channel = MagicMock(return_value=channel)

        client = TestClient(app)
        response = client.put("/api/v1/permissions/1234567890:222222222", json={"allow": 8, "deny": 0})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_update_overwrite_target_not_found_404(self, perm_ext_app):
        """update_permission_overwrite should return 404 when target not found."""
        app, mock_bot, _mock_resolve, _mock_handle, *_ = perm_ext_app

        channel = create_mock_channel(1234567890)
        channel.guild.get_role = MagicMock(return_value=None)
        channel.guild.get_member = MagicMock(return_value=None)
        channel.guild.fetch_member = AsyncMock(side_effect=Exception("Member not found"))

        mock_bot.get_channel = MagicMock(return_value=channel)

        client = TestClient(app)
        response = client.put("/api/v1/permissions/1234567890:888888888", json={"allow": 8, "deny": 0})
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_update_overwrite_exception_path(self, perm_ext_app):
        """update_permission_overwrite should return 500 on unexpected exception."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = perm_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.put("/api/v1/permissions/1234567890:111111111", json={"allow": 8, "deny": 0})
        assert response.status_code == 500
        assert "detail" in response.json()


class TestRemovePermissionOverwriteExtended:
    """Extended tests for DELETE /permissions/{permission_id}."""

    def test_remove_overwrite_channel_from_fetch(self, perm_ext_app):
        """remove_permission_overwrite should fetch channel when not in cache."""
        app, mock_bot, *_ = perm_ext_app

        _mock_target = MagicMock()
        _mock_target.id = 111111111
        _mock_target.name = "test-target"
        _mock_target.permissions = MagicMock()  # This makes it a "role"

        channel = create_mock_channel(5555555555)
        channel.overwrites = {_mock_target: MagicMock()}
        channel.set_permissions = AsyncMock()

        mock_bot.get_channel = MagicMock(return_value=None)  # Not in cache
        mock_bot.fetch_channel = AsyncMock(return_value=channel)

        client = TestClient(app)
        response = client.delete("/api/v1/permissions/5555555555:111111111")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    def test_remove_overwrite_exception_path(self, perm_ext_app):
        """remove_permission_overwrite should return 500 on unexpected exception."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = perm_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.delete("/api/v1/permissions/1234567890:111111111")
        assert response.status_code == 500
        assert "detail" in response.json()


class TestCheckComprehensivePermissionsExtended:
    """Extended tests for POST /permissions/check."""

    def test_check_permissions_role_subject_guild_target(self, ext_perm_client):
        """check_comprehensive_permissions with role subject and guild target."""
        body = {
            "subject": {"type": "role", "id": 123456789},
            "target": {"type": "guild", "id": 987654321},
            "permissions": ["MANAGE_GUILD"],
        }
        response = ext_perm_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_check_permissions_user_subject_channel_target(self, ext_perm_client):
        """check_comprehensive_permissions with user subject and channel target."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "channel", "id": 1234567890},
            "permissions": ["SEND_MESSAGES"],
        }
        response = ext_perm_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_check_permissions_role_subject_channel_target(self, perm_ext_app):
        """check_comprehensive_permissions with role subject and channel target."""
        app, mock_bot, _mock_resolve, _mock_handle, _mock_converter = perm_ext_app

        # Build a guild with the role
        _mock_guild = MagicMock()
        _mock_guild.id = 987654321
        _mock_role = MagicMock()
        _mock_role.id = 123456789
        _mock_role.name = "test-role"
        _mock_role.permissions = MagicMock(value=8)
        _mock_guild.get_role = MagicMock(side_effect=lambda x: _mock_role if x == 123456789 else None)

        _mock_overwrite = MagicMock()
        _mock_overwrite.pair.return_value = (MagicMock(value=8), MagicMock(value=0))

        channel = MagicMock()
        channel.id = 1234567890
        channel.guild = _mock_guild
        channel.overwrites = {_mock_role: _mock_overwrite}
        channel.permissions_for = MagicMock(return_value=MagicMock(value=8))

        mock_bot.get_channel = MagicMock(return_value=channel)

        client = TestClient(app)
        body = {
            "subject": {"type": "role", "id": 123456789},
            "target": {"type": "channel", "id": 1234567890},
            "permissions": ["SEND_MESSAGES"],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_check_permissions_evaluate_mode_channel_user(self, ext_perm_client):
        """check_comprehensive_permissions evaluate mode for channel + user subject."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "channel", "id": 1234567890},
            "permissions": [],
        }
        response = ext_perm_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        data = response.json()
        assert "base" in data["data"]

    def test_check_permissions_evaluate_mode_channel_role(self, perm_ext_app):
        """check_comprehensive_permissions evaluate mode for channel + role subject."""
        app, mock_bot, _mock_resolve, _mock_handle, _mock_converter = perm_ext_app

        # Build a guild with the role so _resolve_subject_entity finds it
        _mock_guild = MagicMock()
        _mock_guild.id = 987654321
        _mock_role = MagicMock()
        _mock_role.id = 123456789
        _mock_role.name = "test-role"
        _mock_role.permissions = MagicMock(value=8)
        _mock_guild.get_role = MagicMock(side_effect=lambda x: _mock_role if x == 123456789 else None)

        _mock_overwrite = MagicMock()
        _mock_overwrite.pair.return_value = (MagicMock(value=8), MagicMock(value=0))

        channel = MagicMock()
        channel.id = 1234567890
        channel.guild = _mock_guild
        channel.overwrites = {_mock_role: _mock_overwrite}
        channel.permissions_for = MagicMock(return_value=MagicMock(value=8))

        mock_bot.get_channel = MagicMock(return_value=channel)

        client = TestClient(app)
        body = {
            "subject": {"type": "role", "id": 123456789},
            "target": {"type": "channel", "id": 1234567890},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        data = response.json()
        assert "base" in data["data"]

    def test_check_permissions_guild_not_found_404(self, perm_ext_app):
        """check_comprehensive_permissions should return 404 when guild not found."""
        app, mock_bot, _mock_resolve, *_ = perm_ext_app

        mock_bot.get_guild = MagicMock(return_value=None)
        mock_bot.fetch_guild = AsyncMock(side_effect=Exception("Guild not found"))

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 999999999},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_check_permissions_guild_from_fetch(self, perm_ext_app):
        """check_comprehensive_permissions should fetch guild when not cached."""
        app, mock_bot, _mock_resolve, *_ = perm_ext_app

        _mock_guild = MagicMock()
        _mock_guild.id = 987654321
        _mock_member = MagicMock()
        _mock_member.id = 111111111
        _mock_member.guild_permissions = MagicMock(value=8)
        _mock_guild.get_member = MagicMock(return_value=_mock_member)
        _mock_guild.fetch_member = AsyncMock(return_value=_mock_member)

        mock_bot.get_guild = MagicMock(return_value=None)  # Not cached
        mock_bot.fetch_guild = AsyncMock(return_value=_mock_guild)

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_check_permissions_channel_not_found_404(self, perm_ext_app):
        """check_comprehensive_permissions should return 404 when channel not found."""
        app, mock_bot, *_ = perm_ext_app

        mock_bot.get_channel = MagicMock(return_value=None)
        mock_bot.fetch_channel = AsyncMock(side_effect=Exception("Channel not found"))

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "channel", "id": 8888888888},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_check_permissions_channel_no_guild_400(self, perm_ext_app):
        """check_comprehensive_permissions should return 400 when channel has no guild."""
        app, mock_bot, *_ = perm_ext_app

        channel = MagicMock()
        channel.id = 7777777777
        channel.guild = None  # No guild

        mock_bot.get_channel = MagicMock(return_value=channel)

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "channel", "id": 7777777777},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_check_permissions_unknown_target_type_400(self, ext_perm_client):
        """check_comprehensive_permissions should return 400 for unknown target type."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "unknown_type", "id": 987654321},
            "permissions": [],
        }
        response = ext_perm_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_check_permissions_member_not_in_guild_404(self, perm_ext_app):
        """check_comprehensive_permissions should return 404 when member not in guild."""
        app, mock_bot, *_ = perm_ext_app

        _mock_guild = MagicMock()
        _mock_guild.id = 987654321
        _mock_guild.get_member = MagicMock(return_value=None)  # Not found
        _mock_guild.fetch_member = AsyncMock(side_effect=Exception("Member not found"))

        mock_bot.get_guild = MagicMock(return_value=_mock_guild)

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 999999999},
            "target": {"type": "guild", "id": 987654321},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_check_permissions_role_not_in_guild_404(self, perm_ext_app):
        """check_comprehensive_permissions should return 404 when role not in guild."""
        app, mock_bot, *_ = perm_ext_app

        _mock_guild = MagicMock()
        _mock_guild.id = 987654321
        _mock_guild.get_role = MagicMock(return_value=None)  # Role not found

        mock_bot.get_guild = MagicMock(return_value=_mock_guild)

        client = TestClient(app)
        body = {
            "subject": {"type": "role", "id": 999999999},
            "target": {"type": "guild", "id": 987654321},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_check_permissions_exception_path(self, perm_ext_app):
        """check_comprehensive_permissions should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = perm_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": [],
        }
        response = client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 500
        assert "detail" in response.json()
