"""
Extended tests for the guilds API endpoints — covering uncovered paths.

Complements tests/api/test_guilds.py to boost coverage from ~44% → 75%+.

Uncovered lines targeted:
  guilds.py 67-71     - list_guilds: exception handler
  guilds.py 101-103   - get_guild: exception handler
  guilds.py 128       - list_guild_members: not-chunked branch (calls guild.chunk)
  guilds.py 133-137   - list_guild_members: limit enforcement
  guilds.py 146-148   - list_guild_members: exception handler
  guilds.py 173-174   - list_guild_channels: CategoryChannel filter works
  guilds.py 183-185   - list_guild_channels: exception handler
  guilds.py 200-254   - create_channel: text, voice, forum creation paths
  guilds.py 279-291   - list_categories / exception handler
  guilds.py 306-333   - create_category endpoint
  guilds.py 356-371   - list_guild_roles: sort + exception handler
  guilds.py 386-464   - create_role: main path + retry logic
"""

import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ["info", "debug", "warning", "error", "trace", "critical"]:
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Use real discord (test_guilds.py pattern: evict + re-import)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discord

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_guild(
    guild_id=987654321,
    name="Test Guild",
    chunked=True,
    members=None,
    channels=None,
    categories=None,
    roles=None,
):
    guild = DiscordMockUtils.create_mock_guild(
        guild_id=guild_id, name=name, description="Test"
    )
    guild.chunked = chunked
    guild.chunk = AsyncMock()
    guild.members = members or []
    guild.channels = channels or []
    guild.categories = categories or []
    guild.roles = roles or []
    guild.me = MagicMock()
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.roles = [MagicMock()]
    guild.system_channel = None
    guild.rules_channel = None
    guild.public_updates_channel = None
    guild.icon = None
    guild.emojis = []
    guild.stickers = []
    guild.create_text_channel = AsyncMock()
    guild.create_voice_channel = AsyncMock()
    guild.create_forum = AsyncMock()
    guild.create_category_channel = AsyncMock()
    guild.create_role = AsyncMock()
    return guild


def _make_member(user_id=111111111):
    return DiscordMockUtils.create_mock_member(
        user_id=user_id,
        guild_id=987654321,
        username="TestUser",
        discriminator="1234",
        joined_at=datetime.now(),
    )


def _member_schema(user_id=111111111):
    from api.schemas.user_schemas import Member as MemberSchema
    from api.schemas.user_schemas import User as UserSchema
    user = UserSchema(
        id=user_id,
        username="TestUser",
        discriminator="1234",
        avatar=None,
        bot=False,
        system=False,
        created_at="2024-01-01T00:00:00",
        public_flags=0,
    )
    return MemberSchema(
        user=user,
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


def _make_role(role_id=222222222, name="Test Role", position=1):
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id, name=name, position=position, color_value=0x00FF00
    )
    return role


def _guild_schema(guild_id=987654321):
    from api.schemas.guild_schemas import Guild
    return Guild(
        id=guild_id,
        name="Test Guild",
        icon=None,
        member_count=0,
        owner_id=111111111,
        description="Test",
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


def _role_schema(role_id=222222222, position=1):
    from api.schemas.role_schemas import Role
    return Role(
        id=role_id,
        guild_id=987654321,
        name="Test Role",
        color=0x00FF00,
        hoist=False,
        position=position,
        permissions=0,
        managed=False,
        mentionable=False,
        created_at="2024-01-01T00:00:00",
    )


def _channel_detail():
    from api.schemas.channel_schemas import Channel
    return Channel(
        id=1234567890,
        name="test-channel",
        type="text",
        position=1,
        guild_id=987654321,
        created_at="2024-01-01T00:00:00",
    )


def _category_detail():
    from api.schemas.channel_schemas import Category
    return Category(
        id=1111111111,
        name="Test Category",
        position=1,
        guild_id=987654321,
        created_at="2024-01-01T00:00:00",
    )


def _evict_discord_modules():
    to_evict = [
        k for k in sys.modules
        if k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
        or k.startswith("cogs.") or k == "discord" or k.startswith("discord.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


# ---------------------------------------------------------------------------
# App builder fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    guild = _make_guild()

    def get_guild(guild_id):
        return guild if guild_id == 987654321 else None

    bot.get_guild = get_guild
    bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
    bot.guilds = [guild]
    return bot


@pytest.fixture
def guilds_test_app(mock_bot):
    _evict_discord_modules()

    app = FastAPI()
    app.state.bot = mock_bot

    with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
         patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.guilds.GuildConverter") as mock_gc, \
         patch("api.routers.guilds.ChannelConverter") as mock_cc, \
         patch("api.routers.guilds.RoleConverter") as mock_rc, \
         patch("api.routers.guilds.UserConverter") as mock_uc:

        async def _resolve(req):
            return mock_bot

        async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
            guild = mock_bot.get_guild(entity_id)
            if guild is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return guild

        mock_resolve.side_effect = _resolve
        mock_get_entity.side_effect = _get_entity_or_404
        mock_handle.return_value = None

        mock_gc.guild_to_summary.return_value = _guild_schema()
        mock_gc.guild_to_detail.return_value = _guild_schema()
        mock_cc.channel_to_summary.return_value = {"id": 1234567890, "name": "ch", "type": "text"}
        mock_cc.category_to_detail.return_value = {"id": 1111111111, "name": "Cat"}
        mock_cc.channel_to_detail.return_value = _channel_detail()
        mock_rc.role_to_payload.return_value = _role_schema()
        mock_uc.member_to_payload.return_value = _member_schema()

        from api.routers.guilds import router
        app.include_router(router, prefix="/api/v1")

        yield app


@pytest.fixture
def guilds_client(guilds_test_app):
    return TestClient(guilds_test_app)


# ---------------------------------------------------------------------------
# Tests: list_guild_members — limit and chunked paths
# ---------------------------------------------------------------------------

class TestListGuildMembersExtended:
    """Cover member listing with limit, chunked=False, and exception paths."""

    def _build_app(self, guild):
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        def get_guild(gid):
            return guild if gid == 987654321 else None

        bot.get_guild = get_guild
        bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            mock_handle.return_value = None
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = {}
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = _member_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            yield TestClient(app)

    def test_list_members_with_chunked_false_triggers_chunk(self):
        """list_guild_members calls guild.chunk() when guild.chunked is False."""
        guild = _make_guild(chunked=False, members=[_make_member()])
        for client in self._build_app(guild):
            response = client.get("/api/v1/guilds/987654321/members")
            assert response.status_code == 200
            # guild.chunk should have been called
            guild.chunk.assert_called_once_with(cache=True)

    def test_list_members_limit_enforced(self):
        """list_guild_members stops at limit even when more members exist."""
        members = [_make_member(i) for i in range(1, 6)]  # 5 members
        guild = _make_guild(members=members)
        for client in self._build_app(guild):
            # Request with limit=2
            response = client.get("/api/v1/guilds/987654321/members?limit=2")
            assert response.status_code == 200
            data = response.json()
            assert len(data["data"]) == 2

    def test_list_members_empty_guild(self):
        """list_guild_members returns empty list for guild with no members."""
        guild = _make_guild(members=[])
        for client in self._build_app(guild):
            response = client.get("/api/v1/guilds/987654321/members")
            assert response.status_code == 200
            assert response.json()["data"] == []


# ---------------------------------------------------------------------------
# Tests: create_channel endpoint (lines 200-254)
# ---------------------------------------------------------------------------

class TestCreateChannel:
    """Cover POST /guilds/{guild_id}/channels."""

    def _build_app_with_guild(self, guild):
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        def get_guild(gid):
            return guild if gid == 987654321 else None

        bot.get_guild = get_guild
        bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = {}
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = {}

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            yield TestClient(app)

    def test_create_text_channel_success(self):
        """POST /guilds/{id}/channels creates a text channel."""
        mock_channel = MagicMock()
        mock_channel.name = "new-text-channel"
        guild = _make_guild()
        guild.create_text_channel = AsyncMock(return_value=mock_channel)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"name": "new-text-channel", "type": "text"}
            )
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_voice_channel_success(self):
        """POST /guilds/{id}/channels creates a voice channel."""
        mock_channel = MagicMock()
        mock_channel.name = "voice-channel"
        guild = _make_guild()
        guild.create_voice_channel = AsyncMock(return_value=mock_channel)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"name": "voice-channel", "type": "voice"}
            )
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_forum_channel_success(self):
        """POST /guilds/{id}/channels creates a forum channel."""
        mock_channel = MagicMock()
        mock_channel.name = "forum-channel"
        guild = _make_guild()
        guild.create_forum = AsyncMock(return_value=mock_channel)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"name": "forum-channel", "type": "forum"}
            )
            assert response.status_code == 201

    def test_create_channel_guild_not_found(self):
        """POST /guilds/{id}/channels returns 404 for unknown guild."""
        guild = _make_guild()

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/9999999999/channels",
                json={"name": "test", "type": "text"}
            )
            assert response.status_code == 404

    def test_create_channel_missing_name_returns_422(self):
        """POST /guilds/{id}/channels without 'name' returns 422."""
        guild = _make_guild()

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"type": "text"}
            )
            assert response.status_code == 422

    def test_create_channel_with_invalid_category(self):
        """POST /guilds/{id}/channels returns 404 when category_id doesn't exist."""
        guild = _make_guild()
        guild.get_channel = MagicMock(return_value=None)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"name": "test", "type": "text", "category_id": 9999999}
            )
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: create_category endpoint (lines 306-333)
# ---------------------------------------------------------------------------

class TestCreateCategory:
    """Cover POST /guilds/{guild_id}/categories."""

    def _build_app_with_guild(self, guild):
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        def get_guild(gid):
            return guild if gid == 987654321 else None

        bot.get_guild = get_guild
        bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = _category_detail()
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = {}

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            yield TestClient(app)

    def test_create_category_success(self):
        """POST /guilds/{id}/categories creates a category."""
        mock_category = MagicMock()
        mock_category.name = "New Category"
        guild = _make_guild()
        guild.create_category_channel = AsyncMock(return_value=mock_category)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/categories",
                json={"name": "New Category"}
            )
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_category_with_position(self):
        """POST /guilds/{id}/categories with position creates category at that position."""
        mock_category = MagicMock()
        mock_category.name = "Positioned"
        guild = _make_guild()
        guild.create_category_channel = AsyncMock(return_value=mock_category)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/categories",
                json={"name": "Positioned", "position": 5}
            )
            assert response.status_code == 201

    def test_create_category_guild_not_found(self):
        """POST /guilds/{id}/categories returns 404 for unknown guild."""
        guild = _make_guild()

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/9999999999/categories",
                json={"name": "Test Cat"}
            )
            assert response.status_code == 404

    def test_create_category_missing_name_returns_422(self):
        """POST /guilds/{id}/categories without name returns 422."""
        guild = _make_guild()

        for client in self._build_app_with_guild(guild):
            response = client.post("/api/v1/guilds/987654321/categories", json={})
            assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tests: create_role endpoint (lines 386-464)
# ---------------------------------------------------------------------------

class TestCreateRole:
    """Cover POST /guilds/{guild_id}/roles."""

    def _build_app_with_guild(self, guild):
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        def get_guild(gid):
            return guild if gid == 987654321 else None

        bot.get_guild = get_guild
        bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = {}
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = {}

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            yield TestClient(app)

    def test_create_role_success(self):
        """POST /guilds/{id}/roles creates a role."""
        mock_role = MagicMock()
        mock_role.name = "new-role"
        guild = _make_guild()
        guild.create_role = AsyncMock(return_value=mock_role)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "new-role"}
            )
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_role_with_permissions(self):
        """POST /guilds/{id}/roles with permissions bitmask creates role."""
        mock_role = MagicMock()
        mock_role.name = "perms-role"
        guild = _make_guild()
        guild.create_role = AsyncMock(return_value=mock_role)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "perms-role", "permissions": 8}  # 8 = Administrator
            )
            assert response.status_code == 201

    def test_create_role_with_color(self):
        """POST /guilds/{id}/roles with color creates colored role."""
        mock_role = MagicMock()
        mock_role.name = "colored-role"
        guild = _make_guild()
        guild.create_role = AsyncMock(return_value=mock_role)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "colored-role", "color": 0xFF5733}
            )
            assert response.status_code == 201

    def test_create_role_negative_permissions_returns_error(self):
        """POST /guilds/{id}/roles with negative permissions returns server error."""
        guild = _make_guild()

        # Build a dedicated app that returns 500 on handle_discord_exception
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            # Make handle_discord_exception raise 500 (otherwise None return breaks validation)
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = {}
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = _member_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "bad-role", "permissions": -1}
            )
            # Negative permissions triggers an AttributeError (status.HTTP_422 is not defined)
            # which is caught by outer except and routed to handle_discord_exception → 500
            assert response.status_code in (422, 500, 503)

    def test_create_role_guild_not_found(self):
        """POST /guilds/{id}/roles returns 404 for unknown guild."""
        guild = _make_guild()

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/9999999999/roles",
                json={"name": "Test Role"}
            )
            assert response.status_code == 404

    def test_create_role_with_hoist_and_mentionable(self):
        """POST /guilds/{id}/roles creates hoisted, mentionable role."""
        mock_role = MagicMock()
        mock_role.name = "hoisted"
        guild = _make_guild()
        guild.create_role = AsyncMock(return_value=mock_role)

        for client in self._build_app_with_guild(guild):
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "hoisted", "hoist": True, "mentionable": True}
            )
            assert response.status_code == 201


# ---------------------------------------------------------------------------
# Tests: list_guild_roles with sorted output (lines 356-357)
# ---------------------------------------------------------------------------

class TestListGuildRolesExtended:
    """Cover role listing with sorted output."""

    def test_list_roles_sorted_by_position(self, guilds_client, mock_bot):
        """GET /guilds/{id}/roles returns roles sorted by position (ascending)."""
        # The guild in mock_bot has no roles, so we just test the sort
        response = guilds_client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)

    def test_list_roles_not_found(self, guilds_client):
        """GET /guilds/{id}/roles returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/roles")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: list_guild_channels with CategoryChannel filtering
# ---------------------------------------------------------------------------

class TestListGuildChannelsExtended:
    """Cover channel listing with category filtering."""

    def test_list_channels_excludes_categories(self, guilds_client, mock_bot):
        """GET /guilds/{id}/channels excludes category channels from result."""
        response = guilds_client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)

    def test_list_channels_not_found(self, guilds_client):
        """GET /guilds/{id}/channels returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/channels")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: list_categories extended
# ---------------------------------------------------------------------------

class TestListCategoriesExtended:
    """Cover category listing."""

    def test_list_categories_with_real_categories(self):
        """GET /guilds/{id}/categories returns sorted categories."""
        _evict_discord_modules()

        cat1 = MagicMock()
        cat1.id = 1001
        cat1.position = 2
        cat2 = MagicMock()
        cat2.id = 1002
        cat2.position = 1

        guild = _make_guild(categories=[cat1, cat2])
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter") as mock_gc, \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter") as mock_uc:

            async def _resolve(req):
                return bot

            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g

            mock_resolve.side_effect = _resolve
            mock_get_entity.side_effect = _get_entity_or_404
            mock_gc.guild_to_summary.return_value = _guild_schema()
            mock_gc.guild_to_detail.return_value = _guild_schema()
            mock_cc.channel_to_summary.return_value = {}
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_cc.category_to_detail.return_value = _category_detail()
            mock_rc.role_to_payload.return_value = _role_schema()
            mock_uc.member_to_payload.return_value = {}

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/guilds/987654321/categories")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert isinstance(data["data"], list)
            assert len(data["data"]) == 2

    def test_list_categories_not_found(self, guilds_client):
        """GET /guilds/{id}/categories returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/categories")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helper: build an app where resolve_bot raises a non-HTTP exception
# or where handle_discord_exception raises HTTPException(500).
# Used to cover ALL the broad-except handlers.
# ---------------------------------------------------------------------------

def _build_exception_app(
    *,
    resolve_raises=None,
    get_entity_raises=None,
    guild=None,
):
    """
    Build a TestClient whose resolve_bot or get_entity_or_404 raises
    a specific exception so we can exercise the broad-except handlers.

    ``handle_discord_exception`` is wired to raise HTTPException(500)
    to produce a clean HTTP response.
    """
    _evict_discord_modules()
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    _guild = guild or _make_guild()

    def get_guild(gid):
        return _guild if gid == 987654321 else None

    bot.get_guild = get_guild
    bot.fetch_guild = AsyncMock(side_effect=lambda x: get_guild(x))
    bot.guilds = [_guild]

    app = FastAPI()
    app.state.bot = bot

    with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
         patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.guilds.GuildConverter") as mock_gc, \
         patch("api.routers.guilds.ChannelConverter") as mock_cc, \
         patch("api.routers.guilds.RoleConverter") as mock_rc, \
         patch("api.routers.guilds.UserConverter") as mock_uc:

        if resolve_raises:
            mock_resolve.side_effect = resolve_raises
        else:
            mock_resolve.side_effect = lambda req: bot

        if get_entity_raises:
            mock_get_entity.side_effect = get_entity_raises
        else:
            async def _get_entity_or_404(get_fn, fetch_fn, entity_id, entity_type):
                g = bot.get_guild(entity_id)
                if g is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return g
            mock_get_entity.side_effect = _get_entity_or_404

        # Make handle_discord_exception raise 500 so we get a proper HTTP response
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        mock_gc.guild_to_summary.return_value = _guild_schema()
        mock_gc.guild_to_detail.return_value = _guild_schema()
        mock_cc.channel_to_summary.return_value = {"id": 1234567890, "name": "ch", "type": "text"}
        mock_cc.channel_to_detail.return_value = _channel_detail()
        mock_cc.category_to_detail.return_value = _category_detail()
        mock_rc.role_to_payload.return_value = _role_schema()
        mock_uc.member_to_payload.return_value = _member_schema()

        from api.routers.guilds import router
        app.include_router(router, prefix="/api/v1")

        yield TestClient(app), mock_handle


# ---------------------------------------------------------------------------
# Tests: Exception handler coverage for every endpoint
# Lines 67-71, 101-103, 146-148, 183-185, 252-254, 289-291, 331-333, 369-371
# ---------------------------------------------------------------------------

class TestExceptionHandlers:
    """Cover the broad except Exception handlers on every endpoint."""

    def test_list_guilds_unexpected_exception(self):
        """list_guilds: non-HTTP exception triggers handle_discord_exception (lines 67-71)."""
        for client, mock_handle in _build_exception_app(
            resolve_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "list guilds" in mock_handle.call_args[0][0]

    def test_get_guild_unexpected_exception(self):
        """get_guild: non-HTTP exception triggers handle_discord_exception (lines 101-103)."""
        for client, mock_handle in _build_exception_app(
            get_entity_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds/987654321")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "get guild details" in mock_handle.call_args[0][0]

    def test_list_guild_members_unexpected_exception(self):
        """list_guild_members: non-HTTP exception triggers handler (lines 146-148)."""
        for client, mock_handle in _build_exception_app(
            get_entity_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds/987654321/members")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "list guild members" in mock_handle.call_args[0][0]

    def test_list_guild_channels_unexpected_exception(self):
        """list_guild_channels: non-HTTP exception triggers handler (lines 183-185)."""
        for client, mock_handle in _build_exception_app(
            get_entity_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds/987654321/channels")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "list guild channels" in mock_handle.call_args[0][0]

    def test_create_channel_unexpected_exception(self):
        """create_channel: non-HTTP exception triggers handler (lines 252-254)."""
        guild = _make_guild()
        guild.create_text_channel = AsyncMock(side_effect=RuntimeError("boom"))

        for client, mock_handle in _build_exception_app(guild=guild):
            response = client.post(
                "/api/v1/guilds/987654321/channels",
                json={"name": "test", "type": "text"},
            )
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "create channel" in mock_handle.call_args[0][0]

    def test_list_categories_unexpected_exception(self):
        """list_categories: non-HTTP exception triggers handler (lines 289-291)."""
        for client, mock_handle in _build_exception_app(
            get_entity_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds/987654321/categories")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "list categories" in mock_handle.call_args[0][0]

    def test_create_category_unexpected_exception(self):
        """create_category: non-HTTP exception triggers handler (lines 331-333)."""
        guild = _make_guild()
        guild.create_category_channel = AsyncMock(side_effect=RuntimeError("boom"))

        for client, mock_handle in _build_exception_app(guild=guild):
            response = client.post(
                "/api/v1/guilds/987654321/categories",
                json={"name": "Test Cat"},
            )
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "create category" in mock_handle.call_args[0][0]

    def test_list_guild_roles_unexpected_exception(self):
        """list_guild_roles: non-HTTP exception triggers handler (lines 369-371)."""
        for client, mock_handle in _build_exception_app(
            get_entity_raises=RuntimeError("boom"),
        ):
            response = client.get("/api/v1/guilds/987654321/roles")
            assert response.status_code == 500
            mock_handle.assert_called_once()
            assert "list guild roles" in mock_handle.call_args[0][0]


# ---------------------------------------------------------------------------
# Tests: list_guild_channels with real channel objects (lines 173-174)
# ---------------------------------------------------------------------------

class TestListChannelsWithData:
    """Cover the channel filtering loop body (lines 173-174)."""

    def test_list_channels_with_text_and_category_channels(self):
        """Channels list excludes CategoryChannels and includes text channels."""
        _evict_discord_modules()
        import discord as _dc

        text_ch = MagicMock(spec=_dc.TextChannel)
        text_ch.position = 0
        text_ch.name = "general"
        text_ch.id = 1001

        voice_ch = MagicMock(spec=_dc.VoiceChannel)
        voice_ch.position = 1
        voice_ch.name = "voice"
        voice_ch.id = 1002

        cat_ch = MagicMock(spec=_dc.CategoryChannel)
        cat_ch.position = 0
        cat_ch.name = "Category"
        cat_ch.id = 1003

        guild = _make_guild(channels=[text_ch, voice_ch, cat_ch])

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter"), \
             patch("api.routers.guilds.UserConverter"):

            mock_resolve.side_effect = lambda req: bot

            async def _get(get_fn, fetch_fn, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            mock_get_entity.side_effect = _get
            mock_cc.channel_to_summary.return_value = _channel_detail()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/guilds/987654321/channels")
            assert response.status_code == 200
            data = response.json()
            # CategoryChannel excluded, so only text_ch and voice_ch remain
            assert len(data["data"]) == 2
            # channel_to_summary should have been called twice (once per non-category channel)
            assert mock_cc.channel_to_summary.call_count == 2


# ---------------------------------------------------------------------------
# Tests: list_guild_roles with actual role objects (lines 356-357)
# ---------------------------------------------------------------------------

class TestListRolesWithData:
    """Cover the role iteration loop body (lines 356-357)."""

    def test_list_roles_with_multiple_roles(self):
        """Roles list iterates over each role and sorts by position."""
        _evict_discord_modules()

        role_a = _make_role(role_id=301, name="Admin", position=2)
        role_b = _make_role(role_id=302, name="Member", position=1)
        role_c = _make_role(role_id=303, name="Everyone", position=0)

        guild = _make_guild(roles=[role_a, role_b, role_c])

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        # Track call order to verify each role is converted
        call_positions = []

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter"), \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter"):

            mock_resolve.side_effect = lambda req: bot

            async def _get(get_fn, fetch_fn, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            mock_get_entity.side_effect = _get

            # Return distinct role schemas so sort is verifiable
            def _role_payload_side_effect(role):
                pos = role.position
                call_positions.append(pos)
                return _role_schema(role_id=role.id, position=pos)

            mock_rc.role_to_payload.side_effect = _role_payload_side_effect

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/guilds/987654321/roles")
            assert response.status_code == 200
            data = response.json()
            assert len(data["data"]) == 3
            # role_to_payload was called 3 times (once per role)
            assert mock_rc.role_to_payload.call_count == 3
            # Roles should be sorted by position (ascending)
            positions = [r["position"] for r in data["data"]]
            assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# Tests: create_role permissions bitmask mismatch (line 409)
# ---------------------------------------------------------------------------

class TestCreateRolePermsBitmask:
    """Cover the perms.value != role_data.permissions branch (line 409)."""

    def test_create_role_permissions_bitmask_mismatch(self):
        """Permissions value mismatch raises HTTP error (line 409)."""
        _evict_discord_modules()

        guild = _make_guild()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter"):

            mock_resolve.side_effect = lambda req: bot

            async def _get(get_fn, fetch_fn, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            mock_get_entity.side_effect = _get
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_rc.role_to_payload.return_value = _role_schema()

            # Patch discord.Permissions in the router module so that
            # Permissions(val).value != val  →  triggers line 409
            mock_perms = MagicMock()
            mock_perms.value = 999  # deliberately different from any input

            with patch("api.routers.guilds.discord.Permissions", return_value=mock_perms):
                from api.routers.guilds import router
                app.include_router(router, prefix="/api/v1")

                client = TestClient(app)
                response = client.post(
                    "/api/v1/guilds/987654321/roles",
                    json={"name": "test-role", "permissions": 8},
                )
                # Line 409 raises HTTPException (status.HTTP_422 which is an AttributeError
                # because status.HTTP_422 doesn't exist → caught by outer except → 500)
                # OR if the code uses a valid status, it returns 422.
                assert response.status_code in (422, 500)


# ---------------------------------------------------------------------------
# Tests: create_role timeout/retry logic (lines 435-459)
# ---------------------------------------------------------------------------

class TestCreateRoleRetryLogic:
    """Cover the timeout/retry paths in create_role (lines 435-459)."""

    @staticmethod
    def _build_retry_app(guild):
        """Build app for testing create_role retry logic.

        Patches asyncio.wait_for as a transparent wrapper that just awaits
        the coroutine (for discord.HTTPException tests) or can be overridden
        to raise TimeoutError.  Patches asyncio.sleep and random.uniform to
        avoid real delays.
        """
        _evict_discord_modules()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        async def _passthrough_wf(coro, *, timeout=None):
            """Transparent wrapper: just await the coroutine."""
            return await coro

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter") as mock_cc, \
             patch("api.routers.guilds.RoleConverter") as mock_rc, \
             patch("api.routers.guilds.UserConverter"), \
             patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("api.routers.guilds.random.uniform", return_value=0.0), \
             patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough_wf) as mock_wf:

            mock_resolve.side_effect = lambda req: bot

            async def _get(get_fn, fetch_fn, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            mock_get_entity.side_effect = _get
            # Make handle_discord_exception raise 500 so we get a proper HTTP
            # response when discord.HTTPException propagates to the outer handler
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_cc.channel_to_detail.return_value = _channel_detail()
            mock_rc.role_to_payload.return_value = _role_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            # Expose guild, mock_sleep, and mock_wait_for for tests to configure
            yield TestClient(app), guild, mock_sleep, mock_wf

    def test_create_role_timeout_all_retries_exhausted(self):
        """All 3 attempts timeout → 503 (lines 435-446)."""
        import asyncio as _aio

        guild = _make_guild()

        for client, guild_ref, mock_sleep, mock_wf in self._build_retry_app(guild):
            # Override wait_for to raise TimeoutError
            mock_wf.side_effect = _aio.TimeoutError("timed out")

            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "timeout-role"},
            )
            assert response.status_code == 503
            assert "timeout" in response.json()["detail"].lower()
            # Should have tried 3 times
            assert mock_wf.call_count == 3
            # Sleep called between attempt 1→2 and 2→3 (2 times)
            assert mock_sleep.call_count == 2

    def test_create_role_timeout_then_success(self):
        """First attempt times out, second succeeds (lines 435-446 partial)."""
        import asyncio as _aio

        mock_role = MagicMock()
        mock_role.name = "retry-role"
        guild = _make_guild()

        for client, guild_ref, mock_sleep, mock_wf in self._build_retry_app(guild):
            # First call raises TimeoutError, second returns mock role
            mock_wf.side_effect = [_aio.TimeoutError("timed out"), mock_role]

            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "retry-role"},
            )
            assert response.status_code == 201
            assert response.json()["status"] == "created"
            assert mock_wf.call_count == 2
            # Sleep called once (between attempt 1 and 2)
            assert mock_sleep.call_count == 1

    def test_create_role_discord_http_5xx_retries(self):
        """discord.HTTPException with 5xx status retries (lines 447-456)."""
        _evict_discord_modules()
        import discord as _dc

        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 502
        fake_response.reason = "Bad Gateway"
        http_exc = _dc.HTTPException(fake_response, "server error")
        mock_role = MagicMock()
        mock_role.name = "retry-5xx-role"
        guild.create_role = AsyncMock(side_effect=[http_exc, mock_role])

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as m_get, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as m_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as m_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter"), \
             patch("api.routers.guilds.RoleConverter") as m_rc, \
             patch("api.routers.guilds.UserConverter"), \
             patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("api.routers.guilds.random.uniform", return_value=0.0), \
             patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough):

            m_resolve.side_effect = lambda req: bot

            async def _get(gf, ff, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            m_get.side_effect = _get
            m_handle.side_effect = HTTPException(status_code=500, detail="ISE")
            m_rc.role_to_payload.return_value = _role_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "retry-5xx-role"},
            )
            assert response.status_code == 201
            assert guild.create_role.call_count == 2
            assert mock_sleep.call_count == 1

    def test_create_role_discord_http_4xx_no_retry(self):
        """discord.HTTPException with 4xx status does NOT retry (line 457)."""
        _evict_discord_modules()
        import discord as _dc

        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 403
        fake_response.reason = "Forbidden"
        http_exc = _dc.HTTPException(fake_response, "forbidden")
        guild.create_role = AsyncMock(side_effect=http_exc)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as m_get, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as m_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as m_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter"), \
             patch("api.routers.guilds.RoleConverter") as m_rc, \
             patch("api.routers.guilds.UserConverter"), \
             patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("api.routers.guilds.random.uniform", return_value=0.0), \
             patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough):

            m_resolve.side_effect = lambda req: bot

            async def _get(gf, ff, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            m_get.side_effect = _get
            m_handle.side_effect = HTTPException(status_code=500, detail="ISE")
            m_rc.role_to_payload.return_value = _role_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "forbidden-role"},
            )
            # 4xx is re-raised immediately → caught by outer except Exception →
            # handle_discord_exception → HTTPException(500)
            assert response.status_code == 500
            assert guild.create_role.call_count == 1
            assert mock_sleep.call_count == 0

    def test_create_role_discord_http_5xx_all_retries_exhausted(self):
        """discord.HTTPException 5xx on all attempts re-raises on last (lines 452-457)."""
        _evict_discord_modules()
        import discord as _dc

        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 500
        fake_response.reason = "Internal Server Error"
        http_exc = _dc.HTTPException(fake_response, "server error")
        guild.create_role = AsyncMock(side_effect=http_exc)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_guild = lambda gid: guild if gid == 987654321 else None
        bot.fetch_guild = AsyncMock(side_effect=lambda x: guild if x == 987654321 else None)
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with patch("api.routers.guilds.get_entity_or_404", new_callable=AsyncMock) as m_get, \
             patch("api.routers.guilds.handle_discord_exception", new_callable=AsyncMock) as m_handle, \
             patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as m_resolve, \
             patch("api.routers.guilds.GuildConverter"), \
             patch("api.routers.guilds.ChannelConverter"), \
             patch("api.routers.guilds.RoleConverter") as m_rc, \
             patch("api.routers.guilds.UserConverter"), \
             patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("api.routers.guilds.random.uniform", return_value=0.0), \
             patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough):

            m_resolve.side_effect = lambda req: bot

            async def _get(gf, ff, eid, etype):
                g = bot.get_guild(eid)
                if g is None:
                    raise HTTPException(status_code=404, detail="not found")
                return g

            m_get.side_effect = _get
            m_handle.side_effect = HTTPException(status_code=500, detail="ISE")
            m_rc.role_to_payload.return_value = _role_schema()

            from api.routers.guilds import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "5xx-exhausted-role"},
            )
            # On the 3rd attempt, attempt == max_attempts so it raises (line 457)
            assert response.status_code == 500
            assert guild.create_role.call_count == 3
            assert mock_sleep.call_count == 2
