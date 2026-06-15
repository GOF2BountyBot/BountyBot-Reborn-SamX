"""
Extended tests for roles API router — covering uncovered error paths.
Target: keep roles.py coverage at 85%+ (currently 84%, add missing lines).

Uncovered lines:
  113-115   (update_role — invalid permissions bitmask branch)
  139-141   (update_role — exception path)
  183-185   (delete_role — exception path)
  216-217   (list_role_members — exception path)
  226-228   (list_role_members — exception path)
  264-268   (assign_role_to_user — fetch member discord.NotFound)
  283-285   (assign_role_to_user — exception path)
  321-325   (remove_role_from_user — fetch member discord.NotFound)
  340-342   (remove_role_from_user — exception path)
  395-397   (check_role_permission — exception path)
  429-433   (check_user_has_role — fetch member discord.NotFound)
  445-447   (check_user_has_role — exception path)
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
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Restore real discord and reload roles router before each test."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import roles as _roles_mod

    importlib.reload(_roles_mod)
    yield


def _make_role_schema():
    from api.schemas.role_schemas import Role as RoleSchema

    return RoleSchema(
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


def _create_mock_role(role_id=123456789):
    role = MagicMock()
    role.id = role_id
    role.name = "test-role"
    role.permissions = MagicMock(value=8)
    role.edit = AsyncMock()
    role.delete = AsyncMock()
    role.members = []
    return role


def _create_mock_member(member_id=111111111):
    member = MagicMock()
    member.id = member_id
    member.display_name = "test-member"
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    _mock_role = _create_mock_role(123456789)
    _mock_member = _create_mock_member(111111111)
    _mock_member.roles = [_mock_role]

    _mock_guild = MagicMock()
    _mock_guild.id = 987654321
    _mock_guild.get_role = MagicMock(side_effect=lambda x: _mock_role if x == 123456789 else None)
    _mock_guild.get_member = MagicMock(side_effect=lambda x: _mock_member if x == 111111111 else None)
    _mock_guild.fetch_member = AsyncMock(return_value=_mock_member)

    bot.guilds = [_mock_guild]
    bot.get_guild = MagicMock(return_value=_mock_guild)
    return bot


@pytest.fixture
def roles_ext_app(mock_bot):
    app = FastAPI(title="Test")
    app.state.bot = mock_bot

    with (
        patch("api.routers.roles.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.roles.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.roles.RoleConverter") as mock_converter,
        patch("api.routers.roles.UserConverter") as mock_user_converter,
    ):

        async def _resolve(request):
            return mock_bot

        mock_resolve.side_effect = _resolve

        async def _handle(op, exc):
            raise HTTPException(status_code=500, detail=f"Failed to {op}: {exc}")

        mock_handle.side_effect = _handle
        mock_converter.role_to_payload.return_value = _make_role_schema()
        mock_user_converter.member_to_payload.return_value = MagicMock()

        from api.routers.roles import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot, mock_resolve, mock_handle, mock_converter


@pytest.fixture
def ext_roles_client(roles_ext_app):
    app, *_ = roles_ext_app
    return TestClient(app)


class TestUpdateRoleExtended:
    """Extended tests for PUT /roles/{role_id}."""

    def test_update_role_invalid_permissions_bitmask(self, roles_ext_app):
        """update_role should return 422 when permissions bitmask doesn't round-trip."""
        app, *_ = roles_ext_app

        # Patch discord.Permissions to return an object with a different .value
        # (simulating a bitmask that loses bits)
        import discord as real_discord

        bad_perms = MagicMock()
        bad_perms.value = 999  # different from input

        with patch("api.routers.roles.discord") as mock_discord_mod:
            mock_discord_mod.Permissions.return_value = bad_perms
            mock_discord_mod.Color = real_discord.Color

            client = TestClient(app)
            response = client.put(
                "/api/v1/roles/123456789",
                json={"permissions": 7},  # 7 != 999, triggers the mismatch check
            )
            assert response.status_code == 422

    def test_update_role_exception_path(self, roles_ext_app):
        """update_role should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789", json={"name": "new-name"})
        assert response.status_code == 500


class TestDeleteRoleExtended:
    """Extended tests for DELETE /roles/{role_id}."""

    def test_delete_role_exception_path(self, roles_ext_app):
        """delete_role should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789")
        assert response.status_code == 500


class TestListRoleMembersExtended:
    """Extended tests for GET /roles/{role_id}/members."""

    def test_list_role_members_exception_path(self, roles_ext_app):
        """list_role_members should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members")
        assert response.status_code == 500


def _make_role_with_members(members):
    role = _create_mock_role()
    role.members = members
    return role


class TestAssignRoleExtended:
    """Extended tests for PUT /roles/{role_id}/members/{user_id}."""

    def test_assign_role_member_not_found_discord_not_found(self, roles_ext_app):
        """assign_role_to_user should return 404 when discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        # Member not in cache; fetch raises discord.NotFound
        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789/members/999999999")
        assert response.status_code == 404

    def test_assign_role_exception_path(self, roles_ext_app):
        """assign_role_to_user should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 500


class TestRemoveRoleExtended:
    """Extended tests for DELETE /roles/{role_id}/members/{user_id}."""

    def test_remove_role_member_not_found_discord_not_found(self, roles_ext_app):
        """remove_role_from_user should return 404 when discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789/members/999999999")
        assert response.status_code == 404

    def test_remove_role_exception_path(self, roles_ext_app):
        """remove_role_from_user should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 500


class TestCheckRolePermissionExtended:
    """Extended tests for GET /roles/{role_id}/permissions/check."""

    def test_check_role_permission_exception_path(self, roles_ext_app):
        """check_role_permission should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/permissions/check?permission=MANAGE_GUILD")
        assert response.status_code == 500


class TestCheckUserHasRoleExtended:
    """Extended tests for GET /roles/{role_id}/members/{user_id}/check."""

    def test_check_user_has_role_member_not_found_discord_not_found(self, roles_ext_app):
        """check_user_has_role should return 404 when discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members/999999999/check")
        assert response.status_code == 404

    def test_check_user_has_role_exception_path(self, roles_ext_app):
        """check_user_has_role should return 500 on unexpected exception."""
        app, _, mock_resolve, _, *_ = roles_ext_app

        async def _fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _fail

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members/111111111/check")
        assert response.status_code == 500
