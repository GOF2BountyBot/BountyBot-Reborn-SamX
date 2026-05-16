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

    # B.13 — image_url preservation on PUT edits

    def test_put_image_url_none_preserves_existing_embed_image(self, announcements_client, mock_bot):
        """B.13: PUT with image_url=None and an existing message embed image preserves the image.

        discord.Message.edit(embed=new_embed) replaces the entire embed. If the
        new embed has no image, Discord clears the previous one.  The router must
        carry forward the existing image URL when the caller passes image_url=None.
        """
        existing_image_url = "https://cdn.example.com/route_map_original.png"

        # Build a fetched message whose embed has an image.
        mock_image = MagicMock()
        mock_image.url = existing_image_url

        mock_embed = MagicMock()
        mock_embed.image = mock_image

        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = [mock_embed]
        fetched_message.edit = AsyncMock()

        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

        # Payload with image_url=None (state-transition edit)
        payload = _make_request_payload()
        payload["metadata"]["image_url"] = None

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            import discord

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

        mock_image = MagicMock()
        mock_image.url = existing_image_url

        mock_embed = MagicMock()
        mock_embed.image = mock_image

        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = [mock_embed]
        fetched_message.edit = AsyncMock()

        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

        payload = _make_request_payload()
        payload["metadata"]["image_url"] = new_image_url

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            import discord

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
        # Message with no embeds at all
        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = []
        fetched_message.edit = AsyncMock()

        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

        payload = _make_request_payload()
        payload["metadata"]["image_url"] = None

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            import discord

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
    """Tests for _build_payout_embed and its integration with create/edit endpoints."""

    def test_build_payout_embed_returns_none_when_reward_missing(self, announcements_test_app):
        """_build_payout_embed returns None when reward is None."""
        for mod_key in list(sys.modules.keys()):
            if "api.routers.announcements" in mod_key:
                sys.modules.pop(mod_key, None)
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=None,
            reward_per_sys=3000,
            route_length=4,
        )
        assert _build_payout_embed(meta) is None

    def test_build_payout_embed_returns_none_when_reward_per_sys_missing(self, announcements_test_app):
        """_build_payout_embed returns None when reward_per_sys is None."""
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=80000,
            reward_per_sys=None,
            route_length=4,
        )
        assert _build_payout_embed(meta) is None

    def test_build_payout_embed_returns_none_when_route_length_missing(self, announcements_test_app):
        """_build_payout_embed returns None when route_length is None."""
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=80000,
            reward_per_sys=3000,
            route_length=None,
        )
        assert _build_payout_embed(meta) is None

    def test_build_payout_embed_correct_field_values(self, announcements_test_app):
        """_build_payout_embed computes capture_bonus=25% of reward, max_sys, max_total."""
        import discord
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        # reward=80000, capture_bonus=int(80000*0.25)=20000
        # reward_per_sys=3000, route_length=4 → max_sys=12000 → max_total=32000
        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=80000,
            reward_per_sys=3000,
            route_length=4,
        )
        embed = _build_payout_embed(meta)
        assert embed is not None
        assert isinstance(embed, discord.Embed)
        assert embed.title == "💰 Payout Breakdown"
        assert embed.color.value == 0xFFD700

        # Extract field values by name
        fields_by_name = {f.name: f.value for f in embed.fields}
        assert fields_by_name["🎯 Capture Bonus"] == "20,000 cr"
        assert fields_by_name["📍 Per System Check"] == "3,000 cr"
        assert fields_by_name["🗺️ Route Length"] == "4 systems"
        assert fields_by_name["💡 Max System Payout"] == "12,000 cr"
        assert fields_by_name["🏆 Max Total Payout"] == "32,000 cr"

    def test_build_payout_embed_all_fields_inline(self, announcements_test_app):
        """All payout embed fields must be inline=True."""
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test", color=0xFF0000, reward=50000, reward_per_sys=2500, route_length=3
        )
        embed = _build_payout_embed(meta)
        assert embed is not None
        for field in embed.fields:
            assert field.inline is True, f"Field {field.name!r} must be inline=True"

    def test_create_with_payout_fields_sends_two_embeds(self, announcements_client, mock_bot):
        """POST with payout fields present sends embeds=[main, payout] (2 embeds)."""
        import discord

        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)

        channel = mock_bot.get_channel(1234567890)
        channel.send = AsyncMock()
        sent_message = MagicMock()
        sent_message.id = 4444444444
        sent_message.author = MagicMock()
        sent_message.author.id = 123456789
        channel.send.return_value = sent_message

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.post(
                "/api/v1/announcements/bounty/channel/1234567890",
                json=payload,
            )

        assert response.status_code == 201
        # channel.send must have been called with embeds=[...] (not embed=...)
        assert channel.send.called
        call_kwargs = channel.send.call_args.kwargs
        assert "embeds" in call_kwargs, "Expected 'embeds' kwarg (2-embed path)"
        assert "embed" not in call_kwargs, "Should NOT have 'embed' kwarg (single-embed path)"
        assert len(call_kwargs["embeds"]) == 2

    def test_create_without_payout_fields_sends_single_embed(self, announcements_client, mock_bot):
        """POST without payout fields (None) sends embed=single (backward compat)."""
        import discord

        payload = _make_request_payload()  # no payout fields → all None

        channel = mock_bot.get_channel(1234567890)
        channel.send = AsyncMock()
        sent_message = MagicMock()
        sent_message.id = 4444444444
        sent_message.author = MagicMock()
        sent_message.author.id = 123456789
        channel.send.return_value = sent_message

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.post(
                "/api/v1/announcements/bounty/channel/1234567890",
                json=payload,
            )

        assert response.status_code == 201
        assert channel.send.called
        call_kwargs = channel.send.call_args.kwargs
        assert "embed" in call_kwargs, "Expected 'embed' kwarg (single-embed fallback)"
        assert "embeds" not in call_kwargs, "Should NOT have 'embeds' kwarg"

    def test_edit_with_payout_fields_sends_two_embeds(self, announcements_client, mock_bot):
        """PUT with payout fields present sends embeds=[main, payout] to message.edit."""
        import discord

        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)

        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = []
        fetched_message.edit = AsyncMock()
        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="Main Embed")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        assert fetched_message.edit.called
        call_kwargs = fetched_message.edit.call_args.kwargs
        assert "embeds" in call_kwargs, "Expected 'embeds' kwarg (2-embed path)"
        assert "embed" not in call_kwargs, "Should NOT have 'embed' kwarg"
        assert len(call_kwargs["embeds"]) == 2


# ===========================================================================
# Adversarial / edge-case tests for _build_payout_embed
# ===========================================================================


class TestPayoutEmbedAdversarial:
    """Adversarial and boundary tests for _build_payout_embed."""

    def test_build_payout_embed_route_length_zero(self, announcements_test_app):
        """route_length=0 must not divide by zero and must render cleanly.

        max_sys_payout = reward_per_sys * 0 = 0
        max_total = capture_bonus + 0 = capture_bonus
        No crash, no nonsensical output.
        """
        import discord
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=40000,
            reward_per_sys=1000,
            route_length=0,
        )
        embed = _build_payout_embed(meta)
        assert embed is not None, "route_length=0 must not cause None return (all fields present)"
        assert isinstance(embed, discord.Embed)

        fields_by_name = {f.name: f.value for f in embed.fields}
        # capture_bonus = int(40000 * 0.25) = 10000
        assert fields_by_name["🎯 Capture Bonus"] == "10,000 cr"
        # max_sys = 1000 * 0 = 0
        assert fields_by_name["💡 Max System Payout"] == "0 cr"
        # route length field
        assert fields_by_name["🗺️ Route Length"] == "0 systems"
        # max_total = 10000 + 0 = 10000
        assert fields_by_name["🏆 Max Total Payout"] == "10,000 cr"

    def test_build_payout_embed_reward_per_sys_zero(self, announcements_test_app):
        """reward_per_sys=0 is a valid int (not None) and must render '0 cr', not fall into None branch.

        A classic-mode bounty or a bounty with no per-sys payout would have
        reward_per_sys=0.  Pydantic's int | None type accepts 0 as a concrete
        value, so the guard 'if meta.reward_per_sys is None' must not trigger.
        """
        import discord
        from api.routers.announcements import _build_payout_embed
        from api.schemas.announcement_schemas import BountyAnnouncementMetadata

        meta = BountyAnnouncementMetadata(
            title="Test",
            color=0xFF0000,
            reward=50000,
            reward_per_sys=0,
            route_length=5,
        )
        embed = _build_payout_embed(meta)
        assert embed is not None, "reward_per_sys=0 must not be treated as None/missing"
        assert isinstance(embed, discord.Embed)

        fields_by_name = {f.name: f.value for f in embed.fields}
        assert fields_by_name["📍 Per System Check"] == "0 cr", (
            "reward_per_sys=0 must render '0 cr', not fall into None branch"
        )
        # max_sys = 0 * 5 = 0
        assert fields_by_name["💡 Max System Payout"] == "0 cr"

    def test_edit_captured_state_still_sends_payout_embed(self, announcements_client, mock_bot):
        """PUT with captured=True and payout fields present still sends embeds=[main, payout].

        The payout embed is built from metadata (reward/reward_per_sys/route_length),
        which are independent of the captured flag.  Captured only affects the
        main embed (suppresses loadout sections and clears image).
        """
        import discord

        payload = _make_request_payload_with_payout(reward=80000, reward_per_sys=3000, route_length=4)
        payload["metadata"]["captured"] = True
        payload["metadata"]["image_url"] = ""  # empty-string sentinel = clear image

        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = []
        fetched_message.edit = AsyncMock()
        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

        with patch("api.routers.announcements.build_loadout_embed") as mock_build:
            mock_build.return_value = discord.Embed(title="✅ Captured")
            response = announcements_client.put(
                "/api/v1/announcements/bounty/channel/1234567890/message/5555555555",
                json=payload,
            )

        assert response.status_code == 200
        assert fetched_message.edit.called
        call_kwargs = fetched_message.edit.call_args.kwargs
        # Payout embed must still be present even in captured state
        assert "embeds" in call_kwargs, "captured=True must still send 2 embeds when payout fields present"
        assert len(call_kwargs["embeds"]) == 2, "Must have [main_embed, payout_embed]"

    def test_edit_captured_image_is_on_first_embed_not_second(self, announcements_client, mock_bot):
        """Image preservation reads from existing_embeds[0], not the payout embed.

        When editing with embeds=[main, payout], the image URL (route map) must
        be on the first embed (main_embed).  The payout embed must have no image.
        This verifies the image-preservation logic targets embeds[0].
        """
        import discord

        existing_image_url = "https://cdn.example.com/route_map.png"

        mock_image = MagicMock()
        mock_image.url = existing_image_url

        # Simulate an existing message with a two-embed layout (main + prior payout)
        mock_main_embed = MagicMock()
        mock_main_embed.image = mock_image

        mock_payout_embed = MagicMock()
        mock_payout_embed.image = MagicMock()
        mock_payout_embed.image.url = None  # payout embed never has image

        fetched_message = MagicMock()
        fetched_message.id = 5555555555
        fetched_message.author = MagicMock()
        fetched_message.author.id = 123456789
        fetched_message.embeds = [mock_main_embed, mock_payout_embed]
        fetched_message.edit = AsyncMock()
        mock_bot.get_channel(1234567890).fetch_message = AsyncMock(return_value=fetched_message)

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
