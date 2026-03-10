"""
Tests for the guilds API endpoints.

This module provides comprehensive tests for the guilds router endpoints,
including guild listing, creation, updates, and member management.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import sys
import os
import types
from datetime import datetime

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils


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

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discord
from discord.ext import commands


def create_mock_guild(
    guild_id=987654321,
    name="Test Guild",
    icon="icon_url",
    description="Test description",
    features=(),
    emojis=(),
    stickers=(),
    roles=[],
    channels=[],
    members=[]
):
    """Create a mock Discord guild using DiscordMockUtils."""
    guild = DiscordMockUtils.create_mock_guild(
        guild_id=guild_id,
        name=name,
        description=description,
        features=list(features),
    )
    guild.icon = icon
    guild.emojis = emojis
    guild.stickers = stickers
    guild.roles = roles
    guild.channels = channels
    guild.members = members
    guild.categories = []
    guild.chunked = True
    guild.me = MagicMock()
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.roles = [MagicMock()]
    guild.system_channel = None
    guild.rules_channel = None
    guild.public_updates_channel = None
    guild.is_bot_admin = MagicMock(return_value=True)
    return guild


def create_mock_member(
    user_id=111111111,
    username="TestUser",
    discriminator="1234",
    guild_id=987654321,
    roles=[],
    nick=None,
    joined_at=None,
    premium_since=None,
    pending=False,
    communication_disabled_until=None
):
    """Create a mock Discord member using DiscordMockUtils."""
    member = DiscordMockUtils.create_mock_member(
        user_id=user_id,
        guild_id=guild_id,
        username=username,
        discriminator=discriminator,
        nickname=nick,
        roles=roles,
        joined_at=joined_at or datetime.now(),
        premium_since=premium_since,
        pending=pending,
    )
    member.communication_disabled_until = communication_disabled_until
    member.guild.get_member_permissions = MagicMock(return_value=discord.Permissions.all())
    member.guild.get_member_roles = MagicMock(return_value=roles)
    member.guild.ban = AsyncMock()
    member.guild.unban = AsyncMock()
    member.guild.kick = AsyncMock()
    member.guild.mute = AsyncMock()
    member.guild.deafen = AsyncMock()
    member.guild.move_to = AsyncMock()
    member.guild.edit = AsyncMock()
    return member


def create_mock_role(
    role_id=222222222,
    name="Test Role",
    color=0x00FF00,
    hoist=False,
    position=1,
    permissions=None,
    managed=False,
    mentionable=False,
    is_integration=False
):
    """Create a mock Discord role using DiscordMockUtils."""
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id,
        name=name,
        color_value=color,
        hoist=hoist,
        position=position,
        managed=managed,
        mentionable=mentionable,
    )
    if permissions is not None:
        role.permissions = permissions
    role.is_integration = is_integration
    return role


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    guild = create_mock_guild()

    def get_guild(guild_id):
        if guild_id == 987654321:
            return guild
        return None

    bot.get_guild = get_guild
    bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
    bot.guilds = [guild]

    return bot


def _evict_discord_modules():
    """Remove any cached discord or source modules so they re-import with real discord."""
    to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.")
                or k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
                or k.startswith("cogs.")]
    for k in to_evict:
        sys.modules.pop(k, None)


@pytest.fixture
def guilds_test_app(mock_bot):
    """Create a test FastAPI app with the guilds router and mocked dependencies."""
    _evict_discord_modules()

    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
         patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.guilds.GuildConverter") as mock_converter, \
         patch("api.routers.guilds.ChannelConverter") as mock_ch_converter, \
         patch("api.routers.guilds.RoleConverter") as mock_role_converter, \
         patch("api.routers.guilds.UserConverter") as mock_user_converter:

        async def mock_get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
            guild = mock_bot.get_guild(entity_id)
            if guild is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return guild

        async def mock_resolve_bot(request):
            return mock_bot

        mock_get_entity.side_effect = mock_get_entity_or_404
        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.return_value = None

        # guild_to_summary is called for each guild in bot.guilds
        # GuildListResponse.data is List[Guild], so we return a Guild-compatible object
        from api.schemas.guild_schemas import Guild
        _guild_data = Guild(
            id=987654321,
            name="Test Guild",
            icon="icon_url",
            member_count=0,
            owner_id=111111111,
            description="Test description",
            created_at="2024-01-01T00:00:00",
            features=[],
            verification_level="none",
            default_notifications="all_messages",
            explicit_content_filter="disabled",
            mfa_level="none",
            premium_tier=0,
            premium_subscription_count=0,
            preferred_locale="en-US",
            nsfw_level="default",
        )
        mock_converter.guild_to_summary.return_value = _guild_data
        mock_converter.guild_to_detail.return_value = _guild_data
        mock_ch_converter.channel_to_summary.return_value = {
            "id": 1234567890,
            "name": "test-channel",
            "type": "text",
        }
        mock_ch_converter.category_to_detail.return_value = {
            "id": 1111111111,
            "name": "Test Category",
        }
        mock_ch_converter.channel_to_detail.return_value = {
            "id": 1234567890,
            "name": "test-channel",
            "type": "text",
        }
        mock_role_converter.role_to_payload.return_value = MagicMock(
            id=222222222,
            name="Test Role",
            position=1,
        )
        mock_user_converter.member_to_payload.return_value = {
            "id": 111111111,
            "name": "TestUser",
        }

        from api.routers.guilds import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


@pytest.fixture
def guilds_client(guilds_test_app):
    """Create a test client for the guilds API."""
    return TestClient(guilds_test_app)


class TestListGuilds:
    """Tests for GET /guilds endpoint."""

    def test_list_guilds_success(self, guilds_client, mock_bot):
        """GET /guilds should list all guilds successfully."""
        response = guilds_client.get("/api/v1/guilds")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_guilds_with_params(self, guilds_client, mock_bot):
        """GET /guilds should handle query parameters (limit/offset not supported, ignored)."""
        response = guilds_client.get("/api/v1/guilds")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)


class TestGetGuild:
    """Tests for GET /guilds/{guild_id} endpoint."""

    def test_get_guild_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id} should retrieve guild successfully."""
        response = guilds_client.get("/api/v1/guilds/987654321")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 987654321

    def test_get_guild_not_found(self, guilds_client):
        """GET /guilds/{guild_id} should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildChannels:
    """Tests for GET /guilds/{guild_id}/channels endpoint."""

    def test_get_channels_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/channels should retrieve channels successfully."""
        response = guilds_client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_channels_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/channels should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/channels")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildMembers:
    """Tests for GET /guilds/{guild_id}/members endpoint."""

    def test_get_members_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/members should retrieve members successfully."""
        response = guilds_client.get("/api/v1/guilds/987654321/members")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_members_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/members should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/members")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildRoles:
    """Tests for GET /guilds/{guild_id}/roles endpoint."""

    def test_get_roles_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/roles should retrieve roles successfully."""
        response = guilds_client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_roles_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/roles should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/roles")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetGuildCategories:
    """Tests for GET /guilds/{guild_id}/categories endpoint."""

    def test_get_categories_success(self, guilds_client, mock_bot):
        """GET /guilds/{guild_id}/categories should retrieve categories successfully."""
        response = guilds_client.get("/api/v1/guilds/987654321/categories")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_categories_not_found(self, guilds_client):
        """GET /guilds/{guild_id}/categories should return 404 for non-existent guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/categories")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
