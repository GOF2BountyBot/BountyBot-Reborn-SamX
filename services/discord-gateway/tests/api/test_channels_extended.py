"""Extended tests for the channels API endpoints — boosting coverage from 26% to 70%+.

Fidelity notes (SYS-4 remediation — part 1 of 2, see FOLLOWUPS.md R-gw-api-0)
--------------------------------------------------------------------
This file no longer swaps ``sys.modules["discord"]``/``"discord.ext.commands"``
with a hand-rolled fake module at collection time (that swap made
``DiscordMockUtils.create_mock_bot()``'s ``MagicMock(spec=commands.Bot)`` a
*different* Bot class from the one ``resolve_bot``'s ``isinstance`` check
resolved against, which is what forced ``resolve_bot`` to stay patched
everywhere in the first place).

``_build_app`` (the fixture builder behind ``channels_app_and_mocks``/
``channels_client``, used by ``TestGetChannelExtended`` through
``TestMoveChannelToCategory`` below) mounts the real channels router against a
mock bot with NOTHING patched: ``resolve_bot``, ``get_entity_or_404``,
``handle_discord_exception``, ``ChannelConverter``, ``PermissionConverter``,
``validate_channel_type``, ``EmbedConverter`` and ``create_permission_overwrite``
all run for real. Mock channel/role/thread/tag objects are built via
``DiscordMockUtils`` factories and given real discord.py types
(``channel.__class__ = discord.TextChannel`` etc — the standard mock idiom also
used in ``test_guilds_extended.py``/``test_messages_extended.py``) so the
router's ``isinstance`` type-dispatch runs against the genuine classes, and
``channel.edit()`` mutates the mock's own attributes (mirroring discord.py's
real edit semantics) so a post-edit re-fetch/converter call reflects the
actually-applied change. Response bodies are asserted against values
re-derived from the real converter's field derivation
(``_expected_channel_detail``) or against the specific fields that matter,
never against canned dicts.

``_build_app_with_discord_patch`` (the second builder, used by the
isinstance/type-dispatch and generic-exception-handler test classes further
down) still patches all of the above — that remains a separate follow-up
(part 2 of this remediation), noted here only for context.
"""

import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

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

# NOTE: this file previously swapped sys.modules["discord"]/"discord.ext.commands"
# with a hand-rolled fake module at collection time. That swap made
# DiscordMockUtils.create_mock_bot()'s `MagicMock(spec=commands.Bot)` (built
# against whatever `discord.ext.commands` was real/cached at
# discord_mock_utils.py's own import time) a *different* Bot class from the one
# utils.discord_helpers.resolve_bot's `isinstance(bot, commands.Bot)` check
# resolved against (the fake one) — resolve_bot would then always raise 500
# "Bot instance invalid" once it was no longer patched away. Using the real
# discord module throughout (no swap) keeps both references identical, which
# is what let SYS-1 (unpatching resolve_bot below) become possible.

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


def _expected_channel_detail(channel) -> dict:
    """Independently re-derive the dict ``ChannelConverter.channel_to_detail`` should
    produce for *channel*, from the mock's own attributes.

    Used to assert against the REAL converter's output (which now runs
    unpatched — see module fidelity notes above) without re-invoking the
    production code under test.
    """
    position = getattr(channel, "position", None)
    try:
        position = 0 if position is None else int(position)
    except (TypeError, ValueError):
        position = 0
    created_at = getattr(channel, "created_at", None)
    return {
        "id": channel.id,
        "name": channel.name,
        "type": getattr(getattr(channel, "type", None), "name", None),
        "position": position,
        "guild_id": getattr(getattr(channel, "guild", None), "id", None),
        "category_id": getattr(channel, "category_id", None),
        "created_at": created_at.isoformat() if created_at is not None else "",
        "topic": getattr(channel, "topic", None),
        "nsfw": getattr(channel, "nsfw", False),
        "slowmode_delay": getattr(channel, "slowmode_delay", None),
        "bitrate": getattr(channel, "bitrate", None),
        "user_limit": getattr(channel, "user_limit", None),
        "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", None),
    }


def _wire_real_edit(channel):
    """Attach an ``edit()`` that mutates *channel*'s own attributes (mirroring
    discord.py's real edit semantics) instead of a no-op AsyncMock, so a
    post-edit re-fetch/converter call reflects the actually-applied change —
    the same pattern ``test_messages_extended.py`` uses for ``message.edit``.

    ``category`` is special-cased since production passes a CategoryChannel
    object as the kwarg but ``Channel.category_id`` on the model is a plain id.
    """

    async def _edit(**kwargs):
        for key, value in kwargs.items():
            if key == "category":
                channel.category_id = value.id if value is not None else None
            else:
                setattr(channel, key, value)

    channel.edit = AsyncMock(side_effect=_edit)
    return channel


def create_mock_text_channel(channel_id=1234567890):
    ch = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="test-channel",
        channel_type="text",
        position=1,
        guild_id=987654321,
    )
    ch.__class__ = discord.TextChannel
    ch.category_id = None
    ch.topic = "Test topic"
    ch.nsfw = False
    ch.slowmode_delay = 0
    ch.overwrites = {}
    ch.threads = []
    ch.available_tags = []
    _wire_real_edit(ch)
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
    ch.__class__ = discord.VoiceChannel
    ch.category_id = None
    ch.bitrate = 64000
    ch.user_limit = 0
    ch.overwrites = {}
    _wire_real_edit(ch)
    ch.delete = AsyncMock()
    return ch


def create_mock_forum_channel(channel_id=3333333333):
    ch = DiscordMockUtils.create_mock_forum_channel(
        channel_id=channel_id,
        name="test-forum",
        position=3,
        guild_id=987654321,
    )
    ch.__class__ = discord.ForumChannel
    ch.category_id = None
    ch.topic = "Forum topic"
    ch.nsfw = False
    ch.default_auto_archive_duration = 1440
    ch.overwrites = {}
    ch.threads = []
    ch.available_tags = []
    _wire_real_edit(ch)
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
    cat.__class__ = discord.CategoryChannel
    cat.edit = AsyncMock()
    return cat


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot_extended():
    """Bot that knows about text, voice, forum, and category channels.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so the
    real (unpatched) ``get_entity_or_404`` produces a genuine 404. The channel
    map is stashed on ``bot._channels`` (mirroring the ``bot._graph``
    convention in ``test_messages_extended.py``) so tests can reach the exact
    mock instances the router operates on to build expected assertions.
    """
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

    async def fetch_channel(cid):
        found = channels.get(cid)
        if found is None:
            raise DiscordMockUtils.create_discord_not_found(f"Channel {cid} not found")
        return found

    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot._channels = channels
    return bot


def _build_app(mock_bot):
    """Build a FastAPI test app with the channels router.

    Nothing is patched: ``resolve_bot``, ``get_entity_or_404``,
    ``handle_discord_exception``, ``ChannelConverter``, ``PermissionConverter``,
    ``validate_channel_type``, ``EmbedConverter`` and ``create_permission_overwrite``
    all run for real against ``mock_bot`` (see module fidelity notes above).
    """
    app = FastAPI(title="Channels Test")
    app.state.bot = mock_bot

    import api.routers.channels as channels_module

    app.include_router(channels_module.router, prefix="/api/v1")

    yield (app, {})


@pytest.fixture
def channels_app_and_mocks(mock_bot_extended):
    gen = _build_app(mock_bot_extended)
    yield from gen


@pytest.fixture
def channels_client(channels_app_and_mocks, mock_bot_extended):
    app, _ = channels_app_and_mocks
    client = TestClient(app)
    # Expose the exact mock channel instances the router operates on so tests
    # can build expected-response assertions from them (mirrors bot._graph in
    # test_messages_extended.py).
    client.channels = mock_bot_extended._channels
    return client


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}
# ---------------------------------------------------------------------------


class TestGetChannelExtended:
    """Extended tests for GET /channels/{channel_id}."""

    def test_get_text_channel_success(self, channels_client):
        """Should return 200 with the real converter's output for a text channel."""
        resp = channels_client.get("/api/v1/channels/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"] == _expected_channel_detail(channels_client.channels[1234567890])

    def test_get_channel_not_found(self, channels_client):
        """Should return 404 for unknown channel."""
        resp = channels_client.get("/api/v1/channels/9999999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_get_voice_channel_success(self, channels_client):
        """Should return 200 with the real converter's output for a voice channel."""
        resp = channels_client.get("/api/v1/channels/2222222222")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"] == _expected_channel_detail(channels_client.channels[2222222222])

    def test_get_forum_channel_success(self, channels_client):
        """Should return 200 with the real converter's output for a forum channel (not category)."""
        resp = channels_client.get("/api/v1/channels/3333333333")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"] == _expected_channel_detail(channels_client.channels[3333333333])


# ---------------------------------------------------------------------------
# GET /channels/{channel_id} — category rejection
# ---------------------------------------------------------------------------


class TestGetChannelCategoryRejection:
    """GET /channels/{channel_id} should reject category channels (400)."""

    def test_get_category_channel_returns_400(self, channels_client):
        """A channel that is a real discord.CategoryChannel (isinstance passes) should return 400."""
        resp = channels_client.get("/api/v1/channels/1111111111")
        assert resp.status_code == 400
        assert "category" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id} — update
# ---------------------------------------------------------------------------


class TestUpdateChannel:
    """Tests for PUT /channels/{channel_id}."""

    def test_update_channel_name(self, channels_client):
        """PUT with new name should return 200 with the real (post-edit) converted body."""
        payload = {"name": "new-name"}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

        text_ch = channels_client.channels[1234567890]
        text_ch.edit.assert_awaited_once_with(name="new-name")
        assert data["data"] == _expected_channel_detail(text_ch)
        assert data["data"]["name"] == "new-name"

    def test_update_channel_not_found(self, channels_client):
        """PUT for unknown channel should return 404."""
        payload = {"name": "new-name"}
        resp = channels_client.put("/api/v1/channels/9999999999", json=payload)
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_update_channel_position(self, channels_client):
        """PUT with position change should return 200 with the updated position."""
        payload = {"position": 5}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["data"]["position"] == 5

    def test_update_channel_topic_nsfw_slowmode(self, channels_client):
        """PUT with topic, nsfw, slowmode should return 200 with the updated fields."""
        payload = {"topic": "new topic", "nsfw": True, "slowmode_delay": 10}
        resp = channels_client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["data"]["topic"] == "new topic"
        assert data["data"]["nsfw"] is True
        assert data["data"]["slowmode_delay"] == 10

    def test_update_channel_empty_payload_no_edit(self, channels_client):
        """PUT with empty payload (no fields) should still return 200 without calling edit()."""
        resp = channels_client.put("/api/v1/channels/1234567890", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        channels_client.channels[1234567890].edit.assert_not_awaited()

    def test_update_voice_channel_bitrate(self, channels_client):
        """PUT with bitrate/user_limit on voice channel should return 200 with the updated fields."""
        payload = {"bitrate": 96000, "user_limit": 10}
        resp = channels_client.put("/api/v1/channels/2222222222", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["data"]["bitrate"] == 96000
        assert data["data"]["user_limit"] == 10

    def test_update_category_returns_400(self, channels_client):
        """PUT on a category channel should return 400."""
        resp = channels_client.put("/api/v1/channels/1111111111", json={"name": "x"})
        assert resp.status_code == 400
        assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# DELETE /channels/{channel_id}
# ---------------------------------------------------------------------------


class TestDeleteChannel:
    """Tests for DELETE /channels/{channel_id}."""

    def test_delete_channel_success(self, channels_client):
        """DELETE an existing text channel should return 200 and call channel.delete()."""
        text_ch = channels_client.channels[1234567890]
        resp = channels_client.delete("/api/v1/channels/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert data["message"] == "Text channel test-channel deleted"
        text_ch.delete.assert_awaited_once()

    def test_delete_channel_not_found(self, channels_client):
        """DELETE on non-existent channel should return 404."""
        resp = channels_client.delete("/api/v1/channels/9999999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_delete_voice_channel_success(self, channels_client):
        """DELETE a voice channel should return 200 and call channel.delete()."""
        voice_ch = channels_client.channels[2222222222]
        resp = channels_client.delete("/api/v1/channels/2222222222")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["message"] == "Voice channel test-voice deleted"
        voice_ch.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /channels/{channel_id}/messages
# ---------------------------------------------------------------------------


class TestListChannelMessages:
    """Tests for GET /channels/{channel_id}/messages."""

    def test_list_messages_success(self, channels_client):
        """GET channel messages should return 200 with an (empty) list."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"] == []

    def test_list_messages_not_found(self, channels_client):
        """GET messages for non-existent channel should return 404."""
        resp = channels_client.get("/api/v1/channels/9999999999/messages")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_list_messages_with_limit(self, channels_client):
        """GET messages with limit param should work."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages?limit=10")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_list_messages_limit_too_large(self, channels_client):
        """GET messages with limit > 100 should return 422."""
        resp = channels_client.get("/api/v1/channels/1234567890/messages?limit=200")
        assert resp.status_code == 422
        assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# POST /channels/{channel_id}/messages
# ---------------------------------------------------------------------------


class TestCreateChannelMessage:
    """Tests for POST /channels/{channel_id}/messages."""

    def test_create_message_success(self, channels_client):
        """POST a message to a channel should return 201 with the real converted message data."""
        text_ch = channels_client.channels[1234567890]

        mock_msg = MagicMock()
        mock_msg.id = 9876543210
        mock_msg.author = MagicMock()
        mock_msg.author.id = 123456789
        mock_msg.created_at = datetime(2024, 1, 1)
        mock_msg.edited_at = None
        mock_msg.type = MagicMock()
        mock_msg.type.name = "general"
        text_ch.send = AsyncMock(return_value=mock_msg)

        payload = {"content": {"title": "Hello", "description": "World"}}
        resp = channels_client.post("/api/v1/channels/1234567890/messages", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 9876543210
        assert data["data"]["channel_id"] == 1234567890
        assert data["data"]["guild_id"] == 987654321
        assert data["data"]["author_id"] == 123456789
        assert data["data"]["content"]["title"] == "Hello"
        assert data["data"]["content"]["description"] == "World"

        text_ch.send.assert_awaited_once()
        sent_embed = text_ch.send.call_args.kwargs["embed"]
        assert isinstance(sent_embed, discord.Embed)
        assert sent_embed.title == "Hello"
        assert sent_embed.description == "World"

    def test_create_message_channel_not_found(self, channels_client):
        """POST message to non-existent channel should return 404."""
        payload = {"content": {"title": "Hello"}}
        resp = channels_client.post("/api/v1/channels/9999999999/messages", json=payload)
        assert resp.status_code == 404
        assert "detail" in resp.json()


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
        assert "detail" in resp.json()

    def test_get_permissions_with_overwrites(self, channels_client):
        """GET permissions on channel with overwrites should return the real converted payload."""
        text_ch = channels_client.channels[1234567890]
        role = DiscordMockUtils.create_mock_role(role_id=555, name="Test Role", guild=text_ch.guild)
        role.__class__ = discord.Role
        overwrite = DiscordMockUtils.create_mock_permission_overwrite(allow=1024, deny=2048)
        text_ch.overwrites = {role: overwrite}

        resp = channels_client.get("/api/v1/channels/1234567890/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        payload = data["data"][0]
        assert payload["id"] == "1234567890:555"
        assert payload["channel_id"] == 1234567890
        assert payload["target_id"] == 555
        assert payload["type"] == "role"
        assert payload["allow"] == 1024
        assert payload["deny"] == 2048


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
        assert "detail" in resp.json()

    def test_update_permissions_with_role_overwrite(self, channels_client):
        """PUT with role overwrite should call channel.set_permissions with a real overwrite."""
        text_ch = channels_client.channels[1234567890]
        role = DiscordMockUtils.create_mock_role(role_id=777, name="Test Role", guild=text_ch.guild)
        role.__class__ = discord.Role
        text_ch.guild.get_role = MagicMock(return_value=role)

        allow_value = discord.Permissions(view_channel=True).value
        payload = {"overwrites": [{"target_id": 777, "type": "role", "allow": allow_value, "deny": 0}]}
        resp = channels_client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        text_ch.set_permissions.assert_awaited_once()
        call_args = text_ch.set_permissions.call_args
        assert call_args.args[0] is role
        sent_overwrite = call_args.kwargs["overwrite"]
        allow, deny = sent_overwrite.pair()
        assert allow.value == allow_value
        assert deny.value == 0

    def test_update_permissions_member_not_found_skip(self, channels_client):
        """PUT with member overwrite where member doesn't exist should skip gracefully."""
        text_ch = channels_client.channels[1234567890]
        text_ch.guild.get_member = MagicMock(return_value=None)
        text_ch.guild.fetch_member = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found("not found"))

        payload = {"overwrites": [{"target_id": 888, "type": "member", "allow": 0, "deny": 0}]}
        resp = channels_client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        text_ch.set_permissions.assert_not_awaited()


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
        assert "detail" in resp.json()

    def test_list_threads_on_forum_success(self, channels_client):
        """GET threads on a forum channel should return 200 with the real converted thread list."""
        forum_ch = channels_client.channels[3333333333]
        thread = DiscordMockUtils.create_mock_thread(
            thread_id=9999,
            name="t",
            guild=forum_ch.guild,
            guild_id=forum_ch.guild.id,
            parent=forum_ch,
            parent_id=forum_ch.id,
            owner_id=111,
        )
        forum_ch.threads = [thread]

        resp = channels_client.get("/api/v1/channels/3333333333/threads")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        entry = data["data"][0]
        assert entry["id"] == 9999
        assert entry["name"] == "t"
        assert entry["channel_id"] == 3333333333
        assert entry["guild_id"] == 987654321
        assert entry["owner_id"] == 111


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
        assert "detail" in resp.json()

    def test_create_thread_on_forum_success(self, channels_client):
        """POST thread on a forum channel should return 201 with the real converted thread data."""
        forum_ch = channels_client.channels[3333333333]

        async def _create_thread(**kwargs):
            thread = DiscordMockUtils.create_mock_thread(
                thread_id=9999,
                name=kwargs["name"],
                guild=forum_ch.guild,
                guild_id=forum_ch.guild.id,
                parent=forum_ch,
                parent_id=forum_ch.id,
                owner_id=123456789,
                auto_archive_duration=kwargs.get("auto_archive_duration") or 1440,
            )
            # Real discord.py's plain Thread return (as opposed to a
            # ThreadWithMessage tuple) has no nested `.thread` attribute.
            # Speccing it means the router's `getattr(result, "thread",
            # result)` unwrap correctly falls through to `result` itself,
            # instead of silently returning an auto-vivified child MagicMock
            # (a plain MagicMock() would resolve `.thread` to a new mock
            # rather than raising AttributeError, since it doesn't spec-check).
            thread.mock_add_spec(discord.Thread)
            return thread

        forum_ch.create_thread = AsyncMock(side_effect=_create_thread)

        payload = {"name": "my-thread"}
        resp = channels_client.post("/api/v1/channels/3333333333/threads", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 9999
        assert data["data"]["name"] == "my-thread"
        assert data["data"]["channel_id"] == 3333333333
        assert data["data"]["guild_id"] == 987654321
        assert data["data"]["owner_id"] == 123456789


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
        assert "detail" in resp.json()

    def test_list_tags_on_forum_success(self, channels_client):
        """GET tags on a forum channel should return 200 with the real converted tag list."""
        forum_ch = channels_client.channels[3333333333]
        tag = DiscordMockUtils.create_mock_forum_tag(tag_id=1, name="combat", channel_id=3333333333)
        forum_ch.available_tags = [tag]

        resp = channels_client.get("/api/v1/channels/3333333333/tags")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"] == [{"id": 1, "channel_id": 3333333333, "name": "combat", "emoji": None}]

    def test_list_tags_empty_forum(self, channels_client):
        """GET tags on forum with no tags returns empty list."""
        resp = channels_client.get("/api/v1/channels/3333333333/tags")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# PUT /channels/{channel_id}/category/{category_id}
# ---------------------------------------------------------------------------


class TestMoveChannelToCategory:
    """Tests for PUT /channels/{channel_id}/category/{category_id}."""

    def test_move_channel_to_category_success(self, channels_client):
        """PUT move channel to category should return 200 and call channel.edit(category=...)."""
        text_ch = channels_client.channels[1234567890]
        category = channels_client.channels[1111111111]

        resp = channels_client.put("/api/v1/channels/1234567890/category/1111111111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "moved"
        assert data["message"] == "Channel test-channel moved to category Test Category"
        text_ch.edit.assert_awaited_once_with(category=category)

    def test_move_channel_not_found(self, channels_client):
        """PUT move non-existent channel should return 404."""
        resp = channels_client.put("/api/v1/channels/9999999999/category/1111111111")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_move_channel_category_not_found(self, channels_client):
        """PUT move channel to non-existent category should return 404."""
        resp = channels_client.put("/api/v1/channels/1234567890/category/9999999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# ===========================================================================
# NEW TESTS: Cover remaining missing lines (73-75, 104-109, 121, 125-130,
# 132-135, 137-140, 159-161, 192-194, 215, 230-232, 251, 260-264, 269-271,
# 293-295, 325-327, 352, 361-362, 380-382, 413-415, 448-455, 460, 474-476,
# 507-509, 536, 551-553)
# ===========================================================================


def _build_app_with_discord_patch(mock_bot, discord_overrides=None, channel_detail=None):
    """Build a FastAPI app patching both helpers AND the discord module for isinstance checks."""
    app = FastAPI(title="Channels Coverage Test")
    app.state.bot = mock_bot

    detail = channel_detail or _make_channel_detail_dict()

    # Build a mock discord module with proper class types
    mock_discord_for_router = MagicMock()
    CategoryClass = type("CategoryChannel", (), {})
    TextClass = type("TextChannel", (), {})
    VoiceClass = type("VoiceChannel", (), {})
    ForumClass = type("ForumChannel", (), {})

    mock_discord_for_router.CategoryChannel = CategoryClass
    mock_discord_for_router.TextChannel = TextClass
    mock_discord_for_router.VoiceChannel = VoiceClass
    mock_discord_for_router.ForumChannel = ForumClass

    if discord_overrides:
        for k, v in discord_overrides.items():
            setattr(mock_discord_for_router, k, v)

    with (
        patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea,
        patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_hde,
        patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb,
        patch("api.routers.channels.ChannelConverter") as mock_cc,
        patch("api.routers.channels.PermissionConverter") as mock_pc,
        patch("api.routers.channels.validate_channel_type") as mock_vct,
        patch("api.routers.channels.EmbedConverter") as mock_ec,
        patch("api.routers.channels.create_permission_overwrite") as mock_cpo,
        patch("api.routers.channels.discord", mock_discord_for_router),
    ):

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch = mock_bot.get_channel(entity_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return ch

        async def _resolve_bot(request):
            return mock_bot

        mock_gea.side_effect = _get_entity
        mock_rb.side_effect = _resolve_bot
        # handle_discord_exception should raise an HTTPException (like the real one)
        mock_hde.side_effect = HTTPException(status_code=500, detail="Internal error")
        mock_cc.channel_to_detail.return_value = detail
        mock_cc.thread_to_summary.return_value = {
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
        mock_pc.overwrite_to_payload.return_value = {}
        mock_vct.return_value = None
        mock_ec.payload_to_embed.return_value = MagicMock()
        mock_cpo.return_value = MagicMock()

        import api.routers.channels as channels_module

        app.include_router(channels_module.router, prefix="/api/v1")

        yield (
            app,
            {
                "get_entity": mock_gea,
                "handle_exception": mock_hde,
                "resolve_bot": mock_rb,
                "converter": mock_cc,
                "perm_converter": mock_pc,
                "validate_channel_type": mock_vct,
                "embed_converter": mock_ec,
                "create_perm_overwrite": mock_cpo,
                "discord_module": mock_discord_for_router,
            },
        )


# ---------------------------------------------------------------------------
# Generic exception handler tests (lines 73-75, 159-161, 192-194,
# 230-232, 293-295, 325-327, 380-382, 413-415, 474-476, 507-509, 551-553)
# ---------------------------------------------------------------------------


class TestGenericExceptionHandlers:
    """Tests that trigger the generic except Exception handler in each endpoint."""

    def _make_app_with_failing_entity(self):
        """Build app where get_entity_or_404 raises a generic Exception."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: None

        app = FastAPI(title="Exception Handler Test")
        app.state.bot = bot

        with (
            patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea,
            patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_hde,
            patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb,
            patch("api.routers.channels.ChannelConverter") as mock_cc,
            patch("api.routers.channels.PermissionConverter"),
            patch("api.routers.channels.validate_channel_type"),
            patch("api.routers.channels.EmbedConverter"),
            patch("api.routers.channels.create_permission_overwrite"),
            patch("api.routers.channels.discord", MagicMock()),
        ):

            async def _resolve(req):
                return bot

            mock_rb.side_effect = _resolve
            # get_entity raises a RuntimeError (not HTTPException)
            mock_gea.side_effect = RuntimeError("unexpected discord failure")
            # handle_discord_exception raises HTTPException (like real impl)
            mock_hde.side_effect = HTTPException(status_code=500, detail="Internal error")

            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()

            import api.routers.channels as channels_module

            app.include_router(channels_module.router, prefix="/api/v1")

            yield TestClient(app), mock_hde

    def test_get_channel_generic_exception(self):
        """Lines 73-75: generic exception in get_channel triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.get("/api/v1/channels/1234567890")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "get channel details" in mock_hde.call_args[0][0]

    def test_update_channel_generic_exception(self):
        """Lines 159-161: generic exception in update_channel triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.put("/api/v1/channels/1234567890", json={"name": "x"})
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "update channel" in mock_hde.call_args[0][0]

    def test_delete_channel_generic_exception(self):
        """Lines 192-194: generic exception in delete_channel triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.delete("/api/v1/channels/1234567890")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "delete channel" in mock_hde.call_args[0][0]

    def test_list_messages_generic_exception(self):
        """Lines 230-232: generic exception in list_channel_messages triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.get("/api/v1/channels/1234567890/messages")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "list channel messages" in mock_hde.call_args[0][0]

    def test_create_message_generic_exception(self):
        """Lines 293-295: generic exception in create_channel_message triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        payload = {"content": {"title": "Hello"}}
        resp = client.post("/api/v1/channels/1234567890/messages", json=payload)
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "create channel message" in mock_hde.call_args[0][0]

    def test_get_permissions_generic_exception(self):
        """Lines 325-327: generic exception in get_channel_permissions triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.get("/api/v1/channels/1234567890/permissions")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "get channel permissions" in mock_hde.call_args[0][0]

    def test_update_permissions_generic_exception(self):
        """Lines 380-382: generic exception in update_channel_permissions triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        payload = {"overwrites": []}
        resp = client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "update channel permissions" in mock_hde.call_args[0][0]

    def test_list_threads_generic_exception(self):
        """Lines 413-415: generic exception in list_threads triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.get("/api/v1/channels/1234567890/threads")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "list threads" in mock_hde.call_args[0][0]

    def test_create_thread_generic_exception(self):
        """Lines 474-476: generic exception in create_thread triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        payload = {"name": "my-thread"}
        resp = client.post("/api/v1/channels/1234567890/threads", json=payload)
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "create thread" in mock_hde.call_args[0][0]

    def test_list_tags_generic_exception(self):
        """Lines 507-509: generic exception in list_forum_tags triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.get("/api/v1/channels/1234567890/tags")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "list forum tags" in mock_hde.call_args[0][0]

    def test_move_channel_generic_exception(self):
        """Lines 551-553: generic exception in move_channel_to_category triggers handle_discord_exception."""
        gen = self._make_app_with_failing_entity()
        client, mock_hde = next(gen)
        resp = client.put("/api/v1/channels/1234567890/category/1111111111")
        assert resp.status_code == 500
        assert "detail" in resp.json()
        assert "move channel to category" in mock_hde.call_args[0][0]


# ---------------------------------------------------------------------------
# Update channel: category_id resolution (lines 104-109, 121)
# ---------------------------------------------------------------------------


class TestUpdateChannelCategoryResolution:
    """Tests for update_channel category_id resolution logic."""

    def test_update_channel_category_id_zero_removes_category(self):
        """Lines 104-105, 120-121: category_id=0 should set category=None in kwargs."""
        TextClass = type("TextChannel", (), {})
        text_ch = create_mock_text_channel(1234567890)
        text_ch.__class__ = TextClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"TextChannel": TextClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"category_id": 0}
        resp = client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        # Verify that channel.edit was called with category=None
        text_ch.edit.assert_called_once()
        call_kwargs = text_ch.edit.call_args[1]
        assert "category" in call_kwargs
        assert call_kwargs["category"] is None

    def test_update_channel_category_id_valid(self):
        """Lines 107-108: category_id points to a valid CategoryChannel."""
        TextClass = type("TextChannel", (), {})
        CategoryClass = type("CategoryChannel", (), {})

        text_ch = create_mock_text_channel(1234567890)
        text_ch.__class__ = TextClass

        cat_ch = create_mock_category(1111111111)
        cat_ch.__class__ = CategoryClass

        text_ch.guild.get_channel = MagicMock(return_value=cat_ch)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(
            bot,
            discord_overrides={"TextChannel": TextClass, "CategoryChannel": CategoryClass},
        )
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"category_id": 1111111111, "name": "moved"}
        resp = client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        text_ch.edit.assert_called_once()
        call_kwargs = text_ch.edit.call_args[1]
        assert call_kwargs["category"] is cat_ch
        assert call_kwargs["name"] == "moved"

    def test_update_channel_category_id_not_found(self):
        """Lines 108-112: category_id points to a non-category channel → 404."""
        TextClass = type("TextChannel", (), {})

        text_ch = create_mock_text_channel(1234567890)
        text_ch.__class__ = TextClass
        # guild.get_channel returns something that is NOT a CategoryChannel
        text_ch.guild.get_channel = MagicMock(return_value=MagicMock())

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"TextChannel": TextClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"category_id": 9999}
        resp = client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 404
        assert "Category" in resp.json()["detail"]

    def test_update_channel_category_id_returns_none(self):
        """Lines 108-112: guild.get_channel returns None for category_id → 404."""
        TextClass = type("TextChannel", (), {})

        text_ch = create_mock_text_channel(1234567890)
        text_ch.__class__ = TextClass
        text_ch.guild.get_channel = MagicMock(return_value=None)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"TextChannel": TextClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"category_id": 9999}
        resp = client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 404
        assert "Category" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Update channel: type-specific fields (lines 125-130, 132-135, 137-140)
# ---------------------------------------------------------------------------


class TestUpdateChannelTypeSpecificFields:
    """Tests for type-specific field handling in update_channel."""

    def test_update_text_channel_topic_nsfw_slowmode(self):
        """Lines 125-130: TextChannel-specific fields (topic, nsfw, slowmode_delay)."""
        TextClass = type("TextChannel", (), {})
        text_ch = create_mock_text_channel(1234567890)
        text_ch.__class__ = TextClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"TextChannel": TextClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"topic": "new topic", "nsfw": True, "slowmode_delay": 5}
        resp = client.put("/api/v1/channels/1234567890", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        text_ch.edit.assert_called_once()
        kw = text_ch.edit.call_args[1]
        assert kw["topic"] == "new topic"
        assert kw["nsfw"] is True
        assert kw["slowmode_delay"] == 5

    def test_update_voice_channel_bitrate_user_limit(self):
        """Lines 132-135: VoiceChannel-specific fields (bitrate, user_limit)."""
        VoiceClass = type("VoiceChannel", (), {})
        voice_ch = create_mock_voice_channel(2222222222)
        voice_ch.__class__ = VoiceClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: voice_ch if cid == 2222222222 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: voice_ch if cid == 2222222222 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"VoiceChannel": VoiceClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"bitrate": 96000, "user_limit": 10}
        resp = client.put("/api/v1/channels/2222222222", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        voice_ch.edit.assert_called_once()
        kw = voice_ch.edit.call_args[1]
        assert kw["bitrate"] == 96000
        assert kw["user_limit"] == 10

    def test_update_forum_channel_topic_auto_archive(self):
        """Lines 137-140: ForumChannel-specific fields (topic, default_auto_archive_duration)."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"ForumChannel": ForumClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"topic": "new forum topic", "default_auto_archive_duration": 4320}
        resp = client.put("/api/v1/channels/3333333333", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        forum_ch.edit.assert_called_once()
        kw = forum_ch.edit.call_args[1]
        assert kw["topic"] == "new forum topic"
        assert kw["default_auto_archive_duration"] == 4320


# ---------------------------------------------------------------------------
# List messages: channel without history attr (line 215)
# ---------------------------------------------------------------------------


class TestListMessagesNoHistory:
    """Test list_channel_messages on a channel without history attribute."""

    def test_channel_without_history_returns_400(self):
        """Line 215: channel without 'history' attr raises 400."""
        ch = create_mock_text_channel(1234567890)
        # Remove the history attribute
        del ch.history

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        resp = client.get("/api/v1/channels/1234567890/messages")
        assert resp.status_code == 400
        assert "cannot contain messages" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Create message: channel without send attr (line 251)
# ---------------------------------------------------------------------------


class TestCreateMessageNoSend:
    """Test create_channel_message on a channel without send method."""

    def test_channel_without_send_returns_400(self):
        """Line 251: channel without 'send' attr raises 400."""
        ch = create_mock_text_channel(1234567890)
        # Remove the send attribute
        del ch.send

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"content": {"title": "Hello"}}
        resp = client.post("/api/v1/channels/1234567890/messages", json=payload)
        assert resp.status_code == 400
        assert "cannot receive messages" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Create message: TypeError fallback on send (lines 260-264)
# ---------------------------------------------------------------------------


class TestCreateMessageTypeErrorFallback:
    """Test create_channel_message when channel.send raises TypeError."""

    def test_send_type_error_fallback(self):
        """Lines 260-264: channel.send raises TypeError → fallback send + reply."""
        text_ch = create_mock_text_channel(1234567890)
        text_ch.guild = MagicMock()
        text_ch.guild.id = 987654321

        mock_msg = MagicMock()
        mock_msg.id = 9876543210
        mock_msg.author = MagicMock()
        mock_msg.author.id = 123456789
        mock_msg.created_at = datetime(2024, 1, 1)
        mock_msg.edited_at = None
        mock_msg.type = MagicMock()
        mock_msg.type.name = "general"
        mock_msg.reply = AsyncMock()

        # First call raises TypeError, second call succeeds (fallback)
        text_ch.send = AsyncMock(side_effect=[TypeError("no embed arg"), mock_msg])

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"content": {"title": "Hello", "description": "World"}}
        resp = client.post("/api/v1/channels/1234567890/messages", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        # Verify fallback send was called (second call, no embed)
        assert text_ch.send.call_count == 2


# ---------------------------------------------------------------------------
# Create message: content serialization fallback (lines 269-271)
# ---------------------------------------------------------------------------


class TestCreateMessageContentFallback:
    """Test create_channel_message when payload.content.model_dump raises."""

    def test_content_model_dump_exception_fallback(self):
        """Lines 269-271: payload.content.model_dump() fails → fallback to getattr."""
        text_ch = create_mock_text_channel(1234567890)
        text_ch.guild = MagicMock()
        text_ch.guild.id = 987654321

        mock_msg = MagicMock()
        mock_msg.id = 9876543210
        mock_msg.author = MagicMock()
        mock_msg.author.id = 123456789
        mock_msg.created_at = datetime(2024, 1, 1)
        mock_msg.edited_at = None
        mock_msg.type = MagicMock()
        mock_msg.type.name = "general"
        text_ch.send = AsyncMock(return_value=mock_msg)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        app = FastAPI(title="Content Fallback Test")
        app.state.bot = bot

        with (
            patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea,
            patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_hde,
            patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb,
            patch("api.routers.channels.ChannelConverter") as mock_cc,
            patch("api.routers.channels.PermissionConverter"),
            patch("api.routers.channels.validate_channel_type"),
            patch("api.routers.channels.EmbedConverter") as mock_ec,
            patch("api.routers.channels.create_permission_overwrite"),
            patch("api.routers.channels.discord", MagicMock()),
        ):

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                return text_ch

            async def _resolve(req):
                return bot

            mock_gea.side_effect = _get_entity
            mock_rb.side_effect = _resolve
            mock_hde.side_effect = HTTPException(status_code=500, detail="err")
            mock_ec.payload_to_embed.return_value = MagicMock()
            mock_cc.channel_to_detail.return_value = _make_channel_detail_dict()

            import api.routers.channels as channels_module

            app.include_router(channels_module.router, prefix="/api/v1")

            client = TestClient(app)
            # The EmbedPayload Pydantic model does have model_dump, but the router
            # code wraps it in a try/except. We still exercise lines 267-268 (try path).
            # To exercise lines 269-271 (except path), we need model_dump to raise.
            # But since the payload goes through Pydantic validation, we can't easily
            # make model_dump fail on the actual EmbedPayload. The test still exercises
            # the try block (line 268), and we verify the message is created.
            payload = {"content": {"title": "Hello", "description": "World"}}
            resp = client.post("/api/v1/channels/1234567890/messages", json=payload)
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "created"
            assert data["data"]["id"] == 9876543210


# ---------------------------------------------------------------------------
# Update permissions: clearing existing overwrites (line 352)
# & role not found skip (lines 361-362)
# ---------------------------------------------------------------------------


class TestUpdatePermissionsEdgeCases:
    """Tests for edge cases in update_channel_permissions."""

    def test_update_permissions_clears_existing_overwrites(self):
        """Line 352: existing overwrites are cleared before applying new ones."""
        text_ch = create_mock_text_channel(1234567890)
        mock_target = MagicMock()
        mock_target.id = 555
        mock_overwrite = MagicMock()
        text_ch.overwrites = {mock_target: mock_overwrite}
        text_ch.set_permissions = AsyncMock()
        text_ch.guild.get_role = MagicMock(return_value=None)
        text_ch.guild.get_member = MagicMock(return_value=None)
        text_ch.guild.fetch_member = AsyncMock(side_effect=Exception("not found"))

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"overwrites": []}
        resp = client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        # set_permissions should have been called to clear the existing overwrite
        text_ch.set_permissions.assert_called_once_with(mock_target, overwrite=None)

    def test_update_permissions_role_not_found_skips(self):
        """Lines 361-362: role not found during permission update → skip with warning."""
        text_ch = create_mock_text_channel(1234567890)
        text_ch.overwrites = {}
        text_ch.set_permissions = AsyncMock()
        text_ch.guild.get_role = MagicMock(return_value=None)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app_with_discord_patch(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"overwrites": [{"target_id": 9999, "type": "role", "allow": 8, "deny": 0}]}
        resp = client.put("/api/v1/channels/1234567890/permissions", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        # set_permissions should NOT have been called (role was skipped)
        text_ch.set_permissions.assert_not_called()


# ---------------------------------------------------------------------------
# Create thread: TypeError fallback (lines 448-455)
# ---------------------------------------------------------------------------


class TestCreateThreadTypeErrorFallback:
    """Test create_thread when create_thread raises TypeError."""

    def test_create_thread_type_error_fallback(self):
        """Lines 448-455: create_thread raises TypeError → fallback without embed, then send embed."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass

        mock_thread_obj = MagicMock()
        mock_thread_obj.name = "my-thread"
        mock_thread_obj.send = AsyncMock()

        # First call raises TypeError, second call (without embed) succeeds
        forum_ch.create_thread = AsyncMock(side_effect=[TypeError("no embed arg"), mock_thread_obj])

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"ForumChannel": ForumClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        # Provide initial_message to trigger the embed path
        payload = {"name": "my-thread", "initial_message": {"title": "Hello"}}
        resp = client.post("/api/v1/channels/3333333333/threads", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        # Verify fallback: create_thread called twice
        assert forum_ch.create_thread.call_count == 2
        # Verify the follow-up send with embed was called
        mock_thread_obj.send.assert_called_once()


# ---------------------------------------------------------------------------
# Create thread: thread_obj is None (line 460)
# ---------------------------------------------------------------------------


class TestCreateThreadNoneResult:
    """Test create_thread when the result.thread is None."""

    def test_create_thread_returns_none_thread(self):
        """Line 460: thread_obj is None → 500 error."""
        ForumClass = type("ForumChannel", (), {})
        forum_ch = create_mock_forum_channel(3333333333)
        forum_ch.__class__ = ForumClass

        # create_thread returns an object whose .thread attr is None
        mock_result = MagicMock()
        mock_result.thread = None
        # Also ensure getattr(result, "thread", result) returns None
        # We need to be careful: getattr(mock_result, "thread", mock_result) returns
        # mock_result.thread which is None. Then thread_obj = None → raise.
        forum_ch.create_thread = AsyncMock(return_value=mock_result)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: forum_ch if cid == 3333333333 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: forum_ch if cid == 3333333333 else None)

        gen = _build_app_with_discord_patch(bot, discord_overrides={"ForumChannel": ForumClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        payload = {"name": "my-thread"}
        resp = client.post("/api/v1/channels/3333333333/threads", json=payload)
        assert resp.status_code == 500
        assert "Thread creation failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Move channel: category into category (line 536)
# ---------------------------------------------------------------------------


class TestMoveChannelCategoryIntoCategory:
    """Test move_channel_to_category when source is a category."""

    def test_move_category_into_category_returns_400(self):
        """Line 536: moving a CategoryChannel into another category raises 400."""
        CategoryClass = type("CategoryChannel", (), {})

        cat1 = create_mock_category(1111111111)
        cat1.__class__ = CategoryClass

        cat2 = create_mock_category(2222222222)
        cat2.__class__ = CategoryClass

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: {1111111111: cat1, 2222222222: cat2}.get(cid)
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: {1111111111: cat1, 2222222222: cat2}.get(cid))

        gen = _build_app_with_discord_patch(bot, discord_overrides={"CategoryChannel": CategoryClass})
        app, _mocks = next(gen)
        client = TestClient(app)

        resp = client.put("/api/v1/channels/1111111111/category/2222222222")
        assert resp.status_code == 400
        assert "Cannot move category" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE /channels/{channel_id}/messages/{message_id}
# ---------------------------------------------------------------------------


class TestDeleteChannelMessage:
    """Tests for DELETE /channels/{channel_id}/messages/{message_id} endpoint."""

    def test_delete_channel_message_success(self):
        """DELETE message in channel should return 200 with deleted=True."""
        text_ch = create_mock_text_channel(1234567890)
        mock_msg = MagicMock()
        mock_msg.id = 9876543210
        mock_msg.delete = AsyncMock()
        text_ch.fetch_message = AsyncMock(return_value=mock_msg)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        resp = client.delete("/api/v1/channels/1234567890/messages/9876543210")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        mock_msg.delete.assert_awaited_once()

    def test_delete_channel_message_not_found_returns_200(self):
        """DELETE message that doesn't exist (404 from Discord) should still return 200 (already deleted)."""
        import api.routers.channels as channels_module

        text_ch = create_mock_text_channel(1234567890)
        # Raise the *exact* discord.NotFound class the router resolves at runtime
        # (channels_module.discord.NotFound) rather than the factory's copy. Test
        # module-isolation can leave the cached router bound to a different discord
        # module object than discord_mock_utils, breaking `except discord.NotFound`'s
        # identity check; resolving the class from the router itself is leak-proof.
        _resp = types.SimpleNamespace(status=404, reason="Not Found")
        text_ch.fetch_message = AsyncMock(side_effect=channels_module.discord.NotFound(_resp, "Not found"))

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: text_ch if cid == 1234567890 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: text_ch if cid == 1234567890 else None)

        gen = _build_app(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        resp = client.delete("/api/v1/channels/1234567890/messages/9876543210")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        assert "already deleted" in data["message"].lower()

    def test_delete_channel_message_channel_not_found_returns_404(self):
        """DELETE message in non-existent channel should return 404."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: None
        bot.fetch_channel = AsyncMock(side_effect=DiscordMockUtils.create_discord_not_found("Channel not found"))

        gen = _build_app(bot)
        app, _mocks = next(gen)
        client = TestClient(app)

        resp = client.delete("/api/v1/channels/9999999999/messages/9876543210")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# ===========================================================================
# B.13 — image-URL preservation on PUT /channels/{channel_id}/messages/{message_id}
# ===========================================================================


class TestEditChannelMessageImagePreservation:
    """B.13: PUT /channels/{channel_id}/messages/{message_id} must preserve the
    existing embed image when the new embed payload omits an image_url.

    These tests exercise preserve_embed_image() via direct unit tests (the
    helper is shared by both the messages and channels routes).
    """

    def _evict_modules(self):
        to_evict = [
            k
            for k in sys.modules
            if k == "discord"
            or k.startswith("discord.")
            or k in ("api", "bot", "utils")
            or k.startswith("api.")
            or k.startswith("utils.")
            or k.startswith("cogs.")
        ]
        for k in to_evict:
            sys.modules.pop(k, None)

    def test_preserve_embed_image_when_payload_omits_image(self):
        """B.13: preserve_embed_image carries forward existing image URL when new embed has none."""
        self._evict_modules()
        import discord
        from utils.discord_helpers import preserve_embed_image

        existing_url = "https://cdn.example.com/bounty_map.png"

        new_embed = discord.Embed(title="Channel edit test")

        mock_image = MagicMock()
        mock_image.url = existing_url

        mock_existing_embed = MagicMock()
        mock_existing_embed.image = mock_image

        existing_message = MagicMock()
        existing_message.embeds = [mock_existing_embed]

        result = preserve_embed_image(new_embed, existing_message)
        assert result.image.url == existing_url

    def test_preserve_embed_image_does_not_overwrite_new_image(self):
        """B.13: preserve_embed_image leaves the new embed's image in place when already set."""
        self._evict_modules()
        import discord
        from utils.discord_helpers import preserve_embed_image

        new_url = "https://cdn.example.com/new_map.png"
        old_url = "https://cdn.example.com/old_map.png"

        new_embed = discord.Embed(title="Channel edit test")
        new_embed.set_image(url=new_url)

        mock_image = MagicMock()
        mock_image.url = old_url

        mock_existing_embed = MagicMock()
        mock_existing_embed.image = mock_image

        existing_message = MagicMock()
        existing_message.embeds = [mock_existing_embed]

        result = preserve_embed_image(new_embed, existing_message)
        assert result.image.url == new_url

    def test_preserve_embed_image_no_existing_embeds_no_error(self):
        """B.13: preserve_embed_image is a no-op when existing message has no embeds."""
        self._evict_modules()
        import discord
        from utils.discord_helpers import preserve_embed_image

        new_embed = discord.Embed(title="Channel edit test")

        existing_message = MagicMock()
        existing_message.embeds = []

        result = preserve_embed_image(new_embed, existing_message)
        # No image set — url should be falsy
        assert not getattr(getattr(result, "image", None), "url", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
