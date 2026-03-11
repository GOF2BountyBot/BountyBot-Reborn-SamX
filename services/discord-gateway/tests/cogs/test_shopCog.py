"""Tests for shopCog — boosting coverage from 0% to 60%+."""

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


def _make_player_data(tier="Bronze", credits=1000, player_id=1):
    """Return a minimal player data dict."""
    return {
        "id": player_id,
        "discord_id": 111111111,
        "guild_id": 987654321,
        "tier": tier,
        "xp": 100,
        "credits": credits,
        "lifetime_credits": credits,
        "prestige_count": 0,
    }


def _make_shop_item(item_id=1, item_name="LaserCannon", item_type="weapon",
                    tier="Bronze", price=500, quantity=10, tech_level=1):
    """Return a minimal shop item dict."""
    return {
        "id": item_id,
        "item_name": item_name,
        "item_type": item_type,
        "tier": tier,
        "price": price,
        "quantity": quantity,
        "tech_level": tech_level,
    }


def _make_transaction(item_name="LaserCannon", item_type="weapon",
                      total_cost=500, remaining_credits=500):
    """Return a minimal transaction dict."""
    return {
        "item_name": item_name,
        "item_type": item_type,
        "total_cost": total_cost,
        "remaining_credits": remaining_credits,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock Discord bot for shopCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture
def mock_shop_cog(mock_bot):
    """Create a ShopCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.shopCog import ShopCog

    cog = ShopCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestShopCogInitialization:
    """Tests for ShopCog initialization."""

    def test_initialization(self, mock_shop_cog, mock_bot):
        """ShopCog should store bot reference and create http_client."""
        assert mock_shop_cog.bot is mock_bot
        assert mock_shop_cog.http_client is not None

    def test_initialization_logs_debug(self, mock_shop_cog):
        """ShopCog __init__ should log a debug message."""
        global _module_logger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("ShopCog initialized")

    def test_valid_tiers_initialized(self, mock_shop_cog):
        """ShopCog should have valid tiers list."""
        assert mock_shop_cog._valid_tiers == ["Bronze", "Silver", "Gold", "Platinum"]

    def test_valid_item_types_initialized(self, mock_shop_cog):
        """ShopCog should have valid item types list."""
        assert mock_shop_cog._valid_item_types == ["ship", "weapon", "module", "turret"]


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for ShopCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_shop_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_shop_cog.cog_unload())
        mock_shop_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_player_data helper
# ---------------------------------------------------------------------------


class TestGetPlayerDataHelper:
    """Tests for the _get_player_data helper method."""

    def test_get_player_data_success(self, mock_shop_cog):
        """_get_player_data should return player dict on success."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _make_player_data()
        mock_shop_cog.http_client.post = AsyncMock(return_value=resp)

        result = asyncio.run(mock_shop_cog._get_player_data(111111111, 987654321))
        assert result is not None
        assert result["tier"] == "Bronze"

    def test_get_player_data_api_error_returns_none(self, mock_shop_cog):
        """_get_player_data should return None on API error."""
        import httpx
        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPError("connection error")
        )

        result = asyncio.run(mock_shop_cog._get_player_data(111111111, 987654321))
        assert result is None

    def test_get_player_data_generic_exception_returns_none(self, mock_shop_cog):
        """_get_player_data should return None on generic exception."""
        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        result = asyncio.run(mock_shop_cog._get_player_data(111111111, 987654321))
        assert result is None


# ---------------------------------------------------------------------------
# tier_autocomplete helper
# ---------------------------------------------------------------------------


class TestTierAutocomplete:
    """Tests for tier_autocomplete."""

    def test_tier_autocomplete_empty_current(self, mock_shop_cog):
        """tier_autocomplete with empty string should return all tiers."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.tier_autocomplete(interaction, ""))
        assert len(result) == 4
        names = [c.name for c in result]
        assert "Bronze" in names
        assert "Platinum" in names

    def test_tier_autocomplete_partial_match(self, mock_shop_cog):
        """tier_autocomplete with partial string should filter."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.tier_autocomplete(interaction, "Br"))
        assert len(result) == 1
        assert result[0].name == "Bronze"

    def test_item_type_autocomplete_empty_current(self, mock_shop_cog):
        """item_type_autocomplete with empty string should return all types."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.item_type_autocomplete(interaction, ""))
        assert len(result) == 4

    def test_item_type_autocomplete_partial_match(self, mock_shop_cog):
        """item_type_autocomplete with partial string should filter."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.item_type_autocomplete(interaction, "wea"))
        assert len(result) == 1
        assert result[0].value == "weapon"


# ---------------------------------------------------------------------------
# shop command
# ---------------------------------------------------------------------------


class TestShopCommand:
    """Tests for the /shop slash command."""

    def test_shop_happy_path_with_items(self, mock_shop_cog):
        """shop should display embed when items are available."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=2000)

        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = [
            _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500),
            _make_shop_item(2, "ShieldModule", "module", "Bronze", 300),
        ]

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_shop_empty_shop(self, mock_shop_cog):
        """shop should send ephemeral message when shop is empty."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze")

        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = []

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "empty" in call_kwargs[0][0].lower()

    def test_shop_invalid_tier(self, mock_shop_cog):
        """shop should send error message for invalid tier."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_shop_cog.shop.callback(
            mock_shop_cog, interaction, "Diamond"
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Invalid tier" in call_kwargs[0][0]

    def test_shop_player_not_found(self, mock_shop_cog):
        """shop should send error when player not found."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("player error")
        )

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_shop_tier_access_locked(self, mock_shop_cog):
        """shop should deny access to higher tier than player's."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze")

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Gold"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Gold" in call_kwargs[0][0]

    def test_shop_http_status_error(self, mock_shop_cog):
        """shop should handle HTTPStatusError gracefully."""
        import httpx
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze")

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Error", request=MagicMock(), response=error_response
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_shop_generic_exception(self, mock_shop_cog):
        """shop should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze")

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# buy command
# ---------------------------------------------------------------------------


class TestBuyCommand:
    """Tests for the /buy slash command."""

    def test_buy_successful_purchase(self, mock_shop_cog):
        """buy should display success embed on valid purchase."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=2000)

        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json.return_value = _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10)

        purchase_resp = MagicMock()
        purchase_resp.raise_for_status = MagicMock()
        purchase_resp.json.return_value = _make_transaction("LaserCannon", "weapon", 500, 1500)

        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=[player_resp, purchase_resp]
        )
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_buy_insufficient_credits(self, mock_shop_cog):
        """buy should send error when player has insufficient credits."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        # Player only has 100 credits but item costs 500
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=100)

        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json.return_value = _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Insufficient credits" in call_kwargs[0][0]

    def test_buy_item_not_found_404(self, mock_shop_cog):
        """buy should handle 404 for missing shop item."""
        import httpx
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=2000)

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=error_response
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 999, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_buy_invalid_quantity(self, mock_shop_cog):
        """buy should reject quantity <= 0."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Quantity" in call_kwargs[0][0]

    def test_buy_player_not_found(self, mock_shop_cog):
        """buy should send error when player not found."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=RuntimeError("player error")
        )

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_buy_insufficient_stock(self, mock_shop_cog):
        """buy should reject purchase when stock is insufficient."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=5000)

        # Item only has 2 in stock but user wants 5
        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json.return_value = _make_shop_item(1, "RareCannon", "weapon", "Bronze", 100, 2)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "stock" in call_kwargs[0][0].lower()

    def test_buy_tier_access_locked(self, mock_shop_cog):
        """buy should reject purchase of item from higher tier."""
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        # Bronze player trying to buy a Gold item
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=5000)

        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json.return_value = _make_shop_item(1, "GoldLaser", "weapon", "Gold", 1000, 5)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_buy_api_400_error_with_detail(self, mock_shop_cog):
        """buy should display error detail from 400 response."""
        import httpx
        interaction = _create_mock_interaction()

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = _make_player_data(tier="Bronze", credits=2000)

        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json.return_value = _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10)

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Already owned"}
        http_error = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=error_response
        )

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# _get_tier_color helper
# ---------------------------------------------------------------------------


class TestGetTierColor:
    """Tests for _get_tier_color helper."""

    def _assert_color(self, color):
        assert type(color).__name__ == "Colour", (
            f"Expected a discord.Colour, got {type(color)}"
        )

    def test_bronze_color(self, mock_shop_cog):
        """Bronze tier should return a color."""
        color = mock_shop_cog._get_tier_color("Bronze")
        self._assert_color(color)

    def test_silver_color(self, mock_shop_cog):
        """Silver tier should return a color."""
        color = mock_shop_cog._get_tier_color("Silver")
        self._assert_color(color)

    def test_gold_color(self, mock_shop_cog):
        """Gold tier should return a color."""
        color = mock_shop_cog._get_tier_color("Gold")
        self._assert_color(color)

    def test_platinum_color(self, mock_shop_cog):
        """Platinum tier should return a color."""
        color = mock_shop_cog._get_tier_color("Platinum")
        self._assert_color(color)

    def test_unknown_tier_defaults(self, mock_shop_cog):
        """Unknown tier should return a default color."""
        color = mock_shop_cog._get_tier_color("Diamond")
        self._assert_color(color)


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_shop_error_handler_response_not_done(self, mock_shop_cog):
        """shop_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_shop_cog.shop_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_shop_error_handler_response_already_done(self, mock_shop_cog):
        """shop_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_shop_cog.shop_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_buy_error_handler_response_not_done(self, mock_shop_cog):
        """buy_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_shop_cog.buy_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_sell_error_handler_response_not_done(self, mock_shop_cog):
        """sell_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_shop_cog.sell_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_shops_error_handler_response_not_done(self, mock_shop_cog):
        """shops_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_shop_cog.shops_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add ShopCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.shopCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        from cogs.shopCog import ShopCog
        assert isinstance(added_arg, ShopCog)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
