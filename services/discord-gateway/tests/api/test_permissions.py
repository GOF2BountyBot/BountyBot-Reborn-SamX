"""
Tests for the permissions API endpoints.

This module provides comprehensive test coverage for the permissions router,
including permission flag listing, overwrite management, and comprehensive checks.

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception`` or ``PermissionConverter``: the mock bot is
``spec=commands.Bot`` (``is_ready()==True``) so the real helpers run
end-to-end, and mock channels/guilds/roles/members carry real-typed
attributes (real ``discord.PermissionOverwrite``, ``__class__`` set to the
real ``discord.Role``/``discord.Member``) so the real ``PermissionConverter``
produces genuine serialized overwrite bodies and the real
``create_permission_overwrite``/permission-math endpoints run unmocked.
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


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot backed by a real-attribute guild/channel/role/member graph.

    ``fetch_channel``/``fetch_guild`` raise real ``discord.NotFound`` on cache
    miss (rather than silently returning ``None``) so the routers' own
    ``bot.get_channel(...) or fetch...`` fallbacks behave exactly like
    production.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    guild = DiscordMockUtils.create_mock_guild(guild_id=987654321)

    role = DiscordMockUtils.create_mock_role(role_id=555, guild=guild, name="mods", permissions=8)
    role.__class__ = discord.Role

    member = DiscordMockUtils.create_mock_member(user_id=111111111, guild=guild, username="test-member")
    member.__class__ = discord.Member
    member.roles = [role]

    channel = DiscordMockUtils.create_mock_text_channel(channel_id=1234567890, guild=guild, guild_id=guild.id)
    overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=8, deny=0)
    channel.overwrites = {role: overwrite}
    channel.set_permissions = AsyncMock()
    channel.permissions_for = MagicMock(return_value=discord.Permissions(8))

    def get_channel(channel_id):
        return channel if channel_id == channel.id else None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    guild.get_role = MagicMock(side_effect=lambda rid: role if rid == role.id else None)
    guild.get_member = MagicMock(side_effect=lambda mid: member if mid == member.id else None)

    async def fetch_member(mid):
        if mid == member.id:
            return member
        raise create_discord_not_found(f"Member {mid} not found")

    guild.fetch_member = AsyncMock(side_effect=fetch_member)

    def get_guild(guild_id):
        return guild if guild_id == guild.id else None

    async def fetch_guild(guild_id):
        found = get_guild(guild_id)
        if found is None:
            raise create_discord_not_found(f"Guild {guild_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot.guilds = [guild]
    bot.get_guild = MagicMock(side_effect=get_guild)
    bot.fetch_guild = AsyncMock(side_effect=fetch_guild)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=create_discord_not_found("User not found"))

    bot._graph = types.SimpleNamespace(guild=guild, role=role, member=member, channel=channel, overwrite=overwrite)

    return bot


@pytest.fixture
def permissions_test_app(mock_bot):
    """Create a test FastAPI app with the permissions router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.permissions import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def permissions_client(permissions_test_app):
    """Create a test client for the permissions API."""
    return TestClient(permissions_test_app)


class TestListAllPermissions:
    """Tests for GET /permissions endpoint."""

    def test_list_all_permissions_returns_200(self, permissions_client):
        """GET /permissions should return 200 with all permission flags."""
        response = permissions_client.get("/api/v1/permissions")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0


class TestListRolePermissions:
    """Tests for GET /permissions/roles endpoint."""

    def test_list_role_permissions_returns_200(self, permissions_client):
        """GET /permissions/roles should return 200 with role permissions."""
        response = permissions_client.get("/api/v1/permissions/roles")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0


class TestListUserPermissions:
    """Tests for GET /permissions/users endpoint."""

    def test_list_user_permissions_returns_200(self, permissions_client):
        """GET /permissions/users should return 200 with user permissions."""
        response = permissions_client.get("/api/v1/permissions/users")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0


class TestListChannelPermissions:
    """Tests for GET /permissions/channels endpoint."""

    def test_list_channel_permissions_returns_200(self, permissions_client):
        """GET /permissions/channels should return 200 with channel permissions."""
        response = permissions_client.get("/api/v1/permissions/channels")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0


class TestListCategoryPermissions:
    """Tests for GET /permissions/categories endpoint."""

    def test_list_category_permissions_returns_200(self, permissions_client):
        """GET /permissions/categories should return 200 with category permissions."""
        response = permissions_client.get("/api/v1/permissions/categories")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0


class TestGetPermissionOverwrite:
    """Tests for GET /permissions/{permission_id} endpoint."""

    def test_get_permission_overwrite_returns_200(self, permissions_client):
        """GET /permissions/{permission_id} should return 200 with the real-serialized overwrite."""
        response = permissions_client.get("/api/v1/permissions/1234567890:555")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == {
            "id": "1234567890:555",
            "channel_id": 1234567890,
            "target_id": 555,
            "type": "role",
            "allow": 8,
            "deny": 0,
        }

    def test_get_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """GET /permissions/{permission_id} should return 400 for invalid ID format."""
        response = permissions_client.get("/api/v1/permissions/invalid")
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_get_permission_overwrite_not_found_channel_returns_404(self, permissions_client):
        """GET /permissions/{permission_id} should return 404 when the channel doesn't exist."""
        response = permissions_client.get("/api/v1/permissions/9999999999:555")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_permission_overwrite_not_found_target_returns_404(self, permissions_client):
        """GET /permissions/{permission_id} should return 404 when no overwrite exists for the target."""
        response = permissions_client.get("/api/v1/permissions/1234567890:999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdatePermissionOverwrite:
    """Tests for PUT /permissions/{permission_id} endpoint."""

    def test_update_permission_overwrite_returns_200(self, permissions_client, mock_bot):
        """PUT /permissions/{permission_id} should apply real allow/deny bits and re-serialize."""
        overwrite_data = {"allow": 8, "deny": 4}
        response = permissions_client.put("/api/v1/permissions/1234567890:555", json=overwrite_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert data["data"] == {
            "id": "1234567890:555",
            "channel_id": 1234567890,
            "target_id": 555,
            "type": "role",
            "allow": 8,
            "deny": 4,
        }
        # Real create_permission_overwrite() + real channel.set_permissions() call.
        channel = mock_bot._graph.channel
        channel.set_permissions.assert_awaited_once()
        target, kwargs = channel.set_permissions.call_args.args[0], channel.set_permissions.call_args.kwargs
        assert target is mock_bot._graph.role
        assert isinstance(kwargs["overwrite"], discord.PermissionOverwrite)

    def test_update_permission_overwrite_member_target_returns_200(self, permissions_client, mock_bot):
        """PUT /permissions/{permission_id} should resolve a member target when no role matches."""
        overwrite_data = {"allow": 1024, "deny": 0}
        response = permissions_client.put("/api/v1/permissions/1234567890:111111111", json=overwrite_data)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["type"] == "member"
        assert data["data"]["target_id"] == 111111111

    def test_update_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """PUT /permissions/{permission_id} should return 400 for invalid ID format."""
        overwrite_data = {"allow": 8, "deny": 4}
        response = permissions_client.put("/api/v1/permissions/invalid", json=overwrite_data)
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_update_permission_overwrite_channel_not_found_returns_404(self, permissions_client):
        """PUT /permissions/{permission_id} should return 404 for a non-existent channel."""
        overwrite_data = {"allow": 8, "deny": 4}
        response = permissions_client.put("/api/v1/permissions/9999999999:555", json=overwrite_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_permission_overwrite_target_not_found_returns_404(self, permissions_client):
        """PUT /permissions/{permission_id} should return 404 when neither a role nor member resolves."""
        overwrite_data = {"allow": 8, "deny": 4}
        response = permissions_client.put("/api/v1/permissions/1234567890:999999999", json=overwrite_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestRemovePermissionOverwrite:
    """Tests for DELETE /permissions/{permission_id} endpoint."""

    def test_remove_permission_overwrite_returns_200(self, permissions_client, mock_bot):
        """DELETE /permissions/{permission_id} should clear the real overwrite and confirm removal."""
        response = permissions_client.delete("/api/v1/permissions/1234567890:555")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert "permission overwrite removed" in data["message"].lower()
        channel = mock_bot._graph.channel
        channel.set_permissions.assert_awaited_once_with(mock_bot._graph.role, overwrite=None)

    def test_remove_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 400 for invalid ID format."""
        response = permissions_client.delete("/api/v1/permissions/invalid")
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_remove_permission_overwrite_channel_not_found_returns_404(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 404 for a non-existent channel."""
        response = permissions_client.delete("/api/v1/permissions/9999999999:555")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_remove_permission_overwrite_target_not_found_returns_404(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 404 when no overwrite exists for the target."""
        response = permissions_client.delete("/api/v1/permissions/1234567890:999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestConvertNamesToValue:
    """Tests for POST /permissions/convert/names-to-value endpoint."""

    def test_convert_names_to_value_returns_200(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 200 with bitfield value."""
        body = {"names": ["MANAGE_GUILD", "KICK_MEMBERS"]}
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "value" in data["data"]
        assert isinstance(data["data"]["value"], int)

    def test_convert_names_to_value_empty_list_returns_422(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 422 for empty list."""
        body = {"names": []}
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 422
        assert "names list must contain at least one permission" in response.json()["detail"].lower()

    def test_convert_names_to_value_invalid_permission_returns_400(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 400 for invalid permission."""
        body = {"names": ["INVALID_PERMISSION"]}
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 400
        assert "unknown permission" in response.json()["detail"].lower()


class TestConvertValueToNames:
    """Tests for POST /permissions/convert/value-to-names endpoint."""

    def test_convert_value_to_names_returns_200(self, permissions_client):
        """POST /permissions/convert/value-to-names should return 200 with permission names."""
        body = {"value": 8}
        response = permissions_client.post("/api/v1/permissions/convert/value-to-names", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "names" in data["data"]
        assert isinstance(data["data"]["names"], list)


class TestCalculatePermissions:
    """Tests for POST /permissions/calculate endpoint."""

    def test_calculate_permissions_returns_200(self, permissions_client):
        """POST /permissions/calculate should return 200 with effective permissions."""
        body = {"base": 8, "allow": 4, "deny": 2}
        response = permissions_client.post("/api/v1/permissions/calculate", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "effective" in data["data"]
        assert isinstance(data["data"]["effective"], int)


class TestCheckComprehensivePermissions:
    """Tests for POST /permissions/check endpoint."""

    def test_check_comprehensive_permissions_returns_200(self, permissions_client):
        """POST /permissions/check should return 200 with permission check results."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": ["MANAGE_GUILD", "KICK_MEMBERS"],
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "allowed" in data["data"]
        assert "denied" in data["data"]
        assert "granted" in data["data"]

    def test_check_comprehensive_permissions_empty_permissions_returns_200(self, permissions_client):
        """POST /permissions/check with empty permissions should return evaluate-style summary."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": [],
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "base" in data["data"]
        assert "allowed_names" in data["data"]
        assert "denied_names" in data["data"]

    def test_check_comprehensive_permissions_invalid_permission_returns_422(self, permissions_client):
        """POST /permissions/check should return 422 for invalid permission."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": ["INVALID_PERMISSION"],
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 422
        assert "unknown permission" in response.json()["detail"].lower()

    def test_check_comprehensive_permissions_invalid_subject_type_returns_400(self, permissions_client):
        """POST /permissions/check should return 400 for invalid subject type."""
        body = {
            "subject": {"type": "invalid", "id": 111111111},
            "target": {"type": "guild", "id": 987654321},
            "permissions": ["MANAGE_GUILD"],
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 400
        assert "unknown subject type" in response.json()["detail"].lower()

    def test_check_comprehensive_permissions_guild_not_found_returns_404(self, permissions_client):
        """POST /permissions/check should return 404 when the target guild can't be resolved."""
        body = {
            "subject": {"type": "user", "id": 111111111},
            "target": {"type": "guild", "id": 555555555},
            "permissions": [],
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in permissions endpoints."""

    def test_handle_discord_exception(self, permissions_client):
        """A non-Discord exception raised while resolving the bot maps, via the real handler, to 500."""
        with patch("api.routers.permissions.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = permissions_client.get("/api/v1/permissions/1234567890:555")
            assert response.status_code == 500
            assert "Test Discord error" in response.json()["detail"]
