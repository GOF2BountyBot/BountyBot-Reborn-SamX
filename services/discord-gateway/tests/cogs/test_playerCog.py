"""Tests for playerCog — boosting coverage from 0% to 60%+."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
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
# prestige command — new confirm-flow tests
# ---------------------------------------------------------------------------


class TestPrestigeConfirmFlow:
    """Tests for the /prestige confirm flow (wired API)."""

    def test_prestige_no_confirm_shows_warning_embed(self, mock_player_cog):
        """/prestige without confirm shows warning embed (ephemeral)."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = player_data
        mock_player_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction, confirm=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)
        # Embed description should mention CONFIRM
        embed = call_kwargs["embed"]
        assert "CONFIRM" in (embed.description or "")

    def test_prestige_wrong_confirm_shows_warning(self, mock_player_cog):
        """/prestige with wrong confirm value shows warning embed."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=1)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = player_data
        mock_player_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction, confirm="yes"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)

    def test_prestige_with_confirm_calls_api_and_shows_success(self, mock_player_cog):
        """/prestige with confirm=CONFIRM calls the prestige API and shows success."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        prestige_data = {
            "player_id": 1,
            "prestige_count": 1,
            "level_before": 10,
            "division_before": "Platinum",
        }
        prestige_resp = MagicMock()
        prestige_resp.raise_for_status = MagicMock()
        prestige_resp.json.return_value = prestige_data

        mock_player_cog.http_client.post = AsyncMock(side_effect=[player_resp, prestige_resp])

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction, confirm="CONFIRM"))

        # followup.send called once with embed (success)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "1" in (embed.description or "")  # prestige_count shown

    def test_prestige_api_400_level_too_low(self, mock_player_cog):
        """/prestige with confirm=CONFIRM shows error on API 400."""
        import httpx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Player must be level 10 to prestige."}
        http_error = httpx.HTTPStatusError(
            "400 Bad Request",
            request=MagicMock(),
            response=error_response,
        )

        mock_player_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction, confirm="CONFIRM"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)
        msg = call_args[0][0]
        assert "level" in msg.lower() or "prestige" in msg.lower()

    def test_prestige_api_failure_generic(self, mock_player_cog):
        """/prestige with confirm=CONFIRM handles generic failure from prestige endpoint."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        mock_player_cog.http_client.post = AsyncMock(side_effect=[player_resp, RuntimeError("prestige service down")])

        asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction, confirm="CONFIRM"))

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
        assert type(color).__name__ == "Colour", f"Expected a discord.Colour, got {type(color)}"

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


# ===========================================================================
# Gap 4: Discord Embed Rendering Rule Tests — PlayerCog
# ===========================================================================


class TestProfileNoTimestampsInBadLocations:
    """Gap 4: Embed rendering rule — <t:...> Discord timestamps must NOT appear
    in the embed footer or author fields, as they render as raw text there.
    Timestamps should only appear in embed fields or the description.
    """

    def _get_profile_embed(self, mock_player_cog):
        """Helper: trigger /profile and return the sent embed."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        # Minimal GET side_effect — stats + promo fail (non-fatal)
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(
            side_effect=[
                stats_resp,
                RuntimeError("promo not needed for this test"),
            ]
        )

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        return call_kwargs.get("embed")

    def test_profile_no_timestamps_in_footer(self, mock_player_cog):
        """Profile embed footer must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> timestamps in fields and descriptions but NOT in footers
        where they appear as raw text, confusing users.
        """
        embed = self._get_profile_embed(mock_player_cog)
        if embed is None:
            return  # embed not sent (error path) — skip

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            # discord.EmbedProxy / MagicMock — try to get .text attribute
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields or description."
        )

    def test_profile_no_timestamps_in_author(self, mock_player_cog):
        """Profile embed author field must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> in fields/descriptions but NOT in author fields.
        """
        embed = self._get_profile_embed(mock_player_cog)
        if embed is None:
            return

        author = embed.author
        author_text = ""
        if author is not None:
            try:
                author_text = str(author.name or "")
            except AttributeError:
                author_text = str(author)

        assert "<t:" not in author_text, (
            f"Discord timestamp found in embed author: {author_text!r}. "
            "Timestamps in author fields render as raw text — move them to fields or description."
        )


# ---------------------------------------------------------------------------
# Helper for role-assignment tests
# ---------------------------------------------------------------------------


def _make_config_resp(
    bh_role_id: int | None,
    bronze_role_id: int | None = 111222001,
    silver_role_id: int | None = 111222002,
    gold_role_id: int | None = 111222003,
    platinum_role_id: int | None = 111222004,
):
    """Return a mock HTTP response for GET /config/guild/{id}."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "bounty_hunter_role_id": bh_role_id,
        "bronze_role_id": bronze_role_id,
        "silver_role_id": silver_role_id,
        "gold_role_id": gold_role_id,
        "platinum_role_id": platinum_role_id,
    }
    return resp


def _make_promo_resp(can_promote=False, next_tier="Silver", threshold=1000):
    """Return a mock HTTP response for GET /players/{id}/promotion-status."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "can_promote": can_promote,
        "next_tier": next_tier,
        "xp_threshold_for_next": threshold,
        "xp_surplus_for_next": None,
    }
    return resp


def _create_interaction_with_roles(user_id=111111111, guild_id=987654321, existing_roles=None):
    """Build interaction mock with a proper roles list and async role methods."""
    interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
    interaction.user.roles = existing_roles if existing_roles is not None else []
    interaction.user.add_roles = AsyncMock()
    interaction.user.remove_roles = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Role assignment in /profile
# ---------------------------------------------------------------------------


class TestProfileRoleAssignment:
    """Tests for Bounty Hunter role assignment logic added to /profile."""

    def test_profile_assigns_bounty_hunter_role_on_first_use(self, mock_player_cog):
        """After player creation, config is fetched, BH + tier roles found, user has none → add_roles called."""
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        bh_role_id = 999888777
        bronze_role_id = 111222001

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()
        config_resp = _make_config_resp(
            bh_role_id, bronze_role_id=bronze_role_id, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )

        # guild.get_role returns distinct role mocks for each ID
        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # GET is called 3 times: stats, promotion-status, config
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles was called once — with BH role + Bronze tier role
        interaction.user.add_roles.assert_awaited_once()
        added_args = interaction.user.add_roles.call_args[0]
        added_ids = {r.id for r in added_args}
        assert added_ids == {bh_role_id, bronze_role_id}

    def test_profile_skips_role_if_already_assigned(self, mock_player_cog):
        """User already has the Bounty Hunter role → add_roles NOT called."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        # User already has the role in their roles list
        interaction = _create_interaction_with_roles(existing_roles=[mock_role])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()
        config_resp = _make_config_resp(bh_role_id)

        interaction.guild.get_role = MagicMock(return_value=mock_role)

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles should NOT be called
        interaction.user.add_roles.assert_not_awaited()

    def test_profile_skips_role_if_config_has_no_role_id(self, mock_player_cog):
        """All role IDs None in config → no role assignment attempted."""
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()
        # No BH role or tier roles configured at all
        config_resp = _make_config_resp(
            None, bronze_role_id=None, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles should NOT be called since no roles configured
        interaction.user.add_roles.assert_not_awaited()

    def test_profile_works_normally_if_role_assignment_fails(self, mock_player_cog):
        """add_roles raises an exception → profile embed is still sent (non-fatal)."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()
        config_resp = _make_config_resp(bh_role_id)

        interaction.guild.get_role = MagicMock(return_value=mock_role)
        # add_roles raises
        interaction.user.add_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was still sent despite role failure
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_profile_skips_role_if_config_fetch_fails(self, mock_player_cog):
        """Config API returns error → profile still works (role assignment non-fatal)."""
        import httpx

        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()

        error_response = MagicMock()
        error_response.status_code = 500
        config_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_error])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # add_roles was never called
        interaction.user.add_roles.assert_not_awaited()


# ---------------------------------------------------------------------------
# /unregister command
# ---------------------------------------------------------------------------


class TestUnregisterCommand:
    """Tests for the /unregister slash command."""

    def test_unregister_removes_role_successfully(self, mock_player_cog):
        """Happy path: user has all 5 BH roles → all removed, confirmation sent."""
        bh_role_id = 999888777
        bronze_id, silver_id, gold_id, platinum_id = 111222001, 111222002, 111222003, 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_silver.name = "Bounty Hunter Silver"
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_gold.name = "Bounty Hunter Gold"
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id
        mock_platinum.name = "Bounty Hunter Platinum"

        all_roles = [mock_bh_role, mock_bronze, mock_silver, mock_gold, mock_platinum]
        interaction = _create_interaction_with_roles(existing_roles=all_roles)

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_resp = _make_config_resp(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        # remove_roles must be called once with all 5 role args
        interaction.user.remove_roles.assert_awaited_once()
        call_args_pos = interaction.user.remove_roles.call_args[0]
        assert len(call_args_pos) == 5, f"Expected 5 roles to be removed, got {len(call_args_pos)}"
        removed_ids = {r.id for r in call_args_pos}
        assert removed_ids == {bh_role_id, bronze_id, silver_id, gold_id, platinum_id}
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_no_role_configured(self, mock_player_cog):
        """bounty_hunter_role_id is None → warning message sent."""
        interaction = _create_interaction_with_roles(existing_roles=[])

        config_resp = _make_config_resp(None)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "no" in msg.lower() or "configured" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_role_not_found_in_guild(self, mock_player_cog):
        """bh_role_id exists in config but guild.get_role() returns None → warning."""
        bh_role_id = 999888777
        interaction = _create_interaction_with_roles(existing_roles=[])
        # guild.get_role returns None for all lookups
        interaction.guild.get_role = MagicMock(return_value=None)

        config_resp = _make_config_resp(bh_role_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "not found" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_user_doesnt_have_role(self, mock_player_cog):
        """User has NONE of the Bounty Hunter roles → info message sent, remove_roles not called."""
        bh_role_id = 999888777
        bronze_id, silver_id, gold_id, platinum_id = 111222001, 111222002, 111222003, 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id

        # User has NO roles
        interaction = _create_interaction_with_roles(existing_roles=[])

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_resp = _make_config_resp(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ℹ️" in msg or "don't have" in msg.lower() or "doesn't have" in msg.lower() or "not have" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_remove_fails(self, mock_player_cog):
        """remove_roles raises → error message sent."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id
        mock_role.name = "Bounty Hunter"

        interaction = _create_interaction_with_roles(existing_roles=[mock_role])
        interaction.guild.get_role = MagicMock(return_value=mock_role)
        interaction.user.remove_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        # No tier roles configured for simplicity
        config_resp = _make_config_resp(
            bh_role_id, bronze_role_id=None, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_config_fetch_fails(self, mock_player_cog):
        """Config API error → error message sent."""
        import httpx

        interaction = _create_interaction_with_roles(existing_roles=[])

        error_response = MagicMock()
        error_response.status_code = 503
        config_error = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=error_response,
        )
        mock_player_cog.http_client.get = AsyncMock(side_effect=config_error)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_error_handler_response_not_done(self, mock_player_cog):
        """unregister_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.unregister_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_unregister_error_handler_response_already_done(self, mock_player_cog):
        """unregister_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.unregister_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_unregister_removes_only_roles_user_has(self, mock_player_cog):
        """User has @Bounty Hunter + @BH-Bronze → only those 2 roles removed."""
        bh_role_id = 999888777
        bronze_id = 111222001
        silver_id = 111222002
        gold_id = 111222003
        platinum_id = 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_silver.name = "Bounty Hunter Silver"
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_gold.name = "Bounty Hunter Gold"
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id
        mock_platinum.name = "Bounty Hunter Platinum"

        # User only has BH + Bronze
        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role, mock_bronze])

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)
        config_resp = _make_config_resp(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        assert len(removed_args) == 2, f"Expected 2 roles removed, got {len(removed_args)}"
        removed_ids = {r.id for r in removed_args}
        assert removed_ids == {bh_role_id, bronze_id}

    def test_unregister_tier_role_id_none_in_config(self, mock_player_cog):
        """Config has some tier role IDs as None → only configured roles considered (no error)."""
        bh_role_id = 999888777
        bronze_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"

        # User has BH + Bronze; silver/gold/platinum are None in config
        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role, mock_bronze])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_id: mock_bronze}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_resp = _make_config_resp(bh_role_id, bronze_id, None, None, None)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # Should succeed without error, removing BH + Bronze
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert removed_ids == {bh_role_id, bronze_id}
        # Success message sent
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()

    # ------------------------------------------------------------------
    # Adversarial edge cases (Q18 / Q19 coverage)
    # ------------------------------------------------------------------

    def test_unregister_user_has_only_tier_role_no_generic_bh_role(self, mock_player_cog):
        """Q18 / Adversarial: User has ONLY a tier role (e.g. BH-Bronze) but NOT
        the generic @Bounty Hunter role (a degenerate state that can happen via admin
        manipulation or a prior A.14-style bug).

        The code must still detect and remove the tier role without erroring.
        The 'role in user.roles' guard for the generic BH role should not prevent
        tier-role cleanup.
        """
        bh_role_id = 999888777
        bronze_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"

        # User has ONLY the tier role — no generic BH role
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_id: mock_bronze}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # Config returns both bh_role_id and bronze_role_id
        config_resp = _make_config_resp(
            bh_role_id, bronze_role_id=bronze_id, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # remove_roles must be called — the tier role should be removed even without the generic BH role
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert bronze_id in removed_ids, (
            "Tier-only user must have their tier role removed even without generic @Bounty Hunter role"
        )
        # bh_role should NOT be in the remove list since user doesn't have it
        assert bh_role_id not in removed_ids

        # Success message should list the removed tier role
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()

    def test_unregister_all_tier_ids_none_and_user_has_no_bh_role(self, mock_player_cog):
        """Q19 / Adversarial: Config has all tier_role_ids = None AND user has no
        generic BH role either. The 'you don't have the role' short-circuit must
        still fire cleanly — no exception, no double-send, no remove_roles call.
        """
        bh_role_id = 999888777

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"

        # User has NO BH-related roles at all
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=mock_bh_role)

        # Config: bh_role_id set but ALL tier IDs are None
        config_resp = _make_config_resp(
            bh_role_id,
            bronze_role_id=None,
            silver_role_id=None,
            gold_role_id=None,
            platinum_role_id=None,
        )
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # Must NOT call remove_roles — nothing to remove
        interaction.user.remove_roles.assert_not_awaited()

        # Must send exactly one message — the "you don't have the role" info message
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ℹ️" in msg or "don't have" in msg.lower() or "not have" in msg.lower(), (
            f"Expected 'you don't have the role' message, got: {msg!r}"
        )
        assert call_args[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# /promote command
# ---------------------------------------------------------------------------


class TestPromoteCommand:
    """Tests for the /promote slash command."""

    def test_promote_success(self, mock_player_cog):
        """/promote succeeds and shows tier promotion embed."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": "Gold",
        }
        promote_resp = MagicMock()
        promote_resp.raise_for_status = MagicMock()
        promote_resp.json.return_value = promote_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(return_value=promote_resp)

        asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_promote_success_eligible_for_next(self, mock_player_cog):
        """/promote with eligible_for_next=True shows further promotion message."""
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 20000,
            "eligible_for_next": True,
            "next_tier": "Gold",
        }
        promote_resp = MagicMock()
        promote_resp.raise_for_status = MagicMock()
        promote_resp.json.return_value = promote_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(return_value=promote_resp)

        asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        # Should mention ability to promote again
        field_values = " ".join(f.value for f in embed.fields)
        assert "promote" in field_values.lower() or "Gold" in field_values

    def test_promote_not_eligible_400_shows_error_embed(self, mock_player_cog):
        """/promote with 400 from API shows error embed."""
        import httpx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Not eligible for promotion. Need 1,000 XP for Silver."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs["embed"]
        assert "Cannot Promote" in embed.title or "❌" in embed.title

    def test_promote_api_error_non_400(self, mock_player_cog):
        """/promote with non-400 API error shows generic error."""
        import httpx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_promote_generic_exception(self, mock_player_cog):
        """/promote handles generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_player_cog.http_client.post = AsyncMock(side_effect=RuntimeError("network error"))

        asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)


# ---------------------------------------------------------------------------
# /profile with promotion status
# ---------------------------------------------------------------------------


class TestProfileWithPromotionStatus:
    """Tests for the promotion status indicator in /profile."""

    def _setup_profile_mocks(self, mock_player_cog, player_data, stats_data, promo_data):
        """Helper: wire up HTTP mock for profile + stats + promotion status."""
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = MagicMock()
        promo_resp.raise_for_status = MagicMock()
        promo_resp.json.return_value = promo_data

        # config fetch (role assignment) raises so it's non-fatal
        config_error = RuntimeError("config fetch failed")

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_error])

    def test_profile_shows_eligible_promotion(self, mock_player_cog):
        """Profile shows 'Eligible for X' when can_promote=True."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": True,
            "next_tier": "Silver",
            "xp_threshold_for_next": 1000,
            "xp_surplus_for_next": 500,
        }

        self._setup_profile_mocks(mock_player_cog, player_data, stats_data, promo_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Promotion" in field_names

    def test_profile_shows_next_tier_threshold_when_not_eligible(self, mock_player_cog):
        """Profile shows threshold when can_promote=False and next_tier is not None."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": False,
            "next_tier": "Silver",
            "xp_threshold_for_next": 1000,
            "xp_surplus_for_next": None,
        }

        self._setup_profile_mocks(mock_player_cog, player_data, stats_data, promo_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Next Tier" in field_names

    def test_profile_shows_max_tier_for_platinum(self, mock_player_cog):
        """Profile shows 'Maximum Tier' when next_tier is None (Platinum)."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Platinum")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": False,
            "next_tier": None,
            "xp_threshold_for_next": None,
            "xp_surplus_for_next": None,
        }

        self._setup_profile_mocks(mock_player_cog, player_data, stats_data, promo_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        # Find the "Tier" field added for Platinum
        tier_fields = [f for f in embed.fields if f.name == "Tier"]
        assert len(tier_fields) > 0
        assert "Maximum" in tier_fields[-1].value

    def test_profile_still_works_if_promotion_status_fails(self, mock_player_cog):
        """Profile still displays normally if promotion status API call fails."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        # Stats succeeds, promo_status fails, config fails
        mock_player_cog.http_client.get = AsyncMock(
            side_effect=[stats_resp, RuntimeError("promo status unavailable"), RuntimeError("config fail")]
        )

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# Error handler for /promote
# ---------------------------------------------------------------------------


class TestPromoteErrorHandler:
    """Tests for the /promote error handler."""

    def test_promote_error_handler_response_not_done(self, mock_player_cog):
        """promote_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.promote_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_promote_error_handler_response_already_done(self, mock_player_cog):
        """promote_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.promote_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bug 6: profile Joined timestamp must be in a field, NOT the footer
# ---------------------------------------------------------------------------


class TestProfileJoinedTimestampLocation:
    """Verify that the 'Joined' timestamp is rendered as an embed field (not footer)."""

    def _setup_profile(self, mock_player_cog, player_data, stats_data):
        """Wire HTTP mocks for a basic /profile call, promotion and config are non-fatal failures."""
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        # promo status and config both fail gracefully
        mock_player_cog.http_client.get = AsyncMock(
            side_effect=[stats_resp, RuntimeError("promo fail"), RuntimeError("config fail")]
        )

    def test_joined_is_in_embed_field(self, mock_player_cog):
        """Profile embed must have a field named 'Joined'."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        self._setup_profile(mock_player_cog, player_data, stats_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Joined" in field_names, f"Expected 'Joined' field; fields are: {field_names}"

    def test_footer_does_not_contain_joined(self, mock_player_cog):
        """Profile embed footer must NOT include the word 'Joined'."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        self._setup_profile(mock_player_cog, player_data, stats_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer and embed.footer.text else ""
        assert "Joined" not in footer_text, f"Footer must not contain 'Joined'; footer text: {footer_text!r}"

    def test_footer_still_contains_player_id(self, mock_player_cog):
        """Profile embed footer should still contain the Player ID."""
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        self._setup_profile(mock_player_cog, player_data, stats_data)

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer and embed.footer.text else ""
        # player_data["id"] is 1 from _make_player_data
        assert "Player ID" in footer_text, f"Footer should contain 'Player ID'; footer text: {footer_text!r}"


# ---------------------------------------------------------------------------
# /loadout command tests (unified LoadoutResponse + shared embed builder)
# ---------------------------------------------------------------------------


def _make_player_loadout_response(**overrides):
    """Return a minimal unified-schema LoadoutResponse dict from bot-core."""
    data = {
        "subject_kind": "player",
        "subject_name": "Alice",
        "subject_mention": "<@12345>",
        "player_id": 1,
        "user_discord_id": 12345,
        "ship_name": "Betty",
        "ship_nickname": None,
        "ship_emoji": "🛸",
        "ship_icon": "https://cdn/betty.png",
        "thumbnail_url": "https://cdn/betty.png",
        "ship_stats": {
            "armour": 200,
            "cargo": 20,
            "handling": 50,
            "hp": 200,
            "dps": 7.5,
            "total_value": 3570,
            "max_primaries": 1,
            "max_secondaries": 0,
            "max_turrets": 0,
            "max_modules": 2,
        },
        "weapons": [{"name": "Nirai Impulse EX 1", "emoji": "<:ni:1>", "dps": 7.5, "value": 2500}],
        "turrets": [],
        "modules": [
            {
                "name": "E2 Exoclad",
                "emoji": "<:e2:1>",
                "type": "ArmourModule",
                "value": 1070,
                "tech_level": 1,
                "effects": [{"label": "Armour", "value": "40"}],
                "combat_tier": "combat",
            }
        ],
        "cargo": [],
        "cargo_total_count": 0,
    }
    data.update(overrides)
    return data


class TestLoadoutCommand:
    """Tests for the /loadout slash command (shared builder consumer)."""

    def _setup_loadout(self, cog, player_data, loadout_data):
        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = player_data
        player_resp.raise_for_status = MagicMock()

        loadout_resp = MagicMock()
        loadout_resp.status_code = 200
        loadout_resp.json.return_value = loadout_data
        loadout_resp.raise_for_status = MagicMock()

        cog.http_client.post = AsyncMock(return_value=player_resp)
        cog.http_client.get = AsyncMock(return_value=loadout_resp)

    def test_loadout_success_self_default_ephemeral(self, mock_player_cog):
        """Self-view with default public=False → defer+followup are ephemeral."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        # defer called with ephemeral=True
        defer_kwargs = interaction.response.defer.call_args[1]
        assert defer_kwargs.get("ephemeral") is True

        # followup ephemeral=True
        send_kwargs = interaction.followup.send.call_args[1]
        assert send_kwargs.get("ephemeral") is True

    def test_loadout_public_true_sends_non_ephemeral(self, mock_player_cog):
        """public=True → defer non-ephemeral, followup non-ephemeral."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        defer_kwargs = interaction.response.defer.call_args[1]
        assert defer_kwargs.get("ephemeral") is False
        send_kwargs = interaction.followup.send.call_args[1]
        assert send_kwargs.get("ephemeral") is False

    def test_loadout_title_uses_live_display_name(self, mock_player_cog):
        """Embed title uses interaction user.display_name, NOT the bot-core subject_name."""
        interaction = _create_mock_interaction()
        interaction.user.display_name = "LiveDisplayName"
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.title == "Loadout — LiveDisplayName"

    def test_loadout_description_is_user_mention(self, mock_player_cog):
        """Description is overwritten to the live Discord mention."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        # Description is <@user.id> mention of the target
        assert embed.description == f"<@{interaction.user.id}>"

    def test_loadout_no_active_ship_sends_ephemeral_error_embed(self, mock_player_cog):
        """'No active ship' response → red error embed, always ephemeral."""
        interaction = _create_mock_interaction()
        no_ship_resp = _make_player_loadout_response(message="No active ship")

        self._setup_loadout(mock_player_cog, _make_player_data(), no_ship_resp)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        send_kwargs = interaction.followup.send.call_args[1]
        # Errors always ephemeral regardless of public=True
        assert send_kwargs.get("ephemeral") is True
        embed = send_kwargs["embed"]
        assert "No active ship" in (embed.description or "")

    def test_loadout_self_view_passes_include_cargo_true(self, mock_player_cog):
        """Self-view must pass include_cargo=true to bot-core."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        get_call = mock_player_cog.http_client.get.call_args_list[0]
        params = get_call[1].get("params", {})
        assert params.get("include_cargo") == "true"

    def test_loadout_other_player_non_admin_passes_include_cargo_false(self, mock_player_cog):
        """Other-player view as non-admin must pass include_cargo=false."""
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        # Patch _check_is_admin to return False (non-admin)
        with patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        get_call = mock_player_cog.http_client.get.call_args_list[0]
        params = get_call[1].get("params", {})
        assert params.get("include_cargo") == "false"

    def test_loadout_other_player_admin_passes_include_cargo_true(self, mock_player_cog):
        """Other-player view as admin must pass include_cargo=true."""
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        with patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=True)):
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        get_call = mock_player_cog.http_client.get.call_args_list[0]
        params = get_call[1].get("params", {})
        assert params.get("include_cargo") == "true"

    def test_loadout_viewer_discord_id_param_included(self, mock_player_cog):
        """viewer_discord_id query param is the target user's Discord ID."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        get_call = mock_player_cog.http_client.get.call_args_list[0]
        params = get_call[1].get("params", {})
        assert params.get("viewer_discord_id") == str(interaction.user.id)

    def test_loadout_profile_post_does_not_overwrite_username(self, mock_player_cog):
        """POST to /players/ sends discord_username=None to avoid overwriting."""
        interaction = _create_mock_interaction()
        self._setup_loadout(mock_player_cog, _make_player_data(), _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        post_call = mock_player_cog.http_client.post.call_args_list[0]
        body = post_call[1].get("json", {})
        assert body.get("discord_username") is None

    def test_loadout_http_404_sends_ephemeral(self, mock_player_cog):
        """404 HTTPStatusError → ephemeral error message."""
        import httpx

        interaction = _create_mock_interaction()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json = MagicMock(return_value={"detail": "not found"})
        err = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)

        player_resp = MagicMock()
        player_resp.json.return_value = _make_player_data()
        player_resp.raise_for_status = MagicMock()
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=err)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        send_kwargs = interaction.followup.send.call_args[1]
        # Errors always ephemeral
        assert send_kwargs.get("ephemeral") is True

    def test_loadout_generic_exception_sends_ephemeral_warning(self, mock_player_cog):
        """Unexpected exception → ephemeral warning."""
        interaction = _create_mock_interaction()
        mock_player_cog.http_client.post = AsyncMock(side_effect=Exception("boom"))

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


class TestLoadoutEmbedContent:
    """Tests that the embed produced by /loadout carries the expected sections."""

    def _setup(self, cog, loadout_data):
        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = _make_player_data()
        player_resp.raise_for_status = MagicMock()

        loadout_resp = MagicMock()
        loadout_resp.status_code = 200
        loadout_resp.json.return_value = loadout_data
        loadout_resp.raise_for_status = MagicMock()

        cog.http_client.post = AsyncMock(return_value=player_resp)
        cog.http_client.get = AsyncMock(return_value=loadout_resp)

    def test_active_ship_field_present(self, mock_player_cog):
        interaction = _create_mock_interaction()
        self._setup(mock_player_cog, _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Active Ship" in field_names
        assert "Ship Stats" in field_names

    def test_weapons_section_header_with_n_over_m(self, mock_player_cog):
        interaction = _create_mock_interaction()
        self._setup(mock_player_cog, _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field = next(f for f in embed.fields if f.name.startswith("Primary Weapons"))
        # 1 weapon, max_primaries=1
        assert field.name == "Primary Weapons <1/1>"

    def test_modules_section_header_with_n_over_m(self, mock_player_cog):
        interaction = _create_mock_interaction()
        self._setup(mock_player_cog, _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        assert field.name == "Modules <1/2>"

    def test_cargo_hold_shown_for_self_view(self, mock_player_cog):
        """Self-view → Cargo Hold header always rendered (empty shows 'Empty')."""
        interaction = _create_mock_interaction()
        self._setup(mock_player_cog, _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        cargo_field = next((f for f in embed.fields if f.name.startswith("Cargo Hold")), None)
        assert cargo_field is not None
        # Capacity from ship_stats.cargo=20
        assert cargo_field.name == "Cargo Hold <0/20>"

    def test_cargo_hidden_for_non_admin_other_view(self, mock_player_cog):
        """Non-admin viewing another player → no Cargo Hold section."""
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        self._setup(mock_player_cog, _make_player_loadout_response())

        with patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        embed = interaction.followup.send.call_args[1]["embed"]
        names = [f.name for f in embed.fields]
        assert not any(n.startswith("Cargo Hold") for n in names)

    def test_no_footer_no_timestamp(self, mock_player_cog):
        """New embed has no footer and no timestamp (spec §3.1)."""
        interaction = _create_mock_interaction()
        self._setup(mock_player_cog, _make_player_loadout_response())

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.footer.text is None
        assert embed.timestamp is None


class TestLoadoutErrorHandler:
    def test_loadout_error_handler_response_not_done(self, mock_player_cog):
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.loadout_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_loadout_error_handler_response_already_done(self, mock_player_cog):
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.loadout_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /register alias (A.19 regression)
# ---------------------------------------------------------------------------


class TestRegisterAlias:
    """A.19: /register is a full behavioural alias for /profile.

    Both commands must produce the same embed shape, invoke the same shared
    handler, and send ``discord_username`` on the player upsert.
    """

    def test_register_happy_path_matches_profile(self, mock_player_cog):
        """/register on a Bronze player yields the same embed shape as /profile."""
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

        asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_register_delegates_to_shared_handler(self, mock_player_cog):
        """Both /profile and /register go through the same _display_profile handler."""
        interaction_p = _create_mock_interaction()
        interaction_r = _create_mock_interaction()

        with patch.object(mock_player_cog, "_display_profile", new=AsyncMock()) as mock_handler:
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction_p))
            asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction_r))

            assert mock_handler.await_count == 2
            # First call came from /profile, second from /register.
            first_arg_p = mock_handler.await_args_list[0][0][0]
            first_arg_r = mock_handler.await_args_list[1][0][0]
            assert first_arg_p is interaction_p
            assert first_arg_r is interaction_r

    def test_register_404_behaves_same_as_profile(self, mock_player_cog):
        """/register must handle a 404 from player upsert identically to /profile."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=error_response,
        )

        mock_player_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        # Ephemeral so only the invoker sees the error.
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "not found" in msg.lower() or "profile" in msg.lower()

    def test_register_sends_discord_username_on_upsert(self, mock_player_cog):
        """A.3-style invariant: /register posts ``discord_username = str(user)``."""
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

        asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        # Find the POST to /players/ — the first positional arg is the URL,
        # and the json= kwarg carries the upsert payload.
        post_calls = mock_player_cog.http_client.post.await_args_list
        players_post = next(call for call in post_calls if "/players/" in call[0][0])
        body = players_post[1]["json"]
        assert body["discord_id"] == interaction.user.id
        assert body["guild_id"] == interaction.guild_id
        # discord_username must be the live str(user), NOT None (preserves
        # the A.3 behaviour that username writes only happen on /profile and
        # now also /register, since /register is a full alias).
        assert body["discord_username"] == str(interaction.user)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
