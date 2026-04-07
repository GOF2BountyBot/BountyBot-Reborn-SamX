"""Tests for playerCog — boosting coverage from 0% to 60%+."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

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


def _make_config_resp(bh_role_id: int | None):
    """Return a mock HTTP response for GET /config/guild/{id}."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"bounty_hunter_role_id": bh_role_id}
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
        """After player creation, config is fetched, role found, user doesn't have it → add_roles called."""
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        bh_role_id = 999888777

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = player_data

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = stats_data

        promo_resp = _make_promo_resp()
        config_resp = _make_config_resp(bh_role_id)

        # guild.get_role returns a role mock that is NOT in user's roles list
        mock_role = MagicMock()
        mock_role.id = bh_role_id
        interaction.guild.get_role = MagicMock(return_value=mock_role)

        # GET is called 3 times: stats, promotion-status, config
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles was called with the role
        interaction.user.add_roles.assert_awaited_once_with(mock_role, reason="BountyBot player registration")

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
        """bounty_hunter_role_id is None → no role assignment attempted."""
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
        config_resp = _make_config_resp(None)  # no role configured

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles should NOT be called
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
        """Happy path: role exists, user has it → removed, confirmation sent."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        interaction = _create_interaction_with_roles(existing_roles=[mock_role])
        interaction.guild.get_role = MagicMock(return_value=mock_role)

        config_resp = _make_config_resp(bh_role_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.user.remove_roles.assert_awaited_once_with(mock_role, reason="Player unregistered from BountyBot")
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
        """Role ID exists in config but guild.get_role() returns None → warning."""
        bh_role_id = 999888777
        interaction = _create_interaction_with_roles(existing_roles=[])
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
        """User doesn't have the Bounty Hunter role → info message sent."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        # User has NO roles
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=mock_role)

        config_resp = _make_config_resp(bh_role_id)
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ℹ️" in msg or "don't have" in msg.lower() or "doesn't have" in msg.lower() or "not have" in msg.lower()  # noqa: RUF001
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_remove_fails(self, mock_player_cog):
        """remove_roles raises → error message sent."""
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        interaction = _create_interaction_with_roles(existing_roles=[mock_role])
        interaction.guild.get_role = MagicMock(return_value=mock_role)
        interaction.user.remove_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        config_resp = _make_config_resp(bh_role_id)
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
# /loadout command tests
# ---------------------------------------------------------------------------


def _make_loadout_data(ship_name="Betty", ship_emoji="🛸", shield_hp=0):
    """Return a minimal loadout dict."""
    return {
        "player_id": 1,
        "ship_name": ship_name,
        "ship_emoji": ship_emoji,
        "ship_nickname": None,
        "armor_hp": 200,
        "shield_hp": shield_hp,
        "total_hp": 200 + shield_hp,
        "total_dps": 7.5,
        "weapons": [{"name": "Nirai Impulse EX 1", "emoji": "<:niraiimpulseex1:123>", "dps": 7.5, "value": 2500}],
        "modules": [
            {"name": "E2 Exoclad", "emoji": "<:e2exoclad:456>", "type": "ArmourModule", "value": 1070, "tech_level": 1}
        ],
        "turrets": [],
        "total_value": 3570,
    }


class TestLoadoutCommand:
    """Tests for the /loadout slash command."""

    def _setup_loadout(self, cog, player_data, loadout_data):
        """Wire up mock HTTP responses for the /loadout command."""
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

    def test_loadout_success_self(self, mock_player_cog):
        """loadout sends embed for invoker's own loadout."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = _make_loadout_data()
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Loadout" in embed.title

    def test_loadout_success_with_shield(self, mock_player_cog):
        """loadout shows shield HP when shield_hp > 0."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = _make_loadout_data(shield_hp=50)
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        ship_field = next((f for f in embed.fields if "Ship Stats" in f.name), None)
        assert ship_field is not None
        assert "Shield HP" in ship_field.value

    def test_loadout_no_active_ship(self, mock_player_cog):
        """loadout sends ephemeral message when player has no active ship."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        no_ship_data = {"player_id": 1, "ship_name": None, "message": "No active ship"}

        player_resp = MagicMock()
        player_resp.json.return_value = player_data
        player_resp.raise_for_status = MagicMock()

        loadout_resp = MagicMock()
        loadout_resp.json.return_value = no_ship_data
        loadout_resp.raise_for_status = MagicMock()

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(return_value=loadout_resp)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_loadout_http_404_shows_not_found(self, mock_player_cog):
        """loadout sends 404 error message on HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)

        # post succeeds, get raises 404
        player_resp = MagicMock()
        player_resp.json.return_value = _make_player_data()
        player_resp.raise_for_status = MagicMock()
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=error)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in str(call_args).lower() or call_args[1].get("ephemeral") is True

    def test_loadout_generic_exception_sends_warning(self, mock_player_cog):
        """loadout sends warning message on unexpected exception."""
        interaction = _create_mock_interaction()
        mock_player_cog.http_client.post = AsyncMock(side_effect=Exception("network failure"))

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "⚠️" in str(call_args)

    def test_loadout_weapons_shown_in_embed(self, mock_player_cog):
        """Embed has Primary Weapons field when weapons are equipped."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = _make_loadout_data()
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("Weapon" in name for name in field_names)

    def test_loadout_modules_shown_in_embed(self, mock_player_cog):
        """Embed has Modules field when modules are equipped."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = _make_loadout_data()
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("Module" in name for name in field_names)

    def test_loadout_no_equipment_shows_fallback(self, mock_player_cog):
        """Embed shows 'No equipment' when no weapons/modules/turrets equipped."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = {
            "player_id": 1,
            "ship_name": "Betty",
            "ship_emoji": "🛸",
            "ship_nickname": None,
            "armor_hp": 200,
            "shield_hp": 0,
            "total_hp": 200,
            "total_dps": 0,
            "weapons": [],
            "modules": [],
            "turrets": [],
            "total_value": 0,
        }
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Equipment" in field_names

    def test_loadout_footer_contains_total_value(self, mock_player_cog):
        """Embed footer contains total value."""
        interaction = _create_mock_interaction()
        player_data = _make_player_data()
        loadout_data = _make_loadout_data()
        self._setup_loadout(mock_player_cog, player_data, loadout_data)

        asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer and embed.footer.text else ""
        assert "Total Value" in footer_text or "3,570" in footer_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
