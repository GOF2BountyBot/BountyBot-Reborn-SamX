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

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``handle_discord_exception``,
``RoleConverter`` or ``UserConverter``: only ``resolve_bot`` (a genuine
network/readiness boundary) is patched, and only for the "...exception_path"
tests, where it's made to raise so the real, unpatched
``handle_discord_exception`` maps the error to a real 500. All "not found"
tests raise a real ``discord.NotFound`` from the mock fetch calls.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

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
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _create_mock_role(role_id=123456789):
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id, guild_id=987654321, name="test-role", permissions=8
    )
    role.__class__ = discord.Role
    role.edit = AsyncMock()
    role.delete = AsyncMock()
    role.members = []
    return role


def _create_mock_member(member_id=111111111):
    member = DiscordMockUtils.create_mock_member(user_id=member_id, guild_id=987654321, username="test-member")
    member.__class__ = discord.Member
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

    with patch("api.routers.roles.resolve_bot", new_callable=AsyncMock) as mock_resolve:

        async def _resolve(request):
            return mock_bot

        mock_resolve.side_effect = _resolve

        from api.routers.roles import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot, mock_resolve


@pytest.fixture
def ext_roles_client(roles_ext_app):
    app, *_ = roles_ext_app
    return TestClient(app)


class TestUpdateRoleExtended:
    """Extended tests for PUT /roles/{role_id}."""

    def test_update_role_invalid_permissions_bitmask(self, roles_ext_app):
        """update_role should return 422 when permissions bitmask doesn't round-trip.

        Real ``discord.Permissions(value)`` stores the int verbatim with no
        masking, so ``perms.value != role_data.permissions`` can never be
        true for a real ``discord.Permissions`` — this defensive branch is
        only reachable by faking a ``Permissions`` whose ``.value`` diverges
        from its input, which is what's done here (a justified V2-boundary
        mock: there is no real object that can exercise this branch).
        """
        app, *_ = roles_ext_app

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
        """update_role should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789", json={"name": "new-name"})
        assert response.status_code == 500
        assert "unexpected error" in response.json()["detail"].lower()


class TestDeleteRoleExtended:
    """Extended tests for DELETE /roles/{role_id}."""

    def test_delete_role_exception_path(self, roles_ext_app):
        """delete_role should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789")
        assert response.status_code == 500


class TestListRoleMembersExtended:
    """Extended tests for GET /roles/{role_id}/members."""

    def test_list_role_members_exception_path(self, roles_ext_app):
        """list_role_members should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members")
        assert response.status_code == 500


class TestAssignRoleExtended:
    """Extended tests for PUT /roles/{role_id}/members/{user_id}."""

    def test_assign_role_member_not_found_discord_not_found(self, roles_ext_app):
        """assign_role_to_user should return 404 for a real discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        # Member not in cache; fetch raises a real discord.NotFound
        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789/members/999999999")
        assert response.status_code == 404

    def test_assign_role_exception_path(self, roles_ext_app):
        """assign_role_to_user should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.put("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 500


class TestRemoveRoleExtended:
    """Extended tests for DELETE /roles/{role_id}/members/{user_id}."""

    def test_remove_role_member_not_found_discord_not_found(self, roles_ext_app):
        """remove_role_from_user should return 404 for a real discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789/members/999999999")
        assert response.status_code == 404

    def test_remove_role_exception_path(self, roles_ext_app):
        """remove_role_from_user should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.delete("/api/v1/roles/123456789/members/111111111")
        assert response.status_code == 500


class TestCheckRolePermissionExtended:
    """Extended tests for GET /roles/{role_id}/permissions/check."""

    def test_check_role_permission_exception_path(self, roles_ext_app):
        """check_role_permission should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/permissions/check?permission=MANAGE_GUILD")
        assert response.status_code == 500


class TestCheckUserHasRoleExtended:
    """Extended tests for GET /roles/{role_id}/members/{user_id}/check."""

    def test_check_user_has_role_member_not_found_discord_not_found(self, roles_ext_app):
        """check_user_has_role should return 404 for a real discord.NotFound raised on fetch."""
        app, mock_bot, *_ = roles_ext_app

        mock_bot.guilds[0].get_member = MagicMock(return_value=None)
        mock_bot.guilds[0].fetch_member = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members/999999999/check")
        assert response.status_code == 404

    def test_check_user_has_role_exception_path(self, roles_ext_app):
        """check_user_has_role should map an unexpected exception to a real 500 via handle_discord_exception."""
        app, _, mock_resolve = roles_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/roles/123456789/members/111111111/check")
        assert response.status_code == 500
