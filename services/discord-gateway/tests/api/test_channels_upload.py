"""Tests for POST /channels/{channel_id}/upload endpoint.

Tests the file upload endpoint added to the channels router.
Written BEFORE the implementation (TDD).
"""

import os
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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

# Setup mock discord module with real exception classes so isinstance checks work.
_mock_discord = DiscordMockUtils.create_mock_discord_module()

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThread = type("Thread", (), {})
_MockEmbed = type("Embed", (), {})
_MockFile = type("File", (), {})

_mock_discord.CategoryChannel = _MockCategoryChannel
_mock_discord.TextChannel = _MockTextChannel
_mock_discord.VoiceChannel = _MockVoiceChannel
_mock_discord.ForumChannel = _MockForumChannel
_mock_discord.Thread = _MockThread
_mock_discord.Embed = _MockEmbed
_mock_discord.File = _MockFile

_MockBot = type("Bot", (), {})
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _MockBot

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_mock_attachment(
    url="https://cdn.discordapp.com/attachments/123/456/route_map.png",
    filename="route_map.png",
    size=12345,
):
    """Create a mock Discord attachment."""
    attachment = MagicMock()
    attachment.url = url
    attachment.filename = filename
    attachment.size = size
    return attachment


def _make_mock_message(message_id=999, attachments=None):
    """Create a mock Discord message with attachments."""
    msg = MagicMock()
    msg.id = message_id
    msg.attachments = attachments if attachments is not None else [_make_mock_attachment()]
    return msg


def _make_mock_channel_with_send(channel_id=1234567890, send_return=None):
    """Create a mock channel that has a .send() method."""
    channel = DiscordMockUtils.create_mock_channel(
        channel_id=channel_id,
        name="bot-images",
        channel_type="text",
        position=1,
        guild_id=987654321,
    )
    channel.send = AsyncMock(return_value=send_return or _make_mock_message())
    return channel


def _make_channel_without_send(channel_id=5555555555):
    """Create a mock channel object that does NOT have a send attribute."""
    channel = MagicMock(spec=[])  # empty spec means no attributes by default
    channel.id = channel_id
    return channel


# ---------------------------------------------------------------------------
# App-builder helper
# ---------------------------------------------------------------------------


@contextmanager
def _build_upload_app(
    mock_bot,
    resolve_bot_side_effect=None,
    get_entity_side_effect=None,
    handle_exception_side_effect=None,
):
    """Build a FastAPI test app with channels router and patched helpers."""
    app = FastAPI(title="Upload Test")
    app.state.bot = mock_bot

    # discord.File mock that accepts constructor args without error
    mock_discord_file = MagicMock()
    mock_discord_file.return_value = MagicMock()  # instance returned by discord.File(...)

    # Build a mock discord module with File and other needed attrs
    mock_discord_module = MagicMock()
    mock_discord_module.File = mock_discord_file
    mock_discord_module.CategoryChannel = _MockCategoryChannel
    mock_discord_module.TextChannel = _MockTextChannel
    mock_discord_module.VoiceChannel = _MockVoiceChannel
    mock_discord_module.ForumChannel = _MockForumChannel

    with (
        patch("api.routers.channels.discord", mock_discord_module),
        patch("api.routers.channels.get_entity_or_404", new_callable=AsyncMock) as mock_gea,
        patch("api.routers.channels.handle_discord_exception", new_callable=AsyncMock) as mock_hde,
        patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb,
        patch("api.routers.channels.ChannelConverter") as mock_cc,
        patch("api.routers.channels.PermissionConverter"),
        patch("api.routers.channels.validate_channel_type"),
        patch("api.routers.channels.EmbedConverter"),
        patch("api.routers.channels.create_permission_overwrite"),
    ):
        if resolve_bot_side_effect is not None:
            mock_rb.side_effect = resolve_bot_side_effect
        else:

            async def _resolve(req):
                return mock_bot

            mock_rb.side_effect = _resolve

        if get_entity_side_effect is not None:
            mock_gea.side_effect = get_entity_side_effect
        else:

            async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
                ch = mock_bot.get_channel(entity_id)
                if ch is None:
                    raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
                return ch

            mock_gea.side_effect = _get_entity

        if handle_exception_side_effect is not None:
            mock_hde.side_effect = handle_exception_side_effect
        else:
            mock_hde.return_value = None

        mock_cc.channel_to_detail.return_value = {
            "id": 1234567890,
            "name": "bot-images",
            "type": "text",
            "position": 1,
            "guild_id": 987654321,
            "category_id": None,
            "created_at": "2024-01-01T00:00:00",
            "topic": None,
            "nsfw": False,
            "slowmode_delay": 0,
        }

        from api.routers.channels import router

        app.include_router(router, prefix="/api/v1")

        yield app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot_with_channel():
    """Bot with a text channel that can receive uploads."""
    channel = _make_mock_channel_with_send(channel_id=1234567890)
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.get_channel = lambda cid: channel if cid == 1234567890 else None
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channel if cid == 1234567890 else None)
    return bot, channel


# ---------------------------------------------------------------------------
# Test: Successful upload with X-Filename header
# ---------------------------------------------------------------------------


class TestUploadFileSuccess:
    """POST /channels/{channel_id}/upload — success cases."""

    def test_upload_file_success(self, mock_bot_with_channel):
        """POST raw bytes with X-Filename header → 201, response has message_id and attachment_url."""
        mock_bot, mock_channel = mock_bot_with_channel
        mock_attachment = _make_mock_attachment(
            url="https://cdn.discordapp.com/attachments/123/456/route_map.png",
            filename="route_map.png",
            size=12345,
        )
        mock_message = _make_mock_message(message_id=999, attachments=[mock_attachment])
        mock_channel.send = AsyncMock(return_value=mock_message)

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=png_bytes,
                headers={"Content-Type": "image/png", "X-Filename": "route_map.png"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert "data" in data
        assert data["data"]["message_id"] == 999
        assert data["data"]["attachment_url"] == "https://cdn.discordapp.com/attachments/123/456/route_map.png"
        assert data["data"]["filename"] == "route_map.png"
        assert data["data"]["size"] == 12345

    def test_upload_file_channel_called_with_discord_file(self, mock_bot_with_channel):
        """channel.send should be called (file upload reaches Discord)."""
        mock_bot, mock_channel = mock_bot_with_channel
        mock_attachment = _make_mock_attachment()
        mock_message = _make_mock_message(message_id=777, attachments=[mock_attachment])
        mock_channel.send = AsyncMock(return_value=mock_message)

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"fake png data",
                headers={"Content-Type": "image/png", "X-Filename": "test.png"},
            )

        assert resp.status_code == 201
        mock_channel.send.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Default filename when X-Filename header is absent
# ---------------------------------------------------------------------------


class TestUploadFileDefaultFilename:
    """POST without X-Filename header → uses 'upload.png' as default."""

    def test_upload_file_default_filename(self, mock_bot_with_channel):
        """When X-Filename header is not provided, filename defaults to 'upload.png'."""
        mock_bot, mock_channel = mock_bot_with_channel
        mock_attachment = _make_mock_attachment(filename="upload.png", url="https://cdn.example.com/upload.png")
        mock_message = _make_mock_message(message_id=888, attachments=[mock_attachment])
        mock_channel.send = AsyncMock(return_value=mock_message)

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"fake image data",
                headers={"Content-Type": "image/png"},
                # No X-Filename header
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        # The endpoint receives default filename 'upload.png' and calls channel.send
        # The response filename comes from the attachment mock (which we set to 'upload.png')
        assert data["data"]["filename"] == "upload.png"

    def test_upload_file_custom_filename_in_response(self, mock_bot_with_channel):
        """Filename from X-Filename header is passed to Discord and returned in the response."""
        mock_bot, mock_channel = mock_bot_with_channel
        mock_attachment = _make_mock_attachment(filename="my_custom_map.png")
        mock_message = _make_mock_message(message_id=555, attachments=[mock_attachment])
        mock_channel.send = AsyncMock(return_value=mock_message)

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"image bytes",
                headers={"Content-Type": "image/png", "X-Filename": "my_custom_map.png"},
            )

        assert resp.status_code == 201
        assert resp.json()["data"]["filename"] == "my_custom_map.png"


# ---------------------------------------------------------------------------
# Test: Empty body → 400
# ---------------------------------------------------------------------------


class TestUploadFileEmptyBody:
    """POST with empty body → 400 error."""

    def test_upload_file_empty_body(self, mock_bot_with_channel):
        """POST with empty body should return 400."""
        mock_bot, _mock_channel = mock_bot_with_channel

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"",
                headers={"Content-Type": "image/png", "X-Filename": "empty.png"},
            )

        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower() or "body" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: Channel not found → 404
# ---------------------------------------------------------------------------


class TestUploadFileChannelNotFound:
    """POST to non-existent channel → 404."""

    def test_upload_file_channel_not_found(self, mock_bot_with_channel):
        """POST to a non-existent channel_id should return 404."""
        mock_bot, _mock_channel = mock_bot_with_channel

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/9999999999/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png", "X-Filename": "route_map.png"},
            )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: Channel without send capability → 400
# ---------------------------------------------------------------------------


class TestUploadFileChannelCannotSend:
    """Channel without send capability → 400."""

    def test_upload_file_channel_cannot_send(self):
        """POST to a channel without .send should return 400."""
        # Create a bot with a channel that has no 'send' attribute
        no_send_channel = _make_channel_without_send(channel_id=7777777777)

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = lambda cid: no_send_channel if cid == 7777777777 else None
        bot.fetch_channel = AsyncMock(side_effect=lambda cid: no_send_channel if cid == 7777777777 else None)

        with _build_upload_app(bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/7777777777/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png", "X-Filename": "route_map.png"},
            )

        assert resp.status_code == 400
        assert "cannot" in resp.json()["detail"].lower() or "send" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: Bot not ready → 503
# ---------------------------------------------------------------------------


class TestUploadFileBotNotReady:
    """Bot not ready → 503."""

    def test_upload_file_bot_not_ready(self):
        """POST when bot is not ready should return 503."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        async def _bot_not_ready(req):
            raise HTTPException(status_code=503, detail="Discord bot is not ready")

        with _build_upload_app(bot, resolve_bot_side_effect=_bot_not_ready) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png", "X-Filename": "route_map.png"},
            )

        assert resp.status_code == 503
        assert "not ready" in resp.json()["detail"].lower()

    def test_upload_file_bot_invalid_instance(self):
        """POST when bot state is invalid should return 500."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        async def _bot_invalid(req):
            raise HTTPException(status_code=500, detail="Bot instance invalid")

        with _build_upload_app(bot, resolve_bot_side_effect=_bot_invalid) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png"},
            )

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Test: No attachments returned from Discord → 500
# ---------------------------------------------------------------------------


class TestUploadFileNoAttachments:
    """Message sent but Discord returns no attachments → 500."""

    def test_upload_file_no_attachments_returned(self, mock_bot_with_channel):
        """If Discord message has no attachments, return 500."""
        mock_bot, mock_channel = mock_bot_with_channel
        # Message with empty attachments list
        mock_message = _make_mock_message(message_id=111, attachments=[])
        mock_channel.send = AsyncMock(return_value=mock_message)

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/1234567890/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png", "X-Filename": "map.png"},
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"].lower()
        assert "attachment" in detail or "no attachment" in detail
