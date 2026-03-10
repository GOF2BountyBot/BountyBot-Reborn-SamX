"""
Tests for the roles API endpoints.

This module provides comprehensive test coverage for the roles router,
including role management, member assignment, and permission checking.
"""

import pytest
import importlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, HTTPException
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

# Mock discord.ext with real commands.Bot for proper isinstance checks
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
    and reload api.routers.roles so its ``discord`` reference is fresh.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force the roles router to re-bind its 'discord' global to real discord
    import api.routers.roles as _roles_mod
    importlib.reload(_roles_mod)
    yield


def create_mock_role(role_id=123456789, guild_id=987654321):
    """Create a mock Discord role using DiscordMockUtils."""
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id,
        guild_id=guild_id,
        name="test-role",
        color_value=0,
        hoist=False,
        position=1,
        permissions=0,
        managed=False,
        mentionable=False,
    )
    role.created_at = datetime.now()
    role.tags = None
    role.members = []
    role.edit = AsyncMock()
    role.delete = AsyncMock()
    return role


def create_mock_member(member_id=111111111, guild_id=987654321):
    """Create a mock Discord member using DiscordMockUtils."""
    member = DiscordMockUtils.create_mock_member(
        user_id=member_id,
        guild_id=guild_id,
        username="test-member",
    )
    member.display_name = "test-member"
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_guild(guild_id):
        if guild_id == 987654321:
            guild = MagicMock()
            guild.id = guild_id
            guild.get_role = MagicMock(side_effect=lambda x: create_mock_role(x, guild_id) if x == 123456789 else None)
            guild.get_member = MagicMock(side_effect=lambda x: create_mock_member(x, guild_id) if x == 111111111 else None)
            guild.fetch_member = AsyncMock(side_effect=lambda x: create_mock_member(x, guild_id))
            guild.members = []
            return guild
        return None

    _guild_instance = get_guild(987654321)

    bot.get_guild = get_guild
    bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)
    bot.guilds = [_guild_instance]  # roles router iterates bot.guilds

    return bot


@pytest.fixture
def roles_test_app(mock_bot):
    """Create a test FastAPI app with the roles router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with patch("api.routers.roles.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.roles.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.roles.RoleConverter") as mock_role_converter:

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.return_value = None

        from api.schemas.role_schemas import Role as RoleSchema
        _mock_role_payload = RoleSchema(
            id=123456789,
            guild_id=987654321,
            name="test-role",
            color=0,
            hoist=False,
            position=1,
            permissions=0,
            managed=False,
            mentionable=False,
            created_at="2024-01-01T00:00:00",
            tags=None,
        )
        mock_role_converter.role_to_payload.return_value = _mock_role_payload

        from api.routers.roles import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


@pytest.fixture
def roles_client(roles_test_app):
    """Create a test client for the roles API."""
    return TestClient(roles_test_app)


class TestGetRole:
    """Tests for GET /roles/{role_id} endpoint."""

    def test_get_role_returns_200(self, roles_client):
        """GET /roles/{role_id} should return 200 with role details."""
        response = roles_client.get("/api/v1/roles/123456789")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 123456789
        assert data["data"]["name"] == "test-role"

    def test_get_role_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id} should return 404 for non-existent role."""
        response = roles_client.get("/api/v1/roles/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_role_invalid_id_returns_400(self, roles_client):
        """GET /roles/{role_id} should return 422 for invalid role ID (FastAPI path param validation)."""
        response = roles_client.get("/api/v1/roles/invalid")
        assert response.status_code in (400, 422)


class TestUpdateRole:
    """Tests for PUT /roles/{role_id} endpoint."""

    def test_update_role_returns_200(self, roles_client):
        """PUT /roles/{role_id} should return 200 with updated role."""
        update_data = {
            "name": "updated-role",
            "color": 16711680,
            "permissions": 8,
            "hoist": True,
            "position": 2,
            "mentionable": True
        }
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data
        assert data["data"]["id"] == 123456789

    def test_update_role_partial_returns_200(self, roles_client):
        """PUT /roles/{role_id} should return 200 with partial updates."""
        update_data = {
            "name": "partial-role"
        }
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_update_role_invalid_permissions_returns_422(self, roles_client):
        """PUT /roles/{role_id} should return 422 for invalid permissions."""
        update_data = {
            "permissions": -1
        }
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 422
        assert "invalid permissions" in response.json()["detail"].lower()

    def test_update_role_not_found_returns_404(self, roles_client):
        """PUT /roles/{role_id} should return 404 for non-existent role."""
        update_data = {
            "name": "new-role"
        }
        response = roles_client.put("/api/v1/roles/999999999", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteRole:
    """Tests for DELETE /roles/{role_id} endpoint."""

    def test_delete_role_returns_200(self, roles_client):
        """DELETE /roles/{role_id} should return 200 with deletion confirmation."""
        response = roles_client.delete("/api/v1/roles/123456789")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert "test-role" in data["message"].lower()

    def test_delete_role_not_found_returns_404(self, roles_client):
        """DELETE /roles/{role_id} should return 404 for non-existent role."""
        response = roles_client.delete("/api/v1/roles/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestListRoleMembers:
    """Tests for GET /roles/{role_id}/members endpoint."""

    def test_list_role_members_returns_200(self, roles_client):
        """GET /roles/{role_id}/members should return 200 with member list."""
        response = roles_client.get("/api/v1/roles/123456789/members")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_role_members_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id}/members should return 404 for non-existent role."""
        response = roles_client.get("/api/v1/roles/999999999/members")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestAssignRoleToUser:
    """Tests for PUT /roles/{role_id}/members/{user_id} endpoint."""

    def test_assign_role_to_user_returns_200(self, roles_client):
        """PUT /roles/{role_id}/members/{user_id} should return 200 with assignment confirmation."""
        response = roles_client.put("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "assigned"
        assert "test-role" in data["message"].lower()
        assert "test-member" in data["message"].lower()

    def test_assign_role_to_user_not_found_returns_404(self, roles_client):
        """PUT /roles/{role_id}/members/{user_id} should return 404 for non-existent role or member."""
        response = roles_client.put("/api/v1/roles/999999999/members/111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestRemoveRoleFromUser:
    """Tests for DELETE /roles/{role_id}/members/{user_id} endpoint."""

    def test_remove_role_from_user_returns_200(self, roles_client):
        """DELETE /roles/{role_id}/members/{user_id} should return 200 with removal confirmation."""
        response = roles_client.delete("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "removed"
        assert "test-role" in data["message"].lower()
        assert "test-member" in data["message"].lower()

    def test_remove_role_from_user_not_found_returns_404(self, roles_client):
        """DELETE /roles/{role_id}/members/{user_id} should return 404 for non-existent role or member."""
        response = roles_client.delete("/api/v1/roles/999999999/members/111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckUserHasRole:
    """Tests for GET /roles/{role_id}/members/{user_id}/check endpoint."""

    def test_check_user_has_role_returns_200(self, roles_client):
        """GET /roles/{role_id}/members/{user_id}/check should return 200 with role membership check."""
        response = roles_client.get("/api/v1/roles/123456789/members/111111111/check")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "allowed" in data["data"]

    def test_check_user_has_role_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id}/members/{user_id}/check should return 404 for non-existent role or member."""
        response = roles_client.get("/api/v1/roles/999999999/members/111111111/check")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckRolePermission:
    """Tests for GET /roles/{role_id}/permissions/check endpoint (deprecated)."""

    def test_check_role_permission_returns_200(self, roles_client):
        """GET /roles/{role_id}/permissions/check should return 200 with permission check."""
        response = roles_client.get("/api/v1/roles/123456789/permissions/check?permission=MANAGE_GUILD")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "allowed" in data["data"]

    def test_check_role_permission_invalid_permission_returns_422(self, roles_client):
        """GET /roles/{role_id}/permissions/check should return 422 for invalid permission."""
        response = roles_client.get("/api/v1/roles/123456789/permissions/check?permission=INVALID_PERMISSION")
        assert response.status_code == 422
        assert "unknown permission" in response.json()["detail"].lower()

    def test_check_role_permission_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id}/permissions/check should return 404 for non-existent role."""
        response = roles_client.get("/api/v1/roles/999999999/permissions/check?permission=MANAGE_GUILD")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in roles endpoints."""

    def test_handle_discord_exception(self, roles_client):
        """Roles endpoints should handle Discord exceptions gracefully."""
        # Mock a Discord exception scenario — make handle_discord_exception raise an HTTP 500
        from fastapi import HTTPException as FastAPIHTTPException
        with patch("api.routers.roles.resolve_bot", side_effect=Exception("Test Discord error")), \
             patch("api.routers.roles.handle_discord_exception",
                   side_effect=FastAPIHTTPException(status_code=500, detail="Internal server error")):
            response = roles_client.get("/api/v1/roles/123456789")
            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()
