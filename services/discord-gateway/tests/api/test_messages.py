"""
Tests for the messages API endpoints.

This module provides comprehensive tests for the messages router endpoints,
including message creation, retrieval, updating, and deletion.

Fidelity notes
--------------
No patches on ``resolve_bot``, ``_find_message``, ``handle_discord_exception``
or ``MessageConverter``: the mock bot is ``spec=commands.Bot``
(``is_ready()==True``) with a real ``guilds -> channels -> fetch_message``
graph, so the real ``_find_message``/``MessageConverter.message_to_payload``
run end-to-end and the response bodies below are genuine serialization
output rather than test-fabricated dicts.
"""

import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

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


@pytest.fixture
def mock_bot():
    """Create a mock bot with a real guild -> channel -> message graph.

    ``_find_message`` (unpatched) scans ``bot.guilds`` -> ``guild.channels``
    -> ``channel.fetch_message(message_id)``, so the fixture wires that path
    for real instead of stubbing the router's own lookup helper. The seeded
    message is authored by the bot itself so edit/delete success paths are
    deterministic (200), matching production's own-message rule.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    guild = DiscordMockUtils.create_mock_guild(guild_id=987654321)
    channel = DiscordMockUtils.create_mock_text_channel(channel_id=1234567890, guild=guild, guild_id=guild.id)

    message = DiscordMockUtils.create_mock_message(
        message_id=1234567890,
        channel_id=1234567890,
        guild_id=987654321,
        author_id=123456789,  # matches bot.user.id -> bot's own message
        content="Test message",
        channel=channel,
        guild=guild,
        created_at=datetime(2024, 1, 1),
    )
    message.author = bot.user

    async def _edit(**kwargs):
        if kwargs.get("embed") is not None:
            message.embeds = [kwargs["embed"]]

    message.edit = AsyncMock(side_effect=_edit)
    message.delete = AsyncMock()

    async def fetch_message(mid):
        if mid == message.id:
            return message
        raise create_discord_not_found(f"Message {mid} not found")

    channel.fetch_message = AsyncMock(side_effect=fetch_message)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(manage_messages=True))

    bot_member = DiscordMockUtils.create_mock_member(user_id=bot.user.id, guild=guild)
    guild.get_member = MagicMock(return_value=bot_member)
    guild.channels = [channel]
    bot.guilds = [guild]

    bot._graph = types.SimpleNamespace(guild=guild, channel=channel, message=message)
    return bot


@pytest.fixture
def messages_test_app(mock_bot):
    """Create a test FastAPI app with the messages router and a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.messages import router

    app.include_router(router, prefix="/api/v1")

    yield app


@pytest.fixture
def messages_client(messages_test_app):
    """Create a test client for the messages API."""
    return TestClient(messages_test_app)


class TestGetMessage:
    """Tests for GET /messages/{message_id} endpoint."""

    def test_get_message_success(self, messages_client, mock_bot):
        """GET /messages/{message_id} should retrieve the real-serialized message."""
        response = messages_client.get("/api/v1/messages/1234567890")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "found"
        assert data["data"]["id"] == 1234567890
        assert data["data"]["channel_id"] == 1234567890
        assert data["data"]["author_id"] == 123456789

    def test_get_message_not_found(self, messages_client):
        """GET /messages/{message_id} should return 404 for non-existent message."""
        response = messages_client.get("/api/v1/messages/9999999999")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


class TestUpdateMessage:
    """Tests for PUT /messages/{message_id} endpoint (actual router uses PUT)."""

    def test_update_message_success(self, messages_client, mock_bot):
        """PUT /messages/{message_id} on the bot's own message should return exactly 200."""
        update_data = {"content": {"title": "Updated title", "description": "Updated content"}}

        response = messages_client.put("/api/v1/messages/1234567890", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        # Real message.edit() was awaited with a real discord.Embed built by EmbedConverter.
        mock_bot._graph.message.edit.assert_awaited_once()
        sent_embed = mock_bot._graph.message.edit.call_args.kwargs["embed"]
        assert isinstance(sent_embed, discord.Embed)
        assert sent_embed.title == "Updated title"

    def test_update_message_not_own_message_returns_403(self, messages_client, mock_bot):
        """PUT /messages/{message_id} should return 403 when the message wasn't sent by the bot."""
        other_author = DiscordMockUtils.create_mock_user(user_id=999999999, username="someone_else")
        mock_bot._graph.message.author = other_author

        update_data = {"content": {"title": "Updated title"}}
        response = messages_client.put("/api/v1/messages/1234567890", json=update_data)
        assert response.status_code == 403
        assert "bot" in response.json()["detail"].lower()

    def test_update_message_not_found(self, messages_client):
        """PUT /messages/{message_id} should return 404 for non-existent message."""
        update_data = {"content": {"title": "Updated title"}}

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
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        mock_bot._graph.message.delete.assert_awaited_once()

    def test_delete_message_not_found(self, messages_client):
        """DELETE /messages/{message_id} should return 404 for non-existent message."""
        response = messages_client.delete("/api/v1/messages/9999999999")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()

    def test_delete_message_not_own_but_has_manage_messages_returns_200(self, messages_client, mock_bot):
        """A non-bot message can still be deleted for real when the bot has manage_messages."""
        other_author = DiscordMockUtils.create_mock_user(user_id=999999999, username="someone_else")
        mock_bot._graph.message.author = other_author

        response = messages_client.delete("/api/v1/messages/1234567890")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    def test_delete_message_not_own_without_permission_returns_403(self, messages_client, mock_bot):
        """Deleting another author's message without manage_messages should return 403."""
        other_author = DiscordMockUtils.create_mock_user(user_id=999999999, username="someone_else")
        mock_bot._graph.message.author = other_author
        mock_bot._graph.channel.permissions_for = MagicMock(return_value=discord.Permissions(manage_messages=False))

        response = messages_client.delete("/api/v1/messages/1234567890")
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
