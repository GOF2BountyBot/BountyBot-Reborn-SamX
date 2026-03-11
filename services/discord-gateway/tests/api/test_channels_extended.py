"""Extended tests for the channels API endpoints — boosting coverage from 26% to 70%+."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import sys
import os
import types
from datetime import datetime

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Module-level mock setup (must happen before any src imports)
# ---------------------------------------------------------------------------

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

# Setup mock discord module with real exception classes so isinstance checks work.
_mock_discord = DiscordMockUtils.create_mock_discord_module()

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThread = type("Thread", (), {})
_MockEmbed = type("Embed", (), {})
_MockPermissionOverwrite = type("PermissionOverwrite", (), {})

_mock_discord.CategoryChannel = _MockCategoryChannel
_mock_discord.TextChannel = _MockTextChannel
_mock_discord.VoiceChannel = _MockVoiceChannel
_mock_discord.ForumChannel = _MockForumChannel
_mock_discord.Thread = _MockThread
_mock_discord.Embed = _MockEmbed
_mock_discord.PermissionOverwrite = _MockPermissionOverwrite

_MockBot = type("Bot", (), {})
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _MockBot

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Channel factory helpers
# ---------------------------------------------------------------------------

def _make_channel_detail_dict(
    channel_id=1234567890,
    name="test-channel",
    ctype="text",
    guild_id=987654321,
    category_id=None,
):
    return {
        "id": channel_id,
        "name": name,
        "type": ctype,
        "position": 1,
        "guild_id": guild_id,
        "category_id": category_id,
        "created_at": "2024-01-01T00:00:00",
        "topic": "Test topic",
        "nsfw": False,
        "slowmode_delay": 0,
    }


def create_mock_text_channel(channel_id=1234567890):
    ch = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="test-channel",
        channel_type="text",
        position=1,
        guild_id=987654321,
    )
    ch.category_id = None
    ch.topic = "Test topic"
    ch.nsfw = False
    ch.slowmode_delay = 0
    ch.overwrites = {}
    ch.threads = []
    ch.available_tags = []
    ch.edit = AsyncMock()
    ch.delete = AsyncMock()
    ch.set_permissions = AsyncMock()
    ch.send = AsyncMock()

    async def _history(limit=50):
        return
        yield  # async generator

    ch.history = _history
    return ch


def create_mock_voice_channel(channel_id=2222222222):
    ch = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="test-voice",
        channel_type="voice",
        position=2,
        guild_id=987654321,
    )
    ch.category_id = None
    ch.bitrate = 64000
    ch.user_limit = 0
    ch.overwrites = {}
    ch.edit = AsyncMock()
    ch.delete = AsyncMock()
    return ch


def create_mock_forum_channel(channel_id=3333333333):
    ch = DiscordMockUtils.create_mock_forum_channel(
        channel_id=channel_id,
        name="test-forum",
        position=3,
        guild_id=987654321,
    )
    ch.category_id = None
    ch.topic = "Forum topic"
    ch.nsfw = False
    ch.default_auto_archive_duration = 1440
    ch.overwrites = {}
    ch.threads = []
    ch.available_tags = []
    ch.edit = AsyncMock()
    ch.delete = AsyncMock()
    ch.set_permissions = AsyncMock()
    ch.create_thread = AsyncMock()
    return ch


def create_mock_category(channel_id=1111111111):
    cat = DiscordMockUtils.create_mock_category_channel(
        channel_id=channel_id,
        name="Test Category",
        position=0,
        guild_id=987654321,
    )
    cat.edit = AsyncMock()
    return cat


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot_extended():
    """Bot that knows about text, voice, forum, and category channels."""
    text_ch = create_mock_text_channel(1234567890)
    voice_ch = create_mock_voice_channel(2222222222)
    forum_ch = create_mock_forum_channel(3333333333)
    category = create_mock_category(1111111111)

    channels = {
        1234567890: text_ch,
        2222222222: voice_ch,
        3333333333: forum_ch,
        1111111111: category,
    }

    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.get_channel = lambda cid: channels.get(cid)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels.get(cid))
    return bot


def _build_app(mock_bot, channel_detail_override=None):
    """Build a FastAPI test app with channels router and patched helpers."""
    app = FastAPI(title="Channels Test")
    app.state.bot = mock_bot

    detail = channel_detail_override or _make_channel_detail_dict()

    with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
         patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_hde, \
         patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
         patch("api.routers.channels.ChannelConverter") as mock_cc, \
         patch("api.routers.channels.PermissionConverter") as mock_pc, \
         patch("api.routers.channels.validate_channel_type") as mock_vct, \
         patch("api.routers.channels.EmbedConverter") as mock_ec, \
         patch("api.routers.channels.create_permission_overwrite") as mock_cpo:

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch = mock_bot.get_channel(entity_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return ch

        async def _resolve_bot(request):
            return mock_bot

        mock_gea.side_effect = _get_entity
        mock_rb.side_effect = _resolve_bot
        mock_hde.return_value = None
        mock_cc.channel_to_detail.return_value = detail
        mock_cc.thread_to_summary.return_value = {}
        mock_cc.thread_to_detail.return_value = {
            "id": 9999,
            "name": "test-thread",
            "channel_id": 3333333333,
            "guild_id": 987654321,
            "owner_id": 111,
            "archived": False,
            "locked": False,
            "message_count": 0,
            "member_count": 1,
            "default_auto_archive_duration": 1440,
            "created_at": "2024-01-01T00:00:00",
            "last_message_id": None,
        }
        mock_cc.forum_tag_to_payload.return_value = {
            "id": 1,
            "channel_id": 3333333333,
            "name": "tag1",
            "emoji": None,
        }
        mock_cc.overwrite_to_payload = MagicMock(return_value={})
        mock_pc.overwrite_to_payload.return_value = {
            "id": "3333333333:111",
            "channel_id": 3333333333,
            "target_id": 111,
            "type": "role",
            "allow": 0,
            "deny": 0,
        }
        mock_vct.return_value = None  # no exception by default
        mock_ec.payload_to_embed.return_value = MagicMock()
        mock_cpo.return_value = MagicMock()

        from api.routers.channels import router
        app.include_router(router, prefix="/api/v1")

        yield app, {
            "get_entity": mock_gea,
            "handle_exception": mock_hde,
            "resolve_bot": mock_rb,
            "converter": mock_cc,
            "perm_converter": mock_pc,
            "validate_channel_type": mock_vct,
            "embed_converter": mock_ec,
            "create_perm_overwrite": mock_cpo,
        }


@pytest.fixture
def channels_app_and_mocks(mock_bot_extended):
    gen = _build_app(mock_bot_extended)
    yield from gen


@pytest.fixture
def channels_client(channels_app_and_mocks):
    app, _ = channels_app_and_mocks
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}
# ---------------------------------------------------------------------------


class TestGetChannelExtended:
    """Extended tests for GET /channels/{channel_id}."""

    def test_get_text_channel_success(self, channels_client):
        """Should return 200 for a text channel."""
        resp = channels_client.get("/api/v1/channels/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "data" in data

    def test_get_channel_not_found(self, channels_client):
        """Should return 404 for unknown channel."""
        resp = channels_client.get("/api/v1/channels/9999999999")
        assert resp.status_code == 404

    def test_get_voice_channel_success(self, channels_client):
        """Should return 200 for a voice channel."""
        resp = channels_client.get("/api/v1/channels/2222222222")
        assert resp.status_code == 200

    def test_get_forum_channel_success(self, channels_client):
        """Should return 200 for a forum channel (not category)."""
        resp = channels_client.get("/api/v1/channels/3333333333")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /channels/{channel_id} — category rejection
# ---------------------------------------------------------------------------


class TestGetChannelCategoryRejection:
    """GET /channels/{channel_id} should reject category channels (400)."""

    def test_get_category_channel_returns_400(self, mock_bot_extended):
        """Channel that is a CategoryChannel (isinstance passes) should return 400.

        We patch the 'discord' module inside the router so that
        isinstance(channel, discord.CategoryChannel) evaluates to True for our
        mock category channel.
        """
        import importlib
        import api.routers.channels as channels_module

        # Create a category channel mock
        category_ch = create_mock_category(1111111111)

        # Create a custom category class that our mock is an instance of,
        # then patch discord.CategoryChannel inside the router to that class.
        CategoryClass = type("CategoryChannel", (), {})
        category_ch.__class__ = CategoryClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: category_ch if cid == 1111111111 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: category_ch if cid == 1111111111 else None)

        app = FastAPI()
        app.state.bot = bot

        # Patch discord.CategoryChannel in the router to be our CategoryClass
        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = CategoryClass
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = type("ForumChannel", (), {})

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter"), \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return category_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.get("/api/v1/channels/1111111111")
            assert resp.status_code == 400
            assert "category" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id} — update
# ---------------------------------------------------------------------------


class TestUpdateChannel:
    """Tests for PUT /channels/{channel_id}."""

    def test_update_channel_name(self, channels_client):
        """PUT with new name should return 200."""
        payload = {"name": "new-name"}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    def test_update_channel_not_found(self, channels_client):
        """PUT for unknown channel should return 404."""
        payload = {"name": "new-name"}
        resp = channels_client.put("/api/v1/channels/9999999999", json=payload)
        assert resp.status_code == 404

    def test_update_channel_position(self, channels_client):
        """PUT with position change should return 200."""
        payload = {"position": 5}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200

    def test_update_channel_topic_nsfw_slowmode(self, channels_client):
        """PUT with topic, nsfw, slowmode should return 200."""
        payload = {"topic": "new topic", "nsfw": True, "slowmode_delay": 10}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200

    def test_update_channel_empty_payload_no_edit(self, channels_client, channels_app_and_mocks):
        """PUT with empty payload (no fields) should still return 200 (no edit call needed)."""
        payload = {}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200

    def test_update_voice_channel_bitrate(self, channels_client):
        """PUT with bitrate/user_limit on voice channel should return 200."""
        payload = {"bitrate": 96000, "user_limit": 10}
        resp = channels_client.put("/api/v1/channels/2222222222", json=payload)
        assert resp.status_code == 200

    def test_update_category_returns_400(self, mock_bot_extended):
        """PUT on a category channel should return 400."""
        CategoryClass = type("CategoryChannel", (), {})
        category_ch = create_mock_category(1111111111)
        category_ch.__class__ = CategoryClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: category_ch if cid == 1111111111 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: category_ch if cid == 1111111111 else None)

        app = FastAPI()
        app.state.bot = bot

        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = CategoryClass
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = type("ForumChannel", (), {})

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter"), \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return category_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.put("/api/v1/channels/1111111111", json={"name": "x"})
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /channels/{channel_id}
# ---------------------------------------------------------------------------


class TestDeleteChannel:
    """Tests for DELETE /channels/{channel_id}."""

    def test_delete_channel_success(self, channels_client):
        """DELETE an existing channel should return 200."""
        resp = channels_client.delete("/api/v1/channels/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True

    def test_delete_channel_not_found(self, channels_client):
        """DELETE on non-existent channel should return 404."""
        resp = channels_client.delete("/api/v1/channels/9999999999")
        assert resp.status_code == 404

    def test_delete_voice_channel_success(self, channels_client):
        """DELETE a voice channel should return 200."""
        resp = channels_client.delete("/api/v1/channels/2222222222")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}/messages
# ---------------------------------------------------------------------------


class TestListChannelMessages:
    """Tests for GET /channels/{channel_id}/messages."""

    def test_list_messages_success(self, channels_client):
        """GET channel messages should return 200 with list."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)

    def test_list_messages_not_found(self, channels_client):
        """GET messages for non-existent channel should return 404."""
        resp = channels_client.get("/api/v1/channels/9999999999/messages")
        assert resp.status_code == 404

    def test_list_messages_with_limit(self, channels_client):
        """GET messages with limit param should work."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages?limit=10")
        assert resp.status_code == 200

    def test_list_messages_limit_too_large(self, channels_client):
        """GET messages with limit > 100 should return 422."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages?limit=200")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /channels/{channel_id}/messages
# ---------------------------------------------------------------------------


class TestCreateChannelMessage:
    """Tests for POST /channels/{channel_id}/messages."""

    def test_create_message_success(self, channels_client, channels_app_and_mocks):
        """POST a message to a channel should return 201."""
        app, mocks = channels_app_and_mocks
        # The channel's send() is AsyncMock; set up a return value
        mock_msg = MagicMock()
        mock_msg.id = 9876543210
        mock_msg.author = MagicMock()
        mock_msg.author.id = 123456789
        mock_msg.created_at = datetime(2024, 1, 1)
        mock_msg.edited_at = None
        mock_msg.type = MagicMock()
        mock_msg.type.name = "general"

        # Get the text channel and patch its send
        text_ch = create_mock_text_channel(1234567890)
        text_ch.guild = MagicMock()
        text_ch.guild.id = 987654321
        text_ch.send = AsyncMock(return_value=mock_msg)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        new_app = FastAPI()
        new_app.state.bot = bot

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter") as mock_ec, \
             patch("api.routers.channels.create_permission_overwrite"):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return text_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.channels import router
            new_app.include_router(router, prefix="/api/v1")

            client = TestClient(new_app)
            payload = {"content": {"title": "Hello", "description": "World"}}
            resp = client.post("/api/v1/channels/1234567890/messages", json=payload)
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "created"

    def test_create_message_channel_not_found(self, channels_client):
        """POST message to non-existent channel should return 404."""
        payload = {"content": {"title": "Hello"}}
        resp = channels_client.post("/api/v1/channels/9999999999/messages", json=payload)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}/permissions
# ---------------------------------------------------------------------------


class TestGetChannelPermissions:
    """Tests for GET /channels/{channel_id}/permissions."""

    def test_get_permissions_empty(self, channels_client):
        """GET permissions on channel with no overwrites should return empty list."""
        resp = channels_client.get("/api/v1/channels/1234567890/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 0  # channel.overwrites = {}

    def test_get_permissions_not_found(self, channels_client):
        """GET permissions on non-existent channel should return 404."""
        resp = channels_client.get("/api/v1/channels/9999999999/permissions")
        assert resp.status_code == 404

    def test_get_permissions_with_overwrites(self, mock_bot_extended):
        """GET permissions on channel with overwrites should return them."""
        text_ch = create_mock_text_channel(1234567890)
        mock_role = MagicMock()
        mock_role.id = 555
        mock_overwrite = MagicMock()
        text_ch.overwrites = {mock_role: mock_overwrite}

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        app = FastAPI()
        app.state.bot = bot

        overwrite_payload = {
            "id": "1234567890:555",
            "channel_id": 1234567890,
            "target_id": 555,
            "type": "role",
            "allow": 0,
            "deny": 0,
        }

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter") as mock_pc, \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return text_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_pc.overwrite_to_payload.return_value = overwrite_payload
            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.get("/api/v1/channels/1234567890/permissions")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["data"]) == 1


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id}/permissions
# ---------------------------------------------------------------------------


class TestUpdateChannelPermissions:
    """Tests for PUT /channels/{channel_id}/permissions."""

    def test_update_permissions_empty_list(self, channels_client):
        """PUT with empty overwrites list should return 200."""
        payload = {"overwrites": []}
        resp = channels_client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    def test_update_permissions_not_found(self, channels_client):
        """PUT permissions on non-existent channel should return 404."""
        payload = {"overwrites": []}
        resp = channels_client.put("/api/v1/channels/9999999999/permissions", json=payload)
        assert resp.status_code == 404

    def test_update_permissions_with_role_overwrite(self, mock_bot_extended):
        """PUT with role overwrite should apply permissions."""
        text_ch = create_mock_text_channel(1234567890)
        text_ch.overwrites = {}
        mock_role = MagicMock()
        mock_role.id = 777
        text_ch.guild.get_role = MagicMock(return_value=mock_role)
        text_ch.guild.get_member = MagicMock(return_value=None)
        text_ch.guild.fetch_member = AsyncMock(return_value=None)
        text_ch.set_permissions = AsyncMock()

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter") as mock_pc, \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite") as mock_cpo:

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return text_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()
            mock_pc.overwrite_to_payload.return_value = {}
            mock_cpo.return_value = MagicMock()

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            payload = {
                "overwrites": [
                    {"target_id": 777, "type": "role", "allow": 0, "deny": 0}
                ]
            }
            resp = client.put("/api/v1/channels/1234567890/permissions", json=payload)
            assert resp.status_code == 200

    def test_update_permissions_member_not_found_skip(self, mock_bot_extended):
        """PUT with member overwrite where member doesn't exist should skip gracefully."""
        text_ch = create_mock_text_channel(1234567890)
        text_ch.overwrites = {}
        text_ch.guild.get_role = MagicMock(return_value=None)
        text_ch.guild.get_member = MagicMock(return_value=None)
        text_ch.guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
        text_ch.set_permissions = AsyncMock()

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        app = FastAPI()
        app.state.bot = bot

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return text_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            payload = {
                "overwrites": [
                    {"target_id": 888, "type": "member", "allow": 0, "deny": 0}
                ]
            }
            resp = client.put("/api/v1/channels/1234567890/permissions", json=payload)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}/threads
# ---------------------------------------------------------------------------


class TestListForumThreads:
    """Tests for GET /channels/{channel_id}/threads."""

    def test_list_threads_on_text_channel_returns_400(self, channels_client):
        """GET threads on a non-forum channel should return 400."""
        resp = channels_client.get("/api/v1/channels/1234567890/threads")
        assert resp.status_code == 400
        assert "forum" in resp.json()["detail"].lower()

    def test_list_threads_channel_not_found(self, channels_client):
        """GET threads on non-existent channel should return 404."""
        resp = channels_client.get("/api/v1/channels/9999999999/threads")
        assert resp.status_code == 404

    def test_list_threads_on_forum_success(self, mock_bot_extended):
        """GET threads on a forum channel should return 200 with thread list."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass
        mock_thread = MagicMock()
        forum_ch.threads = [mock_thread]

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        app = FastAPI()
        app.state.bot = bot

        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = type("CategoryChannel", (), {})
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = ForumClass

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return forum_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve

            thread_summary = {
                "id": 9999,
                "name": "t",
                "channel_id": 3333333333,
                "guild_id": 987654321,
                "owner_id": 111,
                "archived": False,
                "locked": False,
                "message_count": 0,
                "member_count": 1,
                "default_auto_archive_duration": 1440,
                "created_at": "2024-01-01T00:00:00",
                "last_message_id": None,
            }
            mock_cc.thread_to_summary.return_value = thread_summary

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.get("/api/v1/channels/3333333333/threads")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert len(data["data"]) == 1


# ---------------------------------------------------------------------------
# POST /channels/{channel_id}/threads
# ---------------------------------------------------------------------------


class TestCreateForumThread:
    """Tests for POST /channels/{channel_id}/threads."""

    def test_create_thread_on_text_channel_returns_400(self, channels_client):
        """POST thread on a non-forum channel should return 400."""
        payload = {"name": "my-thread"}
        resp = channels_client.post("/api/v1/channels/1234567890/threads", json=payload)
        assert resp.status_code == 400
        assert "forum" in resp.json()["detail"].lower()

    def test_create_thread_channel_not_found(self, channels_client):
        """POST thread on non-existent channel should return 404."""
        payload = {"name": "my-thread"}
        resp = channels_client.post("/api/v1/channels/9999999999/threads", json=payload)
        assert resp.status_code == 404

    def test_create_thread_on_forum_success(self):
        """POST thread on a forum channel should return 201."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass

        mock_thread_obj = MagicMock()
        mock_thread_obj.name = "my-thread"
        # create_thread returns the thread directly (no .thread attr)
        forum_ch.create_thread = AsyncMock(return_value=mock_thread_obj)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        app = FastAPI()
        app.state.bot = bot

        thread_detail = {
            "id": 9999,
            "name": "my-thread",
            "channel_id": 3333333333,
            "guild_id": 987654321,
            "owner_id": 111,
            "archived": False,
            "locked": False,
            "message_count": 0,
            "member_count": 1,
            "default_auto_archive_duration": 1440,
            "created_at": "2024-01-01T00:00:00",
            "last_message_id": None,
        }

        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = type("CategoryChannel", (), {})
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = ForumClass

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter") as mock_ec, \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return forum_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_cc.thread_to_detail.return_value = thread_detail
            mock_ec.payload_to_embed.return_value = None

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            payload = {"name": "my-thread"}
            resp = client.post("/api/v1/channels/3333333333/threads", json=payload)
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "created"


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}/tags
# ---------------------------------------------------------------------------


class TestListForumTags:
    """Tests for GET /channels/{channel_id}/tags."""

    def test_list_tags_on_text_channel_returns_400(self, channels_client):
        """GET tags on a non-forum channel should return 400."""
        resp = channels_client.get("/api/v1/channels/1234567890/tags")
        assert resp.status_code == 400
        assert "forum" in resp.json()["detail"].lower()

    def test_list_tags_channel_not_found(self, channels_client):
        """GET tags on non-existent channel should return 404."""
        resp = channels_client.get("/api/v1/channels/9999999999/tags")
        assert resp.status_code == 404

    def test_list_tags_on_forum_success(self):
        """GET tags on a forum channel should return 200 with tag list."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass
        mock_tag = MagicMock()
        mock_tag.id = 1
        mock_tag.name = "combat"
        forum_ch.available_tags = [mock_tag]

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        app = FastAPI()
        app.state.bot = bot

        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = type("CategoryChannel", (), {})
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = ForumClass

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return forum_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_cc.forum_tag_to_payload.return_value = {
                "id": 1,
                "channel_id": 3333333333,
                "name": "combat",
                "emoji": None,
            }

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.get("/api/v1/channels/3333333333/tags")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert len(data["data"]) == 1

    def test_list_tags_empty_forum(self):
        """GET tags on forum with no tags returns empty list."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass
        forum_ch.available_tags = []

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        app = FastAPI()
        app.state.bot = bot

        mock_discord_for_router = MagicMock()
        mock_discord_for_router.CategoryChannel = type("CategoryChannel", (), {})
        mock_discord_for_router.TextChannel = type("TextChannel", (), {})
        mock_discord_for_router.VoiceChannel = type("VoiceChannel", (), {})
        mock_discord_for_router.ForumChannel = ForumClass

        with patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea, \
             patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb, \
             patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.channels.ChannelConverter") as mock_cc, \
             patch("api.routers.channels.PermissionConverter"), \
             patch("api.routers.channels.validate_channel_type"), \
             patch("api.routers.channels.EmbedConverter"), \
             patch("api.routers.channels.create_permission_overwrite"), \
             patch("api.routers.channels.discord", mock_discord_for_router):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return forum_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve

            from api.routers.channels import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.get("/api/v1/channels/3333333333/tags")
            assert resp.status_code == 200
            assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id}/category/{category_id}
# ---------------------------------------------------------------------------


class TestMoveChannelToCategory:
    """Tests for PUT /channels/{channel_id}/category/{category_id}."""

    def test_move_channel_to_category_success(self, channels_client):
        """PUT move channel to category should return 200."""
        resp = channels_client.put("/api/v1/channels/1234567890/category/1111111111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "moved"

    def test_move_channel_not_found(self, channels_client):
        """PUT move non-existent channel should return 404."""
        resp = channels_client.put("/api/v1/channels/9999999999/category/1111111111")
        assert resp.status_code == 404

    def test_move_channel_category_not_found(self, channels_client):
        """PUT move channel to non-existent category should return 404."""
        resp = channels_client.put("/api/v1/channels/1234567890/category/9999999999")
        assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
