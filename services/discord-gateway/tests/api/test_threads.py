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
"""

import importlib
import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

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

# Setup mock discord module with discord_mock_utils
# create_mock_discord_module() already wires real discord exception classes
# (NotFound, Forbidden, HTTPException) so except clauses work correctly.
_mock_discord = _mock_utils.create_mock_discord_module_with_factories()

# Mock discord.ext with MagicMock Bot for module-level compatibility
_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = MagicMock

_mock_discord.ext = _mock_discord_ext

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_discord():
    """
    Re-assert the real discord module into sys.modules before each test
    and reload api.routers.threads so its ``discord`` reference is fresh.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so create_discord_not_found() uses real discord
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force the threads router to re-bind its 'discord' global to real discord
    from api.routers import threads as _threads_mod
    importlib.reload(_threads_mod)
    yield


def create_mock_thread(thread_id=1234567890):
    """Create a mock Discord thread using DiscordMockUtils."""
    thread = DiscordMockUtils.create_mock_thread(
        thread_id=thread_id,
        name="Test Thread",
        archived=False,
        locked=False,
    )
    thread.edit = AsyncMock()
    thread.send = AsyncMock()
    thread.fetch_message = AsyncMock()
    return thread


def create_mock_message(message_id=999999999):
    """Create a mock Discord message using DiscordMockUtils."""
    msg = DiscordMockUtils.create_mock_message(
        message_id=message_id,
        content="test message",
        author_id=123456789,
    )
    msg.embeds = []
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.guilds = []
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)
    return bot


@pytest.fixture
def threads_test_app(mock_bot):
    """Create a test FastAPI app with the threads router and mocked dependencies."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    # Create mock thread and message objects
    mock_thread = create_mock_thread(1234567890)
    mock_message = create_mock_message(999999999)
    mock_thread.fetch_message = AsyncMock(return_value=mock_message)
    # make thread.send return a mock message
    mock_thread.send = AsyncMock(return_value=mock_message)
    # make thread.history an async generator returning empty list
    async def _empty_history(limit=100):
        return
        yield  # make it an async generator

    mock_thread.history = MagicMock(return_value=_empty_history())

    with patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.threads.find_thread_by_id") as mock_find, \
         patch("api.routers.threads.ChannelConverter") as mock_channel_converter, \
         patch("api.routers.threads.MessageConverter") as mock_message_converter, \
         patch("api.routers.threads.EmbedConverter") as mock_embed_converter:

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        def _find_thread(bot, thread_id):
            if thread_id == 1234567890:
                return mock_thread
            return None

        mock_find.side_effect = _find_thread

        # ChannelConverter.thread_to_detail returns a Thread schema object
        from api.schemas.channel_schemas import Thread as ThreadSchema
        _mock_thread_data = ThreadSchema(
            id=1234567890,
            name="Test Thread",
            channel_id=555555555,
            guild_id=987654321,
            owner_id=111111111,
            archived=False,
            locked=False,
            message_count=0,
            member_count=0,
            created_at="2024-01-01T00:00:00",
        )
        mock_channel_converter.thread_to_detail.return_value = _mock_thread_data

        # MessageConverter.message_to_payload returns a Message schema object
        from api.schemas.message_schemas import Message as MessageSchema
        _mock_message_data = MessageSchema(
            id=999999999,
            channel_id=1234567890,
            guild_id=987654321,
            author_id=123456789,
            content=None,
            timestamp=datetime(2024, 1, 1),
        )
        mock_message_converter.message_to_payload.return_value = _mock_message_data

        # EmbedConverter mock for create_thread_message
        mock_embed_converter.payload_to_embed.return_value = MagicMock()

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
        """GET /threads/{thread_id} should return 200 with thread details."""
        response = threads_client.get("/api/v1/threads/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"]["id"] == 1234567890

    def test_get_thread_not_found_returns_404(self, threads_client):
        """GET /threads/{thread_id} should return 404 for non-existent thread."""
        response = threads_client.get("/api/v1/threads/9999999999")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestUpdateThread:
    """Tests for PUT /threads/{thread_id} endpoint."""

    def test_update_thread_success(self, threads_client):
        """PUT /threads/{thread_id} should update thread successfully."""
        update_data = {
            "name": "Updated Thread Name",
        }
        response = threads_client.put("/api/v1/threads/1234567890", json=update_data)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "updated"
        assert "data" in data

    def test_update_thread_not_found(self, threads_client):
        """PUT /threads/{thread_id} should return 404 for non-existent thread."""
        update_data = {"name": "Updated Thread Name"}
        response = threads_client.put("/api/v1/threads/9999999999", json=update_data)
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestCloseThread:
    """Tests for PUT /threads/{thread_id}/close endpoint."""

    def test_close_thread_success(self, threads_client):
        """PUT /threads/{thread_id}/close should close thread successfully."""
        response = threads_client.put("/api/v1/threads/1234567890/close")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "closed"

    def test_close_thread_not_found(self, threads_client):
        """PUT /threads/{thread_id}/close should return 404 for non-existent thread."""
        response = threads_client.put("/api/v1/threads/9999999999/close")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestOpenThread:
    """Tests for PUT /threads/{thread_id}/open endpoint."""

    def test_open_thread_success(self, threads_client):
        """PUT /threads/{thread_id}/open should open thread successfully."""
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

    def test_get_messages_success(self, threads_client):
        """GET /threads/{thread_id}/messages should return 200 with message list."""
        response = threads_client.get("/api/v1/threads/1234567890/messages")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_get_messages_not_found(self, threads_client):
        """GET /threads/{thread_id}/messages should return 404 for non-existent thread."""
        response = threads_client.get("/api/v1/threads/9999999999/messages")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestCreateThreadMessage:
    """Tests for POST /threads/{thread_id}/messages endpoint."""

    def test_create_message_success(self, threads_client):
        """POST /threads/{thread_id}/messages should create message successfully."""
        # The create_thread_message endpoint expects a MessageCreateRequest
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
        assert "data" in data

    def test_create_message_not_found(self, threads_client):
        """POST /threads/{thread_id}/messages should return 404 for non-existent thread."""
        message_data = {"content": {"title": "Hello"}}
        response = threads_client.post("/api/v1/threads/9999999999/messages", json=message_data)
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestGetThreadMessage:
    """Tests for GET /threads/{thread_id}/messages/{message_id} endpoint."""

    def test_get_thread_message_success(self, threads_client):
        """GET /threads/{thread_id}/messages/{message_id} should return 200."""
        response = threads_client.get("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "found"
        assert "data" in data

    def test_get_thread_message_thread_not_found(self, threads_client):
        """GET /threads/{thread_id}/messages/{message_id} should return 404 if thread not found."""
        response = threads_client.get("/api/v1/threads/9999999999/messages/999999999")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()


class TestErrorHandling:
    """Tests for error handling in threads endpoints."""

    def test_handle_discord_exception(self, threads_client):
        """Threads endpoints should handle Discord exceptions gracefully."""
        from fastapi import HTTPException as FastAPIHTTPException
        with patch("api.routers.threads.resolve_bot", side_effect=Exception("Test Discord error")), \
             patch("api.routers.threads.handle_discord_exception",
                   side_effect=FastAPIHTTPException(status_code=500, detail="Internal server error")):
            response = threads_client.get("/api/v1/threads/1234567890")
            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()
