"""Tests for the announcements API endpoints (A.48 unified bounty render).

Covers:
- POST /api/v1/announcements/bounty/channel/{channel_id}: success path, 404 channel
- PUT  /api/v1/announcements/bounty/channel/{channel_id}/message/{message_id}: success, 404 message
- The unified flow uses build_loadout_embed via _build_bounty_embed.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE importing application code
# ---------------------------------------------------------------------------
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Force real discord (other test modules sometimes monkey-patch sys.modules["discord"])
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_loadout_response_dict():
    """Minimal valid criminal-path LoadoutResponse dict."""
    return {
        "subject_kind": "criminal",
        "subject_name": "Pal Tyyrt",
        "subject_description": "Terran",
        "tech_level": 10,
        "ship_name": "Darkzov",
        "thumbnail_url": "https://cdn/pal.png",
        "ship_stats": {
            "armour": 200,
            "cargo": 40,
            "handling": 50,
            "hp": 740,
            "dps": 75.0,
            "total_value": 60000,
            "max_primaries": 4,
            "max_modules": 14,
        },
        "weapons": [],
        "turrets": [],
        "modules": [],
        "cargo": [],
        "cargo_total_count": 0,
    }


def _make_request_payload():
    return {
        "text_content": "<@&999>",
        "loadout_response": _make_loadout_response_dict(),
        "metadata": {
            "title": "Pal Tyyrt",
            "color": 15844367,
            "footer_text": "Terran",
            "image_url": "https://cdn/route_map.png",
            "prefix_fields": [
                {"name": "Difficulty", "value": "T10", "inline": True},
                {"name": "Reward Pool", "value": "85,000 credits", "inline": True},
                {"name": "Bounty Ends", "value": "<t:1700000000:R>", "inline": True},
            ],
            "suffix_fields": [
                {"name": "Route", "value": "Pan, Mido", "inline": False},
                {"name": "Checked Systems", "value": "> *No systems checked yet*", "inline": False},
            ],
        },
    }


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot with a sendable channel for ID 1234567890."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    bot_user = MagicMock()
    bot_user.id = 123456789

    # Memoize the channel so test-time mutations (e.g. overriding fetch_message)
    # are observable across the multiple calls inside the router invocation.
    _channel_cache: dict[int, MagicMock] = {}

    def get_channel(channel_id):
        if channel_id != 1234567890:
            return None
        if channel_id in _channel_cache:
            return _channel_cache[channel_id]
        channel = MagicMock()
        channel.id = channel_id
        channel.guild = MagicMock()
        channel.guild.id = 987654321
        channel.send = AsyncMock()
        channel.fetch_message = AsyncMock()
        # Configure the sent message
        sent_message = MagicMock()
        sent_message.id = 4444444444
        sent_message.author = MagicMock()
        sent_message.author.id = 123456789
        sent_message.channel = channel
        channel.send.return_value = sent_message
        # Configure fetch_message to return a bot-authored message by default
        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.channel = channel
        fetched_message.edit = AsyncMock()
        channel.fetch_message.return_value = fetched_message
        _channel_cache[channel_id] = channel
        return channel

    bot.user = bot_user
    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=lambda x: get_channel(x))
    return bot


@pytest.fixture
def announcements_test_app(mock_bot):
    """FastAPI app with the announcements router mounted."""
    # Evict cached source modules to ensure a clean import with real discord.
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

    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    with (
        patch("api.routers.announcements.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.announcements.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.announcements.MessageConverter") as mock_converter,
        patch("api.routers.announcements.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
    ):
        from fastapi import HTTPException

        async def _resolve_bot(_req):
            return mock_bot

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            channel = mock_bot.get_channel(entity_id)
            if channel is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
            return channel

        mock_resolve.side_effect = _resolve_bot
        mock_get_entity.side_effect = _get_entity
        mock_handle.return_value = None
        mock_converter.message_to_payload.return_value = {
            "id": 4444444444,
            "channel_id": 1234567890,
            "guild_id": 987654321,
            "author_id": 123456789,
            "content": None,
            "timestamp": "2024-01-01T00:00:00",
            "edited_timestamp": None,
            "message_type": "default",
        }

        from api.routers.announcements import router

        app.include_router(router, prefix="/api/v1")
        yield app


@pytest.fixture
def announcements_client(announcements_test_app):
    return TestClient(announcements_test_app)


# ===========================================================================
# POST tests
# ===========================================================================


class TestCreateBountyAnnouncement:
    def test_post_success_returns_201(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.post(
            "/api/v1/announcements/bounty/channel/1234567890",
            json=payload,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert "data" in data

    def test_post_channel_not_found_returns_404(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.post(
            "/api/v1/announcements/bounty/channel/9999999999",
            json=payload,
        )
        assert response.status_code == 404

    def test_post_invokes_build_loadout_embed_with_overrides(self, announcements_client, mock_bot):
        """The router should call build_loadout_embed with the metadata overrides."""
        payload = _make_request_payload()

        # Patch _build_bounty_embed to assert the args
        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            import discord

            mock_build.return_value = discord.Embed(title="x")
            response = announcements_client.post(
                "/api/v1/announcements/bounty/channel/1234567890",
                json=payload,
            )
            assert response.status_code == 201
            assert mock_build.call_count == 1
            kwargs = mock_build.call_args.kwargs
            assert kwargs["title_override"] == "Pal Tyyrt"
            assert kwargs["color_override"] == 15844367
            assert kwargs["footer_text"] == "Terran"
            assert kwargs["image_url"] == "https://cdn/route_map.png"
            assert len(kwargs["prefix_fields"]) == 3
            assert len(kwargs["suffix_fields"]) == 2

    def test_post_validation_error_when_metadata_missing(self, announcements_client):
        bad_payload = {"text_content": None, "loadout_response": _make_loadout_response_dict()}
        response = announcements_client.post(
            "/api/v1/announcements/bounty/channel/1234567890",
            json=bad_payload,
        )
        assert response.status_code == 422


# ===========================================================================
# PUT tests
# ===========================================================================


class TestEditBountyAnnouncement:
    def test_put_success_returns_200(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"

    def test_put_channel_not_found_returns_404(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/9999999999/message/5555555555",
            json=payload,
        )
        assert response.status_code == 404

    def test_put_message_not_found_returns_404(self, announcements_client, mock_bot):
        """When fetch_message raises NotFound, the router returns 404."""
        import discord

        async def _raise_not_found(_message_id):
            raise discord.NotFound(MagicMock(status=404), "not found")

        # Configure the channel's fetch_message to raise NotFound
        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(side_effect=_raise_not_found)

        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/1234567890/message/9999999999",
            json=payload,
        )
        assert response.status_code == 404
