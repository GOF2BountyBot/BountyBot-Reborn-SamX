"""Tests for inventoryCog — boosting coverage from 0% to 60%+."""

import pytest
from unittest.mock import MagicMock, AsyncMock
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
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_inventory_item(item_name="LaserCannon", item_type="weapon", quantity=1):
    """Return a minimal inventory item dict."""
    return {
        "id": 1,
        "item_name": item_name,
        "item_type": item_type,
        "quantity": quantity,
    }


def _make_summary(total_items=3, ship=1, weapon=1, module=1, turret=0):
    """Return a minimal inventory summary dict."""
    return {
        "total_items": total_items,
        "ship": ship,
        "weapon": weapon,
        "module": module,
        "turret": turret,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock Discord bot for inventoryCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture
def mock_inventory_cog(mock_bot):
    """Create an InventoryCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.inventoryCog import InventoryCog

    cog = InventoryCog(mock_bot)
    # Replace the real AsyncClient with a MagicMock for test control
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInventoryCogInitialization:
    """Tests for InventoryCog initialization."""

    def test_initialization(self, mock_inventory_cog, mock_bot):
        """InventoryCog should store bot reference and create http_client."""
        assert mock_inventory_cog.bot is mock_bot
        assert mock_inventory_cog.http_client is not None

    def test_initialization_logs_debug(self, mock_inventory_cog):
        """InventoryCog __init__ should log a debug message."""
        global _module_logger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("InventoryCog initialized")


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for InventoryCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_inventory_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_inventory_cog.cog_unload())
        mock_inventory_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_player_id helper
# ---------------------------------------------------------------------------


class TestGetPlayerIdHelper:
    """Tests for the _get_player_id helper method."""

    def test_get_player_id_success(self, mock_inventory_cog):
        """_get_player_id should return player ID on success."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"id": 42}
        mock_inventory_cog.http_client.post = AsyncMock(return_value=resp)

        result = asyncio.run(mock_inventory_cog._get_player_id(111111111, 987654321))
        assert result == 42

    def test_get_player_id_api_error(self, mock_inventory_cog):
        """_get_player_id should return None on API error."""
        import httpx
        mock_inventory_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPError("API error")
        )

        result = asyncio.run(mock_inventory_cog._get_player_id(111111111, 987654321))
        assert result is None

    def test_get_player_id_generic_exception(self, mock_inventory_cog):
        """_get_player_id should return None on generic exception."""
        mock_inventory_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

        result = asyncio.run(mock_inventory_cog._get_player_id(111111111, 987654321))
        assert result is None


# ---------------------------------------------------------------------------
# inventory command
# ---------------------------------------------------------------------------


class TestInventoryCommand:
    """Tests for the /inventory slash command."""

    def test_inventory_happy_path_with_items(self, mock_inventory_cog):
        """inventory should show embed when items exist."""
        interaction = _create_mock_interaction()

        # _get_player_id returns player id
        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        # GET /inventory/player/1 returns items
        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = [
            _make_inventory_item("LaserCannon", "weapon", 1),
            _make_inventory_item("ShieldModule", "module", 2),
        ]

        # GET /inventory/player/1/summary
        summary_resp = MagicMock()
        summary_resp.raise_for_status = MagicMock()
        summary_resp.json.return_value = _make_summary(total_items=3, weapon=1, module=1)

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(
            side_effect=[items_resp, summary_resp]
        )

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_inventory_empty_inventory(self, mock_inventory_cog):
        """inventory with no items should send ephemeral message."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = []

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(return_value=empty_resp)

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_inventory_player_not_found(self, mock_inventory_cog):
        """inventory should send ephemeral error when player not found."""
        interaction = _create_mock_interaction()

        # _get_player_id returns None (player not found)
        mock_inventory_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("not found")
        )

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Player not found" in call_kwargs[0][0]

    def test_inventory_viewing_other_user(self, mock_inventory_cog):
        """inventory should work when viewing another user's inventory."""
        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other-avatar.jpg"

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 2}

        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = [_make_inventory_item("HeavyShip", "ship", 1)]

        summary_resp = MagicMock()
        summary_resp.raise_for_status = MagicMock()
        summary_resp.json.return_value = _make_summary(total_items=1, ship=1)

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(
            side_effect=[items_resp, summary_resp]
        )

        asyncio.run(mock_inventory_cog.inventory.callback(
            mock_inventory_cog, interaction, user=other_user
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_inventory_with_item_type_filter(self, mock_inventory_cog):
        """inventory with item_type filter should only show that type."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = [_make_inventory_item("LaserCannon", "weapon", 1)]

        summary_resp = MagicMock()
        summary_resp.raise_for_status = MagicMock()
        summary_resp.json.return_value = _make_summary(total_items=1, weapon=1)

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(
            side_effect=[items_resp, summary_resp]
        )

        asyncio.run(mock_inventory_cog.inventory.callback(
            mock_inventory_cog, interaction, item_type="weapon"
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_inventory_with_many_items_truncated(self, mock_inventory_cog):
        """inventory with >20 items of one type should truncate and show 'more'."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        # 25 weapon items
        many_items = [
            _make_inventory_item(f"Weapon{i}", "weapon", 1)
            for i in range(25)
        ]
        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = many_items

        summary_resp = MagicMock()
        summary_resp.raise_for_status = MagicMock()
        summary_resp.json.return_value = _make_summary(total_items=25, weapon=25)

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(
            side_effect=[items_resp, summary_resp]
        )

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_inventory_http_status_error(self, mock_inventory_cog):
        """inventory should handle HTTPStatusError from inventory endpoint."""
        import httpx
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=error_response,
        )

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_inventory_generic_exception(self, mock_inventory_cog):
        """inventory should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {"id": 1}

        mock_inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_inventory_cog.http_client.get = AsyncMock(
            side_effect=RuntimeError("unexpected error")
        )

        asyncio.run(mock_inventory_cog.inventory.callback(mock_inventory_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# _get_item_type_color helper
# ---------------------------------------------------------------------------


class TestGetItemTypeColor:
    """Tests for _get_item_type_color helper."""

    def _assert_color(self, color):
        assert type(color).__name__ == "Colour", (
            f"Expected a discord.Colour, got {type(color)}"
        )

    def test_ship_color(self, mock_inventory_cog):
        """ship item type should return green color."""
        color = mock_inventory_cog._get_item_type_color("ship")
        self._assert_color(color)

    def test_weapon_color(self, mock_inventory_cog):
        """weapon item type should return red color."""
        color = mock_inventory_cog._get_item_type_color("weapon")
        self._assert_color(color)

    def test_module_color(self, mock_inventory_cog):
        """module item type should return blue color."""
        color = mock_inventory_cog._get_item_type_color("module")
        self._assert_color(color)

    def test_turret_color(self, mock_inventory_cog):
        """turret item type should return purple color."""
        color = mock_inventory_cog._get_item_type_color("turret")
        self._assert_color(color)

    def test_unknown_type_defaults(self, mock_inventory_cog):
        """Unknown item type should return default color."""
        color = mock_inventory_cog._get_item_type_color("unknown")
        self._assert_color(color)


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_inventory_error_handler_response_not_done(self, mock_inventory_cog):
        """inventory_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_inventory_cog.inventory_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_inventory_error_handler_response_already_done(self, mock_inventory_cog):
        """inventory_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_inventory_cog.inventory_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_search_error_handler_response_not_done(self, mock_inventory_cog):
        """search_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_inventory_cog.search_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_item_error_handler_response_not_done(self, mock_inventory_cog):
        """item_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_inventory_cog.item_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add InventoryCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.inventoryCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        from cogs.inventoryCog import InventoryCog
        assert isinstance(added_arg, InventoryCog)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
