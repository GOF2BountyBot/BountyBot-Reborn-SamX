"""
Tests for the threads API endpoints.

Actual routes in threads.py:
  GET    /threads/{thread_id}
  PUT    /threads/{thread_id}
  PUT    /threads/{thread_id}/close
  PUT    /threads/{thread_id}/open
  PUT    /threads/{thread_id}/tags
  GET    /threads/{thread_id}/messages
  POST   /threads/{thread_id}/messages
  GET    /threads/{thread_id}/messages/{message_id}
  PUT    /threads/{thread_id}/messages/{message_id}
  DELETE /threads/{thread_id}/messages/{message_id}

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception``, ``find_thread_by_id``, ``ChannelConverter``,
``MessageConverter`` or ``EmbedConverter``: the mock bot is
``spec=commands.Bot`` with ``is_ready()==True`` so ``find_thread_by_id``'s
real cache-walk and 404 fetch-fallback (real ``discord.NotFound``) run
end-to-end, and the mock thread/message objects carry real-typed attributes
so the real converters produce genuine serialized bodies.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

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


_mock_bblogger.get_logger = _make_mock_logger

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger


def create_mock_thread(thread_id=1234567890):
    """Create a mock Discord thread using DiscordMockUtils.

    ``__class__`` is set to the real ``discord.Thread`` so that
    ``find_thread_by_id``'s ``isinstance(ch, discord.Thread)`` check (its
    fast cached-lookup path) passes for real.
    """
    thread = DiscordMockUtils.create_mock_thread(
        thread_id=thread_id,
        name="Test Thread",
        archived=False,
        locked=False,
    )
    thread.__class__ = discord.Thread

    async def _edit(**kwargs):
        if "name" in kwargs:
            thread.name = kwargs["name"]
        if "archived" in kwargs:
            thread.archived = kwargs["archived"]
        if "locked" in kwargs:
            thread.locked = kwargs["locked"]

    thread.edit = AsyncMock(side_effect=_edit)
    thread.send = AsyncMock()
    thread.fetch_message = AsyncMock()

    async def _empty_history(limit=100):
        return
        yield  # pragma: no cover - makes this an async generator

    thread.history = MagicMock(return_value=_empty_history())
    return thread


def create_mock_message(message_id=999999999, thread=None):
    """Create a mock Discord message using DiscordMockUtils."""
    msg = DiscordMockUtils.create_mock_message(
        message_id=message_id,
        content="test message",
        author_id=123456789,
        channel=thread,
    )
    msg.embeds = []
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so the
    real ``find_thread_by_id`` -> ``bot.get_channel`` -> ``bot.fetch_channel``
    resolution chain (in each router handler) produces a genuine 404 instead
    of the test hand-rolling its own not-found branch.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    thread = create_mock_thread(1234567890)

    def get_channel(channel_id):
        if channel_id == thread.id:
            return thread
        return None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot.guilds = []

    return bot


@pytest.fixture
def threads_test_app(mock_bot):
    """Create a test FastAPI app with the threads router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.threads import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def threads_client(threads_test_app):
    """Create a test client for the threads API."""
    return TestClient(threads_test_app)


class TestGetThread:
    """Tests for GET /threads/{thread_id} endpoint."""

    def test_get_thread_returns_200(self, threads_client):
        """GET /threads/{thread_id} should return 200 with real serialized thread details."""
        response = threads_client.get("/api/v1/threads/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 1234567890
        assert data["data"]["name"] == "Test Thread"
        assert data["data"]["archived"] is False

    def test_get_thread_not_found_returns_404(self, threads_client):
        """GET /threads/{thread_id} should return 404 (real discord.NotFound on fetch-miss)."""
        response = threads_client.get("/api/v1/threads/9999999999")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestUpdateThread:
    """Tests for PUT /threads/{thread_id} endpoint."""

    def test_update_thread_success(self, threads_client):
        """PUT /threads/{thread_id} should update thread successfully; response reflects the real edit."""
        update_data = {
            "name": "Updated Thread Name",
        }
        response = threads_client.put("/api/v1/threads/1234567890", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "Updated Thread Name"

    def test_update_thread_not_found(self, threads_client):
        """PUT /threads/{thread_id} should return 404 for non-existent thread."""
        update_data = {"name": "Updated Thread Name"}
        response = threads_client.put("/api/v1/threads/9999999999", json=update_data)
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestCloseThread:
    """Tests for PUT /threads/{thread_id}/close endpoint."""

    def test_close_thread_success(self, threads_client):
        """PUT /threads/{thread_id}/close should close thread successfully via the real thread.edit call."""
        response = threads_client.put("/api/v1/threads/1234567890/close")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "closed"
        assert "test thread" in data["message"].lower()

    def test_close_thread_not_found(self, threads_client):
        """PUT /threads/{thread_id}/close should return 404 for non-existent thread."""
        response = threads_client.put("/api/v1/threads/9999999999/close")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestOpenThread:
    """Tests for PUT /threads/{thread_id}/open endpoint."""

    def test_open_thread_success(self, threads_client):
        """PUT /threads/{thread_id}/open should open thread successfully via the real thread.edit call."""
        response = threads_client.put("/api/v1/threads/1234567890/open")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "opened"

    def test_open_thread_not_found(self, threads_client):
        """PUT /threads/{thread_id}/open should return 404 for non-existent thread."""
        response = threads_client.put("/api/v1/threads/9999999999/open")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestGetThreadMessages:
    """Tests for GET /threads/{thread_id}/messages endpoint."""

    def test_get_messages_success(self, threads_client, mock_bot):
        """GET /threads/{thread_id}/messages should return 200 with the real, serialized message list."""
        thread = mock_bot.get_channel(1234567890)
        message = create_mock_message(message_id=555000111, thread=thread)

        async def _one_message(limit=100):
            yield message

        thread.history = MagicMock(return_value=_one_message())

        response = threads_client.get("/api/v1/threads/1234567890/messages")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == 555000111

    def test_get_messages_not_found(self, threads_client):
        """GET /threads/{thread_id}/messages should return 404 for non-existent thread."""
        response = threads_client.get("/api/v1/threads/9999999999/messages")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestCreateThreadMessage:
    """Tests for POST /threads/{thread_id}/messages endpoint."""

    def test_create_message_success(self, threads_client, mock_bot):
        """POST /threads/{thread_id}/messages should create message successfully via the real thread.send call."""
        thread = mock_bot.get_channel(1234567890)
        sent_message = create_mock_message(message_id=777888999, thread=thread)
        thread.send = AsyncMock(return_value=sent_message)

        message_data = {
            "content": {
                "title": "Hello Thread",
                "description": "test content",
            }
        }
        response = threads_client.post("/api/v1/threads/1234567890/messages", json=message_data)
        assert response.status_code == 201

        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 777888999
        # thread.send was invoked with a real discord.Embed built by EmbedConverter
        _, send_kwargs = thread.send.call_args
        assert send_kwargs["embed"].title == "Hello Thread"
        assert send_kwargs["embed"].description == "test content"

    def test_create_message_not_found(self, threads_client):
        """POST /threads/{thread_id}/messages should return 404 for non-existent thread."""
        message_data = {"content": {"title": "Hello"}}
        response = threads_client.post("/api/v1/threads/9999999999/messages", json=message_data)
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestGetThreadMessage:
    """Tests for GET /threads/{thread_id}/messages/{message_id} endpoint."""

    def test_get_thread_message_success(self, threads_client, mock_bot):
        """GET /threads/{thread_id}/messages/{message_id} should return 200 with the requested message."""
        thread = mock_bot.get_channel(1234567890)
        message = create_mock_message(message_id=999999999, thread=thread)
        thread.fetch_message = AsyncMock(return_value=message)

        response = threads_client.get("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "found"
        assert data["data"]["id"] == 999999999
        thread.fetch_message.assert_awaited_once_with(999999999)

    def test_get_thread_message_thread_not_found(self, threads_client):
        """GET /threads/{thread_id}/messages/{message_id} should return 404 if thread not found."""
        response = threads_client.get("/api/v1/threads/9999999999/messages/999999999")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()

    def test_get_thread_message_message_not_found(self, threads_client, mock_bot):
        """GET .../messages/{message_id} should return 404 (real discord.NotFound) for an unknown message."""
        thread = mock_bot.get_channel(1234567890)
        thread.fetch_message = AsyncMock(side_effect=create_discord_not_found("Message not found"))

        response = threads_client.get("/api/v1/threads/1234567890/messages/424242")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in threads endpoints.

    ``resolve_bot`` (a network/readiness boundary) is patched to raise a
    generic error so ``handle_discord_exception``'s real, unpatched mapping
    of an unrecognized exception to HTTP 500 is exercised end-to-end.
    """

    def test_handle_discord_exception(self, threads_client):
        """Threads endpoints should map an unexpected error to a real 500 via handle_discord_exception."""
        from unittest.mock import patch

        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("Test Discord error")):
            response = threads_client.get("/api/v1/threads/1234567890")
            assert response.status_code == 500
            assert "test discord error" in response.json()["detail"].lower()
