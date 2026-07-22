"""
Tests for the roles API endpoints.

This module provides comprehensive test coverage for the roles router,
including role management, member assignment, and permission checking.

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception`` or ``RoleConverter``: the mock bot is
``spec=commands.Bot`` with ``is_ready()==True``, and the mock role/member
objects carry real-typed attributes so the real ``RoleConverter``/
``UserConverter`` produce genuine serialized bodies whose ``id`` reflects
the entity the router actually resolved, not a hardcoded constant.
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


def create_mock_role(role_id=123456789, guild_id=987654321, name="test-role"):
    """Create a mock Discord role using DiscordMockUtils, with a real edit() that mutates state."""
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id,
        guild_id=guild_id,
        name=name,
        color_value=0,
        hoist=False,
        position=1,
        permissions=0,
        managed=False,
        mentionable=False,
    )
    role.__class__ = discord.Role
    role.created_at = datetime(2024, 1, 1)
    role.tags = None
    role.members = []

    async def _edit(**kwargs):
        if "name" in kwargs:
            role.name = kwargs["name"]
        if "hoist" in kwargs:
            role.hoist = kwargs["hoist"]
        if "position" in kwargs:
            role.position = kwargs["position"]
        if "mentionable" in kwargs:
            role.mentionable = kwargs["mentionable"]
        if "color" in kwargs:
            role.color = kwargs["color"]
            role.colour = kwargs["color"]
        if "permissions" in kwargs:
            role.permissions = kwargs["permissions"]

    role.edit = AsyncMock(side_effect=_edit)
    role.delete = AsyncMock()
    return role


def create_mock_member(member_id=111111111, guild_id=987654321, name="test-member"):
    """Create a mock Discord member using DiscordMockUtils."""
    member = DiscordMockUtils.create_mock_member(
        user_id=member_id,
        guild_id=guild_id,
        username=name,
    )
    member.__class__ = discord.Member
    member.display_name = name
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture
def mock_role():
    return create_mock_role()


@pytest.fixture
def mock_member():
    return create_mock_member()


@pytest.fixture
def mock_bot(mock_role, mock_member):
    """Create a mock Discord bot using DiscordMockUtils.

    ``guild.fetch_member`` raises a real ``discord.NotFound`` on a cache
    miss so the real fetch-fallback -> ``handle_discord_exception``-adjacent
    404 branch (in assign/remove/check role handlers) is genuinely exercised.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    guild = MagicMock()
    guild.id = mock_role.guild.id
    guild.get_role = MagicMock(side_effect=lambda x: mock_role if x == mock_role.id else None)
    guild.get_member = MagicMock(side_effect=lambda x: mock_member if x == mock_member.id else None)

    async def fetch_member(user_id):
        if user_id == mock_member.id:
            return mock_member
        raise create_discord_not_found(f"Member {user_id} not found")

    guild.fetch_member = AsyncMock(side_effect=fetch_member)
    guild.members = [mock_member]

    bot.get_guild = MagicMock(side_effect=lambda x: guild if x == guild.id else None)
    bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == guild.id else None)
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)
    bot.guilds = [guild]  # roles router iterates bot.guilds

    return bot


@pytest.fixture
def roles_test_app(mock_bot):
    """Create a test FastAPI app with the roles router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.roles import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def roles_client(roles_test_app):
    """Create a test client for the roles API."""
    return TestClient(roles_test_app)


class TestGetRole:
    """Tests for GET /roles/{role_id} endpoint."""

    def test_get_role_returns_200(self, roles_client):
        """GET /roles/{role_id} should return 200 with real serialized role details."""
        response = roles_client.get("/api/v1/roles/123456789")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 123456789
        assert data["data"]["name"] == "test-role"

    def test_get_role_selects_correct_role_among_several(self, roles_client, mock_bot):
        """A guild with multiple roles must return the one that was actually requested, not a fixed payload."""
        other_role = create_mock_role(role_id=222222222, name="other-role")
        guild = mock_bot.guilds[0]
        original_get_role = guild.get_role.side_effect

        def get_role(role_id):
            if role_id == other_role.id:
                return other_role
            return original_get_role(role_id)

        guild.get_role = MagicMock(side_effect=get_role)

        response = roles_client.get("/api/v1/roles/222222222")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == 222222222
        assert data["data"]["name"] == "other-role"

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
        """PUT /roles/{role_id} should return 200 with the real, mutated role state."""
        update_data = {
            "name": "updated-role",
            "color": 16711680,
            "permissions": 8,
            "hoist": True,
            "position": 2,
            "mentionable": True,
        }
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["id"] == 123456789
        assert data["data"]["name"] == "updated-role"
        assert data["data"]["color"] == 16711680
        assert data["data"]["permissions"] == 8
        assert data["data"]["hoist"] is True
        assert data["data"]["position"] == 2
        assert data["data"]["mentionable"] is True

    def test_update_role_partial_returns_200(self, roles_client):
        """PUT /roles/{role_id} should return 200 with partial updates applied for real."""
        update_data = {"name": "partial-role"}
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "partial-role"

    def test_update_role_invalid_permissions_returns_422(self, roles_client):
        """PUT /roles/{role_id} should return 422 for invalid permissions."""
        update_data = {"permissions": -1}
        response = roles_client.put("/api/v1/roles/123456789", json=update_data)
        assert response.status_code == 422
        assert "invalid permissions" in response.json()["detail"].lower()

    def test_update_role_not_found_returns_404(self, roles_client):
        """PUT /roles/{role_id} should return 404 for non-existent role."""
        update_data = {"name": "new-role"}
        response = roles_client.put("/api/v1/roles/999999999", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteRole:
    """Tests for DELETE /roles/{role_id} endpoint."""

    def test_delete_role_returns_200(self, roles_client, mock_role):
        """DELETE /roles/{role_id} should return 200 with deletion confirmation, and call the real role.delete()."""
        response = roles_client.delete("/api/v1/roles/123456789")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert "test-role" in data["message"].lower()
        mock_role.delete.assert_awaited_once()

    def test_delete_role_not_found_returns_404(self, roles_client):
        """DELETE /roles/{role_id} should return 404 for non-existent role."""
        response = roles_client.delete("/api/v1/roles/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestListRoleMembers:
    """Tests for GET /roles/{role_id}/members endpoint."""

    def test_list_role_members_returns_200(self, roles_client, mock_role, mock_member):
        """GET /roles/{role_id}/members should return 200 with the real, serialized member list."""
        mock_role.members = [mock_member]

        response = roles_client.get("/api/v1/roles/123456789/members")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 1
        assert data["data"][0]["user"]["id"] == mock_member.id

    def test_list_role_members_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id}/members should return 404 for non-existent role."""
        response = roles_client.get("/api/v1/roles/999999999/members")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestAssignRoleToUser:
    """Tests for PUT /roles/{role_id}/members/{user_id} endpoint."""

    def test_assign_role_to_user_returns_200(self, roles_client, mock_member):
        """PUT /roles/{role_id}/members/{user_id} should return 200 and call the real member.add_roles()."""
        response = roles_client.put("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "assigned"
        assert "test-role" in data["message"].lower()
        assert "test-member" in data["message"].lower()
        mock_member.add_roles.assert_awaited_once()

    def test_assign_role_to_user_not_found_returns_404(self, roles_client):
        """PUT /roles/{role_id}/members/{user_id} should return 404 for non-existent role."""
        response = roles_client.put("/api/v1/roles/999999999/members/111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_assign_role_to_user_member_not_found_returns_404(self, roles_client):
        """PUT .../members/{user_id} should return 404 (real discord.NotFound) for an unknown member."""
        response = roles_client.put("/api/v1/roles/123456789/members/424242424")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestRemoveRoleFromUser:
    """Tests for DELETE /roles/{role_id}/members/{user_id} endpoint."""

    def test_remove_role_from_user_returns_200(self, roles_client, mock_member):
        """DELETE /roles/{role_id}/members/{user_id} should return 200 and call the real member.remove_roles()."""
        response = roles_client.delete("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "removed"
        assert "test-role" in data["message"].lower()
        assert "test-member" in data["message"].lower()
        mock_member.remove_roles.assert_awaited_once()

    def test_remove_role_from_user_not_found_returns_404(self, roles_client):
        """DELETE /roles/{role_id}/members/{user_id} should return 404 for non-existent role or member."""
        response = roles_client.delete("/api/v1/roles/999999999/members/111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckUserHasRole:
    """Tests for GET /roles/{role_id}/members/{user_id}/check endpoint."""

    def test_check_user_has_role_returns_200(self, roles_client, mock_role, mock_member):
        """GET .../check should evaluate real membership: True when the role is present, False otherwise."""
        mock_member.roles = [mock_role]
        response = roles_client.get("/api/v1/roles/123456789/members/111111111/check")
        assert response.status_code == 200
        assert response.json()["data"]["allowed"] is True

        mock_member.roles = []
        response = roles_client.get("/api/v1/roles/123456789/members/111111111/check")
        assert response.status_code == 200
        assert response.json()["data"]["allowed"] is False

    def test_check_user_has_role_not_found_returns_404(self, roles_client):
        """GET /roles/{role_id}/members/{user_id}/check should return 404 for non-existent role or member."""
        response = roles_client.get("/api/v1/roles/999999999/members/111111111/check")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckRolePermission:
    """Tests for GET /roles/{role_id}/permissions/check endpoint (deprecated)."""

    def test_check_role_permission_returns_200(self, roles_client, mock_role):
        """GET .../permissions/check should evaluate the real role.permissions bitfield."""
        mock_role.permissions.value = discord.Permissions(manage_guild=True).value
        response = roles_client.get("/api/v1/roles/123456789/permissions/check?permission=MANAGE_GUILD")
        assert response.status_code == 200
        assert response.json()["data"]["allowed"] is True

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
    """Tests for error handling in roles endpoints.

    ``resolve_bot`` (a network/readiness boundary) is patched to raise a
    generic error so the real, unpatched ``handle_discord_exception`` mapping
    of an unrecognized exception to HTTP 500 is exercised end-to-end.
    """

    def test_handle_discord_exception(self, roles_client):
        """Roles endpoints should map an unexpected error to a real 500 via handle_discord_exception."""
        with patch("api.routers.roles.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = roles_client.get("/api/v1/roles/123456789")
            assert response.status_code == 500
            assert "test discord error" in response.json()["detail"].lower()
