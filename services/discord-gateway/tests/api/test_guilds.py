"""
Tests for the guilds API endpoints.

This module provides comprehensive tests for the guilds router endpoints,
including guild listing, creation, updates, and member management.

Fidelity notes
--------------
No patches on ``resolve_bot``, ``get_entity_or_404``, ``handle_discord_exception``
or ``GuildConverter``/``ChannelConverter``/``RoleConverter``/``UserConverter``:
the mock bot is ``spec=commands.Bot`` (``is_ready()==True``) and the mock
guild carries a real channel/category/role/member graph with real-typed
attributes, so the real converters produce genuine serialized bodies.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
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


def create_mock_guild():
    """Create a mock Discord guild with a real channel/category/role/member graph."""
    guild = DiscordMockUtils.create_mock_guild(
        guild_id=987654321,
        name="Test Guild",
        icon_url="https://cdn.discordapp.com/icons/987654321/abc123.png",
        description="Test description",
        verification_level="none",
        default_notifications="all_messages",
        explicit_content_filter="none",
        mfa_level="none",
        nsfw_level="default",
    )

    text_channel = DiscordMockUtils.create_mock_text_channel(
        channel_id=1234567890, name="general", position=0, guild=guild, guild_id=guild.id
    )
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=1111111111, name="Test Category", position=0, guild=guild, guild_id=guild.id
    )
    category.__class__ = discord.CategoryChannel

    role = DiscordMockUtils.create_mock_role(
        role_id=222222222, name="Test Role", guild=guild, position=1, permissions=8
    )
    role.__class__ = discord.Role

    member = DiscordMockUtils.create_mock_member(user_id=111111111, username="TestUser", guild=guild, roles=[role])
    member.__class__ = discord.Member
    # Real discord.Member delegates undefined attribute access (avatar, created_at,
    # public_flags, bot, system) to its underlying User via __getattr__;
    # create_mock_member only sets these on member.user, not on member itself.
    # UserConverter.user_to_payload(member) reads them straight off `member`
    # (member_to_payload passes the Member in as if it were a User), so mirror
    # that delegation here to match real discord.py's behaviour.
    member.avatar = member.user.avatar
    member.created_at = member.user.created_at
    member.public_flags = member.user.public_flags
    member.bot = member.user.bot
    member.system = member.user.system

    guild.channels = [text_channel, category]
    guild.categories = [category]
    guild.roles = [role]
    guild.members = [member]
    guild.chunked = True
    guild.chunk = AsyncMock()

    return guild


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils.

    ``fetch_guild`` raises a real ``discord.NotFound`` on cache miss so the
    real ``get_entity_or_404`` chain produces a genuine 404.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    guild = create_mock_guild()

    def get_guild(guild_id):
        return guild if guild_id == guild.id else None

    async def fetch_guild(guild_id):
        found = get_guild(guild_id)
        if found is None:
            raise create_discord_not_found(f"Guild {guild_id} not found")
        return found

    bot.get_guild = get_guild
    bot.fetch_guild = AsyncMock(side_effect=fetch_guild)
    bot.guilds = [guild]

    return bot


@pytest.fixture
def guilds_test_app(mock_bot):
    """Create a test FastAPI app with the guilds router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.guilds import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def guilds_client(guilds_test_app):
    """Create a test client for the guilds API."""
    return TestClient(guilds_test_app)


class TestListGuilds:
    """Tests for GET /guilds endpoint."""

    def test_list_guilds_success(self, guilds_client, mock_bot):
        """GET /guilds should list all guilds with real-serialized summaries."""
        response = guilds_client.get("/api/v1/guilds")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == 987654321
        assert data["data"][0]["name"] == "Test Guild"
        assert data["data"][0]["icon"] == "https://cdn.discordapp.com/icons/987654321/abc123.png"

    def test_list_guilds_with_params(self, guilds_client, mock_bot):
        """GET /guilds should handle query parameters (limit/offset not supported, ignored)."""
        response = guilds_client.get("/api/v1/guilds?limit=10&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)


class TestGetGuild:
    """Tests for GET /guilds/{guild_id} endpoint."""

    def test_get_guild_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id} should retrieve the real-serialized guild."""
        response = guilds_client.get("/api/v1/guilds/987654321")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == {
            "id": 987654321,
            "name": "Test Guild",
            "icon": "https://cdn.discordapp.com/icons/987654321/abc123.png",
            "member_count": 10,
            "owner_id": 1,
            "description": "Test description",
            "created_at": "2020-01-01T00:00:00",
            "features": [],
            "verification_level": "none",
            "default_notifications": "all_messages",
            "explicit_content_filter": "none",
            "mfa_level": "none",
            "premium_tier": 0,
            "premium_subscription_count": None,
            "preferred_locale": "en-US",
            "nsfw_level": "default",
        }

    def test_get_guild_not_found(self, guilds_client):
        """GET /guilds/{guild_id} should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildChannels:
    """Tests for GET /guilds/{guild_id}/channels endpoint."""

    def test_get_channels_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/channels excludes categories and real-serializes the rest."""
        response = guilds_client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        # The category (id=1111111111) is filtered out by the router's real isinstance check.
        assert [c["id"] for c in data["data"]] == [1234567890]
        assert data["data"][0]["name"] == "general"
        assert data["data"][0]["type"] == "text"

    def test_get_channels_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/channels should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/channels")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildMembers:
    """Tests for GET /guilds/{guild_id}/members endpoint."""

    def test_get_members_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/members should retrieve real-serialized members."""
        response = guilds_client.get("/api/v1/guilds/987654321/members")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["user"]["id"] == 111111111
        assert data["data"][0]["user"]["username"] == "TestUser"
        assert data["data"][0]["roles"] == [222222222]

    def test_get_members_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/members should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/members")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildRoles:
    """Tests for GET /guilds/{guild_id}/roles endpoint."""

    def test_get_roles_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/roles should retrieve real-serialized roles."""
        response = guilds_client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == 222222222
        assert data["data"][0]["name"] == "Test Role"
        assert data["data"][0]["permissions"] == 8

    def test_get_roles_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/roles should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/roles")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildCategories:
    """Tests for GET /guilds/{guild_id}/categories endpoint."""

    def test_get_categories_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/categories should retrieve real-serialized categories."""
        response = guilds_client.get("/api/v1/guilds/987654321/categories")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == 1111111111
        assert data["data"][0]["name"] == "Test Category"

    def test_get_categories_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/categories should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/categories")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
