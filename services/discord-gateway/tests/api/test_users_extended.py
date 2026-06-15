"""
Extended tests for users API router — covering uncovered error paths.
Target: push users.py from 67% to 85%+.

Uncovered lines:
  49-53   (get_bot_identity — exception path)
  80-83   (get_user — user found in cache)
  117-118 (get_member — member found in cache)
  138-140 (get_member — generic exception)
  167-168 (update_member — found in cache)
  186     (update_member — no updates provided)
  189-199 (update_member — roles update with valid roles)
  202-212 (update_member — channel_id updates)
  218-225 (update_member — voice error code 40032)
  236-238 (update_member — generic exception)
  275-280 (check_member_permission — member not found)
  283-284 (check_member_permission — member found in guild)
  296-300 (check_member_permission — generic exception)
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Restore real discord and reload users router before each test."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import users as _users_mod

    importlib.reload(_users_mod)
    yield


def _make_user_payload():
    from api.schemas.user_schemas import User as UserSchema

    return UserSchema(
        id=111111111,
        username="test-user",
        discriminator="1234",
        avatar=None,
        bot=False,
        system=False,
        created_at="2024-01-01T00:00:00",
        public_flags=0,
    )


def _make_member_payload():
    from api.schemas.user_schemas import Member as MemberSchema

    return MemberSchema(
        user=_make_user_payload(),
        guild_id=987654321,
        nick=None,
        roles=[],
        joined_at="2024-01-01T00:00:00",
        premium_since=None,
        deaf=False,
        mute=False,
        pending=False,
        permissions=0,
    )


@pytest.fixture
def mock_bot_with_user():
    """Bot with user in cache."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    mock_user = MagicMock()
    mock_user.id = 111111111
    mock_user.name = "cached-user"
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    mock_member = MagicMock()
    mock_member.id = 111111111
    mock_member.display_name = "test-member"
    mock_member.edit = AsyncMock()
    mock_member.guild = MagicMock()
    mock_member.guild.id = 987654321
    mock_member.guild.get_role = MagicMock(return_value=None)
    mock_member.guild.get_channel = MagicMock(return_value=None)

    mock_guild = MagicMock()
    mock_guild.id = 987654321
    mock_guild.get_member = MagicMock(return_value=mock_member)
    mock_guild.fetch_member = AsyncMock(return_value=mock_member)
    bot.guilds = [mock_guild]
    bot.get_channel = MagicMock(return_value=None)
    return bot


@pytest.fixture
def users_ext_app(mock_bot_with_user):
    """Create test app with patched users router dependencies."""
    app = FastAPI(title="Test")
    app.state.bot = mock_bot_with_user

    with (
        patch("api.routers.users.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.users.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.users.UserConverter") as mock_user_converter,
    ):

        async def _resolve(request):
            return mock_bot_with_user

        mock_resolve.side_effect = _resolve

        async def _handle(op, exc):
            raise HTTPException(status_code=500, detail=f"Failed to {op}: {exc}")

        mock_handle.side_effect = _handle

        _mock_user_payload = _make_user_payload()
        mock_user_converter.user_to_payload.return_value = _mock_user_payload

        _mock_member_payload = _make_member_payload()
        mock_user_converter.member_to_payload.return_value = _mock_member_payload

        from api.routers.users import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot_with_user, mock_resolve, mock_handle, mock_user_converter


@pytest.fixture
def ext_users_client(users_ext_app):
    """Test client for extended users tests."""
    app, *_ = users_ext_app
    return TestClient(app)


class TestGetBotIdentityExtended:
    """Extended tests for GET /users/@me."""

    def test_get_bot_identity_returns_user_data(self, ext_users_client):
        """GET /users/@me should return bot user details."""
        response = ext_users_client.get("/api/v1/users/@me")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == 111111111

    def test_get_bot_identity_generic_exception_returns_500(self, users_ext_app):
        """GET /users/@me should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, _mock_user_converter = users_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.get("/api/v1/users/@me")
        assert response.status_code == 500


class TestGetUserExtended:
    """Extended tests for GET /users/{user_id}."""

    def test_get_user_found_in_cache_returns_200(self, ext_users_client):
        """GET /users/{user_id} should return 200 when user is in cache."""
        response = ext_users_client.get("/api/v1/users/111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_get_user_generic_exception_returns_500(self, users_ext_app):
        """GET /users/{user_id} should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, _mock_user_converter = users_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.get("/api/v1/users/111111111")
        assert response.status_code == 500


class TestGetMemberExtended:
    """Extended tests for GET /members/{member_id}."""

    def test_get_member_found_in_cache(self, ext_users_client):
        """GET /members/{member_id} should use cached member."""
        response = ext_users_client.get("/api/v1/members/111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_get_member_not_found_across_guilds(self, users_ext_app):
        """GET /members/{member_id} should return 404 when not found in any guild."""
        app, mock_bot, *_ = users_ext_app

        # Make member not found in any guild
        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)
        mock_guild.fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/999999999")
        assert response.status_code == 404

    def test_get_member_generic_exception_returns_500(self, users_ext_app):
        """GET /members/{member_id} should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = users_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111")
        assert response.status_code == 500


class TestUpdateMemberExtended:
    """Extended tests for PUT /members/{member_id}."""

    def test_update_member_found_in_cache(self, ext_users_client):
        """PUT /members/{member_id} should use cached member."""
        update_data = {"nick": "new-nick"}
        response = ext_users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_no_updates_returns_200(self, ext_users_client):
        """PUT /members/{member_id} with no updates should still return 200."""
        update_data = {}
        response = ext_users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_with_roles(self, users_ext_app):
        """PUT /members/{member_id} should update member roles."""
        app, mock_bot, *_ = users_ext_app

        role = MagicMock()
        role.id = 555555555
        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock()
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321
        mock_member.guild.get_role = MagicMock(return_value=role)

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"roles": [555555555]}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_with_roles_not_found(self, users_ext_app):
        """PUT /members/{member_id} should return 404 when role not found."""
        app, mock_bot, *_ = users_ext_app

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock()
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321
        mock_member.guild.get_role = MagicMock(return_value=None)  # Role not found

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"roles": [999888777]}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_member_voice_channel_disconnect(self, users_ext_app):
        """PUT /members/{member_id} with channel_id=0 should disconnect from voice."""
        app, mock_bot, *_ = users_ext_app

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock()
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321
        mock_member.guild.get_channel = MagicMock(return_value=None)

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"channel_id": 0}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_voice_channel_not_found(self, users_ext_app):
        """PUT /members/{member_id} with invalid channel_id should return 404."""
        app, mock_bot, *_ = users_ext_app

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock()
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321
        mock_member.guild.get_channel = MagicMock(return_value=None)  # Channel not found

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"channel_id": 123456789}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_member_voice_channel_valid(self, users_ext_app):
        """PUT /members/{member_id} with valid channel_id should update voice channel."""
        app, mock_bot, *_ = users_ext_app

        voice_channel = MagicMock()
        voice_channel.id = 123456789

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock()
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321
        mock_member.guild.get_channel = MagicMock(return_value=voice_channel)

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"channel_id": 123456789}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_voice_error_40032(self, users_ext_app):
        """PUT /members/{member_id} should handle error code 40032 (not in voice)."""
        import discord as _discord

        app, mock_bot, *_ = users_ext_app

        # Build a real discord.HTTPException with code 40032
        class FakeResponse:
            status = 400
            reason = "Bad Request"

        fake_exc = _discord.HTTPException(FakeResponse(), {"message": "not in voice", "code": 40032})

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.edit = AsyncMock(side_effect=fake_exc)
        mock_member.guild = MagicMock()
        mock_member.guild.id = 987654321

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=mock_member)
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        update_data = {"nick": "new-nick"}
        response = client.put("/api/v1/members/111111111", json=update_data)
        # Should raise 400 for voice error
        assert response.status_code == 400
        assert "voice" in response.json()["detail"].lower()

    def test_update_member_generic_exception_returns_500(self, users_ext_app):
        """PUT /members/{member_id} should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = users_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.put("/api/v1/members/111111111", json={"nick": "x"})
        assert response.status_code == 500


class TestCheckMemberPermissionExtended:
    """Extended tests for GET /members/{member_id}/permissions/check."""

    def test_check_member_permission_member_from_fetch(self, users_ext_app):
        """check_member_permission should fetch member if not in cache."""
        app, mock_bot, *_ = users_ext_app

        mock_member = MagicMock()
        mock_member.id = 111111111
        mock_member.display_name = "test-member"
        mock_member.guild_permissions = MagicMock(value=8)

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)  # Not in cache
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200

    def test_check_member_permission_member_not_found(self, users_ext_app):
        """check_member_permission should return 404 when member not found."""
        app, mock_bot, *_ = users_ext_app

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)
        mock_guild.fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/999999999/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_check_member_permission_found_in_guild(self, ext_users_client):
        """check_member_permission should use member found in guild cache."""
        response = ext_users_client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "allowed" in data["data"]

    def test_check_member_permission_generic_exception_returns_500(self, users_ext_app):
        """check_member_permission should handle generic exceptions."""
        app, _mock_bot, mock_resolve, _mock_handle, *_ = users_ext_app

        async def _resolve_fail(request):
            raise RuntimeError("Unexpected error")

        mock_resolve.side_effect = _resolve_fail

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 500
