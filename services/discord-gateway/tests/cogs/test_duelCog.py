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
    challenger_name="challenger_player",
    challenger_credits=1500,
    challenger_hp=1000,
    challenger_dps=50.0,
    target_id=200,
    target_name="target_player",
    target_credits=500,
    target_hp=800,
    target_dps=40.0,
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
        "challenger_name": challenger_name,
        "challenger_credits": challenger_credits,
        "challenger_hp": challenger_hp,
        "challenger_dps": challenger_dps,
        "target_id": target_id,
        "target_name": target_name,
        "target_credits": target_credits,
        "target_hp": target_hp,
        "target_dps": target_dps,
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

        # POST calls: 1) resolve challenger player ID, 2) resolve target player ID, 3) create duel
        challenger_player_resp = make_mock_response({"id": 1})
        target_player_resp = make_mock_response({"id": 2})
        duel_resp = make_mock_response(_make_mock_duel(duel_id=1, challenger_id=1, target_id=2))
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[challenger_player_resp, target_player_resp, duel_resp])

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 500))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Duel Challenge" in embed.title or "⚔️" in embed.title

    def test_challenge_self_duel_rejected(self, mock_duel_cog, make_mock_response):
        """/duel-challenge with self as target should show error on 400."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=100, username="SameUser")

        # Player resolution succeeds for both; duel creation returns 400
        challenger_player_resp = make_mock_response({"id": 1})
        target_player_resp = make_mock_response({"id": 1})  # same player ID (self-duel)
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "A player cannot challenge themselves to a duel."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        async def post_side_effect(*args, **kwargs):
            if mock_duel_cog.http_client.post.call_count <= 2:
                if mock_duel_cog.http_client.post.call_count == 1:
                    return challenger_player_resp
                return target_player_resp
            raise http_error

        mock_duel_cog.http_client.post = AsyncMock(side_effect=[challenger_player_resp, target_player_resp, http_error])

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_challenge_insufficient_credits(self, mock_duel_cog, make_mock_response):
        """/duel-challenge with insufficient credits should show error on 400."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        # Player resolution succeeds; duel creation returns 400 insufficient credits
        challenger_player_resp = make_mock_response({"id": 1})
        target_player_resp = make_mock_response({"id": 2})
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Challenger has insufficient credits: has 100, needs 500."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[challenger_player_resp, target_player_resp, http_error])

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 500))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_challenge_api_error_handled(self, mock_duel_cog, make_mock_response):
        """/duel-challenge generic exception during duel creation should show error message."""
        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")
        # Player resolution succeeds; the duel creation itself throws a non-HTTP error
        challenger_player_resp = make_mock_response({"id": 1})
        target_player_resp = make_mock_response({"id": 2})
        mock_duel_cog.http_client.post = AsyncMock(
            side_effect=[challenger_player_resp, target_player_resp, RuntimeError("connection refused")]
        )

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_challenge_500_uses_sanitized_embed(self, mock_duel_cog, make_mock_response):
        """B.31b: non-400 HTTPStatusError during duel creation flows through the helper
        and produces an embed whose description does NOT contain the raw bot-core URL."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        # Player resolution succeeds; the duel creation returns 500
        challenger_player_resp = make_mock_response({"id": 1})
        target_player_resp = make_mock_response({"id": 2})
        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[challenger_player_resp, target_player_resp, http_error])

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")


# ---------------------------------------------------------------------------
# /duel-accept command
# ---------------------------------------------------------------------------


class TestDuelAcceptCommand:
    """Tests for the /duel-accept slash command."""

    def test_accept_winner_result_shows_embed(self, mock_duel_cog, make_mock_response):
        """/duel-accept with decisive result should show winner embed."""
        interaction = _create_mock_interaction(user_id=200)
        # POST calls: 1) resolve player ID, 2) accept duel
        player_resp = make_mock_response({"id": 2})
        accept_resp = make_mock_response(
            _make_accept_result(
                duel_id=1,
                is_stalemate=False,
                winner_name="Ship A",
                loser_name="Ship B",
                credits_transferred=500,
            )
        )
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, accept_resp])

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
        # POST calls: 1) resolve player ID, 2) accept duel
        player_resp = make_mock_response({"id": 2})
        accept_resp = make_mock_response(
            _make_accept_result(
                duel_id=1,
                is_stalemate=True,
                winner_name="",
                loser_name="",
                credits_transferred=0,
            )
        )
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, accept_resp])

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Stalemate" in embed.title
        import discord

        assert embed.color == discord.Color.yellow()

    def test_accept_duel_not_found(self, mock_duel_cog, make_mock_response):
        """/duel-accept with non-existent duel should show not found error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        # Player resolution succeeds; duel accept returns 404
        player_resp = make_mock_response({"id": 2})
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

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
# _build_accept_embed — winner/loser player-name display (B.63)
# ---------------------------------------------------------------------------


class TestBuildAcceptEmbedPlayerNames:
    """Tests for _build_accept_embed showing player names instead of ship names (B.63)."""

    def test_winner_is_challenger_when_challenger_ttk_higher(self, mock_duel_cog):
        """When challenger TTK > target TTK, embed shows challenger_name as Winner."""
        # challenger_ttk = 1000 / 40 = 25s;  target_ttk = 800 / 50 = 16s → challenger wins
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Betty",
            loser_name="Betty",
            challenger_name="samx.ai",
            challenger_hp=1000,
            challenger_dps=50.0,
            target_name="general_failure.",
            target_hp=800,
            target_dps=40.0,
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "samx.ai" in embed.description
        assert "general_failure." in embed.description
        assert embed.description.index("samx.ai") < embed.description.index("general_failure.")

    def test_winner_is_target_when_target_ttk_higher(self, mock_duel_cog):
        """When target TTK > challenger TTK, embed shows target_name as Winner."""
        # challenger_ttk = 500 / 80 = 6.25s;  target_ttk = 1000 / 30 = 33.3s → target wins
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Betty",
            loser_name="Betty",
            challenger_name="samx.ai",
            challenger_hp=500,
            challenger_dps=30.0,
            target_name="general_failure.",
            target_hp=1000,
            target_dps=80.0,
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "general_failure." in embed.description
        assert "samx.ai" in embed.description
        assert embed.description.index("general_failure.") < embed.description.index("samx.ai")

    def test_same_ship_name_different_players_shows_player_names(self, mock_duel_cog):
        """When both players fly the same ship, player names appear instead of duplicated ship name."""
        # Both fly "Betty" — old code would show "Winner: Betty / Loser: Betty"
        # challenger_ttk = 1200 / 60 = 20s;  target_ttk = 900 / 50 = 18s → challenger wins
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Betty",
            loser_name="Betty",
            challenger_name="player_one",
            challenger_hp=1200,
            challenger_dps=50.0,
            target_name="player_two",
            target_hp=900,
            target_dps=60.0,
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        # Should NOT show raw ship names as winner/loser identifiers when player names available
        description = embed.description
        # Player names appear
        assert "player_one" in description
        assert "player_two" in description
        # Challenger (player_one) won
        assert description.index("player_one") < description.index("player_two")

    def test_fallback_to_ship_names_when_dps_zero(self, mock_duel_cog):
        """When DPS values are zero (edge case), embed falls back to ship names."""
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Eagle",
            loser_name="Wraith",
            challenger_name="samx.ai",
            challenger_hp=1000,
            challenger_dps=0.0,  # zero DPS → fallback
            target_name="general_failure.",
            target_hp=800,
            target_dps=0.0,
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "Eagle" in embed.description
        assert "Wraith" in embed.description

    def test_fallback_to_ship_names_when_stats_absent(self, mock_duel_cog):
        """When hp/dps fields are missing, embed falls back to ship names."""
        # Minimal response with no hp/dps fields (should not raise)
        data = {
            "duel_id": 1,
            "is_stalemate": False,
            "winner_name": "Eagle",
            "loser_name": "Wraith",
            "credits_transferred": 0,
            "stakes": 0,
            "challenger_id": 100,
            "target_id": 200,
        }
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "Eagle" in embed.description
        assert "Wraith" in embed.description

    def test_stalemate_embed_unchanged(self, mock_duel_cog):
        """Stalemate embed is not affected by the player-name fix."""
        data = _make_accept_result(
            is_stalemate=True,
            winner_name="",
            loser_name="",
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "Stalemate" in embed.title
        import discord

        assert embed.color == discord.Color.yellow()

    def test_player_names_used_without_stakes(self, mock_duel_cog):
        """Player names appear in Winner/Loser even for friendly (stakes=0) duels."""
        # challenger_ttk = 1000 / 50 = 20s;  target_ttk = 800 / 60 = 13.3s → challenger wins
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Betty",
            loser_name="Betty",
            challenger_name="friendly_challenger",
            challenger_hp=1000,
            challenger_dps=60.0,
            target_name="friendly_target",
            target_hp=800,
            target_dps=50.0,
            credits_transferred=0,
            stakes=0,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert "friendly_challenger" in embed.description
        assert "friendly_target" in embed.description

    def test_final_balances_field_uses_player_names_with_stakes(self, mock_duel_cog):
        """Final Balances field still shows player names with correct credit values."""
        data = _make_accept_result(
            is_stalemate=False,
            winner_name="Betty",
            loser_name="Betty",
            challenger_name="samx.ai",
            challenger_credits=1500,
            challenger_hp=1000,
            challenger_dps=50.0,
            target_name="general_failure.",
            target_credits=500,
            target_hp=800,
            target_dps=40.0,
            credits_transferred=500,
            stakes=500,
        )
        embed = mock_duel_cog._build_accept_embed(1, data)
        assert len(embed.fields) == 1
        balance_field = embed.fields[0]
        assert "samx.ai" in balance_field.value
        assert "general_failure." in balance_field.value
        assert "1,500" in balance_field.value
        assert "500" in balance_field.value


# ---------------------------------------------------------------------------
# /duel-reject command
# ---------------------------------------------------------------------------


class TestDuelRejectCommand:
    """Tests for the /duel-reject slash command."""

    def test_reject_success_shows_confirmation(self, mock_duel_cog, make_mock_response):
        """/duel-reject success should show rejection confirmation embed."""
        interaction = _create_mock_interaction(user_id=200)
        # POST calls: 1) resolve player ID, 2) reject duel
        player_resp = make_mock_response({"id": 2})
        reject_resp = make_mock_response(
            _make_mock_duel(duel_id=1, challenger_id=100, target_id=200, status="rejected")
        )
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, reject_resp])

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Reject" in embed.title or "🚫" in embed.title

    def test_reject_duel_not_found(self, mock_duel_cog, make_mock_response):
        """/duel-reject with non-existent duel should show not found error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        # Player resolution succeeds; duel reject returns 404
        player_resp = make_mock_response({"id": 2})
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

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
        # POST call: resolve player ID; GET call: fetch pending duels
        player_resp = make_mock_response({"id": 200})
        duels_resp = make_mock_response(duels)
        mock_duel_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_duel_cog.http_client.get = AsyncMock(return_value=duels_resp)
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

    def test_autocomplete_api_failure_returns_empty_list(self, mock_duel_cog, make_mock_response):
        """pending_duel_autocomplete should return empty list on API failure during duels fetch."""
        # Player resolution succeeds; duel list fetch fails
        player_resp = make_mock_response({"id": 200})
        mock_duel_cog.http_client.post = AsyncMock(return_value=player_resp)
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


# ---------------------------------------------------------------------------
# _is_guild_not_configured helper
# ---------------------------------------------------------------------------


class TestIsGuildNotConfigured:
    """Tests for the module-level _is_guild_not_configured helper."""

    def test_returns_true_for_not_configured_400(self, mock_duel_cog):
        """_is_guild_not_configured returns True for a 400 with 'not configured' detail."""
        import httpx
        from cogs.duelCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is True

    def test_returns_true_for_admin_setup_message(self, mock_duel_cog):
        """_is_guild_not_configured returns True for a 400 mentioning admin_setup."""
        import httpx
        from cogs.duelCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Run /admin_setup first"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is True

    def test_returns_false_for_non_400(self, mock_duel_cog):
        """_is_guild_not_configured returns False for non-400 errors."""
        import httpx
        from cogs.duelCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is False

    def test_returns_false_for_other_400(self, mock_duel_cog):
        """_is_guild_not_configured returns False for 400 without config message."""
        import httpx
        from cogs.duelCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Insufficient credits"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is False


# ---------------------------------------------------------------------------
# _get_player_id helper
# ---------------------------------------------------------------------------


class TestGetPlayerId:
    """Tests for the DuelCog._get_player_id helper method."""

    def test_get_player_id_success(self, mock_duel_cog, make_mock_response):
        """_get_player_id should return player ID on success."""
        resp = make_mock_response({"id": 42})
        mock_duel_cog.http_client.post = AsyncMock(return_value=resp)

        result = asyncio.run(mock_duel_cog._get_player_id(111111111, 987654321))

        assert result == 42

    def test_get_player_id_non_configured_error_reraises(self, mock_duel_cog):
        """_get_player_id should re-raise HTTPStatusError for guild-not-configured 400."""
        import httpx

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(mock_duel_cog._get_player_id(111111111, 987654321))

    def test_get_player_id_other_http_error_returns_none(self, mock_duel_cog):
        """_get_player_id should return None for non-guild-config HTTP errors."""
        import httpx

        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        result = asyncio.run(mock_duel_cog._get_player_id(111111111, 987654321))

        assert result is None

    def test_get_player_id_network_error_returns_none(self, mock_duel_cog):
        """_get_player_id should return None on network errors."""
        mock_duel_cog.http_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        result = asyncio.run(mock_duel_cog._get_player_id(111111111, 987654321))

        assert result is None


# ---------------------------------------------------------------------------
# Player resolution in /duel-challenge
# ---------------------------------------------------------------------------


class TestDuelChallengePlayerResolution:
    """Tests verifying player-ID resolution in /duel-challenge (B.51)."""

    def test_challenge_uses_player_pk_not_discord_id(self, mock_duel_cog, make_mock_response):
        """/duel-challenge should POST player PKs (not Discord snowflakes) to the duels API."""
        interaction = _create_mock_interaction(user_id=402296276617527306)  # real-looking Discord snowflake
        target = DiscordMockUtils.create_mock_user(user_id=970691862035841048, username="TargetUser")
        target.mention = "<@970691862035841048>"

        # _get_player_id returns small PKs, not snowflakes
        player_resp_challenger = make_mock_response({"id": 1})
        player_resp_target = make_mock_response({"id": 2})
        duel_resp = make_mock_response(_make_mock_duel(duel_id=5, challenger_id=1, target_id=2))

        post_responses = [player_resp_challenger, player_resp_target, duel_resp]
        mock_duel_cog.http_client.post = AsyncMock(side_effect=post_responses)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 100))

        # Verify the third POST (the actual duel creation) used player PKs not Discord snowflakes
        duel_post_call = mock_duel_cog.http_client.post.call_args_list[2]
        json_body = duel_post_call.kwargs["json"]
        assert json_body["challenger_id"] == 1, "Must use player PK, not Discord snowflake"
        assert json_body["target_id"] == 2, "Must use player PK, not Discord snowflake"
        assert json_body["challenger_id"] != 402296276617527306
        assert json_body["target_id"] != 970691862035841048

    def test_challenge_challenger_not_found_shows_error(self, mock_duel_cog, make_mock_response):
        """/duel-challenge when challenger has no player profile should show error."""
        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        # _get_player_id returns None for challenger (404 from players endpoint)
        import httpx

        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "profile" in msg.lower() or "register" in msg.lower()

    def test_challenge_target_not_found_shows_error(self, mock_duel_cog, make_mock_response):
        """/duel-challenge when target has no player profile should show error."""
        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        challenger_resp = make_mock_response({"id": 1})

        import httpx

        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)

        # First call (challenger) succeeds, second call (target) returns None
        target_resp = MagicMock()
        target_resp.raise_for_status = MagicMock(side_effect=http_error)
        target_resp.status_code = 404

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return challenger_resp
            raise http_error

        mock_duel_cog.http_client.post = AsyncMock(side_effect=side_effect)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "target" in msg.lower() or "profile" in msg.lower()

    def test_challenge_guild_not_configured_shows_setup_message(self, mock_duel_cog):
        """/duel-challenge when guild not configured should show setup message."""
        import httpx

        interaction = _create_mock_interaction(user_id=100)
        target = DiscordMockUtils.create_mock_user(user_id=200, username="TargetUser")

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "admin_setup" in msg.lower() or "set up" in msg.lower()


# ---------------------------------------------------------------------------
# Player resolution in /duel-accept
# ---------------------------------------------------------------------------


class TestDuelAcceptPlayerResolution:
    """Tests verifying player-ID resolution in /duel-accept (B.51)."""

    def test_accept_uses_player_pk_not_discord_id(self, mock_duel_cog, make_mock_response):
        """/duel-accept should pass player PK (not Discord snowflake) as user_id param."""
        interaction = _create_mock_interaction(user_id=402296276617527306)

        player_resp = make_mock_response({"id": 2})
        accept_resp = make_mock_response(
            _make_accept_result(duel_id=1, is_stalemate=False, winner_name="Ship A", loser_name="Ship B")
        )

        post_calls = [player_resp, accept_resp]
        mock_duel_cog.http_client.post = AsyncMock(side_effect=post_calls)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        # The second POST (accept) should pass player PK not Discord snowflake as user_id
        accept_post_call = mock_duel_cog.http_client.post.call_args_list[1]
        params = accept_post_call.kwargs.get("params", {})
        assert params["user_id"] == 2, "Must use player PK, not Discord snowflake"
        assert params["user_id"] != 402296276617527306

    def test_accept_player_not_found_shows_error(self, mock_duel_cog):
        """/duel-accept when user has no player profile should show error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "profile" in msg.lower() or "register" in msg.lower()

    def test_accept_guild_not_configured_shows_setup_message(self, mock_duel_cog):
        """/duel-accept when guild not configured should show setup message."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "admin_setup" in msg.lower() or "set up" in msg.lower()


# ---------------------------------------------------------------------------
# Player resolution in /duel-reject
# ---------------------------------------------------------------------------


class TestDuelRejectPlayerResolution:
    """Tests verifying player-ID resolution in /duel-reject (B.51)."""

    def test_reject_uses_player_pk_not_discord_id(self, mock_duel_cog, make_mock_response):
        """/duel-reject should pass player PK (not Discord snowflake) as user_id param."""
        interaction = _create_mock_interaction(user_id=402296276617527306)

        player_resp = make_mock_response({"id": 2})
        reject_resp = make_mock_response(_make_mock_duel(duel_id=1, challenger_id=1, target_id=2, status="rejected"))

        post_calls = [player_resp, reject_resp]
        mock_duel_cog.http_client.post = AsyncMock(side_effect=post_calls)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "1"))

        # The second POST (reject) should pass player PK not Discord snowflake as user_id
        reject_post_call = mock_duel_cog.http_client.post.call_args_list[1]
        params = reject_post_call.kwargs.get("params", {})
        assert params["user_id"] == 2, "Must use player PK, not Discord snowflake"
        assert params["user_id"] != 402296276617527306

    def test_reject_player_not_found_shows_error(self, mock_duel_cog):
        """/duel-reject when user has no player profile should show error."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "profile" in msg.lower() or "register" in msg.lower()

    def test_reject_guild_not_configured_shows_setup_message(self, mock_duel_cog):
        """/duel-reject when guild not configured should show setup message."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "admin_setup" in msg.lower() or "set up" in msg.lower()


# ---------------------------------------------------------------------------
# Player resolution in pending_duel_autocomplete
# ---------------------------------------------------------------------------


class TestAutocompletePlayerResolution:
    """Tests verifying player-ID resolution in pending_duel_autocomplete (B.51)."""

    def test_autocomplete_uses_player_pk_not_discord_id(self, mock_duel_cog, make_mock_response):
        """pending_duel_autocomplete should look up pending duels by player PK, not Discord ID."""
        interaction = _create_mock_interaction(user_id=402296276617527306)

        # _get_player_id resolves the Discord snowflake to player PK 2
        player_resp = make_mock_response({"id": 2})
        duels_resp = make_mock_response([_make_mock_duel(duel_id=1, challenger_id=1, target_id=2, stakes=500)])

        mock_duel_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_duel_cog.http_client.get = AsyncMock(return_value=duels_resp)

        result = asyncio.run(mock_duel_cog.pending_duel_autocomplete(interaction, ""))

        # Verify GET used player PK (2), not Discord snowflake
        get_call = mock_duel_cog.http_client.get.call_args
        params = get_call.kwargs.get("params", {})
        assert params["user_id"] == 2, "Must use player PK for autocomplete lookup"
        assert params["user_id"] != 402296276617527306
        assert len(result) == 1

    def test_autocomplete_returns_empty_when_player_not_found(self, mock_duel_cog):
        """pending_duel_autocomplete should return [] when player resolution fails."""
        import httpx

        interaction = _create_mock_interaction(user_id=200)
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.json.return_value = {"detail": "Not found"}
        http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=error_response)
        mock_duel_cog.http_client.post = AsyncMock(side_effect=http_error)

        result = asyncio.run(mock_duel_cog.pending_duel_autocomplete(interaction, ""))

        assert result == []
