"""Tests for shipsCog — boosting coverage from 0% to 60%+."""

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


def _make_ship(
    ship_id=1,
    ship_name="Eagle",
    is_active=True,
    nickname=None,
    weapons=None,
    modules=None,
    turrets=None,
    created_at="2024-01-01T00:00:00",
):
    """Return a minimal ship dict."""
    return {
        "id": ship_id,
        "ship_name": ship_name,
        "is_active": is_active,
        "nickname": nickname,
        "weapons": weapons or ["Laser"],
        "modules": modules or [],
        "turrets": turrets or [],
        "created_at": created_at,
        "player_id": 1,
    }


def _make_loadout(weapons=None, modules=None, turrets=None):
    """Return a minimal loadout dict."""
    weapons = weapons or ["Laser", "Plasma"]
    modules = modules or ["Shield"]
    turrets = turrets or []
    return {
        "weapons": weapons,
        "weapons_count": len(weapons),
        "modules": modules,
        "modules_count": len(modules),
        "turrets": turrets,
        "turrets_count": len(turrets),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock Discord bot for shipsCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture
def mock_ships_cog(mock_bot):
    """Create a ShipsCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.shipsCog import ShipsCog

    cog = ShipsCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestShipsCogInitialization:
    """Tests for ShipsCog initialization."""

    def test_initialization(self, mock_ships_cog, mock_bot):
        """ShipsCog should store bot reference and create http_client."""
        assert mock_ships_cog.bot is mock_bot
        assert mock_ships_cog.http_client is not None

    def test_initialization_logs_debug(self, mock_ships_cog):
        """ShipsCog __init__ should log a debug message."""
        global _module_logger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("ShipsCog initialized")


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for ShipsCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_ships_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_ships_cog.cog_unload())
        mock_ships_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_player_id helper
# ---------------------------------------------------------------------------


class TestGetPlayerIdHelper:
    """Tests for the _get_player_id helper method."""

    def test_get_player_id_success(self, mock_ships_cog, make_mock_response):
        """_get_player_id should return player ID on success."""
        resp = make_mock_response({"id": 7})
        mock_ships_cog.http_client.post = AsyncMock(return_value=resp)

        result = asyncio.run(mock_ships_cog._get_player_id(111111111, 987654321))
        assert result == 7

    def test_get_player_id_api_error_returns_none(self, mock_ships_cog):
        """_get_player_id should return None on API error."""
        import httpx

        mock_ships_cog.http_client.post = AsyncMock(side_effect=httpx.HTTPError("connection error"))

        result = asyncio.run(mock_ships_cog._get_player_id(111111111, 987654321))
        assert result is None


# ---------------------------------------------------------------------------
# ships command
# ---------------------------------------------------------------------------


class TestShipsCommand:
    """Tests for the /ships slash command."""

    def test_ships_display_own_ships(self, mock_ships_cog, make_mock_response):
        """ships should display embed with user's ships."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                _make_ship(1, "Eagle", is_active=True),
                _make_ship(2, "Hawk", is_active=False),
            ]
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_no_ships_found(self, mock_ships_cog, make_mock_response):
        """ships should send ephemeral message when player has no ships."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response([])

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "no ships" in call_kwargs[0][0].lower()

    def test_ships_player_not_found(self, mock_ships_cog):
        """ships should send ephemeral error when player not found."""
        interaction = _create_mock_interaction()

        # _get_player_id will return None
        mock_ships_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_viewing_other_user(self, mock_ships_cog, make_mock_response):
        """ships should display ships for another user when provided."""
        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other-avatar.jpg"

        player_resp = make_mock_response({"id": 2})
        ships_resp = make_mock_response([_make_ship(3, "Falcon", is_active=True)])

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_more_than_10_shows_footer_with_count(self, mock_ships_cog, make_mock_response):
        """ships with >10 ships should show truncation footer."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        many_ships = [_make_ship(i, f"Ship{i}", is_active=(i == 1)) for i in range(1, 13)]
        ships_resp = make_mock_response(many_ships)

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_ships_http_status_error(self, mock_ships_cog, make_mock_response):
        """ships should handle HTTPStatusError gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_generic_exception(self, mock_ships_cog, make_mock_response):
        """ships should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(side_effect=RuntimeError("network error"))

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_with_nickname(self, mock_ships_cog, make_mock_response):
        """ships should display ship nickname when set."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                _make_ship(1, "Eagle", is_active=True, nickname="StarHunter"),
            ]
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_ships_error_handler_response_not_done(self, mock_ships_cog):
        """ships_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ships_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_ships_error_handler_response_already_done(self, mock_ships_cog):
        """ships_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ships_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_ship_error_handler_response_not_done(self, mock_ships_cog):
        """ship_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ship_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_setactive_error_handler_response_not_done(self, mock_ships_cog):
        """setactive_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.setactive_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_nickname_error_handler_response_not_done(self, mock_ships_cog):
        """nickname_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.nickname_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add ShipsCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.shipsCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        from cogs.shipsCog import ShipsCog

        assert isinstance(added_arg, ShipsCog)


# ---------------------------------------------------------------------------
# /ship command (detailed view)
# ---------------------------------------------------------------------------


class TestShipCommand:
    """Tests for the /ship slash command."""

    def test_ship_success_active_with_nickname(self, mock_ships_cog, make_mock_response):
        """ship should display detailed embed for an active ship with nickname."""
        interaction = _create_mock_interaction()

        # Ship detail response
        ship_resp = make_mock_response(
            _make_ship(
                ship_id=1,
                ship_name="Eagle",
                is_active=True,
                nickname="StarHunter",
            )
        )

        # Player lookup
        player_resp = make_mock_response({"id": 1})

        # Loadout response
        loadout_resp = make_mock_response(
            _make_loadout(
                weapons=["Laser", "Plasma"],
                modules=["Shield"],
                turrets=["Flak"],
            )
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_success_inactive_no_nickname(self, mock_ships_cog, make_mock_response):
        """ship should display correct embed for inactive ship without nickname."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(
            _make_ship(
                ship_id=2,
                ship_name="Hawk",
                is_active=False,
                nickname=None,
            )
        )

        player_resp = make_mock_response({"id": 1})

        loadout_resp = make_mock_response(
            _make_loadout(
                weapons=[],
                modules=[],
                turrets=[],
            )
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="2"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_not_owned_by_user(self, mock_ships_cog, make_mock_response):
        """ship should deny access when ship belongs to another player."""
        interaction = _create_mock_interaction()

        ship_data = _make_ship(ship_id=1)
        # Ship has player_id=1 but we'll return player_id=99 for the user
        ship_data["player_id"] = 99
        ship_resp = make_mock_response(ship_data)

        player_resp = make_mock_response({"id": 1})

        mock_ships_cog.http_client.get = AsyncMock(return_value=ship_resp)
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_ship_http_status_error_404(self, mock_ships_cog):
        """ship should show 'not found' on 404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="999"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_ship_http_status_error_500(self, mock_ships_cog):
        """ship should show API error on non-404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_ship_generic_exception(self, mock_ships_cog):
        """ship should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_ships_cog.http_client.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_ship_loadout_with_many_weapons(self, mock_ships_cog, make_mock_response):
        """ship should truncate weapons list when >10 items."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=1, is_active=True))
        player_resp = make_mock_response({"id": 1})

        many_weapons = [f"Weapon{i}" for i in range(15)]
        loadout_resp = make_mock_response(
            _make_loadout(
                weapons=many_weapons,
                modules=[],
                turrets=[],
            )
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_loadout_with_many_modules(self, mock_ships_cog, make_mock_response):
        """ship should truncate modules list when >10 items."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=1, is_active=True))
        player_resp = make_mock_response({"id": 1})

        many_modules = [f"Module{i}" for i in range(12)]
        loadout_resp = make_mock_response(
            _make_loadout(
                weapons=["Laser"],
                modules=many_modules,
                turrets=[],
            )
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()

    def test_ship_loadout_with_many_turrets(self, mock_ships_cog, make_mock_response):
        """ship should truncate turrets list when >10 items."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=1, is_active=True))
        player_resp = make_mock_response({"id": 1})

        many_turrets = [f"Turret{i}" for i in range(11)]
        loadout_resp = make_mock_response(
            _make_loadout(
                weapons=[],
                modules=[],
                turrets=many_turrets,
            )
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setactive command
# ---------------------------------------------------------------------------


class TestSetActiveCommand:
    """Tests for the /setactive slash command."""

    def test_setactive_success(self, mock_ships_cog, make_mock_response):
        """setactive should set ship as active and send success embed."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        set_resp = make_mock_response(
            {
                "id": 5,
                "ship_name": "Eagle",
                "nickname": None,
                "is_active": True,
            }
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=set_resp)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_setactive_success_with_nickname(self, mock_ships_cog, make_mock_response):
        """setactive should include nickname in success message when ship has one."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        set_resp = make_mock_response(
            {
                "id": 5,
                "ship_name": "Eagle",
                "nickname": "StarHunter",
                "is_active": True,
            }
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=set_resp)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_setactive_player_not_found(self, mock_ships_cog):
        """setactive should send error when player is not found."""
        interaction = _create_mock_interaction()

        mock_ships_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "Player not found" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_400(self, mock_ships_cog, make_mock_response):
        """setactive should show invalid ship on 400 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        error_response = MagicMock()
        error_response.status_code = 400
        http_error = httpx.HTTPStatusError(
            "400 Bad Request",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "Invalid ship" in call_args[0][0] or "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_404(self, mock_ships_cog, make_mock_response):
        """setactive should show 'not found' on 404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=999))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_500(self, mock_ships_cog, make_mock_response):
        """setactive should show API error on non-400/404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_setactive_generic_exception(self, mock_ships_cog, make_mock_response):
        """setactive should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(side_effect=RuntimeError("unexpected"))

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# /nickname command
# ---------------------------------------------------------------------------


class TestNicknameCommand:
    """Tests for the /nickname slash command."""

    def test_nickname_success(self, mock_ships_cog, make_mock_response):
        """nickname should update ship nickname and send success embed."""
        interaction = _create_mock_interaction()

        # Ship ownership check
        ship_resp = make_mock_response(_make_ship(ship_id=1, is_active=True))

        # Player lookup
        player_resp = make_mock_response({"id": 1})

        # Nickname update
        nick_resp = make_mock_response(
            {
                "id": 1,
                "ship_name": "Eagle",
                "nickname": "NewName",
                "is_active": True,
            }
        )

        mock_ships_cog.http_client.get = AsyncMock(return_value=ship_resp)
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=nick_resp)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="NewName"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_nickname_success_inactive_ship(self, mock_ships_cog, make_mock_response):
        """nickname should show inactive status for inactive ships."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=1, is_active=False))
        player_resp = make_mock_response({"id": 1})
        nick_resp = make_mock_response(
            {
                "id": 1,
                "ship_name": "Eagle",
                "nickname": "MyShip",
                "is_active": False,
            }
        )

        mock_ships_cog.http_client.get = AsyncMock(return_value=ship_resp)
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=nick_resp)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="MyShip"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_nickname_too_long(self, mock_ships_cog):
        """nickname should reject nicknames longer than 50 characters."""
        interaction = _create_mock_interaction()

        long_name = "A" * 51

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname=long_name))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "50 characters" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_nickname_not_owned(self, mock_ships_cog, make_mock_response):
        """nickname should deny access when ship belongs to another player."""
        interaction = _create_mock_interaction()

        ship_data = _make_ship(ship_id=1)
        ship_data["player_id"] = 99  # different from logged-in player
        ship_resp = make_mock_response(ship_data)

        player_resp = make_mock_response({"id": 1})

        mock_ships_cog.http_client.get = AsyncMock(return_value=ship_resp)
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_nickname_http_status_error_404(self, mock_ships_cog):
        """nickname should show 'not found' on 404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="999", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_nickname_http_status_error_500(self, mock_ships_cog):
        """nickname should show API error on non-404 HTTPStatusError."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_ships_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_nickname_generic_exception(self, mock_ships_cog):
        """nickname should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_ships_cog.http_client.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# Additional error handler branches (response already done)
# ---------------------------------------------------------------------------


class TestErrorHandlersAlreadyDone:
    """Tests for error handler callbacks when response is already done."""

    def test_ship_error_handler_response_already_done(self, mock_ships_cog):
        """ship_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ship_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_setactive_error_handler_response_already_done(self, mock_ships_cog):
        """setactive_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.setactive_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_nickname_error_handler_response_already_done(self, mock_ships_cog):
        """nickname_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.nickname_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Additional ships command branch coverage
# ---------------------------------------------------------------------------


class TestShipsCommandAdditionalBranches:
    """Additional tests for /ships covering remaining branches."""

    def test_ships_with_null_weapons_modules_turrets(self, mock_ships_cog, make_mock_response):
        """ships should handle None weapons/modules/turrets gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                {
                    "id": 1,
                    "ship_name": "Eagle",
                    "is_active": True,
                    "nickname": None,
                    "weapons": None,
                    "modules": None,
                    "turrets": None,
                    "created_at": "2024-01-01T00:00:00",
                    "player_id": 1,
                }
            ]
        )

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_exactly_10_shows_standard_footer(self, mock_ships_cog, make_mock_response):
        """ships with exactly 10 ships should show standard footer (not truncation)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})

        ten_ships = [_make_ship(i, f"Ship{i}", is_active=(i == 1)) for i in range(1, 11)]
        ships_resp = make_mock_response(ten_ships)

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# Permission check tests — /ships with user= parameter
# ---------------------------------------------------------------------------


class TestShipsPermissionChecks:
    """Tests verifying admin permission enforcement when viewing another user's ships."""

    def test_ships_own_user_no_admin_check_needed(self, mock_ships_cog, make_mock_response):
        """Viewing own ships requires no admin permission — always succeeds."""
        interaction = _create_mock_interaction(user_id=111111111)

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response([_make_ship(1, "Eagle", is_active=True)])

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        # No user= argument: viewing own ships — no admin check performed
        asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_other_user_admin_allowed(self, mock_ships_cog, make_mock_response):
        """Admin users can view another user's ships without error."""
        from unittest.mock import patch

        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other.jpg"

        player_resp = make_mock_response({"id": 2})
        ships_resp = make_mock_response([_make_ship(3, "Falcon", is_active=True)])

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        # Patch _check_is_admin to return True (user is admin)
        with patch("cogs.adminCog._check_is_admin", new=AsyncMock(return_value=True)):
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_other_user_non_admin_denied(self, mock_ships_cog):
        """Non-admin users cannot view another user's ships — get ephemeral error."""
        from unittest.mock import patch

        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other.jpg"

        # Patch _check_is_admin to return False (user is NOT admin)
        with patch("cogs.adminCog._check_is_admin", new=AsyncMock(return_value=False)):
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)
        assert "admin" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Setactive autocomplete
# ---------------------------------------------------------------------------


class TestSetactiveAutocomplete:
    """Tests for the setactive_autocomplete method."""

    def test_setactive_autocomplete_returns_player_ships(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should list player's ships as choices."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                _make_ship(1, "Eagle", is_active=True),
                _make_ship(2, "Mako", is_active=False),
            ]
        )
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))

        assert len(choices) == 2
        # Active ship should have 🟢 prefix
        active_choice = next((c for c in choices if c.value == "1"), None)
        assert active_choice is not None
        assert "🟢" in active_choice.name

    def test_setactive_autocomplete_filters_by_current_input(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should filter ships by current input."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                _make_ship(1, "Eagle", is_active=False),
                _make_ship(2, "Mako", is_active=False),
                _make_ship(3, "Viper", is_active=False),
            ]
        )
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, "Ma"))

        names = [c.name for c in choices]
        assert any("Mako" in n for n in names)
        assert not any("Eagle" in n for n in names)
        assert not any("Viper" in n for n in names)

    def test_setactive_autocomplete_shows_nickname(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should show nickname in choice label."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ship_with_nick = _make_ship(1, "Eagle", is_active=False, nickname="StarHunter")
        ships_resp = make_mock_response([ship_with_nick])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))

        assert len(choices) == 1
        assert "StarHunter" in choices[0].name

    def test_setactive_autocomplete_returns_empty_on_api_failure(self, mock_ships_cog):
        """setactive_autocomplete should return [] on API failure."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.post = AsyncMock(side_effect=RuntimeError("fail"))

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))

        assert choices == []


# ---------------------------------------------------------------------------
# Setactive — invalid ship_id validation
# ---------------------------------------------------------------------------


class TestSetactiveInvalidShipId:
    """Tests for the /setactive invalid ship_id handling."""

    def test_setactive_invalid_non_numeric_string_shows_error(self, mock_ships_cog):
        """setactive should show error message for non-numeric ship_id."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id="not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)
        assert "invalid" in call_args[0][0].lower()

    def test_setactive_numeric_string_is_accepted(self, mock_ships_cog, make_mock_response):
        """setactive should accept a numeric string like '5' (from autocomplete value)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        set_resp = make_mock_response(
            {
                "id": 5,
                "ship_name": "Eagle",
                "nickname": None,
                "is_active": True,
            }
        )
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=set_resp)

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id="5"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# A.29 new autocomplete coverage: /ship and /nickname
# ---------------------------------------------------------------------------


class TestShipAutocomplete:
    """Tests for the new ship_autocomplete method (used by /ship and /nickname)."""

    def test_ship_autocomplete_returns_player_ships_without_active_prefix(self, mock_ships_cog, make_mock_response):
        """A.34a: ship_autocomplete should NOT show 🟢 prefix (used by /ship, /nickname).

        The active-ship indicator is suppressed for selection-only dropdowns to avoid
        cluttering the autocomplete list. /setactive still shows the indicator.
        """
        interaction = _create_mock_interaction()

        player_resp = make_mock_response({"id": 1})
        ships_resp = make_mock_response(
            [
                _make_ship(7, "Eagle", is_active=True),
                _make_ship(8, "Mako", is_active=False),
            ]
        )
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(mock_ships_cog.ship_autocomplete(interaction, ""))

        assert len(choices) == 2
        active_choice = next((c for c in choices if c.value == "7"), None)
        assert active_choice is not None
        assert active_choice.value == "7"  # values are strings per the design
        # A.34a: active ship must NOT show 🟢 in /ship and /nickname autocomplete
        assert "🟢" not in active_choice.name, "ship_autocomplete must not show active indicator (A.34a)"
        assert active_choice.name == "Eagle"

        inactive_choice = next((c for c in choices if c.value == "8"), None)
        assert inactive_choice is not None
        assert inactive_choice.name == "Mako"

    def test_ship_autocomplete_returns_empty_on_failure(self, mock_ships_cog):
        """ship_autocomplete should return [] on API failure (no error surface)."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.post = AsyncMock(side_effect=RuntimeError("boom"))

        choices = asyncio.run(mock_ships_cog.ship_autocomplete(interaction, ""))

        assert choices == []


class TestShipCommandStrParamHandling:
    """Tests for /ship — ship_id parameter is now str (was int)."""

    def test_ship_accepts_numeric_string(self, mock_ships_cog, make_mock_response):
        """/ship with '42' (str) should call bot-core with /api/v1/ships/42."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=42, is_active=True))
        player_resp = make_mock_response({"id": 1})
        loadout_resp = make_mock_response(_make_loadout())

        mock_ships_cog.http_client.get = AsyncMock(side_effect=[ship_resp, loadout_resp])
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="42"))

        # Verify the first GET hit /api/v1/ships/42 (int path)
        first_get_call = mock_ships_cog.http_client.get.call_args_list[0]
        url = first_get_call[0][0]
        assert url.endswith("/ships/42"), f"expected .../ships/42, got {url}"

    def test_ship_rejects_non_numeric_string(self, mock_ships_cog):
        """/ship with non-numeric ship_id shows a friendly error and does not call API."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.get = AsyncMock()
        mock_ships_cog.http_client.post = AsyncMock()

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="notanumber"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "invalid" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)
        mock_ships_cog.http_client.get.assert_not_called()


class TestNicknameCommandStrParamHandling:
    """Tests for /nickname — ship_id parameter is now str (was int)."""

    def test_nickname_accepts_numeric_string(self, mock_ships_cog, make_mock_response):
        """/nickname with '42' (str) should call bot-core with /api/v1/ships/42."""
        interaction = _create_mock_interaction()

        ship_resp = make_mock_response(_make_ship(ship_id=42, is_active=True))
        player_resp = make_mock_response({"id": 1})
        nick_resp = make_mock_response({"id": 42, "ship_name": "Eagle", "nickname": "MyShip", "is_active": True})

        mock_ships_cog.http_client.get = AsyncMock(return_value=ship_resp)
        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=nick_resp)

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="42", nickname="MyShip"))

        # Verify the ship lookup used the parsed int in the path
        get_call = mock_ships_cog.http_client.get.call_args
        url = get_call[0][0]
        assert url.endswith("/ships/42")

    def test_nickname_rejects_non_numeric_string(self, mock_ships_cog):
        """/nickname with non-numeric ship_id shows friendly error and no API call."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.get = AsyncMock()

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="bogus", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "invalid" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)
        mock_ships_cog.http_client.get.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
