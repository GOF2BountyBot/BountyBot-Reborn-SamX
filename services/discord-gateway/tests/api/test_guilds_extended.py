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

Fidelity notes
--------------
No patches on ``resolve_bot``, ``get_entity_or_404``, ``handle_discord_exception``
or ``GuildConverter``/``ChannelConverter``/``RoleConverter``/``UserConverter``
anywhere in this file: every app is built via ``_build_client()``, which sets
a real ``spec=commands.Bot`` bot on ``app.state`` and mounts the router
unpatched. Real per-endpoint exceptions are driven by making the actual
Discord-facing call (``bot.get_guild``, ``guild.create_role``, ...) raise —
never by re-implementing the router's own error handling. The
``asyncio.wait_for``/``asyncio.sleep``/``random.uniform`` patches in
``TestCreateRoleRetryLogic`` remain: they are a genuine timing boundary
(the real retry loop sleeps with exponential backoff) and the tests assert
on the router's own real retry loop, not a reimplementation of it.
"""

import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

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
    guild = DiscordMockUtils.create_mock_guild(guild_id=guild_id, name=name, description="Test")
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


def _make_member(user_id=111111111, guild=None):
    member = DiscordMockUtils.create_mock_member(
        user_id=user_id,
        guild=guild,
        guild_id=guild.id if guild else 987654321,
        username="TestUser",
        discriminator="1234",
        joined_at=datetime(2024, 1, 1),
    )
    member.__class__ = discord.Member
    # Real discord.Member delegates undefined attribute access (avatar,
    # created_at, public_flags, bot, system) to its underlying User via
    # __getattr__; create_mock_member only sets these on member.user.
    # UserConverter.user_to_payload(member) reads them straight off `member`,
    # so mirror that delegation here to match real discord.py's behaviour.
    member.avatar = member.user.avatar
    member.created_at = member.user.created_at
    member.public_flags = member.user.public_flags
    member.bot = member.user.bot
    member.system = member.user.system
    return member


def _make_role(role_id=222222222, name="Test Role", position=1, guild=None):
    role = DiscordMockUtils.create_mock_role(
        role_id=role_id, name=name, position=position, color_value=0x00FF00, guild=guild
    )
    role.__class__ = discord.Role
    return role


def _make_channel(channel_id=1234567890, name="test-channel", position=0, guild=None):
    channel = DiscordMockUtils.create_mock_text_channel(
        channel_id=channel_id, name=name, position=position, guild=guild, guild_id=guild.id if guild else 987654321
    )
    channel.__class__ = discord.TextChannel
    return channel


def _make_category(channel_id=1111111111, name="Test Category", position=0, guild=None):
    category = DiscordMockUtils.create_mock_category_channel(
        channel_id=channel_id, name=name, position=position, guild=guild, guild_id=guild.id if guild else 987654321
    )
    category.__class__ = discord.CategoryChannel
    return category


def _build_bot(guild, user_id=123456789):
    """Build a real ``spec=commands.Bot`` mock whose ``fetch_guild`` raises a
    real ``discord.NotFound`` on cache miss, matching production's
    cache -> fetch -> 404 behaviour."""
    bot = DiscordMockUtils.create_mock_bot(user_id=user_id, username="TestBot")

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


def _build_client(guild=None, bot=None):
    """Build a TestClient with the guilds router mounted against a real bot
    state — no helper/converter patches."""
    if bot is None:
        bot = _build_bot(guild or _make_guild())

    app = FastAPI()
    app.state.bot = bot

    from api.routers.guilds import router

    app.include_router(router, prefix="/api/v1")

    return TestClient(app), bot


# ---------------------------------------------------------------------------
# Module-level fixtures: a guild with one category, one channel, one role,
# one member so the "list X" tests exercise genuine real-converter output.
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    guild = _make_guild()
    category = _make_category(guild=guild)
    channel = _make_channel(guild=guild)
    role = _make_role(guild=guild)
    member = _make_member(guild=guild)
    guild.categories = [category]
    guild.channels = [channel, category]
    guild.roles = [role]
    guild.members = [member]
    return _build_bot(guild)


@pytest.fixture
def guilds_client(mock_bot):
    app = FastAPI()
    app.state.bot = mock_bot

    from api.routers.guilds import router

    app.include_router(router, prefix="/api/v1")

    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: list_guild_members — limit and chunked paths
# ---------------------------------------------------------------------------


class TestListGuildMembersExtended:
    """Cover member listing with limit, chunked=False, and exception paths."""

    def test_list_members_with_chunked_false_triggers_chunk(self):
        """list_guild_members calls guild.chunk() when guild.chunked is False."""
        guild = _make_guild(chunked=False)
        guild.members = [_make_member(guild=guild)]
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/members")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        guild.chunk.assert_called_once_with(cache=True)

    def test_list_members_limit_enforced(self):
        """list_guild_members stops at limit even when more members exist."""
        guild = _make_guild()
        guild.members = [_make_member(i, guild=guild) for i in range(1, 6)]  # 5 members
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/members?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["user"]["id"] == 1
        assert data["data"][1]["user"]["id"] == 2

    def test_list_members_empty_guild(self):
        """list_guild_members returns empty list for guild with no members."""
        guild = _make_guild(members=[])
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/members")
        assert response.status_code == 200
        assert response.json()["data"] == []


# ---------------------------------------------------------------------------
# Tests: create_channel endpoint (lines 200-254)
# ---------------------------------------------------------------------------


class TestCreateChannel:
    """Cover POST /guilds/{guild_id}/channels."""

    def test_create_text_channel_success(self):
        """POST /guilds/{id}/channels creates a text channel and real-serializes it."""
        guild = _make_guild()
        new_channel = _make_channel(channel_id=5001, name="new-text-channel", guild=guild)
        guild.create_text_channel = AsyncMock(return_value=new_channel)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/channels", json={"name": "new-text-channel", "type": "text"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 5001
        assert data["data"]["name"] == "new-text-channel"
        assert data["data"]["type"] == "text"

    def test_create_voice_channel_success(self):
        """POST /guilds/{id}/channels creates a voice channel and real-serializes it."""
        guild = _make_guild()
        new_channel = DiscordMockUtils.create_mock_voice_channel(
            channel_id=5002, name="voice-channel", guild=guild, guild_id=guild.id
        )
        new_channel.__class__ = discord.VoiceChannel
        guild.create_voice_channel = AsyncMock(return_value=new_channel)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/channels", json={"name": "voice-channel", "type": "voice"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 5002
        assert data["data"]["type"] == "voice"

    def test_create_forum_channel_success(self):
        """POST /guilds/{id}/channels creates a forum channel and real-serializes it."""
        guild = _make_guild()
        new_channel = DiscordMockUtils.create_mock_forum_channel(
            channel_id=5003, name="forum-channel", guild=guild, guild_id=guild.id
        )
        new_channel.__class__ = discord.ForumChannel
        guild.create_forum = AsyncMock(return_value=new_channel)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/channels", json={"name": "forum-channel", "type": "forum"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 5003
        assert data["data"]["type"] == "forum"

    def test_create_channel_guild_not_found(self):
        """POST /guilds/{id}/channels returns 404 for unknown guild."""
        client, _bot = _build_client(_make_guild())
        response = client.post("/api/v1/guilds/9999999999/channels", json={"name": "test", "type": "text"})
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_create_channel_missing_name_returns_422(self):
        """POST /guilds/{id}/channels without 'name' returns 422."""
        client, _bot = _build_client(_make_guild())
        response = client.post("/api/v1/guilds/987654321/channels", json={"type": "text"})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_create_channel_with_invalid_category(self):
        """POST /guilds/{id}/channels returns 404 when category_id doesn't exist."""
        guild = _make_guild()
        guild.get_channel = MagicMock(return_value=None)
        client, _bot = _build_client(guild)

        response = client.post(
            "/api/v1/guilds/987654321/channels", json={"name": "test", "type": "text", "category_id": 9999999}
        )
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: create_category endpoint (lines 306-333)
# ---------------------------------------------------------------------------


class TestCreateCategory:
    """Cover POST /guilds/{guild_id}/categories."""

    def test_create_category_success(self):
        """POST /guilds/{id}/categories creates a category and real-serializes it."""
        guild = _make_guild()
        new_category = _make_category(channel_id=6001, name="New Category", guild=guild)
        guild.create_category_channel = AsyncMock(return_value=new_category)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/categories", json={"name": "New Category"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 6001
        assert data["data"]["name"] == "New Category"

    def test_create_category_with_position(self):
        """POST /guilds/{id}/categories with position creates category at that position."""
        guild = _make_guild()
        new_category = _make_category(channel_id=6002, name="Positioned", position=5, guild=guild)
        guild.create_category_channel = AsyncMock(return_value=new_category)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/categories", json={"name": "Positioned", "position": 5})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["position"] == 5
        guild.create_category_channel.assert_awaited_once_with(name="Positioned", position=5)

    def test_create_category_guild_not_found(self):
        """POST /guilds/{id}/categories returns 404 for unknown guild."""
        client, _bot = _build_client(_make_guild())
        response = client.post("/api/v1/guilds/9999999999/categories", json={"name": "Test Cat"})
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_create_category_missing_name_returns_422(self):
        """POST /guilds/{id}/categories without name returns 422."""
        client, _bot = _build_client(_make_guild())
        response = client.post("/api/v1/guilds/987654321/categories", json={})
        assert response.status_code == 422
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: create_role endpoint (lines 386-464)
# ---------------------------------------------------------------------------


class TestCreateRole:
    """Cover POST /guilds/{guild_id}/roles."""

    def test_create_role_success(self):
        """POST /guilds/{id}/roles creates a role and real-serializes it."""
        guild = _make_guild()
        new_role = _make_role(role_id=7001, name="new-role", guild=guild)
        guild.create_role = AsyncMock(return_value=new_role)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/roles", json={"name": "new-role"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 7001
        assert data["data"]["name"] == "new-role"

    def test_create_role_with_permissions(self):
        """POST /guilds/{id}/roles with permissions bitmask creates role for real."""
        guild = _make_guild()
        new_role = _make_role(role_id=7002, name="perms-role", guild=guild)
        guild.create_role = AsyncMock(return_value=new_role)
        client, _bot = _build_client(guild)

        response = client.post(
            "/api/v1/guilds/987654321/roles",
            json={"name": "perms-role", "permissions": 8},  # 8 = Administrator
        )
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        # Real discord.Permissions(8) was constructed and passed through.
        kwargs = guild.create_role.call_args.kwargs
        assert isinstance(kwargs["permissions"], discord.Permissions)
        assert kwargs["permissions"].administrator is True

    def test_create_role_with_color(self):
        """POST /guilds/{id}/roles with color creates colored role for real."""
        guild = _make_guild()
        new_role = _make_role(role_id=7003, name="colored-role", guild=guild)
        guild.create_role = AsyncMock(return_value=new_role)
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/roles", json={"name": "colored-role", "color": 0xFF5733})
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        kwargs = guild.create_role.call_args.kwargs
        assert isinstance(kwargs["color"], discord.Color)
        assert kwargs["color"].value == 0xFF5733

    def test_create_role_negative_permissions_returns_422(self):
        """POST /guilds/{id}/roles with negative permissions returns 422.

        History (TRUEUP-P2, fixed): this branch used the nonexistent
        ``status.HTTP_422`` attribute, so it raised ``AttributeError`` and the
        outer generic handler turned the intended 422 into a 500 — see
        FOLLOWUPS.md R-gw-api-0. Now uses ``HTTP_422_UNPROCESSABLE_CONTENT``.
        """
        guild = _make_guild()
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/roles", json={"name": "bad-role", "permissions": -1})
        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid permissions bitmask"

    def test_create_role_guild_not_found(self):
        """POST /guilds/{id}/roles returns 404 for unknown guild."""
        client, _bot = _build_client(_make_guild())
        response = client.post("/api/v1/guilds/9999999999/roles", json={"name": "Test Role"})
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_create_role_with_hoist_and_mentionable(self):
        """POST /guilds/{id}/roles creates hoisted, mentionable role for real."""
        guild = _make_guild()
        new_role = _make_role(role_id=7004, name="hoisted", guild=guild)
        guild.create_role = AsyncMock(return_value=new_role)
        client, _bot = _build_client(guild)

        response = client.post(
            "/api/v1/guilds/987654321/roles", json={"name": "hoisted", "hoist": True, "mentionable": True}
        )
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        kwargs = guild.create_role.call_args.kwargs
        assert kwargs["name"] == "hoisted"
        assert kwargs["hoist"] is True
        assert kwargs["mentionable"] is True


# ---------------------------------------------------------------------------
# Tests: list_guild_roles with sorted output
# ---------------------------------------------------------------------------


class TestListGuildRolesExtended:
    """Cover role listing with sorted output."""

    def test_list_roles_sorted_by_position(self, guilds_client, mock_bot):
        """GET /guilds/{id}/roles returns real-serialized roles sorted by position."""
        response = guilds_client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == 222222222
        assert data["data"][0]["name"] == "Test Role"

    def test_list_roles_not_found(self, guilds_client):
        """GET /guilds/{id}/roles returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/roles")
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: list_guild_channels with CategoryChannel filtering
# ---------------------------------------------------------------------------


class TestListGuildChannelsExtended:
    """Cover channel listing with category filtering."""

    def test_list_channels_excludes_categories(self, guilds_client, mock_bot):
        """GET /guilds/{id}/channels excludes category channels from the real result."""
        response = guilds_client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        # mock_bot's guild has 1 text channel + 1 category; only the text channel survives.
        assert [c["id"] for c in data["data"]] == [1234567890]
        assert data["data"][0]["type"] == "text"

    def test_list_channels_not_found(self, guilds_client):
        """GET /guilds/{id}/channels returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/channels")
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: list_categories extended
# ---------------------------------------------------------------------------


class TestListCategoriesExtended:
    """Cover category listing."""

    def test_list_categories_with_real_categories(self):
        """GET /guilds/{id}/categories returns real-serialized categories sorted by position."""
        guild = _make_guild()
        cat1 = _make_category(channel_id=1001, name="Second", position=2, guild=guild)
        cat2 = _make_category(channel_id=1002, name="First", position=1, guild=guild)
        guild.categories = [cat1, cat2]
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/categories")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        # Sorted by position ascending: cat2 (1) before cat1 (2).
        assert [c["id"] for c in data["data"]] == [1002, 1001]
        assert [c["name"] for c in data["data"]] == ["First", "Second"]

    def test_list_categories_not_found(self, guilds_client):
        """GET /guilds/{id}/categories returns 404 for unknown guild."""
        response = guilds_client.get("/api/v1/guilds/9999999999/categories")
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: Exception handler coverage for every endpoint
# Lines 67-71, 101-103, 146-148, 183-185, 252-254, 289-291, 331-333, 369-371
#
# Every case below drives a REAL non-Discord exception from an actual
# Discord-facing call (bot.get_guild / guild.create_x) and lets the real
# ``handle_discord_exception`` map it — no re-implementation of the router's
# error handling, and assertions check the real mapped detail text rather
# than "was the mock called".
# ---------------------------------------------------------------------------


class TestExceptionHandlers:
    """Cover the broad except Exception handlers on every endpoint."""

    def test_list_guilds_unexpected_exception(self):
        """list_guilds: non-HTTP exception triggers handle_discord_exception (lines 67-71).

        ``resolve_bot`` is patched here only because it's the one call that
        happens before any Discord-facing mock exists to fail instead.
        """
        client, _bot = _build_client(_make_guild())
        with patch("api.routers.guilds.resolve_bot", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.side_effect = RuntimeError("boom")
            response = client.get("/api/v1/guilds")
        assert response.status_code == 500
        assert "list guilds" in response.json()["detail"].lower()
        assert "boom" in response.json()["detail"]

    def test_get_guild_unexpected_exception(self):
        """get_guild: non-HTTP exception triggers handle_discord_exception (lines 101-103)."""
        guild = _make_guild()
        client, bot = _build_client(guild)
        bot.get_guild = MagicMock(side_effect=RuntimeError("boom"))

        response = client.get("/api/v1/guilds/987654321")
        assert response.status_code == 500
        assert "get guild details" in response.json()["detail"].lower()

    def test_list_guild_members_unexpected_exception(self):
        """list_guild_members: non-HTTP exception triggers handler (lines 146-148)."""
        guild = _make_guild()
        client, bot = _build_client(guild)
        bot.get_guild = MagicMock(side_effect=RuntimeError("boom"))

        response = client.get("/api/v1/guilds/987654321/members")
        assert response.status_code == 500
        assert "list guild members" in response.json()["detail"].lower()

    def test_list_guild_channels_unexpected_exception(self):
        """list_guild_channels: non-HTTP exception triggers handler (lines 183-185)."""
        guild = _make_guild()
        client, bot = _build_client(guild)
        bot.get_guild = MagicMock(side_effect=RuntimeError("boom"))

        response = client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 500
        assert "list guild channels" in response.json()["detail"].lower()

    def test_create_channel_unexpected_exception(self):
        """create_channel: non-HTTP exception triggers handler (lines 252-254)."""
        guild = _make_guild()
        guild.create_text_channel = AsyncMock(side_effect=RuntimeError("boom"))
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/channels", json={"name": "test", "type": "text"})
        assert response.status_code == 500
        assert "create channel" in response.json()["detail"].lower()

    def test_list_categories_unexpected_exception(self):
        """list_categories: non-HTTP exception triggers handler (lines 289-291)."""
        guild = _make_guild()
        client, bot = _build_client(guild)
        bot.get_guild = MagicMock(side_effect=RuntimeError("boom"))

        response = client.get("/api/v1/guilds/987654321/categories")
        assert response.status_code == 500
        assert "list categories" in response.json()["detail"].lower()

    def test_create_category_unexpected_exception(self):
        """create_category: non-HTTP exception triggers handler (lines 331-333)."""
        guild = _make_guild()
        guild.create_category_channel = AsyncMock(side_effect=RuntimeError("boom"))
        client, _bot = _build_client(guild)

        response = client.post("/api/v1/guilds/987654321/categories", json={"name": "Test Cat"})
        assert response.status_code == 500
        assert "create category" in response.json()["detail"].lower()

    def test_list_guild_roles_unexpected_exception(self):
        """list_guild_roles: non-HTTP exception triggers handler (lines 369-371)."""
        guild = _make_guild()
        client, bot = _build_client(guild)
        bot.get_guild = MagicMock(side_effect=RuntimeError("boom"))

        response = client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 500
        assert "list guild roles" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: list_guild_channels with real channel objects (lines 173-174)
# ---------------------------------------------------------------------------


class TestListChannelsWithData:
    """Cover the channel filtering loop body (lines 173-174)."""

    def test_list_channels_with_text_and_category_channels(self):
        """Channels list excludes CategoryChannels and real-serializes text/voice channels."""
        guild = _make_guild()
        text_ch = _make_channel(channel_id=1001, name="general", position=0, guild=guild)
        voice_ch = DiscordMockUtils.create_mock_voice_channel(
            channel_id=1002, name="voice", position=1, guild=guild, guild_id=guild.id
        )
        voice_ch.__class__ = discord.VoiceChannel
        cat_ch = _make_category(channel_id=1003, name="Category", position=0, guild=guild)
        guild.channels = [text_ch, voice_ch, cat_ch]
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/channels")
        assert response.status_code == 200
        data = response.json()
        # CategoryChannel excluded (real isinstance check), text_ch and voice_ch remain,
        # real-serialized (not fabricated) — sorted by position: text_ch(0) then voice_ch(1).
        assert [c["id"] for c in data["data"]] == [1001, 1002]
        assert [c["type"] for c in data["data"]] == ["text", "voice"]


# ---------------------------------------------------------------------------
# Tests: list_guild_roles with actual role objects (lines 356-357)
# ---------------------------------------------------------------------------


class TestListRolesWithData:
    """Cover the role iteration loop body (lines 356-357)."""

    def test_list_roles_with_multiple_roles(self):
        """Roles list iterates over each role and sorts by position, real-serialized."""
        guild = _make_guild()
        role_a = _make_role(role_id=301, name="Admin", position=2, guild=guild)
        role_b = _make_role(role_id=302, name="Member", position=1, guild=guild)
        role_c = _make_role(role_id=303, name="Everyone", position=0, guild=guild)
        guild.roles = [role_a, role_b, role_c]
        client, _bot = _build_client(guild)

        response = client.get("/api/v1/guilds/987654321/roles")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 3
        # Roles should be sorted by position (ascending) — real router sort, real converter output.
        assert [r["id"] for r in data["data"]] == [303, 302, 301]
        positions = [r["position"] for r in data["data"]]
        assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# Tests: create_role permissions bitmask mismatch (line 409)
# ---------------------------------------------------------------------------


class TestCreateRolePermsBitmask:
    """Cover the perms.value != role_data.permissions branch (line 409)."""

    def test_create_role_permissions_bitmask_mismatch(self):
        """Permissions value mismatch returns the intended 422 (TRUEUP-P2 fixed).

        ``discord.Permissions`` is patched here only to force the mismatch
        branch itself (999 != 8 — unreachable with the real class, whose
        ``__init__`` stores the input verbatim); everything else is real.
        """
        guild = _make_guild()
        client, _bot = _build_client(guild)

        mock_perms = MagicMock()
        mock_perms.value = 999  # deliberately different from any input

        with patch("api.routers.guilds.discord.Permissions", return_value=mock_perms):
            response = client.post(
                "/api/v1/guilds/987654321/roles",
                json={"name": "test-role", "permissions": 8},
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid permissions bitmask"


# ---------------------------------------------------------------------------
# Tests: create_role timeout/retry logic (lines 435-459)
# ---------------------------------------------------------------------------


class TestCreateRoleRetryLogic:
    """Cover the timeout/retry paths in create_role (lines 435-459).

    ``asyncio.wait_for``/``asyncio.sleep``/``random.uniform`` stay patched:
    this is the router's own real retry loop under test, and waiting out
    real exponential backoff would make the suite slow — a genuine timing
    boundary, not a re-implementation of the loop's logic.
    """

    @staticmethod
    def _build_retry_client(guild):
        """Build a client with only the timing primitives patched."""
        return _build_client(guild)

    def test_create_role_timeout_all_retries_exhausted(self):
        """All 3 attempts timeout → 503 (lines 435-446)."""
        guild = _make_guild()

        async def _always_timeout(coro, *, timeout=None):
            coro.close()  # avoid an "unawaited coroutine" warning on the discarded attempt
            raise TimeoutError("timed out")

        with (
            patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("api.routers.guilds.random.uniform", return_value=0.0),
            patch("api.routers.guilds.asyncio.wait_for", side_effect=_always_timeout) as mock_wf,
        ):
            client, _bot = self._build_retry_client(guild)
            response = client.post("/api/v1/guilds/987654321/roles", json={"name": "timeout-role"})

        assert response.status_code == 503
        assert "timeout" in response.json()["detail"].lower()
        assert mock_wf.call_count == 3
        assert mock_sleep.call_count == 2

    def test_create_role_timeout_then_success(self):
        """First attempt times out, second succeeds (lines 435-446 partial)."""
        guild = _make_guild()
        new_role = _make_role(role_id=7005, name="retry-role", guild=guild)
        guild.create_role = AsyncMock(return_value=new_role)

        calls = {"n": 0}

        async def _wf(coro, *, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                coro.close()  # avoid an "unawaited coroutine" warning on the discarded attempt
                raise TimeoutError("timed out")
            return await coro

        with (
            patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("api.routers.guilds.random.uniform", return_value=0.0),
            patch("api.routers.guilds.asyncio.wait_for", side_effect=_wf) as mock_wf,
        ):
            client, _bot = self._build_retry_client(guild)
            response = client.post("/api/v1/guilds/987654321/roles", json={"name": "retry-role"})

        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert mock_wf.call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_role_discord_http_5xx_retries(self):
        """discord.HTTPException with 5xx status retries (lines 447-456)."""
        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 502
        fake_response.reason = "Bad Gateway"
        http_exc = discord.HTTPException(fake_response, "server error")
        new_role = _make_role(role_id=7006, name="retry-5xx-role", guild=guild)
        guild.create_role = AsyncMock(side_effect=[http_exc, new_role])

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with (
            patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("api.routers.guilds.random.uniform", return_value=0.0),
            patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough),
        ):
            client, _bot = self._build_retry_client(guild)
            response = client.post("/api/v1/guilds/987654321/roles", json={"name": "retry-5xx-role"})

        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert guild.create_role.call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_role_discord_http_4xx_no_retry(self):
        """discord.HTTPException with 4xx status does NOT retry (line 457)."""
        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 403
        fake_response.reason = "Forbidden"
        http_exc = discord.HTTPException(fake_response, "forbidden")
        guild.create_role = AsyncMock(side_effect=http_exc)

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with (
            patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("api.routers.guilds.random.uniform", return_value=0.0),
            patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough),
        ):
            client, _bot = self._build_retry_client(guild)
            response = client.post("/api/v1/guilds/987654321/roles", json={"name": "forbidden-role"})

        # 4xx is re-raised immediately → caught by outer except Exception →
        # real handle_discord_exception, which maps a 403 discord.HTTPException
        # straight through to a real HTTP 403 (not a generic 500).
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
        assert guild.create_role.call_count == 1
        assert mock_sleep.call_count == 0

    def test_create_role_discord_http_5xx_all_retries_exhausted(self):
        """discord.HTTPException 5xx on all attempts re-raises on last (lines 452-457)."""
        guild = _make_guild()
        fake_response = MagicMock()
        fake_response.status = 500
        fake_response.reason = "Internal Server Error"
        http_exc = discord.HTTPException(fake_response, "server error")
        guild.create_role = AsyncMock(side_effect=http_exc)

        async def _passthrough(coro, *, timeout=None):
            return await coro

        with (
            patch("api.routers.guilds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("api.routers.guilds.random.uniform", return_value=0.0),
            patch("api.routers.guilds.asyncio.wait_for", side_effect=_passthrough),
        ):
            client, _bot = self._build_retry_client(guild)
            response = client.post("/api/v1/guilds/987654321/roles", json={"name": "5xx-exhausted-role"})

        # On the 3rd attempt, attempt == max_attempts so it raises (line 457),
        # and the real handle_discord_exception maps an unmapped-status
        # discord.HTTPException to a real 502 (Bad Gateway), not a generic 500.
        assert response.status_code == 502
        assert "detail" in response.json()
        assert guild.create_role.call_count == 3
        assert mock_sleep.call_count == 2
