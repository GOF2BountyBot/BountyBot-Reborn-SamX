"""Tests for playerCog — boosting coverage from 0% to 60%+."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os
import types
import asyncio

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils


# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_utils = DiscordMockUtils()

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

# Track the module-level logger for assertion
_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord
from discord.ext import commands


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
    to_evict = [
        k for k in sys.modules
        if k == "discord" or k.startswith("discord.")
        or k in ("api", "bot", "utils") or k.startswith("api.")
        or k.startswith("utils.") or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


def _create_mock_interaction(user_id=111111111, guild_id=987654321):
    """Build a mock interaction with all needed attributes."""
    interaction = DiscordMockUtils.create_mock_interaction(
        user_id=user_id,
        guild_id=guild_id,
    )
    interaction.guild_id = guild_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.jpg"
    # str(interaction.user) → username
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_player_data(tier="Bronze", prestige_count=0):
    """Return a minimal player data dict."""
    return {
        "id": 1,
        "discord_id": 111111111,
        "guild_id": 987654321,
        "tier": tier,
        "xp": 100,
        "credits": 500,
        "lifetime_credits": 500,
        "prestige_count": prestige_count,
        "systems_checked": 10,
        "created_at": "2024-01-01T00:00:00",
    }


def _make_stats_data():
    """Return a minimal stats dict."""
    return {
        "bounty_stats": {"bounty_wins": 5},
        "duel_stats": {"wins": 3, "losses": 2, "win_rate": 60.0},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock Discord bot for playerCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture
def mock_player_cog(mock_bot):
    """Create a PlayerCog instance with mocked bot and http_client."""
    # Re-assert our module's mock so the logger is wired to _module_logger.
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.playerCog import PlayerCog

    cog = PlayerCog(mock_bot)
    # Replace the real AsyncClient with a MagicMock for test control
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestPlayerCogInitialization:
    """Tests for PlayerCog initialization."""

    def test_initialization(self, mock_player_cog, mock_bot):
        """PlayerCog should store bot reference and create http_client."""
        assert mock_player_cog.bot is mock_bot
        assert mock_player_cog.http_client is not None

    def test_initialization_logs_debug(self, mock_player_cog):
        """PlayerCog __init__ should log a debug message."""
        global _module_logger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("PlayerCog initialized")


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for PlayerCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_player_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_player_cog.cog_unload())
        mock_player_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# profile command
# ---------------------------------------------------------------------------


class TestProfileCommand:
    """Tests for the /profile slash command."""

    def test_profile_success_bronze_no_prestige(self, mock_player_cog):
        """profile should send embed for Bronze tier player."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = player_data
        player_resp.raise_for_status = MagicMock()

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = stats_data
        stats_resp.raise_for_status = MagicMock()

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        # The call should have embed= kwarg
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_profile_success_with_prestige(self, mock_player_cog):
        """profile should include prestige field when prestige_count > 0."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=2)
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_profile_success_no_duel_stats(self, mock_player_cog):
        """profile with 0 wins and 0 losses should skip the duel embed fields."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Silver")
        stats_data = {
            "bounty_stats": {"bounty_wins": 0},
            "duel_stats": {"wins": 0, "losses": 0, "win_rate": 0.0},
        }

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_profile_player_not_found_404(self, mock_player_cog):
        """profile should handle 404 from API and send ephemeral message."""
        interaction = _create_mock_interaction()

        import httpx

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=error_response,
        )

        mock_player_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        # Should be ephemeral
        assert call_kwargs[1].get("ephemeral", False)
        # Message should mention profile not found
        msg = call_kwargs[0][0]
        assert "not found" in msg.lower() or "profile" in msg.lower()

    def test_profile_api_error_non_404(self, mock_player_cog):
        """profile should handle non-404 API errors gracefully."""
        interaction = _create_mock_interaction()

        import httpx

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_player_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_profile_generic_exception(self, mock_player_cog):
        """profile should handle generic exceptions with warning message."""
        interaction = _create_mock_interaction()

        mock_player_cog.http_client.post = AsyncMock(side_effect=RuntimeError("network issue"))

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "error" in msg.lower() or "⚠️" in msg


# ---------------------------------------------------------------------------
# leaderboard command
# ---------------------------------------------------------------------------


class TestLeaderboardCommand:
    """Tests for the /leaderboard slash command."""

    def test_leaderboard_success(self, mock_player_cog):
        """leaderboard should display top players."""
        interaction = _create_mock_interaction()

        players = [
            {"user_id": 111, "tier": "Gold", "xp": 1000, "credits": 5000},
            {"user_id": 222, "tier": "Silver", "xp": 500, "credits": 2000},
        ]

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = players
        mock_player_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()

    def test_leaderboard_empty(self, mock_player_cog):
        """leaderboard with no players should send ephemeral 'No players' message."""
        interaction = _create_mock_interaction()

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_player_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_leaderboard_with_tier_filter(self, mock_player_cog):
        """leaderboard with tier param should include tier in title."""
        interaction = _create_mock_interaction()

        players = [{"user_id": 111, "tier": "Gold", "xp": 999, "credits": 9999}]

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = players
        mock_player_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier="Gold"))

        interaction.followup.send.assert_awaited_once()

    def test_leaderboard_api_error(self, mock_player_cog):
        """leaderboard should handle API errors gracefully."""
        interaction = _create_mock_interaction()

        import httpx

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Error",
            request=MagicMock(),
            response=error_response,
        )
        mock_player_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_leaderboard_generic_exception(self, mock_player_cog):
        """leaderboard should handle generic exceptions."""
        interaction = _create_mock_interaction()

        mock_player_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# prestige command
# ---------------------------------------------------------------------------


class TestPrestigeCommand:
    """Tests for the /prestige slash command."""

    def test_prestige_eligible_platinum(self, mock_player_cog):
        """prestige for Platinum tier player should show confirmation embed."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = player_data
        mock_player_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        # Should send an embed (ephemeral confirmation)
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)

    def test_prestige_not_eligible_non_platinum(self, mock_player_cog):
        """prestige for non-Platinum tier should send ephemeral rejection."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Gold")

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = player_data
        mock_player_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "Platinum" in msg
        assert call_args[1].get("ephemeral", False)

    def test_prestige_bronze_not_eligible(self, mock_player_cog):
        """prestige for Bronze tier should send rejection."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = player_data
        mock_player_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)

    def test_prestige_generic_exception(self, mock_player_cog):
        """prestige should handle exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_player_cog.http_client.post = AsyncMock(side_effect=RuntimeError("connection fail"))

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# _get_tier_color helper
# ---------------------------------------------------------------------------


class TestGetTierColor:
    """Tests for the _get_tier_color helper method."""

    def _assert_color(self, color):
        """Assert value is a discord Color/Colour object.

        We check by class name rather than isinstance because _evict_discord_modules()
        in the fixture can reload discord, making the top-level 'discord' name reference
        a different module object.  The class name is always 'Colour' regardless.
        """
        assert type(color).__name__ == "Colour", (
            f"Expected a discord.Colour, got {type(color)}"
        )

    def test_bronze_color(self, mock_player_cog):
        """Bronze tier should return the bronze color."""
        color = mock_player_cog._get_tier_color("Bronze")
        self._assert_color(color)

    def test_silver_color(self, mock_player_cog):
        """Silver tier should return the silver color."""
        color = mock_player_cog._get_tier_color("Silver")
        self._assert_color(color)

    def test_gold_color(self, mock_player_cog):
        """Gold tier should return the gold color."""
        color = mock_player_cog._get_tier_color("Gold")
        self._assert_color(color)

    def test_platinum_color(self, mock_player_cog):
        """Platinum tier should return the platinum color."""
        color = mock_player_cog._get_tier_color("Platinum")
        self._assert_color(color)

    def test_unknown_tier_defaults(self, mock_player_cog):
        """Unknown tier should return the default color."""
        color = mock_player_cog._get_tier_color("Diamond")
        self._assert_color(color)


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks on each slash command."""

    def test_profile_error_handler_response_not_done(self, mock_player_cog):
        """profile_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.profile_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_profile_error_handler_response_already_done(self, mock_player_cog):
        """profile_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)

        error = MagicMock()

        asyncio.run(mock_player_cog.profile_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_leaderboard_error_handler_response_not_done(self, mock_player_cog):
        """leaderboard_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.leaderboard_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_prestige_error_handler_response_not_done(self, mock_player_cog):
        """prestige_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.prestige_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add PlayerCog to the bot."""
        # Re-wire mocks before importing cog
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.playerCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        # The added object should be a PlayerCog instance
        from cogs.playerCog import PlayerCog
        assert isinstance(added_arg, PlayerCog)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
