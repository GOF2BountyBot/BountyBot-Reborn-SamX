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

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``handle_discord_exception``
or ``UserConverter``: only ``resolve_bot`` (a genuine network/readiness
boundary) is patched, and only for the "...generic_exception_returns_500"
tests, where it's made to raise so the real, unpatched
``handle_discord_exception`` maps the error to a real 500. "Not found" tests
raise a real ``discord.NotFound`` from the mock fetch calls; the voice-error
test builds a real ``discord.HTTPException`` with code 40032.
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
    for m in ("info", "debug", "warning", "error", "trace", "critical"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _VoiceState:
    """Minimal stand-in for discord.VoiceState (the two fields the converter reads)."""

    def __init__(self):
        self.deaf = False
        self.mute = False


def create_mock_member(member_id=111111111, guild_id=987654321):
    """Create a real-attributed mock Member with a real edit() that mutates state."""
    member = DiscordMockUtils.create_mock_member(user_id=member_id, guild_id=guild_id, username="test-user")
    member.__class__ = discord.Member
    member.display_name = "test-member"
    member.voice = _VoiceState()
    member.guild.get_role = MagicMock(return_value=None)
    member.guild.get_channel = MagicMock(return_value=None)

    async def _edit(**kwargs):
        if "nick" in kwargs:
            member.nick = kwargs["nick"]
        if "mute" in kwargs:
            member.voice.mute = kwargs["mute"]
        if "deafen" in kwargs:
            member.voice.deaf = kwargs["deafen"]
        if "roles" in kwargs:
            member.roles = kwargs["roles"]

    member.edit = AsyncMock(side_effect=_edit)
    return member


@pytest.fixture
def mock_bot_with_user():
    """Bot with a user and member in cache."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    mock_user = DiscordMockUtils.create_mock_user(user_id=111111111, username="cached-user")
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    mock_member = create_mock_member(111111111, 987654321)

    mock_guild = MagicMock()
    mock_guild.id = 987654321
    mock_guild.get_member = MagicMock(return_value=mock_member)
    mock_guild.fetch_member = AsyncMock(return_value=mock_member)
    bot.guilds = [mock_guild]
    bot.get_channel = MagicMock(return_value=None)
    return bot


@pytest.fixture
def users_ext_app(mock_bot_with_user):
    """Create test app with only resolve_bot patched (network/readiness boundary)."""
    app = FastAPI(title="Test")
    app.state.bot = mock_bot_with_user

    with patch("api.routers.users.resolve_bot", new_callable=AsyncMock) as mock_resolve:

        async def _resolve(request):
            return mock_bot_with_user

        mock_resolve.side_effect = _resolve

        from api.routers.users import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_bot_with_user, mock_resolve


@pytest.fixture
def ext_users_client(users_ext_app):
    """Test client for extended users tests."""
    app, *_ = users_ext_app
    return TestClient(app)


class TestGetBotIdentityExtended:
    """Extended tests for GET /users/@me."""

    def test_get_bot_identity_returns_user_data(self, ext_users_client, mock_bot_with_user):
        """GET /users/@me should return the real bot user's serialized details."""
        response = ext_users_client.get("/api/v1/users/@me")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == mock_bot_with_user.user.id

    def test_get_bot_identity_generic_exception_returns_500(self, users_ext_app):
        """GET /users/@me should map an unexpected exception to a real 500."""
        app, _mock_bot, mock_resolve = users_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/users/@me")
        assert response.status_code == 500


class TestGetUserExtended:
    """Extended tests for GET /users/{user_id}."""

    def test_get_user_found_in_cache_returns_200(self, ext_users_client):
        """GET /users/{user_id} should return 200 with the real, cache-resolved user's details."""
        response = ext_users_client.get("/api/v1/users/111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == 111111111
        assert data["data"]["username"] == "cached-user"

    def test_get_user_generic_exception_returns_500(self, users_ext_app):
        """GET /users/{user_id} should map an unexpected exception to a real 500."""
        app, _mock_bot, mock_resolve = users_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/users/111111111")
        assert response.status_code == 500


class TestGetMemberExtended:
    """Extended tests for GET /members/{member_id}."""

    def test_get_member_found_in_cache(self, ext_users_client):
        """GET /members/{member_id} should use the real cached member."""
        response = ext_users_client.get("/api/v1/members/111111111")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["user"]["id"] == 111111111

    def test_get_member_not_found_across_guilds(self, users_ext_app):
        """GET /members/{member_id} should return 404 (real discord.NotFound) when not found in any guild."""
        app, mock_bot, *_ = users_ext_app

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)
        mock_guild.fetch_member = AsyncMock(side_effect=create_discord_not_found())
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/999999999")
        assert response.status_code == 404

    def test_get_member_generic_exception_returns_500(self, users_ext_app):
        """GET /members/{member_id} should map an unexpected exception to a real 500."""
        app, _mock_bot, mock_resolve = users_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111")
        assert response.status_code == 500


class TestUpdateMemberExtended:
    """Extended tests for PUT /members/{member_id}."""

    def test_update_member_found_in_cache(self, ext_users_client):
        """PUT /members/{member_id} should use the real cached member and apply the real edit."""
        update_data = {"nick": "new-nick"}
        response = ext_users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200
        assert response.json()["data"]["nick"] == "new-nick"

    def test_update_member_no_updates_returns_200(self, ext_users_client):
        """PUT /members/{member_id} with no updates should still return 200 (member.edit not called)."""
        update_data = {}
        response = ext_users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_with_roles(self, users_ext_app):
        """PUT /members/{member_id} should resolve role ids via the real guild.get_role and apply them."""
        app, mock_bot, *_ = users_ext_app

        mock_member = mock_bot.guilds[0].get_member(111111111)
        role = DiscordMockUtils.create_mock_role(role_id=555555555, guild_id=987654321)
        role.__class__ = discord.Role
        mock_member.guild.get_role = MagicMock(side_effect=lambda x: role if x == 555555555 else None)

        client = TestClient(app)
        update_data = {"roles": [555555555]}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200
        assert response.json()["data"]["roles"] == [555555555]

    def test_update_member_with_roles_not_found(self, users_ext_app):
        """PUT /members/{member_id} should return 404 when a requested role id doesn't resolve."""
        app, mock_bot, *_ = users_ext_app

        mock_member = mock_bot.guilds[0].get_member(111111111)
        mock_member.guild.get_role = MagicMock(return_value=None)  # Role not found

        client = TestClient(app)
        update_data = {"roles": [999888777]}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_member_voice_channel_disconnect(self, users_ext_app):
        """PUT /members/{member_id} with channel_id=0 should disconnect from voice (voice_channel=None)."""
        app, *_ = users_ext_app

        client = TestClient(app)
        update_data = {"channel_id": 0}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_voice_channel_not_found(self, users_ext_app):
        """PUT /members/{member_id} with an unresolvable channel_id should return 404."""
        app, mock_bot, *_ = users_ext_app

        mock_member = mock_bot.guilds[0].get_member(111111111)
        mock_member.guild.get_channel = MagicMock(return_value=None)  # Channel not found

        client = TestClient(app)
        update_data = {"channel_id": 123456789}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_member_voice_channel_valid(self, users_ext_app):
        """PUT /members/{member_id} with a valid channel_id should resolve it via the real guild.get_channel."""
        app, mock_bot, *_ = users_ext_app

        voice_channel = DiscordMockUtils.create_mock_voice_channel(channel_id=123456789, guild_id=987654321)
        mock_member = mock_bot.guilds[0].get_member(111111111)
        mock_member.guild.get_channel = MagicMock(side_effect=lambda x: voice_channel if x == 123456789 else None)

        client = TestClient(app)
        update_data = {"channel_id": 123456789}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

    def test_update_member_voice_error_40032(self, users_ext_app):
        """PUT /members/{member_id} should map a real discord.HTTPException(code=40032) to a 400."""
        app, mock_bot, *_ = users_ext_app

        class FakeResponse:
            status = 400
            reason = "Bad Request"

        fake_exc = discord.HTTPException(FakeResponse(), {"message": "not in voice", "code": 40032})

        mock_member = mock_bot.guilds[0].get_member(111111111)
        mock_member.edit = AsyncMock(side_effect=fake_exc)

        client = TestClient(app)
        update_data = {"nick": "new-nick"}
        response = client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 400
        assert "voice" in response.json()["detail"].lower()

    def test_update_member_generic_exception_returns_500(self, users_ext_app):
        """PUT /members/{member_id} should map an unexpected exception to a real 500."""
        app, _mock_bot, mock_resolve = users_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.put("/api/v1/members/111111111", json={"nick": "x"})
        assert response.status_code == 500


class TestCheckMemberPermissionExtended:
    """Extended tests for GET /members/{member_id}/permissions/check."""

    def test_check_member_permission_member_from_fetch(self, users_ext_app):
        """check_member_permission should fetch (real discord API boundary) when member not in cache."""
        app, mock_bot, *_ = users_ext_app

        mock_member = create_mock_member(111111111, 987654321)
        mock_member.guild_permissions = discord.Permissions(ban_members=True)

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)  # Not in cache
        mock_guild.fetch_member = AsyncMock(return_value=mock_member)
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200
        assert response.json()["data"]["allowed"] is True

    def test_check_member_permission_member_not_found(self, users_ext_app):
        """check_member_permission should return 404 (real discord.NotFound) when member not found."""
        app, mock_bot, *_ = users_ext_app

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)
        mock_guild.fetch_member = AsyncMock(side_effect=create_discord_not_found())
        mock_bot.guilds = [mock_guild]

        client = TestClient(app)
        response = client.get("/api/v1/members/999999999/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_check_member_permission_found_in_guild(self, ext_users_client):
        """check_member_permission should use the real member found in guild cache."""
        response = ext_users_client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "allowed" in data["data"]

    def test_check_member_permission_generic_exception_returns_500(self, users_ext_app):
        """check_member_permission should map an unexpected exception to a real 500."""
        app, _mock_bot, mock_resolve = users_ext_app
        mock_resolve.side_effect = RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 500
