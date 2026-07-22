"""Tests for the announcements API endpoints (A.48 unified bounty render).

Covers:
- POST /api/v1/announcements/bounty/channel/{channel_id}: success path, 404 channel
- PUT  /api/v1/announcements/bounty/channel/{channel_id}/message/{message_id}: success, 404 message
- The unified flow uses build_loadout_embed via _build_bounty_embed.

Fidelity notes
--------------
No patches on ``resolve_bot``, ``get_entity_or_404``, ``handle_discord_exception``
or ``MessageConverter``: the mock bot is ``spec=commands.Bot``
(``is_ready()==True``) with a real channel/message graph, so the real helpers
and the real ``MessageConverter.message_to_payload`` run end-to-end and the
response bodies asserted below are genuine serialization output.
``build_loadout_embed`` stays patched where a test needs to assert on its
call args — it's the shared heavy embed-rendering cog function, a legitimate
boundary — but several tests below (POST/PUT success, 404s) exercise the
*real* ``build_loadout_embed`` too, exactly as the original suite did.
``message.edit(embed=...)`` is given a real side effect that swaps
``message.embeds`` (mirroring discord.py's in-place mutation), so the
post-edit ``MessageConverter`` call reflects the *actually sent* embed.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils, create_discord_not_found

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


def _make_message(message_id, channel, guild, author, embeds=None):
    """Build a real-attribute mock message whose ``.edit(embed=...)`` mutates
    ``.embeds`` in place — mirroring discord.py's real "edit replaces the
    embed" behaviour — so a subsequent ``MessageConverter.message_to_payload``
    call reflects the actually-sent embed."""
    msg = DiscordMockUtils.create_mock_message(
        message_id=message_id,
        channel=channel,
        guild=guild,
        channel_id=channel.id,
        guild_id=guild.id,
        author=author,
        embeds=embeds or [],
    )

    async def _edit(**kwargs):
        if kwargs.get("embed") is not None:
            msg.embeds = [kwargs["embed"]]

    msg.edit = AsyncMock(side_effect=_edit)
    return msg


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot with a sendable channel for ID 1234567890.

    ``fetch_channel`` raises a real ``discord.NotFound`` on cache miss so the
    real ``get_entity_or_404`` chain produces a genuine 404. The channel is
    memoized so test-time mutations (e.g. overriding ``fetch_message``) are
    observable across the multiple calls inside the router invocation.
    """
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    guild = DiscordMockUtils.create_mock_guild(guild_id=987654321)
    channel = DiscordMockUtils.create_mock_text_channel(channel_id=1234567890, guild=guild, guild_id=guild.id)

    channel.send = AsyncMock(return_value=_make_message(4444444444, channel, guild, author=bot.user))
    channel.fetch_message = AsyncMock(return_value=_make_message(5555555555, channel, guild, author=bot.user))

    def get_channel(channel_id):
        return channel if channel_id == channel.id else None

    async def fetch_channel(channel_id):
        found = get_channel(channel_id)
        if found is None:
            raise create_discord_not_found(f"Channel {channel_id} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot._graph = types.SimpleNamespace(guild=guild, channel=channel)
    return bot


@pytest.fixture
def announcements_test_app(mock_bot):
    """FastAPI app with the announcements router mounted against a real bot state."""
    app = FastAPI(title="Discord Gateway API Test")
    app.state.bot = mock_bot

    from api.routers.announcements import router  # pylint: disable=no-name-in-module

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
        """The real build_loadout_embed + real MessageConverter run end-to-end."""
        payload = _make_request_payload()
        response = announcements_client.post(
            "/api/v1/announcements/bounty/channel/1234567890",
            json=payload,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["id"] == 4444444444
        assert data["data"]["channel_id"] == 1234567890
        assert data["data"]["author_id"] == 123456789

    def test_post_channel_not_found_returns_404(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.post(
            "/api/v1/announcements/bounty/channel/9999999999",
            json=payload,
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_post_invokes_build_loadout_embed_with_overrides(self, announcements_client, mock_bot):
        """The router should call build_loadout_embed with the metadata overrides."""
        payload = _make_request_payload()

        # Patch _build_bounty_embed to assert the args
        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
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
        """The real build_loadout_embed + real MessageConverter run end-to-end."""
        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["id"] == 5555555555

    def test_put_channel_not_found_returns_404(self, announcements_client):
        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/9999999999/message/5555555555",
            json=payload,
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_put_message_not_found_returns_404(self, announcements_client, mock_bot):
        """When fetch_message raises NotFound, the router returns 404."""

        async def _raise_not_found(_message_id):
            raise create_discord_not_found("not found")

        # Configure the channel's fetch_message to raise NotFound
        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(side_effect=_raise_not_found)

        payload = _make_request_payload()
        response = announcements_client.put(
            "/api/v1/announcements/bounty/channel/1234567890/message/9999999999",
            json=payload,
        )
        assert response.status_code == 404

    # B.13 — image_url preservation on PUT edits

    def test_put_image_url_none_preserves_existing_embed_image(self, announcements_client, mock_bot):
        """B.13: PUT with image_url=None and an existing message embed image preserves the image.

        discord.Message.edit(embed=new_embed) replaces the entire embed. If the
        new embed has no image, Discord clears the previous one.  The router must
        carry forward the existing image URL when the caller passes image_url=None.
        """
        existing_image_url = "https://cdn.example.com/route_map_original.png"
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        existing_embed = DiscordMockUtils.create_mock_embed(image={"url": existing_image_url})
        fetched_message = _make_message(5555555555, channel, guild, author=mock_bot.user, embeds=[existing_embed])
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        # Payload with image_url=None (state-transition edit)
        payload = _make_request_payload()
        payload["metadata"]["image_url"] = None

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="test")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        # The embed builder must have received the existing image URL (not None)
        assert mock_build.call_count == 1
        actual_image_url = mock_build.call_args.kwargs.get("image_url")
        assert actual_image_url == existing_image_url, (
            f"Expected image_url={existing_image_url!r} to be preserved, got {actual_image_url!r}"
        )

    def test_put_explicit_image_url_overrides_existing(self, announcements_client, mock_bot):
        """B.13: PUT with an explicit image_url replaces the existing image (not preserved).

        When the caller provides a non-None image_url, it should be used as-is
        even if the existing message has a different image.
        """
        existing_image_url = "https://cdn.example.com/old_route_map.png"
        new_image_url = "https://cdn.example.com/new_route_map.png"
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        existing_embed = DiscordMockUtils.create_mock_embed(image={"url": existing_image_url})
        fetched_message = _make_message(5555555555, channel, guild, author=mock_bot.user, embeds=[existing_embed])
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        payload = _make_request_payload()
        payload["metadata"]["image_url"] = new_image_url

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="test")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        actual_image_url = mock_build.call_args.kwargs.get("image_url")
        assert actual_image_url == new_image_url, (
            f"Expected new image_url={new_image_url!r} to be used, got {actual_image_url!r}"
        )

    def test_put_image_url_none_no_existing_image_renders_without_image(self, announcements_client, mock_bot):
        """B.13: PUT with image_url=None and no existing image renders embed without image (no error)."""
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        # Message with no embeds at all
        fetched_message = _make_message(5555555555, channel, guild, author=mock_bot.user, embeds=[])
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        payload = _make_request_payload()
        payload["metadata"]["image_url"] = None

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="test")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        # image_url should remain None — no existing image to preserve
        actual_image_url = mock_build.call_args.kwargs.get("image_url")
        assert actual_image_url is None, f"Expected image_url=None (no existing image), got {actual_image_url!r}"


# ===========================================================================
# Payout embed tests (Task B)
# ===========================================================================


def _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4):
    """Request payload that includes all three payout fields."""
    payload = _make_request_payload()
    payload["metadata"]["reward"] = reward
    payload["metadata"]["reward_per_sys"] = reward_per_sys
    payload["metadata"]["route_length"] = route_length
    return payload


class TestPayoutEmbed:
    """Tests verifying the bounty board always sends a single embed (Item B).

    The payout breakdown embed has been removed from the bounty board path entirely.
    All POST and PUT operations must send a single embed regardless of whether
    payout metadata fields (reward, reward_per_sys, route_length) are present.
    """

    def test_create_with_payout_fields_sends_single_embed(self, announcements_client, mock_bot):
        """POST with payout fields present sends a single embed (payout embed removed, Item B)."""
        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)
        channel = mock_bot._graph.channel

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.post(
                "/api/v1/announcements/bounty/channel/1234567890",
                json=payload,
            )

        assert response.status_code == 201
        assert channel.send.called
        call_kwargs = channel.send.call_args.kwargs
        # Item B: always single embed regardless of payout fields
        assert "embed" in call_kwargs, "Expected 'embed' kwarg (single-embed path)"
        assert "embeds" not in call_kwargs, "Should NOT have 'embeds' kwarg after Item B"

    def test_create_without_payout_fields_sends_single_embed(self, announcements_client, mock_bot):
        """POST without payout fields sends a single embed."""
        payload = _make_request_payload()  # no payout fields → all None
        channel = mock_bot._graph.channel

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.post(
                "/api/v1/announcements/bounty/channel/1234567890",
                json=payload,
            )

        assert response.status_code == 201
        assert channel.send.called
        call_kwargs = channel.send.call_args.kwargs
        assert "embed" in call_kwargs, "Expected 'embed' kwarg (single-embed path)"
        assert "embeds" not in call_kwargs, "Should NOT have 'embeds' kwarg"

    def test_edit_with_payout_fields_sends_single_embed(self, announcements_client, mock_bot):
        """PUT with payout fields present sends a single embed to message.edit (Item B)."""
        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        fetched_message = _make_message(5555555555, channel, guild, author=mock_bot.user, embeds=[])
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        assert fetched_message.edit.called
        call_kwargs = fetched_message.edit.call_args.kwargs
        # Item B: always single embed regardless of payout fields
        assert "embed" in call_kwargs, "Expected 'embed' kwarg (single-embed path)"
        assert "embeds" not in call_kwargs, "Should NOT have 'embeds' kwarg after Item B"


# ===========================================================================
# Additional single-embed enforcement tests (Item B)
# ===========================================================================


class TestPayoutEmbedAdversarial:
    """Verifies the bounty board always uses single-embed after Item B removal."""

    def test_edit_captured_state_sends_single_embed(self, announcements_client, mock_bot):
        """PUT with captured=True sends a single embed (no payout embed after Item B).

        The payout embed was removed from the bounty board path in Item B.
        The captured flag only affects the main embed layout (suppresses loadout sections).
        """
        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)
        payload["metadata"]["captured"] = True
        payload["metadata"]["image_url"] = ""  # empty-string sentinel = clear image
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        fetched_message = _make_message(5555555555, channel, guild, author=mock_bot.user, embeds=[])
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="✅ Captured")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        assert fetched_message.edit.called
        call_kwargs = fetched_message.edit.call_args.kwargs
        # Item B: always single embed — no payout embed on bounty board
        assert "embed" in call_kwargs, "Expected 'embed' kwarg (single-embed path)"
        assert "embeds" not in call_kwargs, "Should NOT have 'embeds' kwarg after Item B"

    def test_edit_image_preservation_reads_from_first_embed(self, announcements_client, mock_bot):
        """Image preservation reads from existing_embeds[0].

        When the existing message has multiple embeds (e.g. from a previous state
        before Item B), the image URL (route map) must be read from the first embed
        (embeds[0]). This verifies the image-preservation logic correctly targets embeds[0].
        """
        existing_image_url = "https://cdn.example.com/route_map.png"
        guild, channel = mock_bot._graph.guild, mock_bot._graph.channel

        # Simulate an existing message with a two-embed layout (main + prior payout)
        main_embed = DiscordMockUtils.create_mock_embed(image={"url": existing_image_url})
        payout_embed = DiscordMockUtils.create_mock_embed()  # payout embed never has image

        fetched_message = _make_message(
            5555555555, channel, guild, author=mock_bot.user, embeds=[main_embed, payout_embed]
        )
        channel.fetch_message = AsyncMock(return_value=fetched_message)

        # image_url=None in payload → preservation logic runs, reads from embeds[0]
        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)
        payload["metadata"]["image_url"] = None

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        # The builder must receive the preserved image URL (from embeds[0])
        actual_image_url = mock_build.call_args.kwargs.get("image_url")
        assert actual_image_url == existing_image_url, (
            f"Image preservation must read from embeds[0], got {actual_image_url!r}"
        )
