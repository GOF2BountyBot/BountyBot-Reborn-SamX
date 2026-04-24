"""Tests for duelCog — covers /duel-challenge, /duel-accept, /duel-reject, autocomplete."""

import asyncio
import os
import sys
import types
from datetime import datetime
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
    interaction.user.mention = f"<@{user_id}>"
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_mock_duel(
    duel_id=1,
    challenger_id=100,
    target_id=200,
    stakes=500,
    status="pending",
    guild_id=987654321,
):
    """Return a minimal duel request dict."""
    return {
        "id": duel_id,
        "challenger_id": challenger_id,
        "target_id": target_id,
        "stakes": stakes,
        "status": status,
        "guild_id": guild_id,
        "created_at": datetime(2026, 1, 1, 12, 0, 0).isoformat(),
        "expires_at": datetime(2026, 1, 2, 12, 0, 0).isoformat(),
    }


def _make_accept_result(
    duel_id=1,
    is_stalemate=False,
    winner_name="Ship A",
    loser_name="Ship B",
    credits_transferred=500,
    stakes=500,
    challenger_id=100,
    challenger_credits=1500,
    target_id=200,
    target_credits=500,
):
    """Return a minimal accept result dict."""
    return {
        "duel_id": duel_id,
        "is_stalemate": is_stalemate,
        "winner_name": winner_name,
        "loser_name": loser_name,
        "credits_transferred": credits_transferred,
        "stakes": stakes,
        "challenger_id": challenger_id,
        "challenger_credits": challenger_credits,
        "target_id": target_id,
        "target_credits": target_credits,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock Discord bot for DuelCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    return bot


@pytest.fixture
def mock_duel_cog(mock_bot):
    """Create a DuelCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.duelCog import DuelCog

    cog = DuelCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestDuelCogInitialization:
    """Tests for DuelCog initialization."""

    def test_initialization(self, mock_duel_cog, mock_bot):
        """DuelCog should store bot reference and create http_client."""
        assert mock_duel_cog.bot is mock_bot
        assert mock_duel_cog.http_client is not None

    def test_cog_unload_closes_http_client(self, mock_duel_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_duel_cog.cog_unload())
        mock_duel_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# /duel-challenge command
# ---------------------------------------------------------------------------


class TestDuelChallengeCommand:
    """Tests for the /duel-challenge slash command."""

    def test_challenge_success_displays_embed(self, mock_duel_cog, make_mock_response):
        """/duel-challenge success should display an embed with duel details."""
        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")
        target.mention = "<@200>"

        resp = make_mock_response(_make_mock_duel(duel_id=1, challenger_id=100, target_id=200))
        mock_duel_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 500))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Duel Challenge" in embed.title or "⚔️" in embed.title

    def test_challenge_self_duel_rejected(self, mock_duel_cog):
        """/duel-challenge with self as target should show error on 400."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=100, username="SameUser")

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "A player cannot challenge themselves to a duel."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_challenge_insufficient_credits(self, mock_duel_cog):
        """/duel-challenge with insufficient credits should show error on 400."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Challenger has insufficient credits: has 100, needs 500."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 500))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_challenge_api_error_handled(self, mock_duel_cog):
        """/duel-challenge generic exception should show error message."""
        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")
        mock_duel_cog.http_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# /duel-accept command
# ---------------------------------------------------------------------------


class TestDuelAcceptCommand:
    """Tests for the /duel-accept slash command."""

    def test_accept_winner_result_shows_embed(self, mock_duel_cog, make_mock_response):
        """/duel-accept with decisive result should show winner embed."""
        interaction = _create_mock_interaction(user_id=200)
        resp = make_mock_response(
            _make_accept_result(
                duel_id=1,
                is_stalemate=False,
                winner_name="Ship A",
                loser_name="Ship B",
                credits_transferred=500,
            )
        )
        mock_duel_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Victory" in embed.title or "⚔️" in embed.title
        import discord

        assert embed.color == discord.Color.green()

    def test_accept_stalemate_result_shows_embed(self, mock_duel_cog, make_mock_response):
        """/duel-accept with stalemate result should show stalemate embed."""
        interaction = _create_mock_interaction(user_id=200)
        resp = make_mock_response(
            _make_accept_result(
                duel_id=1,
                is_stalemate=True,
                winner_name="",
                loser_name="",
                credits_transferred=0,
            )
        )
        mock_duel_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Stalemate" in embed.title
        import discord

        assert embed.color == discord.Color.yellow()

    def test_accept_duel_not_found(self, mock_duel_cog):
        """/duel-accept with non-existent duel should show not found error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_accept_invalid_duel_string(self, mock_duel_cog):
        """/duel-accept with non-numeric duel string should show error."""
        interaction = _create_mock_interaction(user_id=200)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# /duel-reject command
# ---------------------------------------------------------------------------


class TestDuelRejectCommand:
    """Tests for the /duel-reject slash command."""

    def test_reject_success_shows_confirmation(self, mock_duel_cog, make_mock_response):
        """/duel-reject success should show rejection confirmation embed."""
        interaction = _create_mock_interaction(user_id=200)
        resp = make_mock_response(_make_mock_duel(duel_id=1, challenger_id=100, target_id=200, status="rejected"))
        mock_duel_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Reject" in embed.title or "🚫" in embed.title

    def test_reject_duel_not_found(self, mock_duel_cog):
        """/duel-reject with non-existent duel should show not found error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_reject_invalid_duel_string(self, mock_duel_cog):
        """/duel-reject with non-numeric duel string should show error."""
        interaction = _create_mock_interaction(user_id=200)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "bad-id"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


class TestPendingDuelAutocomplete:
    """Tests for pending_duel_autocomplete method."""

    def test_autocomplete_returns_formatted_choices(self, mock_duel_cog, make_mock_response):
        """pending_duel_autocomplete should return formatted duel choices."""
        duels = [
            _make_mock_duel(duel_id=1, challenger_id=100, target_id=200, stakes=500),
            _make_mock_duel(duel_id=2, challenger_id=300, target_id=200, stakes=0),
        ]
        resp = make_mock_response(duels)
        mock_duel_cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_mock_interaction(user_id=200)

        result = asyncio.run(mock_duel_cog.pending_duel_autocomplete(interaction, ""))

        assert len(result) == 2
        # First duel has stakes
        assert result[0].value == "1"
        assert "500" in result[0].name
        assert "cr" in result[0].name
        # Second duel is friendly
        assert result[1].value == "2"
        assert "friendly" in result[1].name.lower()

    def test_autocomplete_api_failure_returns_empty_list(self, mock_duel_cog):
        """pending_duel_autocomplete should return empty list on API failure."""
        mock_duel_cog.http_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        interaction = _create_mock_interaction(user_id=200)

        result = asyncio.run(mock_duel_cog.pending_duel_autocomplete(interaction, ""))

        assert result == []


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_duel_challenge_error_handler_response_not_done(self, mock_duel_cog):
        """duel_challenge_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_duel_cog.duel_challenge_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_duel_accept_error_handler_response_not_done(self, mock_duel_cog):
        """duel_accept_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_duel_cog.duel_accept_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_duel_reject_error_handler_response_not_done(self, mock_duel_cog):
        """duel_reject_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_duel_cog.duel_reject_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_duel_challenge_error_handler_response_already_done(self, mock_duel_cog):
        """duel_challenge_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_duel_cog.duel_challenge_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()
