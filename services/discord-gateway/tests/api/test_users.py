import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import sys
import os
import types
import importlib
from datetime import datetime

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils


# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

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

# Setup mock discord module with discord_mock_utils
# create_mock_discord_module() already wires real discord exception classes
# (NotFound, Forbidden, HTTPException) so except clauses work correctly.
_mock_discord = _mock_utils.create_mock_discord_module_with_factories()

# Mock CategoryChannel, TextChannel, VoiceChannel, ForumChannel, ThreadChannel, Thread, Embed, PermissionOverwrite, Guild, User, Member, Role, Message
_mock_discord.CategoryChannel = MagicMock()
_mock_discord.TextChannel = MagicMock()
_mock_discord.VoiceChannel = MagicMock()
_mock_discord.ForumChannel = MagicMock()
_mock_discord.ThreadChannel = MagicMock()
_mock_discord.Thread = MagicMock()
_mock_discord.Embed = MagicMock()
_mock_discord.PermissionOverwrite = MagicMock()
_mock_discord.Guild = MagicMock()
_mock_discord.User = MagicMock()
_mock_discord.Member = MagicMock()
_mock_discord.Role = MagicMock()
_mock_discord.Message = MagicMock()

# Mock discord.ext
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = MagicMock

_mock_discord.ext = _mock_discord_ext

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_discord():
    """
    Re-assert the real discord module into sys.modules before each test
    and reload api.routers.users so its ``discord`` reference is fresh.

    When the full test suite runs, test_guilds.py evicts all discord modules
    from sys.modules and re-imports real discord.  By the time test_users.py
    fixtures execute, sys.modules["discord"] = real discord (not the hand-
    rolled fake with real NotFound set up here).  The users.py router will
    have been imported — or re-imported — with real discord, so
    ``except discord.NotFound`` uses the real class.

    We restore real discord and reload both discord_mock_utils (so its
    module-level ``discord`` binding is refreshed) and api.routers.users
    (so its ``discord`` binding is refreshed).  After reload, raising a real
    ``discord.NotFound`` (via DiscordMockUtils.create_discord_not_found())
    is properly caught by the router's ``except discord.NotFound`` clause.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force the users router to re-bind its 'discord' global to real discord
    from api.routers import users as _users_mod
    importlib.reload(_users_mod)
    yield


def create_mock_user(user_id=111111111):
    """Create a mock Discord user using discord_mock_utils."""
    return _mock_utils.create_mock_user(
        user_id=user_id,
        username="test-user",
        discriminator="1234",
        avatar=None,
        bot=False,
        system=False,
        created_at=datetime(2024, 1, 1),
        public_flags=0
    )


def create_mock_member(member_id=111111111, guild_id=987654321):
    """Create a mock Discord member using discord_mock_utils."""
    return _mock_utils.create_mock_member(
        user_id=member_id,
        guild_id=guild_id,
        username="test-user",
        discriminator="1234",
        avatar=None,
        bot=False,
        system=False,
        created_at=datetime(2024, 1, 1),
        public_flags=0,
        display_name="test-member",
        nick=None,
        roles=[],
        joined_at=datetime(2024, 1, 1),
        premium_since=None,
        pending=False,
        voice=None,
        guild_permissions=MagicMock(value=0)
    )


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using discord_mock_utils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    mock_member = create_mock_member(111111111, 987654321)
    mock_member.edit = AsyncMock()
    mock_guild = MagicMock()
    mock_guild.id = 987654321
    mock_guild.get_member = MagicMock(
        side_effect=lambda x: mock_member if x == 111111111 else None
    )
    mock_guild.fetch_member = AsyncMock(
        side_effect=lambda x: mock_member if x == 111111111 else (_ for _ in ()).throw(DiscordMockUtils.create_discord_not_found())
    )

    bot.guilds = [mock_guild]
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found())
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)

    return bot


@pytest.fixture
def users_test_app(mock_bot):
    """Create a test FastAPI app with the users router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    with patch("api.routers.users.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.users.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.users.UserConverter") as mock_user_converter:

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        # UserConverter.user_to_payload returns a User schema object
        from api.schemas.user_schemas import User as UserSchema
        _mock_user_payload = UserSchema(
            id=111111111,
            username="test-user",
            discriminator="1234",
            avatar=None,
            bot=False,
            system=False,
            created_at="2024-01-01T00:00:00",
            public_flags=0,
        )
        mock_user_converter.user_to_payload.return_value = _mock_user_payload

        # UserConverter.member_to_payload returns a Member schema object
        from api.schemas.user_schemas import Member as MemberSchema
        _mock_member_payload = MemberSchema(
            user=_mock_user_payload,
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
        mock_user_converter.member_to_payload.return_value = _mock_member_payload

        from api.routers.users import router
        app.include_router(router, prefix="/api/v1")

        yield app


@pytest.fixture
def users_client(users_test_app):
    """Create a test client for the users API."""
    return TestClient(users_test_app)


class TestGetBotIdentity:
    """Tests for GET /users/@me endpoint."""

    def test_get_bot_identity_returns_200(self, users_client):
        """GET /users/@me should return 200 with bot user details."""
        response = users_client.get("/api/v1/users/@me")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data


class TestGetUser:
    """Tests for GET /users/{user_id} endpoint."""

    def test_get_user_not_found_returns_404(self, users_client):
        """GET /users/{user_id} should return 404 for non-existent user (bot.fetch_user raises NotFound)."""
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
        """GET /members/{member_id} should return 200 with member details."""
        response = users_client.get("/api/v1/members/111111111")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["user"]["id"] == 111111111
        assert data["data"]["guild_id"] == 987654321

    def test_get_member_not_found_returns_404(self, users_client):
        """GET /members/{member_id} should return 404 for non-existent member."""
        response = users_client.get("/api/v1/members/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateMember:
    """Tests for PUT /members/{member_id} endpoint."""

    def test_update_member_returns_200(self, users_client):
        """PUT /members/{member_id} should return 200 with updated member."""
        update_data = {
            "nick": "updated-nick",
        }
        response = users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data
        assert data["data"]["user"]["id"] == 111111111

    def test_update_member_partial_returns_200(self, users_client):
        """PUT /members/{member_id} should return 200 with partial updates."""
        update_data = {"mute": True}
        response = users_client.put("/api/v1/members/111111111", json=update_data)
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_update_member_not_found_returns_404(self, users_client):
        """PUT /members/{member_id} should return 404 for non-existent member."""
        update_data = {"nick": "new-nick"}
        response = users_client.put("/api/v1/members/999999999", json=update_data)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckMemberPermission:
    """Tests for GET /members/{member_id}/permissions/check endpoint."""

    def test_check_member_permission_returns_200(self, users_client):
        """GET /members/{member_id}/permissions/check should return 200."""
        response = users_client.get("/api/v1/members/111111111/permissions/check?permission=BAN_MEMBERS")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert "allowed" in data["data"]

    def test_check_member_permission_invalid_permission_returns_422(self, users_client):
        """GET /members/{member_id}/permissions/check should return 422 for unknown permission."""
        response = users_client.get("/api/v1/members/111111111/permissions/check?permission=INVALID_PERM")
        assert response.status_code == 422


class TestErrorHandling:
    """Tests for error handling in users endpoints."""

    def test_handle_discord_exception(self, users_client):
        """Users endpoints should handle Discord exceptions gracefully."""
        from fastapi import HTTPException as FastAPIHTTPException
        with patch("api.routers.users.resolve_bot", side_effect=Exception("Test Discord error")), \
             patch("api.routers.users.handle_discord_exception",
                   side_effect=FastAPIHTTPException(status_code=500, detail="Internal server error")):
            response = users_client.get("/api/v1/users/111111111")
            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()


if __name__ == '__main__':
    pytest.main([__file__])
