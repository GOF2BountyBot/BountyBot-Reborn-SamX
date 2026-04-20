"""Tests for bountyCog — covers /check, /bounties, /route, /criminal-loadout."""

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
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.jpg"
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_bounty_public(
    bounty_id=1,
    criminal_name="BlackViper",
    division="bronze",
    reward=5000,
    reward_per_sys=500,
    route=None,
    checked=None,
    status="active",
):
    """Return a minimal BountyPublicResponse dict."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma"]
    if checked is None:
        checked = {}
    return {
        "id": bounty_id,
        "guild_id": 987654321,
        "division": division,
        "criminal_name": criminal_name,
        "criminal_faction": "Outlaws",
        "route": route,
        "reward": reward,
        "reward_per_sys": reward_per_sys,
        "checked": checked,
        "issue_time": "2026-03-14T10:00:00",
        "end_time": "2026-03-15T10:00:00",
        "tech_level": 2,
        "status": status,
    }


def _make_check_response(result="correct", bounty_id=1, message=""):
    """Return a minimal BountyCheckResponse dict."""
    return {
        "result": result,
        "bounty_id": bounty_id,
        "message": message,
    }


def _make_route_response(
    bounty_id=1,
    criminal_name="BlackViper",
    route=None,
    checked=None,
    status="active",
):
    """Return a minimal route response dict."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma"]
    if checked is None:
        checked = {}
    return {
        "bounty_id": bounty_id,
        "criminal_name": criminal_name,
        "route": route,
        "checked": checked,
        "status": status,
    }


def _make_loadout_response(
    bounty_id=1,
    criminal_name="BlackViper",
    tech_level=2,
    criminal_ship=None,
):
    """Return a minimal loadout response dict."""
    if criminal_ship is None:
        criminal_ship = {
            "ship_name": "Viper MkII",
            "ship_emoji": "",
            "ship_armour": 150,
            "armor_hp": 310,
            "shield_hp": 380,
            "total_hp": 690,
            "weapons": [
                {"name": "Pulse Laser", "emoji": "", "dps": 10},
                {"name": "Beam Laser", "emoji": "", "dps": 15},
            ],
            "modules": [
                {
                    "name": "D'iol Armour",
                    "emoji": "",
                    "type": "ArmourModule",
                    "extra_atts": {"armour": 160},
                },
                {
                    "name": "Particle Shield",
                    "emoji": "",
                    "type": "ShieldModule",
                    "extra_atts": {"shield": 380},
                },
            ],
            "turrets": [],
        }
    return {
        "bounty_id": bounty_id,
        "criminal_name": criminal_name,
        "criminal_ship": criminal_ship,
        "tech_level": tech_level,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _close_coro(coro):
    """Close coroutine to prevent 'never awaited' warning."""
    coro.close()
    return MagicMock()


@pytest.fixture
def mock_bot():
    """Mock Discord bot for bountyCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    # loop.create_task is required for the preload scheduling in __init__
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture
def mock_bounty_cog(mock_bot):
    """Create a BountyCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.bountyCog import BountyCog

    cog = BountyCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestBountyCogInitialization:
    """Tests for BountyCog initialization."""

    def test_initialization(self, mock_bounty_cog, mock_bot):
        """BountyCog should store bot reference and create http_client."""
        assert mock_bounty_cog.bot is mock_bot
        assert mock_bounty_cog.http_client is not None

    def test_initialization_has_systems_list(self, mock_bounty_cog):
        """BountyCog should initialize with an empty _systems list."""
        assert hasattr(mock_bounty_cog, "_systems")
        assert isinstance(mock_bounty_cog._systems, list)

    def test_cog_unload_closes_http_client(self, mock_bounty_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_bounty_cog.cog_unload())
        mock_bounty_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Preload
# ---------------------------------------------------------------------------


class TestPreloadData:
    """Tests for _preload_data method."""

    def test_preload_data_populates_systems(self, mock_bounty_cog, make_mock_response):
        """_preload_data should populate _systems list from API response."""
        systems_data = [
            {"name": "Sol", "id": 1},
            {"name": "Alpha Centauri", "id": 2},
            {"name": "Proxima", "id": 3},
        ]
        resp = make_mock_response(systems_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems == ["Sol", "Alpha Centauri", "Proxima"]

    def test_preload_data_handles_api_failure_gracefully(self, mock_bounty_cog):
        """_preload_data should set _systems to [] after all retries exhausted."""
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))

        # Patch asyncio.sleep so retries don't actually wait
        with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems == []
        # Should have slept 5 times (once per retry attempt)
        assert mock_sleep.call_count == 5

    def test_preload_data_retries_on_timeout(self, mock_bounty_cog, make_mock_response):
        """_preload_data should retry on TimeoutException and succeed on 2nd attempt."""
        import httpx

        systems_data = [{"name": "Sol", "id": 1}]
        success_resp = make_mock_response(systems_data)

        timeout_exc = httpx.TimeoutException("timeout")
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=[timeout_exc, success_resp])

        with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems == ["Sol"]
        # Should have slept once after the first failure
        assert mock_sleep.call_count == 1

    def test_preload_data_retries_correct_delays(self, mock_bounty_cog):
        """_preload_data should use exponential backoff delays [5, 10, 20, 40, 60]."""
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("error"))

        with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            asyncio.run(mock_bounty_cog._preload_data())

        expected_delays = [5, 10, 20, 40, 60]
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == expected_delays

    def test_preload_data_logs_warning_on_retry(self, mock_bounty_cog):
        """_preload_data should log a warning on each failed attempt."""
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()):
            asyncio.run(mock_bounty_cog._preload_data())

        # Should have logged a warning for each attempt and an error at the end
        assert _module_logger.warning.call_count == 5
        assert _module_logger.error.call_count >= 1

    def test_preload_data_returns_immediately_on_success(self, mock_bounty_cog, make_mock_response):
        """_preload_data should return after first successful attempt, not retry."""
        systems_data = [{"name": "Sol", "id": 1}]
        resp = make_mock_response(systems_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems == ["Sol"]
        # No sleep should occur on first-attempt success
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# System autocomplete
# ---------------------------------------------------------------------------


class TestSystemAutocomplete:
    """Tests for system_autocomplete method."""

    def test_system_autocomplete_returns_matching_systems(self, mock_bounty_cog):
        """system_autocomplete should return systems matching current input."""
        mock_bounty_cog._systems = ["Sol", "Alpha Centauri", "Proxima", "Sirius"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, "sol"))

        assert len(result) == 1
        assert result[0].name == "Sol"
        assert result[0].value == "Sol"

    def test_system_autocomplete_empty_input_returns_all(self, mock_bounty_cog):
        """system_autocomplete with empty input should return all systems (up to 25)."""
        mock_bounty_cog._systems = ["Sol", "Alpha Centauri", "Proxima"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, ""))

        assert len(result) == 3

    def test_system_autocomplete_max_25_results(self, mock_bounty_cog):
        """system_autocomplete should cap results at 25."""
        mock_bounty_cog._systems = [f"System{i}" for i in range(50)]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, ""))

        assert len(result) == 25

    def test_system_autocomplete_empty_systems_returns_empty(self, mock_bounty_cog):
        """system_autocomplete with empty _systems list should return empty list."""
        mock_bounty_cog._systems = []
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, "Sol"))

        assert result == []


# ---------------------------------------------------------------------------
# Bounty autocomplete
# ---------------------------------------------------------------------------


class TestBountyAutocomplete:
    """Tests for bounty_autocomplete method."""

    def test_bounty_autocomplete_returns_formatted_choices(self, mock_bounty_cog, make_mock_response):
        """bounty_autocomplete should return formatted bounty choices."""
        bounties = [
            _make_bounty_public(1, "Falcon-Jones", "gold", reward=5000, reward_per_sys=500),
        ]
        resp = make_mock_response(bounties)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        assert len(result) == 1
        assert result[0].value == "1"
        assert "Falcon-Jones" in result[0].name
        assert "Gold" in result[0].name
        assert "5,000cr" in result[0].name

    def test_bounty_autocomplete_filters_by_current_input(self, mock_bounty_cog, make_mock_response):
        """bounty_autocomplete should filter choices by current input."""
        bounties = [
            _make_bounty_public(1, "BlackViper", "bronze", reward=1000),
            _make_bounty_public(2, "RedFang", "silver", reward=2000),
        ]
        resp = make_mock_response(bounties)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, "black"))

        assert len(result) == 1
        assert "BlackViper" in result[0].name

    def test_bounty_autocomplete_handles_api_failure(self, mock_bounty_cog):
        """bounty_autocomplete should return empty list on API failure."""
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        assert result == []


# ---------------------------------------------------------------------------
# /check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Tests for the /check slash command."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID for all /check tests."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    def test_check_correct_result_green_embed(self, mock_bounty_cog, make_mock_response):
        """/check CORRECT result should display green embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("correct", bounty_id=1, message="Target neutralised!"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # Green color for CORRECT
        import discord

        assert embed.color == discord.Color.green()

    def test_check_not_found_result_orange_embed(self, mock_bounty_cog, make_mock_response):
        """/check NOT_FOUND result should display orange embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("not_found"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Delta"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        import discord

        assert call_kwargs["embed"].color == discord.Color.orange()

    def test_check_incorrect_result_red_embed(self, mock_bounty_cog, make_mock_response):
        """/check INCORRECT result should display red embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("incorrect", message="Bounty is 2 jumps away."))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Beta"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        import discord

        assert call_kwargs["embed"].color == discord.Color.red()

    def test_check_already_checked_result_yellow_embed(self, mock_bounty_cog, make_mock_response):
        """/check ALREADY_CHECKED result should display yellow embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("already_checked"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        import discord

        assert call_kwargs["embed"].color == discord.Color.yellow()

    def test_check_cooldown_429_response(self, mock_bounty_cog, make_mock_response):
        """/check 429 response should show cooldown message."""
        interaction = _create_mock_interaction()
        resp = make_mock_response({}, status_code=429)
        resp.status_code = 429
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "cooldown" in call_kwargs[0][0].lower()

    def test_check_api_error_handled_gracefully(self, mock_bounty_cog):
        """/check generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_check_http_status_error_handled(self, mock_bounty_cog):
        """/check HTTPStatusError (non-429) should show API error."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Error", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "API Error" in call_kwargs[0][0]


# ---------------------------------------------------------------------------
# /bounties command
# ---------------------------------------------------------------------------


class TestBountiesCommand:
    """Tests for the /bounties slash command."""

    def test_bounties_lists_active_bounties(self, mock_bounty_cog, make_mock_response):
        """/bounties should list active bounties in an embed."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "BlackViper", "bronze"),
            _make_bounty_public(2, "RedFang", "silver"),
        ]
        resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_bounties_no_active_bounties_shows_empty_message(self, mock_bounty_cog, make_mock_response):
        """/bounties with no bounties should show 'No active bounties'."""
        interaction = _create_mock_interaction()
        resp = make_mock_response([])
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "no active bounties" in embed.description.lower()

    def test_bounties_with_division_filter(self, mock_bounty_cog, make_mock_response):
        """/bounties with division filter should pass division param to API."""
        interaction = _create_mock_interaction()
        bounty_list = [_make_bounty_public(1, "GoldHawk", "gold")]
        resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, division="gold"))

        call_kwargs = mock_bounty_cog.http_client.get.call_args[1]
        assert call_kwargs["params"].get("division") == "gold"
        interaction.followup.send.assert_awaited_once()

    def test_bounties_api_error_handled(self, mock_bounty_cog):
        """/bounties generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# /route command
# ---------------------------------------------------------------------------


class TestRouteCommand:
    """Tests for the /route slash command."""

    def test_route_displays_checked_and_unchecked_systems(self, mock_bounty_cog, make_mock_response):
        """/route should show strikethrough for checked systems."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_route_response(
                route=["Alpha", "Beta", "Gamma"],
                checked={"Alpha": 1},
            )
        )
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # The field value should contain strikethrough for Alpha
        field_value = embed.fields[0].value
        assert "~~Alpha~~" in field_value
        assert "Beta" in field_value
        assert "Gamma" in field_value

    def test_route_invalid_bounty_string_shows_error(self, mock_bounty_cog):
        """/route with non-numeric bounty string should show error message."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()

    def test_route_404_shows_bounty_not_found(self, mock_bounty_cog):
        """/route 404 should send bounty not found message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_route_api_error_handled(self, mock_bounty_cog):
        """/route generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_route_shows_division_in_description(self, mock_bounty_cog, make_mock_response):
        """/route should show the bounty's division (tier) in the embed description."""
        interaction = _create_mock_interaction()
        route_data = _make_route_response(route=["Alpha", "Beta"])
        route_data["division"] = "gold"
        resp = make_mock_response(route_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Gold" in embed.description

    def test_route_no_division_in_description_when_not_present(self, mock_bounty_cog, make_mock_response):
        """/route description should not include tier when division is absent."""
        interaction = _create_mock_interaction()
        route_data = _make_route_response(route=["A", "B"])
        # No division key in response
        resp = make_mock_response(route_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Tier:" not in embed.description


# ---------------------------------------------------------------------------
# /criminal-loadout command
# ---------------------------------------------------------------------------


class TestCriminalLoadoutCommand:
    """Tests for the /criminal-loadout slash command."""

    def test_criminal_loadout_displays_ship_weapons_modules(self, mock_bounty_cog, make_mock_response):
        """/criminal-loadout should display ship name, weapons, and modules."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # Ship name should be in the embed
        field_names = [f.name for f in embed.fields]
        assert any("Ship" in name for name in field_names)

    def test_criminal_loadout_invalid_bounty_string_shows_error(self, mock_bounty_cog):
        """/criminal-loadout with non-numeric bounty string should show error."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()

    def test_criminal_loadout_404_shows_not_found(self, mock_bounty_cog):
        """/criminal-loadout 404 should send bounty not found message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_criminal_loadout_api_error_handled(self, mock_bounty_cog):
        """/criminal-loadout generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_criminal_loadout_displays_armor_and_shield_hp(self, mock_bounty_cog, make_mock_response):
        """/criminal-loadout should show Armor HP, Shield HP, and Total HP when shield present."""
        interaction = _create_mock_interaction()
        criminal_ship = {
            "ship_name": "Viper MkII",
            "ship_emoji": "",
            "ship_armour": 150,
            "armor_hp": 310,
            "shield_hp": 380,
            "total_hp": 690,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        resp = make_mock_response(_make_loadout_response(criminal_ship=criminal_ship))
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # Ship field should contain Armor HP, Shield HP, Total HP
        ship_field = next(f for f in embed.fields if "Ship" in f.name)
        assert "310" in ship_field.value  # armor_hp
        assert "380" in ship_field.value  # shield_hp
        assert "690" in ship_field.value  # total_hp

    def test_criminal_loadout_displays_base_hp_when_no_shield(self, mock_bounty_cog, make_mock_response):
        """/criminal-loadout should show just HP when shield_hp is 0."""
        interaction = _create_mock_interaction()
        criminal_ship = {
            "ship_name": "Betty",
            "ship_emoji": "",
            "ship_armour": 120,
            "armor_hp": 280,
            "shield_hp": 0,
            "total_hp": 280,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        resp = make_mock_response(_make_loadout_response(criminal_ship=criminal_ship))
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        ship_field = next(f for f in embed.fields if "Ship" in f.name)
        assert "280" in ship_field.value
        # Should NOT say "Shield HP" or "Total HP" when shield is 0
        assert "Shield HP" not in ship_field.value

    def test_criminal_loadout_falls_back_to_ship_armour_if_no_hp_fields(self, mock_bounty_cog, make_mock_response):
        """/criminal-loadout with legacy loadout (no armor_hp) falls back to ship_armour."""
        interaction = _create_mock_interaction()
        criminal_ship = {
            "ship_name": "OldShip",
            "ship_emoji": "",
            "ship_armour": 250,
            # No armor_hp / shield_hp / total_hp keys
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        resp = make_mock_response(_make_loadout_response(criminal_ship=criminal_ship))
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        ship_field = next(f for f in embed.fields if "Ship" in f.name)
        assert "250" in ship_field.value


# ---------------------------------------------------------------------------
# Division autocomplete
# ---------------------------------------------------------------------------


class TestDivisionAutocomplete:
    """Tests for division_autocomplete."""

    def test_autocomplete_empty_current_returns_all_divisions(self, mock_bounty_cog):
        """division_autocomplete with empty string should return all 4 divisions."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_bounty_cog.division_autocomplete(interaction, ""))
        assert len(result) == 4
        names = [c.name for c in result]
        assert "Bronze" in names
        assert "Silver" in names
        assert "Gold" in names
        assert "Platinum" in names

    def test_autocomplete_partial_match_filters(self, mock_bounty_cog):
        """division_autocomplete with partial string should filter."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_bounty_cog.division_autocomplete(interaction, "bro"))
        assert len(result) == 1
        assert result[0].value == "bronze"


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_check_error_handler_response_not_done(self, mock_bounty_cog):
        """check_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.check_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_bounties_error_handler_response_not_done(self, mock_bounty_cog):
        """bounties_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.bounties_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_route_error_handler_response_not_done(self, mock_bounty_cog):
        """route_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.route_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_criminal_loadout_error_handler_response_not_done(self, mock_bounty_cog):
        """criminal_loadout_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.criminal_loadout_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_check_error_handler_response_already_done(self, mock_bounty_cog):
        """check_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.check_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ===========================================================================
# Gap 4: Discord Embed Rendering Rule Tests — BountyCog
# ===========================================================================


class TestBountiesNoTimestampsInBadLocations:
    """Gap 4: Embed rendering rule — <t:...> Discord timestamps must NOT appear
    in the embed footer or author fields for the /bounties command.
    """

    def _get_bounties_embed(self, mock_bounty_cog, make_mock_response):
        """Helper: trigger /bounties and return the sent embed."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "BlackViper", "bronze"),
        ]
        resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        return call_kwargs.get("embed")

    def test_bounties_no_timestamps_in_footer(self, mock_bounty_cog, make_mock_response):
        """The /bounties embed footer must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> timestamps in fields and descriptions but NOT in footer
        text where they appear as raw code, confusing users.
        """
        embed = self._get_bounties_embed(mock_bounty_cog, make_mock_response)
        if embed is None:
            return  # embed not sent — skip

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in /bounties embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields or description."
        )

    def test_bounties_no_timestamps_in_author(self, mock_bounty_cog, make_mock_response):
        """The /bounties embed author field must not contain a Discord timestamp (<t:...) pattern."""
        embed = self._get_bounties_embed(mock_bounty_cog, make_mock_response)
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
            f"Discord timestamp found in /bounties embed author: {author_text!r}. "
            "Timestamps in author fields render as raw text."
        )


# ===========================================================================
# _format_combat_summary — unit tests
# ===========================================================================


def _make_combat_result(
    s1_name="Betty",
    s1_raw_hp=95,
    s1_varied_hp=93,
    s1_raw_dps=5.2,
    s1_ttk=18.2,
    s2_name="Pirate Bob",
    s2_raw_hp=120,
    s2_varied_hp=118,
    s2_raw_dps=6.0,
    s2_ttk=15.8,
    is_stalemate=False,
    winner_name="Betty",
    loser_name="Pirate Bob",
):
    """Build a minimal combat_result dict."""
    return {
        "winner_name": winner_name,
        "loser_name": loser_name,
        "is_stalemate": is_stalemate,
        "ship1_stats": {
            "ship_name": s1_name,
            "raw_hp": s1_raw_hp,
            "varied_hp": s1_varied_hp,
            "raw_dps": s1_raw_dps,
            "ttk": s1_ttk,
        },
        "ship2_stats": {
            "ship_name": s2_name,
            "raw_hp": s2_raw_hp,
            "varied_hp": s2_varied_hp,
            "raw_dps": s2_raw_dps,
            "ttk": s2_ttk,
        },
        "variance_percent": 0.05,
    }


class TestFormatCombatSummary:
    """Unit tests for BountyCog._format_combat_summary()."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        """Ensure cog is imported for static method access."""
        self.cog = mock_bounty_cog

    def test_contains_player_ship_name(self):
        """Summary should include the player ship name."""
        combat = _make_combat_result(s1_name="StarFighter")
        result = self.cog._format_combat_summary(combat)
        assert "StarFighter" in result

    def test_contains_criminal_ship_name(self):
        """Summary should include the criminal ship name."""
        combat = _make_combat_result(s2_name="DeathBringer")
        result = self.cog._format_combat_summary(combat)
        assert "DeathBringer" in result

    def test_contains_player_hp_values(self):
        """Summary should include raw_hp and varied_hp for player ship."""
        combat = _make_combat_result(s1_raw_hp=200, s1_varied_hp=195)
        result = self.cog._format_combat_summary(combat)
        assert "200" in result
        assert "195" in result

    def test_contains_criminal_hp_values(self):
        """Summary should include raw_hp and varied_hp for criminal ship."""
        combat = _make_combat_result(s2_raw_hp=300, s2_varied_hp=290)
        result = self.cog._format_combat_summary(combat)
        assert "300" in result
        assert "290" in result

    def test_contains_player_dps(self):
        """Summary should include DPS for player ship formatted to 1 decimal."""
        combat = _make_combat_result(s1_raw_dps=12.5)
        result = self.cog._format_combat_summary(combat)
        assert "12.5" in result

    def test_contains_criminal_dps(self):
        """Summary should include DPS for criminal ship formatted to 1 decimal."""
        combat = _make_combat_result(s2_raw_dps=8.3)
        result = self.cog._format_combat_summary(combat)
        assert "8.3" in result

    def test_contains_player_ttk(self):
        """Summary should show time to kill for player ship."""
        combat = _make_combat_result(s1_ttk=18.2)
        result = self.cog._format_combat_summary(combat)
        assert "18.2s" in result

    def test_contains_criminal_ttk(self):
        """Summary should show time to kill for criminal ship."""
        combat = _make_combat_result(s2_ttk=15.8)
        result = self.cog._format_combat_summary(combat)
        assert "15.8s" in result

    def test_ttk_none_shown_as_infinity(self):
        """When ttk is None (can never kill), summary should show '∞'."""
        combat = _make_combat_result(s1_ttk=None, s2_ttk=None)
        result = self.cog._format_combat_summary(combat)
        assert "∞" in result

    def test_stalemate_shows_stalemate_result(self):
        """When is_stalemate is True, summary should include 'Stalemate'."""
        combat = _make_combat_result(is_stalemate=True)
        result = self.cog._format_combat_summary(combat)
        assert "Stalemate" in result

    def test_no_stalemate_text_when_not_stalemate(self):
        """When is_stalemate is False, summary should NOT include 'Stalemate'."""
        combat = _make_combat_result(is_stalemate=False)
        result = self.cog._format_combat_summary(combat)
        assert "Stalemate" not in result

    def test_empty_combat_dict_returns_string(self):
        """_format_combat_summary with an empty dict should return a string (no crash)."""
        result = self.cog._format_combat_summary({})
        assert isinstance(result, str)
        # Should show '?' for unknown ship names
        assert "?" in result

    def test_missing_ship_stats_uses_defaults(self):
        """When ship_stats dicts are missing, defaults (0, '?') should be used."""
        combat = {"is_stalemate": False}
        result = self.cog._format_combat_summary(combat)
        # HP/DPS defaults to 0
        assert "0" in result
        # Ship name defaults to '?'
        assert "?" in result

    def test_both_ships_labelled(self):
        """Summary should label 'Your Ship' and 'Criminal Ship'."""
        combat = _make_combat_result()
        result = self.cog._format_combat_summary(combat)
        assert "Your Ship" in result
        assert "Criminal Ship" in result


# ===========================================================================
# _build_check_embed — new result types
# ===========================================================================


class TestBuildCheckEmbedNewResultTypes:
    """Tests for _build_check_embed() with the new combat result types."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def _call(self, data: dict):
        """Call _build_check_embed with given data dict."""
        return self.cog._build_check_embed(data)

    # --- "captured" (Bronze) ---

    def test_captured_bonus_won_green_embed(self):
        """'captured' with bonus_won=True should produce a green embed."""
        import discord

        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "bonus_won": True,
                "combat_result": None,
            }
        )
        assert embed.color == discord.Color.green()

    def test_captured_bonus_won_shows_2x_reward(self):
        """'captured' with bonus_won=True should show total_reward with '2×' label."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "bonus_won": True,
                "combat_result": None,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,000" in reward_field.value
        assert "2×" in reward_field.value

    def test_captured_no_bonus_shows_base_reward_only(self):
        """'captured' with bonus_won=False should show base reward without 2× label."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": None,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "500" in reward_field.value
        assert "2×" not in reward_field.value

    def test_captured_with_combat_result_shows_combat_summary(self):
        """'captured' with combat_result should include a Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_captured_without_combat_result_no_combat_summary(self):
        """'captured' without combat_result should NOT include a Combat Summary field."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    def test_captured_title_says_bounty_captured(self):
        """'captured' embed title should be '🎯 Bounty Captured!'."""
        embed = self._call({"result": "captured", "criminal_name": "Pirate Bob", "reward": 500, "bonus_won": False})
        assert "Bounty Captured" in embed.title

    def test_captured_description_includes_criminal_name(self):
        """'captured' embed description should mention the criminal name."""
        embed = self._call({"result": "captured", "criminal_name": "Warlord Kane", "reward": 500, "bonus_won": False})
        assert "Warlord Kane" in embed.description

    # --- "combat_win" (Silver+) ---

    def test_combat_win_green_embed(self):
        """'combat_win' should produce a green embed."""
        import discord

        embed = self._call(
            {
                "result": "combat_win",
                "division": "silver",
                "criminal_name": "Pirate Bob",
                "reward": 2000,
                "combat_result": None,
            }
        )
        assert embed.color == discord.Color.green()

    def test_combat_win_title(self):
        """'combat_win' embed title should say 'Combat Victory!'."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        assert "Combat Victory" in embed.title

    def test_combat_win_shows_reward(self):
        """'combat_win' should show the reward amount."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "2,000" in reward_field.value

    def test_combat_win_description_includes_criminal_name(self):
        """'combat_win' description should mention the criminal name."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Shadow Wing", "reward": 3000, "combat_result": None}
        )
        assert "Shadow Wing" in embed.description

    def test_combat_win_with_combat_result_shows_summary(self):
        """'combat_win' with combat_result should include Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": combat}
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_combat_win_without_combat_result_no_summary(self):
        """'combat_win' without combat_result should NOT include Combat Summary field."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- "combat_loss" (Silver+) ---

    def test_combat_loss_dark_red_embed(self):
        """'combat_loss' should produce a dark_red embed."""
        import discord

        embed = self._call(
            {
                "result": "combat_loss",
                "division": "silver",
                "criminal_name": "Pirate Bob",
                "combat_result": None,
            }
        )
        assert embed.color == discord.Color.dark_red()

    def test_combat_loss_title(self):
        """'combat_loss' embed title should say 'Combat Defeat!'."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        assert "Combat Defeat" in embed.title

    def test_combat_loss_description_includes_criminal_name(self):
        """'combat_loss' description should mention the criminal and note reset."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Iron Fist", "combat_result": None})
        assert "Iron Fist" in embed.description
        assert "reset" in embed.description.lower()

    def test_combat_loss_with_combat_result_shows_summary(self):
        """'combat_loss' with combat_result should include Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": combat})
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_combat_loss_without_combat_result_no_summary(self):
        """'combat_loss' without combat_result should NOT include Combat Summary field."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    def test_combat_loss_no_reward_field(self):
        """'combat_loss' should NOT include a Reward field (player earned nothing)."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        field_names = [f.name for f in embed.fields]
        assert not any("Reward" in n for n in field_names)

    # --- legacy result types still work ---

    def test_existing_correct_result_still_works(self):
        """Legacy 'correct' result should still return a green embed."""
        import discord

        embed = self._call({"result": "correct", "system_name": "Sol", "message": "Found!"})
        assert embed.color == discord.Color.green()

    def test_existing_incorrect_result_still_works(self):
        """Legacy 'incorrect' result should still return a red embed."""
        import discord

        embed = self._call({"result": "incorrect", "system_name": "Sol", "message": "Not here."})
        assert embed.color == discord.Color.red()

    def test_existing_already_checked_result_still_works(self):
        """Legacy 'already_checked' result should still return a yellow embed."""
        import discord

        embed = self._call({"result": "already_checked", "system_name": "Sol", "message": "Already done."})
        assert embed.color == discord.Color.yellow()

    def test_existing_unknown_result_still_works(self):
        """Unknown result type should fall back to orange embed."""
        import discord

        embed = self._call({"result": "some_unknown_result", "system_name": "Sol", "message": ""})
        assert embed.color == discord.Color.orange()


# ===========================================================================
# /check command — new combat result types end-to-end
# ===========================================================================


class TestCheckCommandCombatResults:
    """Tests for /check command with new combat result types."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    def _make_full_check_response(self, **kwargs):
        """Build a complete check response dict with sensible defaults."""
        base = {
            "result": "captured",
            "division": "bronze",
            "criminal_name": "Pirate Bob",
            "reward": 500,
            "total_reward": 500,
            "bonus_won": False,
            "combat_result": None,
            "message": "",
        }
        base.update(kwargs)
        return base

    def test_check_captured_bonus_won_sends_green_embed(self, mock_bounty_cog, make_mock_response):
        """/check with result='captured' and bonus_won=True sends green embed."""
        import discord

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            self._make_full_check_response(
                result="captured",
                bonus_won=True,
                total_reward=1000,
                reward=500,
            )
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color == discord.Color.green()


# ===========================================================================
# Tests: _build_check_embed — result="correct" with combat_won field
# (The primary fix: bronze/silver/gold/platinum captures all return result="correct")
# ===========================================================================


class TestBuildCheckEmbedCorrectResultWithCombatWon:
    """Tests for _build_check_embed() with result='correct' and combat_won field.

    Bot-core always returns result='correct' for a successful system check.
    The combat_won field distinguishes:
    - combat_won=True (or None): capture succeeded
    - combat_won=False: player lost, criminal escaped
    """

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def _call(self, data: dict):
        """Call _build_check_embed with given data dict."""
        return self.cog._build_check_embed(data)

    # --- combat_won=True: Successful capture ---

    def test_correct_combat_won_true_green_embed(self):
        """result='correct' + combat_won=True → green embed (capture succeeded)."""
        import discord

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
            }
        )
        assert embed.color == discord.Color.green()

    def test_correct_combat_won_true_title_says_bounty_captured(self):
        """result='correct' + combat_won=True → title contains 'Bounty Captured'."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
            }
        )
        assert "Bounty Captured" in embed.title

    def test_correct_combat_won_true_description_includes_criminal_name(self):
        """result='correct' + combat_won=True → description mentions criminal."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "reward": 2000,
                "combat_won": True,
            }
        )
        assert "Iron Fist" in embed.description

    def test_correct_combat_won_true_shows_base_reward(self):
        """result='correct' + combat_won=True, no bonus → shows base reward only."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1500,
                "combat_won": True,
                "bonus_won": False,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,500" in reward_field.value
        assert "2×" not in reward_field.value

    def test_correct_combat_won_true_with_bonus_shows_doubled_reward(self):
        """result='correct' + combat_won=True + bonus_won=True → shows total_reward with 2× label."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "combat_won": True,
                "bonus_won": True,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,000" in reward_field.value
        assert "2×" in reward_field.value

    def test_correct_combat_won_true_with_combat_result_shows_summary(self):
        """result='correct' + combat_won=True + combat_result → shows Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_correct_combat_won_true_without_combat_result_no_summary(self):
        """result='correct' + combat_won=True, no combat_result → no Combat Summary field."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- combat_won=False: Criminal escaped ---

    def test_correct_combat_won_false_dark_red_embed(self):
        """result='correct' + combat_won=False → dark_red embed (player lost)."""
        import discord

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert embed.color == discord.Color.dark_red()

    def test_correct_combat_won_false_title_says_combat_defeat(self):
        """result='correct' + combat_won=False → title contains 'Combat Defeat'."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert "Combat Defeat" in embed.title

    def test_correct_combat_won_false_description_includes_criminal_name(self):
        """result='correct' + combat_won=False → description mentions criminal."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Shadow Wing",
                "combat_won": False,
            }
        )
        assert "Shadow Wing" in embed.description

    def test_correct_combat_won_false_description_mentions_reset(self):
        """result='correct' + combat_won=False → description mentions checks reset."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert "reset" in embed.description.lower()

    def test_correct_combat_won_false_no_reward_field(self):
        """result='correct' + combat_won=False → no Reward field shown."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Reward" in n for n in field_names)

    def test_correct_combat_won_false_with_combat_result_shows_summary(self):
        """result='correct' + combat_won=False + combat_result → shows Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_correct_combat_won_false_without_combat_result_no_summary(self):
        """result='correct' + combat_won=False, no combat_result → no Combat Summary."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- combat_won=None (missing): No combat → treat as successful capture ---

    def test_correct_no_combat_won_field_shows_green_embed(self):
        """result='correct' without combat_won → defaults to capture (green embed)."""
        import discord

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
            }
        )
        assert embed.color == discord.Color.green()

    def test_correct_no_combat_won_shows_reward_field(self):
        """result='correct' without combat_won → shows Reward field."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Reward" in n for n in field_names)


# ===========================================================================
# Tests: autocomplete functions with normalize_for_search
# ===========================================================================


class TestBountyCogAutocompleteNormalization:
    """Tests for autocomplete functions using normalize_for_search."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def test_division_autocomplete_matches_ascii_input(self):
        """division_autocomplete should match ASCII input as before."""
        choices = asyncio.run(self.cog.division_autocomplete(MagicMock(), "bron"))
        assert any(c.value == "bronze" for c in choices)

    def test_system_autocomplete_matches_accented_name(self):
        """system_autocomplete should match unaccented input against accented system names."""
        self.cog._systems = ["Behén", "N'saan", "Alpha Centauri"]
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "behen"))
        assert any(c.name == "Behén" for c in choices)

    def test_system_autocomplete_preserves_original_name_in_choices(self):
        """system_autocomplete should preserve accented names in Choice.name."""
        self.cog._systems = ["Behén", "Normal"]
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "behen"))
        # Choice.name should be original with accent; value should also be original
        assert all(c.name == c.value for c in choices)
        matching = [c for c in choices if c.name == "Behén"]
        assert len(matching) == 1

    def test_division_autocomplete_empty_query_returns_all(self):
        """division_autocomplete with empty query should return all divisions."""
        choices = asyncio.run(self.cog.division_autocomplete(MagicMock(), ""))
        assert len(choices) == 4

    def test_system_autocomplete_no_match_returns_empty(self):
        """system_autocomplete with unmatched query returns empty list."""
        self.cog._systems = ["Alpha", "Beta"]
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "zzzzz"))
        assert choices == []


# ===========================================================================
# Tests: /check — cooldown_until timestamp and recently_spotted
# ===========================================================================


class TestCheckCommandCooldownAndRecentlySpotted:
    """Tests for cooldown_until and recently_spotted handling in /check."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    def _make_on_cooldown_response(self, cooldown_until=None, message="On cooldown"):
        return {
            "result": "on_cooldown",
            "message": message,
            "cooldown_until": cooldown_until,
        }

    def test_on_cooldown_with_cooldown_until_uses_discord_timestamp(self, mock_bounty_cog, make_mock_response):
        """When result=on_cooldown and cooldown_until is set, message uses <t:X:R> format."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(self._make_on_cooldown_response(cooldown_until=1700000000))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "<t:1700000000:R>" in sent_msg
        assert "check again" in sent_msg.lower()

    def test_on_cooldown_without_cooldown_until_falls_back_to_message(self, mock_bounty_cog, make_mock_response):
        """When result=on_cooldown with no cooldown_until, fallback to the message string."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            self._make_on_cooldown_response(cooldown_until=None, message="On cooldown for 60 more seconds")
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "60 more seconds" in sent_msg

    def test_recently_spotted_incorrect_shows_orange_embed(self, mock_bounty_cog, make_mock_response):
        """When result=incorrect and recently_spotted=True, embed is orange with 'Recently Spotted' title."""
        import discord

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "Recently spotted here!",
                "recently_spotted": True,
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color == discord.Color.orange()
        assert "Recently Spotted" in embed.title

    def test_recently_spotted_false_shows_red_embed(self, mock_bounty_cog, make_mock_response):
        """When result=incorrect and recently_spotted=False, embed is red (standard)."""
        import discord

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "No sign of criminal",
                "recently_spotted": False,
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color == discord.Color.red()

    def test_recently_spotted_missing_defaults_to_false(self, mock_bounty_cog, make_mock_response):
        """When recently_spotted key is absent, embed defaults to red (not recently spotted)."""
        import discord

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "No sign of criminal",
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color == discord.Color.red()
