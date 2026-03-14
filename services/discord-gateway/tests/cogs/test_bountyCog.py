"""Tests for bountyCog — covers /check, /bounties, /route, /criminal-loadout."""

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


def _make_check_response(result="CORRECT", bounty_id=1, message=""):
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
            "name": "Viper MkII",
            "weapons": ["Pulse Laser", "Beam Laser"],
            "modules": ["Shield Booster"],
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


@pytest.fixture
def mock_bot():
    """Mock Discord bot for bountyCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    # loop.create_task is required for the preload scheduling in __init__
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
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
        """_preload_data should set _systems to [] on API failure."""
        mock_bounty_cog.http_client.get = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

        asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems == []


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

    def test_bounty_autocomplete_returns_formatted_choices(
        self, mock_bounty_cog, make_mock_response
    ):
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

    def test_bounty_autocomplete_filters_by_current_input(
        self, mock_bounty_cog, make_mock_response
    ):
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
        mock_bounty_cog.http_client.get = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        assert result == []


# ---------------------------------------------------------------------------
# /check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Tests for the /check slash command."""

    def test_check_correct_result_green_embed(self, mock_bounty_cog, make_mock_response):
        """/check CORRECT result should display green embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_check_response("CORRECT", bounty_id=1, message="Target neutralised!")
        )
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
        resp = make_mock_response(_make_check_response("NOT_FOUND"))
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
        resp = make_mock_response(
            _make_check_response("INCORRECT", message="Bounty is 2 jumps away.")
        )
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
        resp = make_mock_response(_make_check_response("ALREADY_CHECKED"))
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
        mock_bounty_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

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
        http_error = httpx.HTTPStatusError(
            "500 Error", request=MagicMock(), response=error_response
        )
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

    def test_bounties_no_active_bounties_shows_empty_message(
        self, mock_bounty_cog, make_mock_response
    ):
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

        asyncio.run(
            mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, division="gold")
        )

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

    def test_route_displays_checked_and_unchecked_systems(
        self, mock_bounty_cog, make_mock_response
    ):
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
        http_error = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=error_response
        )
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


# ---------------------------------------------------------------------------
# /criminal-loadout command
# ---------------------------------------------------------------------------


class TestCriminalLoadoutCommand:
    """Tests for the /criminal-loadout slash command."""

    def test_criminal_loadout_displays_ship_weapons_modules(
        self, mock_bounty_cog, make_mock_response
    ):
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

        asyncio.run(
            mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "not-a-number")
        )

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
        http_error = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=error_response
        )
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


# ---------------------------------------------------------------------------
# Division autocomplete
# ---------------------------------------------------------------------------


class TestDivisionAutocomplete:
    """Tests for division_autocomplete."""

    def test_autocomplete_empty_current_returns_all_divisions(self, mock_bounty_cog):
        """division_autocomplete with empty string should return all 3 divisions."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_bounty_cog.division_autocomplete(interaction, ""))
        assert len(result) == 3
        names = [c.name for c in result]
        assert "Bronze" in names
        assert "Silver" in names
        assert "Gold" in names

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
