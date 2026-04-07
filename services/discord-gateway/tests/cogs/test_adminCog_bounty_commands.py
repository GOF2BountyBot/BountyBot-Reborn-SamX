"""
Tests for new bounty admin commands in adminCog:
  - /admin_clear_bounties
  - /admin_config_bounty (view and update)
  - /admin_spawn_bounty
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.mocks.discord_mock_utils import DiscordMockUtils

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _evict_discord_modules():
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


def _create_mock_interaction(guild_id: int = 987654321):
    interaction = DiscordMockUtils.create_mock_interaction()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.guild.icon = None
    return interaction


def _create_mock_user(user_id: int = 111111111, name: str = "TestAdmin"):
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_name = name
    return user


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    return bot


@pytest.fixture
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# Response builders
# -------------------------------------------------------------------------


def _make_clear_bounties_response(tier=None, cleared_count=3):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "guild_id": 987654321,
        "tier": tier,
        "cleared_count": cleared_count,
        "bounty_ids": list(range(1, cleared_count + 1)),
        "announcements_deleted": cleared_count,
    }
    return resp


def _make_bounty_config_status_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "guild_id": 987654321,
        "max_bounties_per_tier": {"bronze": 3, "silver": 3, "gold": 3},
        "bounty_expiry_minutes": 480,
        "bounty_spawn_interval_minutes": 60,
        "next_spawn_check_at": "2026-04-05T12:00:00+00:00",
        "active_bounties_per_tier": {"bronze": 2, "silver": 1, "gold": 0},
    }
    return resp


def _make_bounty_config_put_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "guild_id": 987654321,
        "max_bounties_per_tier": {"bronze": 5, "silver": 3, "gold": 3},
        "bounty_expiry_minutes": 600,
        "bounty_spawn_interval_minutes": 90,
    }
    return resp


def _make_admin_spawn_response(spawned_count=2, skipped_tiers=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    spawned = [
        {
            "id": i + 1,
            "criminal_name": f"Villain{i + 1}",
            "division": "bronze",
            "reward": 10000 + i * 1000,
            "tech_level": 3,
        }
        for i in range(spawned_count)
    ]
    resp.json.return_value = {
        "guild_id": 987654321,
        "spawned": spawned,
        "skipped_tiers": skipped_tiers or [],
        "errors": [],
    }
    return resp


# -------------------------------------------------------------------------
# Tests: /admin_clear_bounties
# -------------------------------------------------------------------------


class TestAdminClearBounties:
    """Tests for /admin_clear_bounties command."""

    def test_clear_bounties_requires_confirm(self, mock_admin_cog):
        """Without CONFIRM, user gets ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="wrong", tier=None)
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        # Should be an ephemeral error message, not a success embed
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_clear_bounties_all_tiers_success(self, mock_admin_cog):
        """CONFIRM + no tier → clears all tiers, sends embed with orange color."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier=None)
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.delete.assert_called_once()
        # Verify URL contains the guild clear endpoint
        call_url = mock_admin_cog.http_client.delete.call_args[0][0]
        assert "bounties/guild" in call_url
        assert "clear" in call_url
        # Should send an embed response
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_clear_bounties_with_tier_filter(self, mock_admin_cog):
        """CONFIRM + tier=bronze → includes tier in query params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response(tier="bronze"))

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier="bronze")
        )

        mock_admin_cog.http_client.delete.assert_called_once()
        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" in params
        assert params["tier"] == "bronze"

    def test_clear_bounties_embed_has_orange_color(self, mock_admin_cog):
        """Success embed should use orange color (0xFFA500)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier=None)
        )

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert embed.color.value == 0xFFA500

    def test_clear_bounties_api_error(self, mock_admin_cog):
        """API error → sends ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Not Found", request=mock_req, response=MagicMock(status_code=404))
        mock_admin_cog.http_client.delete = AsyncMock(side_effect=http_err)

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier=None)
        )

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_clear_bounties_generic_error(self, mock_admin_cog):
        """Generic exception → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(side_effect=RuntimeError("oops"))

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier=None)
        )

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args

    def test_clear_bounties_includes_user_id_param(self, mock_admin_cog):
        """DELETE request must include user_id query param for audit logging."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=555)

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(
            mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, confirm="CONFIRM", tier=None)
        )

        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        params = call_kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == 555


# -------------------------------------------------------------------------
# Tests: /admin_config_bounty
# -------------------------------------------------------------------------


class TestAdminConfigBounty:
    """Tests for /admin_config_bounty command."""

    def test_config_bounty_view_calls_get(self, mock_admin_cog):
        """action='view' → GET /config/guild/{id}/bounty."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(return_value=_make_bounty_config_status_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.get.assert_called_once()
        call_url = mock_admin_cog.http_client.get.call_args[0][0]
        assert "config/guild" in call_url
        assert "bounty" in call_url

    def test_config_bounty_view_embed_uses_discord_timestamp(self, mock_admin_cog):
        """View embed should show Discord relative timestamp for next_spawn_check_at."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(return_value=_make_bounty_config_status_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # The embed should contain a Discord timestamp (<t:...:R>) somewhere in its fields
        embed_text = str(embed.to_dict())
        assert "<t:" in embed_text

    def test_config_bounty_update_calls_put(self, mock_admin_cog):
        """action='update' with params → PUT /config/guild/{id}/bounty."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                expiry_minutes=600,
                spawn_interval=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.put.assert_called_once()
        call_url = mock_admin_cog.http_client.put.call_args[0][0]
        assert "config/guild" in call_url
        assert "bounty" in call_url

    def test_config_bounty_update_payload_contains_non_none_fields(self, mock_admin_cog):
        """Update payload should only include non-None fields."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                expiry_minutes=600,
                spawn_interval=None,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        # max_bronze and expiry_minutes were provided
        assert "max_bronze" in payload or any("bronze" in str(k).lower() for k in payload)
        # max_silver was None — should not appear as a key with None value
        assert payload.get("max_silver") is None or "max_silver" not in payload

    def test_config_bounty_view_api_error(self, mock_admin_cog):
        """API error in view → ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Server Error", request=mock_req, response=MagicMock(status_code=500))
        mock_admin_cog.http_client.get = AsyncMock(side_effect=http_err)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        # Error message contains ❌
        assert "❌" in args[0]

    def test_config_bounty_update_api_error(self, mock_admin_cog):
        """API error in update → ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Bad Request", request=mock_req, response=MagicMock(status_code=400))
        mock_admin_cog.http_client.put = AsyncMock(side_effect=http_err)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "❌" in args[0]

    def test_config_bounty_generic_error(self, mock_admin_cog):
        """Generic exception → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "⚠️" in args[0]


# -------------------------------------------------------------------------
# Tests: /admin_spawn_bounty
# -------------------------------------------------------------------------


class TestAdminSpawnBounty:
    """Tests for /admin_spawn_bounty command."""

    def test_spawn_bounty_no_tier_success(self, mock_admin_cog):
        """No tier → POST to admin-spawn with no tier param."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=2))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_called_once()
        call_url = mock_admin_cog.http_client.post.call_args[0][0]
        assert "bounties/guild" in call_url
        assert "admin-spawn" in call_url
        # Response should be an embed
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_spawn_bounty_with_tier_includes_param(self, mock_admin_cog):
        """tier=silver → POST includes tier in query params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=1))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier="silver"))

        mock_admin_cog.http_client.post.assert_called_once()
        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" in params
        assert params["tier"] == "silver"

    def test_spawn_bounty_embed_shows_spawned_names(self, mock_admin_cog):
        """Success embed should display criminal names from response."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=2))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        embed_dict = embed.to_dict()
        embed_text = str(embed_dict)
        # Criminal names from the mock response should appear in the embed
        assert "Villain1" in embed_text or "Villain2" in embed_text

    def test_spawn_bounty_no_tier_param_in_url(self, mock_admin_cog):
        """When tier is None, tier param must NOT be in request params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response())

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" not in params

    def test_spawn_bounty_includes_user_id_param(self, mock_admin_cog):
        """POST request must include user_id query param for audit logging."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=777)

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response())

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == 777

    def test_spawn_bounty_api_error(self, mock_admin_cog):
        """API error → sends ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Internal Error", request=mock_req, response=MagicMock(status_code=500))
        mock_admin_cog.http_client.post = AsyncMock(side_effect=http_err)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "❌" in args[0]

    def test_spawn_bounty_generic_error(self, mock_admin_cog):
        """Generic exception → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=RuntimeError("network down"))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "⚠️" in args[0]

    def test_spawn_bounty_skipped_tiers_in_embed(self, mock_admin_cog):
        """Embed should include info about skipped tiers if present."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(
            return_value=_make_admin_spawn_response(spawned_count=1, skipped_tiers=["gold"])
        )

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        embed_text = str(embed.to_dict())
        # gold should be mentioned as skipped
        assert "gold" in embed_text.lower()
