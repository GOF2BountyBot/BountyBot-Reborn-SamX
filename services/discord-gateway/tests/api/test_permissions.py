"""
Tests for the permissions API endpoints.

This module provides comprehensive test coverage for the permissions router,
including permission flag listing, overwrite management, and comprehensive checks.
"""

import pytest
import importlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient
import sys
import os
import types

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
    and reload api.routers.permissions so its ``discord`` reference is fresh.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force the permissions router to re-bind its 'discord' global to real discord
    from api.routers import permissions as _permissions_mod
    importlib.reload(_permissions_mod)
    yield


def create_mock_channel(channel_id=1234567890):
    """Create a mock Discord channel using DiscordMockUtils."""
    channel = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="test-channel",
        channel_type="text",
        guild_id=987654321,
    )
    channel.overwrites = {}
    channel.permissions_for = MagicMock()
    return channel


def create_mock_role(role_id=123456789):
    """Create a mock Discord role using DiscordMockUtils."""
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id,
        name="test-role",
        permissions=0,
    )
    return role


def create_mock_member(member_id=111111111):
    """Create a mock Discord member using DiscordMockUtils."""
    member = DiscordMockUtils.create_mock_member(
        user_id=member_id,
        username="test-member",
    )
    member.display_name = "test-member"
    return member


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    # Create a mock target with id 111111111
    _mock_target = MagicMock()
    _mock_target.id = 111111111
    _mock_target.name = "test-role"

    # Create a mock overwrite
    _mock_overwrite = MagicMock()
    _mock_overwrite.pair.return_value = (MagicMock(value=8), MagicMock(value=4))

    def get_channel(channel_id):
        if channel_id not in (1234567890, 111111111):
            return None
        channel = create_mock_channel(channel_id)
        # Add overwrites with target_id = 111111111
        channel.overwrites = {_mock_target: _mock_overwrite}
        channel.set_permissions = AsyncMock()
        return channel

    # Create mock guild for comprehensive permission checks
    _mock_guild = MagicMock()
    _mock_guild.id = 987654321
    _mock_role = create_mock_role(123456789)
    _mock_member = create_mock_member(111111111)
    _mock_guild.get_role = MagicMock(side_effect=lambda x: _mock_role if x == 123456789 else None)
    _mock_guild.get_member = MagicMock(side_effect=lambda x: _mock_member if x == 111111111 else None)
    _mock_guild.fetch_member = AsyncMock(return_value=_mock_member)
    _mock_guild.me = _mock_member
    _mock_guild.roles = [_mock_role]
    _mock_guild.members = [_mock_member]
    _mock_member.guild = _mock_guild
    _mock_member.guild_permissions = MagicMock(value=8)
    _mock_member.roles = [_mock_role]

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
def permissions_test_app(mock_bot):
    """Create a test FastAPI app with the permissions router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with patch("api.routers.permissions.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.permissions.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.permissions.PermissionConverter") as mock_converter:

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.return_value = None

        # Set up mock PermissionConverter with a proper PermissionOverwrite Pydantic object
        from api.schemas.permission_schemas import PermissionOverwrite as PermOverwrite
        _mock_overwrite_payload = PermOverwrite(
            id="1234567890:111111111",
            channel_id=1234567890,
            target_id=111111111,
            type="role",
            allow=8,
            deny=0,
        )
        mock_converter.overwrite_to_payload.return_value = _mock_overwrite_payload

        from api.routers.permissions import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


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
        """GET /permissions/{permission_id} should return 200 with permission overwrite."""
        response = permissions_client.get("/api/v1/permissions/1234567890:111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == "1234567890:111111111"

    def test_get_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """GET /permissions/{permission_id} should return 400 for invalid ID format."""
        response = permissions_client.get("/api/v1/permissions/invalid")
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_get_permission_overwrite_not_found_returns_404(self, permissions_client):
        """GET /permissions/{permission_id} should return 404 for non-existent overwrite."""
        response = permissions_client.get("/api/v1/permissions/9999999999:111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdatePermissionOverwrite:
    """Tests for PUT /permissions/{permission_id} endpoint."""

    def test_update_permission_overwrite_returns_200(self, permissions_client):
        """PUT /permissions/{permission_id} should return 200 with updated overwrite."""
        overwrite_data = {
            "allow": 8,
            "deny": 4
        }
        response = permissions_client.put("/api/v1/permissions/1234567890:111111111", json=overwrite_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data
        assert data["data"]["id"] == "1234567890:111111111"

    def test_update_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """PUT /permissions/{permission_id} should return 400 for invalid ID format."""
        overwrite_data = {
            "allow": 8,
            "deny": 4
        }
        response = permissions_client.put("/api/v1/permissions/invalid", json=overwrite_data)
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_update_permission_overwrite_not_found_returns_404(self, permissions_client):
        """PUT /permissions/{permission_id} should return 404 for non-existent channel or target."""
        overwrite_data = {
            "allow": 8,
            "deny": 4
        }
        response = permissions_client.put("/api/v1/permissions/9999999999:111111111", json=overwrite_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestRemovePermissionOverwrite:
    """Tests for DELETE /permissions/{permission_id} endpoint."""

    def test_remove_permission_overwrite_returns_200(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 200 with removal confirmation."""
        response = permissions_client.delete("/api/v1/permissions/1234567890:111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert "permission overwrite removed" in data["message"].lower()

    def test_remove_permission_overwrite_invalid_id_returns_400(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 400 for invalid ID format."""
        response = permissions_client.delete("/api/v1/permissions/invalid")
        assert response.status_code == 400
        assert "permission_id must be in format" in response.json()["detail"].lower()

    def test_remove_permission_overwrite_not_found_returns_404(self, permissions_client):
        """DELETE /permissions/{permission_id} should return 404 for non-existent overwrite."""
        response = permissions_client.delete("/api/v1/permissions/9999999999:111111111")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestConvertNamesToValue:
    """Tests for POST /permissions/convert/names-to-value endpoint."""

    def test_convert_names_to_value_returns_200(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 200 with bitfield value."""
        body = {
            "names": ["MANAGE_GUILD", "KICK_MEMBERS"]
        }
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "value" in data["data"]
        assert isinstance(data["data"]["value"], int)

    def test_convert_names_to_value_empty_list_returns_422(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 422 for empty list."""
        body = {
            "names": []
        }
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 422
        assert "names list must contain at least one permission" in response.json()["detail"].lower()

    def test_convert_names_to_value_invalid_permission_returns_400(self, permissions_client):
        """POST /permissions/convert/names-to-value should return 400 for invalid permission."""
        body = {
            "names": ["INVALID_PERMISSION"]
        }
        response = permissions_client.post("/api/v1/permissions/convert/names-to-value", json=body)
        assert response.status_code == 400
        assert "unknown permission" in response.json()["detail"].lower()


class TestConvertValueToNames:
    """Tests for POST /permissions/convert/value-to-names endpoint."""

    def test_convert_value_to_names_returns_200(self, permissions_client):
        """POST /permissions/convert/value-to-names should return 200 with permission names."""
        body = {
            "value": 8
        }
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
        body = {
            "base": 8,
            "allow": 4,
            "deny": 2
        }
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
            "subject": {
                "type": "user",
                "id": 111111111
            },
            "target": {
                "type": "guild",
                "id": 987654321
            },
            "permissions": ["MANAGE_GUILD", "KICK_MEMBERS"]
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
            "subject": {
                "type": "user",
                "id": 111111111
            },
            "target": {
                "type": "guild",
                "id": 987654321
            },
            "permissions": []
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
            "subject": {
                "type": "user",
                "id": 111111111
            },
            "target": {
                "type": "guild",
                "id": 987654321
            },
            "permissions": ["INVALID_PERMISSION"]
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 422
        assert "unknown permission" in response.json()["detail"].lower()

    def test_check_comprehensive_permissions_invalid_subject_type_returns_400(self, permissions_client):
        """POST /permissions/check should return 400 for invalid subject type."""
        body = {
            "subject": {
                "type": "invalid",
                "id": 111111111
            },
            "target": {
                "type": "guild",
                "id": 987654321
            },
            "permissions": ["MANAGE_GUILD"]
        }
        response = permissions_client.post("/api/v1/permissions/check", json=body)
        assert response.status_code == 400
        assert "unknown subject type" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in permissions endpoints."""

    def test_handle_discord_exception(self, permissions_client):
        """Permissions endpoints should handle Discord exceptions gracefully."""
        from fastapi import HTTPException as FastAPIHTTPException
        with patch("api.routers.permissions.resolve_bot", side_effect=Exception("Test Discord error")), \
             patch("api.routers.permissions.handle_discord_exception",
                   side_effect=FastAPIHTTPException(status_code=500, detail="internal server error")):
            response = permissions_client.get("/api/v1/permissions/1234567890:111111111")
            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()
