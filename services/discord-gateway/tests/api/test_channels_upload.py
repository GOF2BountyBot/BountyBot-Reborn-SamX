"""Tests for POST /channels/{channel_id}/upload endpoint.

Tests the file upload endpoint added to the channels router.
Written BEFORE the implementation (TDD).

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap. No patches on ``get_entity_or_404``,
``handle_discord_exception`` or ``discord.File`` — the real ``resolve_bot``/
``get_entity_or_404`` helpers run against a ``spec=commands.Bot`` mock, and a
real ``discord.File`` is constructed from the uploaded bytes exactly as
production does (it is a lazy wrapper — no network I/O happens until
``channel.send`` actually consumes it, and that stays a mock since a
live Discord channel can't be constructed in tests).

``resolve_bot`` stays patched for exactly one scenario
(``test_upload_file_bot_not_ready``): the real helper's not-ready branch
waits up to 15s on ``bot.wait_until_ready()`` before timing out, which is a
genuine process/timing boundary unsuitable for a fast unit test. The
"invalid bot instance" 500 case is instead driven for real by giving
``app.state.bot`` a non-``commands.Bot`` object.
"""

import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

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
def _build_upload_app(mock_bot, resolve_bot_side_effect=None):
    """Build a FastAPI test app with the channels router and a real bot state.

    ``resolve_bot`` is only patched when the caller explicitly supplies
    ``resolve_bot_side_effect`` (the not-ready-timeout boundary case); every
    other helper (``get_entity_or_404``, ``handle_discord_exception``,
    ``discord.File``) runs for real.
    """
    app = FastAPI(title="Upload Test")
    app.state.bot = mock_bot

    if resolve_bot_side_effect is not None:
        with patch("api.routers.channels.resolve_bot", new_callable=AsyncMock) as mock_rb:
            mock_rb.side_effect = resolve_bot_side_effect

            from api.routers.channels import router

            app.include_router(router, prefix="/api/v1")

            yield app
        return

    from api.routers.channels import router

    app.include_router(router, prefix="/api/v1")

    yield app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot_with_channel():
    """Bot with a text channel that can receive uploads.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so the
    real ``get_entity_or_404`` chain produces a genuine 404.
    """
    channel = _make_mock_channel_with_send(channel_id=1234567890)
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(cid):
        return channel if cid == 1234567890 else None

    async def fetch_channel(cid):
        found = get_channel(cid)
        if found is None:
            raise create_discord_not_found(f"Channel {cid} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
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
        """channel.send should be called with a real discord.File wrapping the uploaded bytes."""
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
        sent_file = mock_channel.send.call_args.kwargs["file"]
        assert isinstance(sent_file, discord.File)
        assert sent_file.filename == "test.png"


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
        assert data["data"]["filename"] == "upload.png"
        # The real endpoint passed the default filename into a real discord.File.
        assert mock_channel.send.call_args.kwargs["file"].filename == "upload.png"

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
        """POST to a non-existent channel_id should return a real 404 via get_entity_or_404."""
        mock_bot, _mock_channel = mock_bot_with_channel

        with _build_upload_app(mock_bot) as app:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/channels/9999999999/upload",
                content=b"fake png",
                headers={"Content-Type": "image/png", "X-Filename": "route_map.png"},
            )

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


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
        """POST when bot is not ready should return 503.

        ``resolve_bot`` is patched here only because the real not-ready
        branch waits up to 15s on ``bot.wait_until_ready()`` before timing
        out — a genuine timing boundary unsuitable for a fast unit test.
        """
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
        """POST when app.state.bot is not a commands.Bot should return 500 via the real resolve_bot check."""
        app = FastAPI(title="Upload Test")
        app.state.bot = MagicMock()  # not a commands.Bot instance — trips the real isinstance check

        from api.routers.channels import router

        app.include_router(router, prefix="/api/v1")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/channels/1234567890/upload",
            content=b"fake png",
            headers={"Content-Type": "image/png"},
        )

        assert resp.status_code == 500
        assert "bot instance invalid" in resp.json()["detail"].lower()


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
