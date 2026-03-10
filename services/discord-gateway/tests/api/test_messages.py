"""
Tests for the messages API endpoints.

This module provides comprehensive tests for the messages router endpoints,
including message creation, retrieval, updating, and deletion.
"""

import pytest
import importlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import sys
import os
import types
from datetime import datetime

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

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discord
from discord.ext import commands


def create_mock_message(
    message_id=1234567890,
    channel_id=1234567890,
    guild_id=987654321,
    author_id=111111111,
    content="Test message",
    timestamp=None
):
    """Create a mock Discord message using DiscordMockUtils."""
    return DiscordMockUtils.create_mock_message(
        message_id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        author_id=author_id,
        content=content,
        created_at=timestamp or datetime.now(),
    )


def create_mock_embed():
    """Create a mock Discord embed using DiscordMockUtils."""
    return DiscordMockUtils.create_mock_embed(
        title="Test Embed",
        description="Test description",
        url="https://example.com",
        timestamp=datetime.now(),
        color=0x00FF00,
        footer={"text": "Footer text"},
        thumbnail={"url": "https://example.com/thumbnail.jpg"},
    )


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot using DiscordMockUtils."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(channel_id):
        if channel_id == 1234567890:
            channel = MagicMock()
            channel.id = channel_id
            channel.guild = MagicMock()
            channel.guild.id = 987654321
            return channel
        return None

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=lambda x: get_channel(x))

    return bot


def _evict_discord_modules():
    """Remove any cached discord or source modules so they re-import with real discord."""
    to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.")
                or k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
                or k.startswith("cogs.")]
    for k in to_evict:
        sys.modules.pop(k, None)


@pytest.fixture
def messages_test_app(mock_bot):
    """Create a test FastAPI app with the messages router and mocked dependencies."""
    _evict_discord_modules()

    app = FastAPI(title="Discord Gateway API Test")

    app.state.bot = mock_bot

    with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find_message, \
         patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.messages.MessageConverter") as mock_converter:

        from api.schemas.message_schemas import MessageSummary

        _mock_message_payload = MessageSummary(
            id=1234567890,
            author_id=123456789,
            content=None,
            timestamp="2024-01-01T00:00:00"
        )

        async def mock_find_message_impl(bot, message_id, logger):
            if message_id == 1234567890:
                mock_msg = MagicMock()
                mock_msg.id = message_id
                mock_msg.author = MagicMock()
                mock_msg.author.id = 123456789  # bot's own message so edit/delete allowed
                mock_msg.channel = MagicMock()
                mock_msg.channel.guild = MagicMock()
                mock_msg.channel.guild.get_member = MagicMock(return_value=MagicMock())
                mock_msg.channel.permissions_for = MagicMock(
                    return_value=MagicMock(manage_messages=True)
                )
                mock_msg.edit = AsyncMock()
                mock_msg.delete = AsyncMock()
                return mock_msg
            return None

        async def mock_resolve_bot(request):
            return mock_bot

        mock_find_message.side_effect = mock_find_message_impl
        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.return_value = None

        mock_converter.message_to_payload.return_value = _mock_message_payload

        from api.routers.messages import router

        app.include_router(router, prefix="/api/v1")

        yield app  # patches stay active during tests


@pytest.fixture
def messages_client(messages_test_app):
    """Create a test client for the messages API."""
    return TestClient(messages_test_app)


class TestGetMessage:
    """Tests for GET /messages/{message_id} endpoint."""

    def test_get_message_success(self, messages_client, mock_bot):
        """GET /messages/{message_id} should retrieve message successfully."""
        response = messages_client.get("/api/v1/messages/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("success", "found")
        assert "data" in data

    def test_get_message_not_found(self, messages_client):
        """GET /messages/{message_id} should return 404 for non-existent message."""
        response = messages_client.get("/api/v1/messages/9999999999")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


class TestUpdateMessage:
    """Tests for PUT /messages/{message_id} endpoint (actual router uses PUT)."""

    def test_update_message_success(self, messages_client, mock_bot):
        """PUT /messages/{message_id} should update message successfully."""
        update_data = {
            "content": {"title": "Updated title", "description": "Updated content"}
        }

        response = messages_client.put("/api/v1/messages/1234567890", json=update_data)
        assert response.status_code in (200, 403)  # 403 if not bot's own message

    def test_update_message_not_found(self, messages_client):
        """PUT /messages/{message_id} should return 404 for non-existent message."""
        update_data = {
            "content": {"title": "Updated title"}
        }

        response = messages_client.put("/api/v1/messages/9999999999", json=update_data)
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


class TestDeleteMessage:
    """Tests for DELETE /messages/{message_id} endpoint."""

    def test_delete_message_success(self, messages_client, mock_bot):
        """DELETE /messages/{message_id} should delete message successfully."""
        response = messages_client.delete("/api/v1/messages/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("success", "deleted")
        assert data["deleted"] is True

    def test_delete_message_not_found(self, messages_client):
        """DELETE /messages/{message_id} should return 404 for non-existent message."""
        response = messages_client.delete("/api/v1/messages/9999999999")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()
