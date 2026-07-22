"""
Tests for the users API endpoints.

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception`` or ``UserConverter``: the mock bot is
``spec=commands.Bot`` with ``is_ready()==True``, and the mock user/member
objects carry real-typed attributes (including a real ``discord.Permissions``
for ``guild_permissions``) so the real ``UserConverter``/``has_guild_permission``
produce genuine results whose ``id``/``allowed`` reflect the entity actually
resolved, not a hardcoded constant.
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

# Setup mock shared.bblogger module
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


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger


def create_mock_user(user_id=555000111):
    """Create a mock Discord user using discord_mock_utils."""
    return DiscordMockUtils.create_mock_user(
        user_id=user_id,
        username="test-user",
        discriminator="1234",
        avatar=None,
        bot=False,
        system=False,
        created_at=datetime(2024, 1, 1),
        public_flags=0,
    )


class _VoiceState:
    """Minimal stand-in for discord.VoiceState: just the two fields the
    converter reads (``deaf``/``mute``)."""

    def __init__(self):
        self.deaf = False
        self.mute = False


def create_mock_member(member_id=111111111, guild_id=987654321):
    """Create a mock Discord member using discord_mock_utils, with a real edit() that mutates state."""
    member = DiscordMockUtils.create_mock_member(
        user_id=member_id,
        guild_id=guild_id,
        username="test-user",
        discriminator="1234",
        display_name="test-member",
        nick=None,
        roles=[],
        joined_at=datetime(2024, 1, 1),
        premium_since=None,
        pending=False,
        guild_permissions=discord.Permissions(0),
    )
    member.__class__ = discord.Member
    member.voice = _VoiceState()

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
def mock_member():
    return create_mock_member(111111111, 987654321)


@pytest.fixture
def mock_bot(mock_member):
    """Create a mock Discord bot using discord_mock_utils.

    ``fetch_user``/``guild.fetch_member`` raise a real ``discord.NotFound``
    on cache miss so the real cache-then-fetch resolution chain in each
    router handler produces a genuine 404.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    known_user = create_mock_user(555000111)

    def get_user(user_id):
        return known_user if user_id == known_user.id else None

    async def fetch_user(user_id):
        if user_id == known_user.id:
            return known_user
        raise create_discord_not_found(f"User {user_id} not found")

    guild = MagicMock()
    guild.id = mock_member.guild.id
    guild.get_member = MagicMock(side_effect=lambda x: mock_member if x == mock_member.id else None)

    async def fetch_member(user_id):
        if user_id == mock_member.id:
            return mock_member
        raise create_discord_not_found(f"Member {user_id} not found")

    guild.fetch_member = AsyncMock(side_effect=fetch_member)

    bot.guilds = [guild]
    bot.get_user = MagicMock(side_effect=get_user)
    bot.fetch_user = AsyncMock(side_effect=fetch_user)
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)

    return bot


@pytest.fixture
def users_test_app(mock_bot):
    """Create a test FastAPI app with the users router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.users import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def users_client(users_test_app):
    """Create a test client for the users API."""
    return TestClient(users_test_app)


class TestGetBotIdentity:
    """Tests for GET /users/@me endpoint."""

    def test_get_bot_identity_returns_200(self, users_client, mock_bot):
        """GET /users/@me should return 200 with the real bot user's serialized details."""
        response = users_client.get("/api/v1/users/@me")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == mock_bot.user.id
        assert data["data"]["bot"] is True


class TestGetUser:
    """Tests for GET /users/{user_id} endpoint."""

    def test_get_user_returns_200(self, users_client):
        """GET /users/{user_id} should return 200 with the real, cache-resolved user's details."""
        response = users_client.get("/api/v1/users/555000111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["id"] == 555000111
        assert data["data"]["username"] == "test-user"

    def test_get_user_not_found_returns_404(self, users_client):
        """GET /users/{user_id} should return 404 (real discord.NotFound) for non-existent user."""
        response = users_client.get("/api/v1/users/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_user_invalid_id_returns_422(self, users_client):
        """GET /users/{user_id} should return 422 for invalid user ID (FastAPI path param validation)."""
        response = users_client.get("/api/v1/users/invalid")
        assert response.status_code == 422


class TestGetMember:
    """Tests for GET /members/{member_id} endpoint."""

    def test_get_member_returns_200(self, users_client):
        """GET /members/{member_id} should return 200 with the real, resolved member's details."""
        response = users_client.get("/api/v1/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["user"]["id"] == 111111111
        assert data["data"]["guild_id"] == 987654321

    def test_get_member_not_found_returns_404(self, users_client):
        """GET /members/{member_id} should return 404 (real discord.NotFound) for non-existent member."""
        response = users_client.get("/api/v1/members/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateMember:
    """Tests for PUT /members/{member_id} endpoint."""

    def test_update_member_returns_200(self, users_client):
        """PUT /members/{member_id} should return 200 with the real, mutated member's nick."""
        update_data = {
            "nick": "updated-nick",
        }
        response = users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["user"]["id"] == 111111111
        assert data["data"]["nick"] == "updated-nick"

    def test_update_member_partial_returns_200(self, users_client):
        """PUT /members/{member_id} should return 200 with the real, mutated voice-state mute flag."""
        update_data = {"mute": True}
        response = users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["mute"] is True

    def test_update_member_not_found_returns_404(self, users_client):
        """PUT /members/{member_id} should return 404 for non-existent member."""
        update_data = {"nick": "new-nick"}
        response = users_client.put("/api/v1/members/999999999", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckMemberPermission:
    """Tests for GET /members/{member_id}/permissions/check endpoint."""

    def test_check_member_permission_returns_200(self, users_client, mock_member):
        """GET .../permissions/check should evaluate the real member.guild_permissions bitfield."""
        mock_member.guild_permissions = discord.Permissions(ban_members=True)
        response = users_client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["allowed"] is True

        mock_member.guild_permissions = discord.Permissions(0)
        response = users_client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.json()["data"]["allowed"] is False

    def test_check_member_permission_invalid_permission_returns_422(self, users_client):
        """GET /members/{member_id}/permissions/check should return 422 for unknown permission."""
        response = users_client.get("/api/v1/members/111111111/permissions/check?permission=INVALID_PERM")
        assert response.status_code == 422


class TestErrorHandling:
    """Tests for error handling in users endpoints.

    ``resolve_bot`` (a network/readiness boundary) is patched to raise a
    generic error so the real, unpatched ``handle_discord_exception`` mapping
    of an unrecognized exception to HTTP 500 is exercised end-to-end.
    """

    def test_handle_discord_exception(self, users_client):
        """Users endpoints should map an unexpected error to a real 500 via handle_discord_exception."""
        with patch("api.routers.users.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = users_client.get("/api/v1/users/111111111")
            assert response.status_code == 500
            assert "test discord error" in response.json()["detail"].lower()


if __name__ == "__main__":
    pytest.main([__file__])
