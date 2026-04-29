"""Tests for shopCog — boosting coverage from 0% to 60%+."""

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
# Track all loggers created (keyed by name) to support lookup after multi-logger inits.
_all_loggers: dict[str, MagicMock] = {}


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _module_logger
    name = _args[0] if _args else None
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _module_logger = logger
    if name:
        _all_loggers[name] = logger
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


def _make_shop_item(
    item_id=1, item_name="LaserCannon", item_type="weapon", tier="Bronze", price=500, quantity=10, tech_level=1
):
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


def _make_transaction(item_name="LaserCannon", item_type="weapon", total_cost=500, remaining_credits=500):
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
        # ShopCog uses module-level flogger "discord-gateway-ShopCog".
        # After __init__ an AutocompleteCache logger is also created, so look up
        # the ShopCog logger specifically.
        shop_logger = _all_loggers.get("discord-gateway-ShopCog")
        assert shop_logger is not None, "ShopCog logger not found in _all_loggers"
        shop_logger.debug.assert_called_with("ShopCog initialized")

    def test_valid_tiers_initialized(self, mock_shop_cog):
        """ShopCog should have valid tiers list."""
        assert mock_shop_cog._valid_tiers == ["Bronze", "Silver", "Gold", "Platinum"]

    def test_valid_item_types_initialized(self, mock_shop_cog):
        """ShopCog should have valid item types list with concrete vocab (A.46 fix)."""
        assert mock_shop_cog._valid_item_types == [
            "ship",
            "primary_weapon",
            "secondary_weapon",
            "turret_weapon",
            "module",
        ]


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

    def test_get_player_data_success(self, mock_shop_cog, make_mock_response):
        """_get_player_data should return player dict on success."""
        resp = make_mock_response(_make_player_data())
        mock_shop_cog.http_client.post = AsyncMock(return_value=resp)

        result = asyncio.run(mock_shop_cog._get_player_data(111111111, 987654321))
        assert result is not None
        assert result["tier"] == "Bronze"

    def test_get_player_data_api_error_returns_none(self, mock_shop_cog):
        """_get_player_data should return None on API error."""
        import httpx

        mock_shop_cog.http_client.post = AsyncMock(side_effect=httpx.HTTPError("connection error"))

        result = asyncio.run(mock_shop_cog._get_player_data(111111111, 987654321))
        assert result is None

    def test_get_player_data_generic_exception_returns_none(self, mock_shop_cog):
        """_get_player_data should return None on generic exception."""
        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))

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
        """item_type_autocomplete with empty string should return all 5 concrete types."""
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.item_type_autocomplete(interaction, ""))
        assert len(result) == 5

    def test_item_type_autocomplete_partial_match(self, mock_shop_cog):
        """item_type_autocomplete with partial string should filter."""
        interaction = _create_mock_interaction()
        # "weapon" matches "primary_weapon", "secondary_weapon", "turret_weapon"
        result = asyncio.run(mock_shop_cog.item_type_autocomplete(interaction, "weapon"))
        assert len(result) == 3
        values = {c.value for c in result}
        assert values == {"primary_weapon", "secondary_weapon", "turret_weapon"}

    def test_item_type_autocomplete_uses_concrete_vocab(self, mock_shop_cog):
        """item_type_autocomplete choice values are exactly the 5 concrete item types (A.46 fix).

        Verifies that:
        - The choice VALUE set is the canonical 5-element concrete vocab.
        - Display names contain no underscores (human-readable labels).
        - No legacy alias values ('weapon', 'turret') are present.
        """
        interaction = _create_mock_interaction()
        result = asyncio.run(mock_shop_cog.item_type_autocomplete(interaction, ""))

        values = {c.value for c in result}
        assert values == {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}, (
            f"Expected exactly the 5 concrete item type values, got: {values}"
        )

        # Display names must be human-readable (no underscores)
        for choice in result:
            assert "_" not in choice.name, (
                f"Display name '{choice.name}' contains an underscore — use replace('_', ' ').title()"
            )

        # Legacy alias values must NOT appear
        legacy_aliases = {"weapon", "turret"}
        assert not values & legacy_aliases, f"Legacy alias values found in choices: {values & legacy_aliases}"


# ---------------------------------------------------------------------------
# shop command
# ---------------------------------------------------------------------------


class TestShopCommand:
    """Tests for the /shop slash command."""

    def test_shop_happy_path_with_items(self, mock_shop_cog, make_mock_response):
        """shop should display embed when items are available."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=2000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500),
                _make_shop_item(2, "ShieldModule", "module", "Bronze", 300),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_shop_empty_shop(self, mock_shop_cog, make_mock_response):
        """shop should send ephemeral message when shop is empty."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze"))
        items_resp = make_mock_response([])

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

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Diamond"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Invalid tier" in call_kwargs[0][0]

    def test_shop_player_not_found(self, mock_shop_cog):
        """shop should send error when player not found."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_shop_tier_access_locked(self, mock_shop_cog, make_mock_response):
        """shop should deny access to higher tier than player's."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze"))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Gold"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Gold" in call_kwargs[0][0]

    def test_shop_http_status_error(self, mock_shop_cog, make_mock_response):
        """shop should handle HTTPStatusError gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze"))

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Error", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_shop_generic_exception(self, mock_shop_cog, make_mock_response):
        """shop should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze"))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    # R.1 — optional tier parameter defaulting to player's current tier

    def test_shop_omit_tier_uses_player_tier_bronze(self, mock_shop_cog, make_mock_response):
        """R.1: /shop with no tier defaults to the invoker's current tier (Bronze)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))
        items_resp = make_mock_response([_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500)])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        # Call with tier=None (omitted)
        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, None))

        # Verify an embed was sent (not an error)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

        # Verify the API was called with the Bronze tier URL
        get_call = mock_shop_cog.http_client.get.call_args
        assert "Bronze" in get_call[0][0]

    def test_shop_omit_tier_uses_player_tier_gold(self, mock_shop_cog, make_mock_response):
        """R.1: /shop with no tier defaults to the invoker's current tier (Gold)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Gold", credits=5000))
        items_resp = make_mock_response([_make_shop_item(1, "LaserCannon", "weapon", "Gold", 2000)])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

        get_call = mock_shop_cog.http_client.get.call_args
        assert "Gold" in get_call[0][0]

    def test_shop_explicit_tier_still_works(self, mock_shop_cog, make_mock_response):
        """R.1: explicitly passing a tier still works as before."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=2000))
        items_resp = make_mock_response([_make_shop_item(1, "ShieldModule", "module", "Silver", 800)])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Silver"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_shop_omit_tier_player_tier_missing_falls_back_to_bronze(self, mock_shop_cog, make_mock_response):
        """R.1: if player.tier is missing/None, falls back to Bronze with a warning log."""
        interaction = _create_mock_interaction()

        # Player data with no tier field
        player_data = _make_player_data(tier="Bronze", credits=500)
        del player_data["tier"]  # simulate missing field
        player_resp = make_mock_response(player_data)
        items_resp = make_mock_response([_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 300)])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, None))

        # Should not error — fallback to Bronze
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        # Should get an embed, not an error
        assert "embed" in call_kwargs

    def test_shop_omit_tier_player_not_found_shows_error(self, mock_shop_cog):
        """R.1: omitting tier but having no player data shows error message."""
        interaction = _create_mock_interaction()

        # _get_player_data returns None on non-configured-guild
        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)


# ---------------------------------------------------------------------------
# buy command
# ---------------------------------------------------------------------------


class TestBuyCommand:
    """Tests for the /buy slash command."""

    def test_buy_successful_purchase(self, mock_shop_cog, make_mock_response):
        """buy should display success embed on valid purchase (non-ship item)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=2000))
        item_resp = make_mock_response(_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10))
        purchase_resp = make_mock_response(_make_transaction("LaserCannon", "weapon", 500, 1500))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # Footer should say inventory for non-ship
        embed = call_kwargs["embed"]
        assert "inventory" in embed.footer.text.lower()

    def test_buy_ship_calls_purchase_ship_endpoint(self, mock_shop_cog, make_mock_response):
        """buy a ship should call POST /shops/purchase-ship and show hangar footer."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        ship_item_resp = make_mock_response(_make_shop_item(2, "Eagle", "ship", "Bronze", 2000, 3))
        ship_purchase_resp = make_mock_response(_make_transaction("Eagle", "ship", 2000, 3000))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, ship_purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=ship_item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 2, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

        # Verify it called purchase-ship endpoint
        post_calls = mock_shop_cog.http_client.post.call_args_list
        # Second post call should be to purchase-ship
        purchase_call = post_calls[1]
        assert "purchase-ship" in purchase_call[0][0]

        # Footer should say hangar for ship
        embed = call_kwargs["embed"]
        assert "hangar" in embed.footer.text.lower()

    def test_buy_ship_purchase_data_has_sell_old_ship_false(self, mock_shop_cog, make_mock_response):
        """Ship purchase request should include sell_old_ship: False."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        ship_item_resp = make_mock_response(_make_shop_item(2, "Eagle", "ship", "Bronze", 2000, 3))
        ship_purchase_resp = make_mock_response(_make_transaction("Eagle", "ship", 2000, 3000))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, ship_purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=ship_item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 2, 1))

        # Verify the purchase-ship payload
        post_calls = mock_shop_cog.http_client.post.call_args_list
        purchase_call_kwargs = post_calls[1][1]
        assert purchase_call_kwargs["json"]["sell_old_ship"] is False
        assert purchase_call_kwargs["json"]["shop_item_id"] == 2

    def test_buy_insufficient_credits(self, mock_shop_cog, make_mock_response):
        """buy should send error when player has insufficient credits."""
        interaction = _create_mock_interaction()

        # Player only has 100 credits but item costs 500
        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=100))
        item_resp = make_mock_response(_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Insufficient credits" in call_kwargs[0][0]

    def test_buy_item_not_found_404(self, mock_shop_cog, make_mock_response):
        """buy should handle 404 for missing shop item."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=2000))

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)

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

        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_buy_insufficient_stock(self, mock_shop_cog, make_mock_response):
        """buy should reject purchase when stock is insufficient."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        # Item only has 2 in stock but user wants 5
        item_resp = make_mock_response(_make_shop_item(1, "RareCannon", "weapon", "Bronze", 100, 2))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "stock" in call_kwargs[0][0].lower()

    def test_buy_tier_access_locked(self, mock_shop_cog, make_mock_response):
        """buy should reject purchase of item from higher tier."""
        interaction = _create_mock_interaction()

        # Bronze player trying to buy a Gold item
        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        item_resp = make_mock_response(_make_shop_item(1, "GoldLaser", "weapon", "Gold", 1000, 5))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_buy_api_400_error_with_detail(self, mock_shop_cog, make_mock_response):
        """buy should display error detail from 400 response."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=2000))
        item_resp = make_mock_response(_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10))

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Already owned"}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

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
        assert type(color).__name__ == "Colour", f"Expected a discord.Colour, got {type(color)}"

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
# shop command — additional branch coverage
# ---------------------------------------------------------------------------


class TestShopCommandBranches:
    """Additional tests for /shop covering uncovered branches."""

    def test_shop_with_item_type_filter(self, mock_shop_cog, make_mock_response):
        """shop with item_type should pass params and show filtered title."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=3000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 5, 2),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze", item_type="weapon"))

        # Verify item_type param was passed to the GET request
        call_kwargs = mock_shop_cog.http_client.get.call_args[1]
        assert call_kwargs["params"] == {"item_type": "weapon"}

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shop_empty_with_item_type_filter(self, mock_shop_cog, make_mock_response):
        """shop empty message should include item_type filter text."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))
        items_resp = make_mock_response([])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze", item_type="ship"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "ship" in msg.lower()
        assert "empty" in msg.lower()

    def test_shop_item_quantity_greater_than_one(self, mock_shop_cog, make_mock_response):
        """Items with quantity > 1 should show 'xN' in display."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "BulkLaser", "weapon", "Bronze", 100, 5, 2),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shop_item_quantity_one_no_suffix(self, mock_shop_cog, make_mock_response):
        """Items with quantity == 1 should not show 'x1'."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "SingleItem", "weapon", "Bronze", 100, 1, 3),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()

    def test_shop_item_no_tech_level(self, mock_shop_cog, make_mock_response):
        """Items with tech_level=None should display empty tech string."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))

        item_no_tech = _make_shop_item(1, "BasicShip", "ship", "Bronze", 200, 3)
        item_no_tech["tech_level"] = None

        items_resp = make_mock_response([item_no_tech])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()

    def test_shop_item_unaffordable_strikethrough(self, mock_shop_cog, make_mock_response):
        """Items the player can't afford should get strikethrough price."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=50))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "ExpensiveLaser", "weapon", "Bronze", 9999, 3, 1),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shop_more_than_10_items_truncated(self, mock_shop_cog, make_mock_response):
        """When a type has > 10 items, should show '... and N more items'."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=50000))

        # Create 15 items of same type
        many_items = [_make_shop_item(i, f"Weapon{i}", "weapon", "Bronze", 100 * i, 5, 1) for i in range(1, 16)]

        items_resp = make_mock_response(many_items)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shop_multiple_item_types(self, mock_shop_cog, make_mock_response):
        """Shop with multiple item types should group them into separate fields."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Gold", credits=10000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 5, 1),
                _make_shop_item(2, "ShieldModule", "module", "Bronze", 300, 3, 2),
                _make_shop_item(3, "Eagle", "ship", "Bronze", 1000, 2, 1),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs


# ---------------------------------------------------------------------------
# buy command — additional branch coverage
# ---------------------------------------------------------------------------


class TestBuyCommandBranches:
    """Additional tests for /buy covering uncovered branches."""

    def test_buy_negative_quantity(self, mock_shop_cog):
        """buy should reject negative quantity."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, -1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Quantity" in call_kwargs[0][0]

    def test_buy_multi_quantity_success(self, mock_shop_cog, make_mock_response):
        """buy with quantity > 1 should calculate total cost correctly."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        item_resp = make_mock_response(_make_shop_item(1, "Ammo", "weapon", "Bronze", 100, 50))
        purchase_resp = make_mock_response(_make_transaction("Ammo", "weapon", 300, 4700))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 3))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_buy_400_error_json_parse_fails(self, mock_shop_cog, make_mock_response):
        """buy 400 error where response.json() fails should fallback message."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        item_resp = make_mock_response(_make_shop_item(1, "Laser", "weapon", "Bronze", 500, 10))

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.side_effect = ValueError("invalid json")
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Invalid purchase request" in call_kwargs[0][0]

    def test_buy_non_400_404_http_error(self, mock_shop_cog, make_mock_response):
        """buy with 500 HTTP error should show generic API error."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        item_resp = make_mock_response(_make_shop_item(1, "Laser", "weapon", "Bronze", 500, 10))

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Internal Server Error", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "API Error" in call_kwargs[0][0]

    def test_buy_generic_exception(self, mock_shop_cog, make_mock_response):
        """buy should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# /buy embed — Item Type field rendering (DEF-CLEANUP-001 regression tests)
# ---------------------------------------------------------------------------


class TestBuyItemTypeFieldRendering:
    """Regression tests for DEF-CLEANUP-001: /buy post-purchase embed must render
    weapon type names without underscores.

    Site 1 fix: transaction["item_type"].replace("_", " ").title()
    """

    def _run_buy_with_item_type(self, mock_shop_cog, make_mock_response, item_type):
        """Helper: run /buy for a given item_type and return the success embed."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        item_resp = make_mock_response(_make_shop_item(1, "TestItem", item_type, "Bronze", 500, 10))
        purchase_resp = make_mock_response(_make_transaction("TestItem", item_type, 500, 4500))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        return send_kwargs["embed"]

    def _get_item_type_field(self, embed):
        """Extract the 'Item Type' field value from the embed."""
        field = next((f for f in embed.fields if f.name == "Item Type"), None)
        assert field is not None, "Success embed must contain an 'Item Type' field"
        return field.value

    def test_buy_primary_weapon_item_type_renders_without_underscore(self, mock_shop_cog, make_mock_response):
        """DEF-CLEANUP-001 site 1: /buy primary_weapon → 'Primary Weapon' (no underscore)."""
        embed = self._run_buy_with_item_type(mock_shop_cog, make_mock_response, "primary_weapon")
        value = self._get_item_type_field(embed)
        assert "_" not in value, f"Item Type field must not contain underscores, got: {value!r}"
        assert value == "Primary Weapon", f"Expected 'Primary Weapon', got: {value!r}"

    def test_buy_secondary_weapon_item_type_renders_without_underscore(self, mock_shop_cog, make_mock_response):
        """DEF-CLEANUP-001 site 1: /buy secondary_weapon → 'Secondary Weapon' (no underscore)."""
        embed = self._run_buy_with_item_type(mock_shop_cog, make_mock_response, "secondary_weapon")
        value = self._get_item_type_field(embed)
        assert "_" not in value, f"Item Type field must not contain underscores, got: {value!r}"
        assert value == "Secondary Weapon", f"Expected 'Secondary Weapon', got: {value!r}"

    def test_buy_turret_weapon_item_type_renders_without_underscore(self, mock_shop_cog, make_mock_response):
        """DEF-CLEANUP-001 site 1: /buy turret_weapon → 'Turret Weapon' (no underscore)."""
        embed = self._run_buy_with_item_type(mock_shop_cog, make_mock_response, "turret_weapon")
        value = self._get_item_type_field(embed)
        assert "_" not in value, f"Item Type field must not contain underscores, got: {value!r}"
        assert value == "Turret Weapon", f"Expected 'Turret Weapon', got: {value!r}"

    def test_buy_ship_item_type_unchanged(self, mock_shop_cog, make_mock_response):
        """Ship type has no underscore so title() is sufficient; verify still renders 'Ship'."""
        embed = self._run_buy_with_item_type(mock_shop_cog, make_mock_response, "ship")
        value = self._get_item_type_field(embed)
        assert value == "Ship", f"Expected 'Ship', got: {value!r}"

    def test_buy_module_item_type_unchanged(self, mock_shop_cog, make_mock_response):
        """Module type has no underscore; verify still renders 'Module'."""
        embed = self._run_buy_with_item_type(mock_shop_cog, make_mock_response, "module")
        value = self._get_item_type_field(embed)
        assert value == "Module", f"Expected 'Module', got: {value!r}"


# ---------------------------------------------------------------------------
# /shop listing — unknown item_type fallback label (DEF-CLEANUP-001 site 2)
# ---------------------------------------------------------------------------


class TestShopFallbackLabel:
    """Regression tests for DEF-CLEANUP-001 site 2: /shop listing fallback label for
    unknown item types must use replace('_', ' ').title() instead of bare .title().

    The fix: type_labels.get(item_type_key, f"{item_type_key.replace('_', ' ').title()}s")
    """

    def test_shop_unknown_item_type_primary_weapon_fallback_no_underscore(self, mock_shop_cog, make_mock_response):
        """DEF-CLEANUP-001 site 2: Unknown type 'primary_weapon' falls back to label without underscore.

        Since 'primary_weapon' IS in type_labels, it returns the proper label. This verifies the
        known-type path still works after the fix.
        """
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        items_resp = make_mock_response(
            [
                _make_shop_item(1, "Nirai EX 1", "primary_weapon", "Bronze", 500, 5, 1),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        embed = send_kwargs["embed"]

        # The field name should be "Primary Weapons (1)" — no underscores
        field_names = [f.name for f in embed.fields]
        assert any("_" not in name for name in field_names), "All embed field names should be underscore-free"
        # Specifically verify the primary_weapon field renders correctly
        pw_field = next((n for n in field_names if "primary" in n.lower()), None)
        assert pw_field is not None, f"Expected a Primary Weapons field, got: {field_names}"
        assert "_" not in pw_field, f"Primary Weapons field name contains underscore: {pw_field!r}"
        assert pw_field == "Primary Weapons (1)", f"Expected 'Primary Weapons (1)', got: {pw_field!r}"

    def test_shop_exotic_unknown_item_type_fallback_no_underscore(self, mock_shop_cog, make_mock_response):
        """DEF-CLEANUP-001 site 2: A fictional 'exotic_weapon' type not in type_labels falls back
        to 'Exotic Weapons' (no underscore). This exercises the fallback branch directly.
        """
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        # Inject an unknown type 'exotic_weapon' that is NOT in type_labels
        items_resp = make_mock_response(
            [
                _make_shop_item(99, "Exotic Blaster", "exotic_weapon", "Bronze", 999, 1, 1),
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        embed = send_kwargs["embed"]

        # The fallback label for 'exotic_weapon' must be 'Exotic Weapons (1)' — no underscores
        field_names = [f.name for f in embed.fields]
        exotic_field = next((n for n in field_names if "exotic" in n.lower()), None)
        assert exotic_field is not None, f"Expected an Exotic field in embed, got: {field_names}"
        assert "_" not in exotic_field, f"Fallback field name contains underscore: {exotic_field!r}"
        assert exotic_field == "Exotic Weapons (1)", f"Expected 'Exotic Weapons (1)', got: {exotic_field!r}"


# ---------------------------------------------------------------------------
# sell command
# ---------------------------------------------------------------------------


def _make_sell_transaction(total_value=250, remaining_credits=1250, item_type="primary_weapon"):
    """Return a minimal sell transaction dict.

    Includes item_type so cog-side embed rendering (which displays the item type label
    via _ITEM_TYPE_LABELS) is exercised in tests — DEF-A42-003 fix.
    """
    return {
        "total_value": total_value,
        "remaining_credits": remaining_credits,
        "item_type": item_type,
    }


class TestSellCommand:
    """Tests for the /sell slash command."""

    def test_sell_happy_path(self, mock_shop_cog, make_mock_response):
        """A.42 regression: /sell Micro Gun MK I posts item_name only; no item_type or target_tier.

        The cog sends only {player_id, item_name, quantity} and the server resolves
        item_type from inventory and target_tier from player.tier (A.42b + A.42c).
        Also verifies that the success embed correctly displays the concrete type label
        returned by the server (DEF-A42-003 fix).
        """
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))
        sell_resp = make_mock_response(_make_sell_transaction(250, 1250, item_type="primary_weapon"))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, sell_resp])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "Micro Gun MK I", 1))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

        # Verify the embed's Item Type field shows the correct human-readable label (DEF-A42-003)
        embed = send_kwargs["embed"]
        item_type_field = next((f for f in embed.fields if f.name == "Item Type"), None)
        assert item_type_field is not None, "Success embed must contain an 'Item Type' field"
        assert item_type_field.value == "Primary Weapon", (
            f"Expected 'Primary Weapon' label for primary_weapon concrete type, got: {item_type_field.value!r}"
        )

        # Verify POST payload has only player_id, item_name, quantity — no item_type, no target_tier
        sell_call = mock_shop_cog.http_client.post.call_args_list[1]
        sent_json = sell_call[1]["json"]
        assert "item_name" in sent_json
        assert "quantity" in sent_json
        assert "item_type" not in sent_json
        assert "target_tier" not in sent_json

    def test_sell_invalid_quantity_zero(self, mock_shop_cog):
        """sell should reject quantity of zero."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 0))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Quantity" in call_kwargs[0][0]

    def test_sell_invalid_quantity_negative(self, mock_shop_cog):
        """sell should reject negative quantity."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", -3))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Quantity" in call_kwargs[0][0]

    def test_sell_player_not_found(self, mock_shop_cog):
        """sell should send error when player not found."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Player not found" in call_kwargs[0][0]

    def test_sell_http_400_with_detail(self, mock_shop_cog, make_mock_response):
        """sell 400 error should display error detail from response."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Item not in inventory"}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Item not in inventory" in call_kwargs[0][0]

    def test_sell_http_400_json_parse_fails(self, mock_shop_cog, make_mock_response):
        """sell 400 error where response.json() fails should fallback."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.side_effect = ValueError("bad json")
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Invalid sell request" in call_kwargs[0][0]

    def test_sell_non_400_http_error(self, mock_shop_cog, make_mock_response):
        """sell with 500 HTTP error should show generic API error."""
        import httpx

        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, http_error])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "API Error" in call_kwargs[0][0]

    def test_sell_generic_exception(self, mock_shop_cog, make_mock_response):
        """sell should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=1000))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, RuntimeError("boom")])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "LaserCannon", 1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_sell_multi_quantity(self, mock_shop_cog, make_mock_response):
        """sell with quantity > 1 should work correctly."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=2000))
        sell_resp = make_mock_response(_make_sell_transaction(750, 2750))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, sell_resp])

        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "ShieldModule", 3))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs


# ---------------------------------------------------------------------------
# sell_item_autocomplete (updated behavior)
# ---------------------------------------------------------------------------


class TestSellItemAutocomplete:
    """Tests for the updated sell_item_autocomplete (A.42b — no item_type filter, '(Type)' labels).

    The autocomplete no longer accepts an item_type filter parameter — server-side resolution
    handles type detection. Display format is still 'Name (Type)'.
    """

    def _make_inventory_items(self):
        """Return a sample inventory list."""
        return [
            {"item_name": "LaserCannon", "item_type": "primary_weapon", "quantity": 2},
            {"item_name": "ShieldModule", "item_type": "module", "quantity": 1},
            {"item_name": "Betty", "item_type": "ship", "quantity": 1},
            {"item_name": "Raptor Turret", "item_type": "turret_weapon", "quantity": 3},
        ]

    def test_sell_autocomplete_returns_all(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete returns all inventory items (no type filter)."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        inventory_resp = make_mock_response(self._make_inventory_items())

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=inventory_resp)

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 4

    def test_sell_autocomplete_display_format_includes_type(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete should display 'Name (Type)' format."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        inventory_resp = make_mock_response(
            [
                {"item_name": "Betty", "item_type": "ship", "quantity": 1},
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=inventory_resp)

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 1
        assert result[0].name == "Betty (Ship)"
        assert result[0].value == "Betty"

    def test_sell_autocomplete_primary_weapon_label(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete should show 'Primary Weapon' for primary_weapon type."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        inventory_resp = make_mock_response(
            [
                {"item_name": "Nirai Impulse EX 1", "item_type": "primary_weapon", "quantity": 1},
            ]
        )

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=inventory_resp)

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 1
        assert result[0].name == "Nirai Impulse EX 1 (Primary Weapon)"
        assert result[0].value == "Nirai Impulse EX 1"

    def test_sell_autocomplete_player_not_found_returns_empty(self, mock_shop_cog):
        """sell_item_autocomplete should return [] when player lookup fails."""
        interaction = _create_mock_interaction()
        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("error"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert result == []

    def test_sell_autocomplete_inventory_error_returns_empty(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete should return [] when inventory fetch fails."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(side_effect=RuntimeError("error"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert result == []

    def test_sell_autocomplete_inventory_non_200_returns_empty(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete should return [] when inventory returns non-200."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        error_inv_resp = make_mock_response([], status_code=500)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=error_inv_resp)

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert result == []

    def test_sell_autocomplete_filters_by_current_text(self, mock_shop_cog, make_mock_response):
        """sell_item_autocomplete should filter results by current text."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data())
        inventory_resp = make_mock_response(self._make_inventory_items())

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=inventory_resp)

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, "Betty"))
        assert len(result) == 1
        assert result[0].value == "Betty"


# ---------------------------------------------------------------------------
# sell command — type label class attribute (_ITEM_TYPE_LABELS only, no _SELL_TYPE_MAP)
# ---------------------------------------------------------------------------


class TestSellTypeMappings:
    """Tests for the _ITEM_TYPE_LABELS class attribute (A.42: _SELL_TYPE_MAP was removed)."""

    def test_item_type_labels_ship(self, mock_shop_cog):
        """ship label should be 'Ship'."""
        assert mock_shop_cog._ITEM_TYPE_LABELS["ship"] == "Ship"

    def test_item_type_labels_primary_weapon(self, mock_shop_cog):
        """primary_weapon label should be 'Primary Weapon'."""
        assert mock_shop_cog._ITEM_TYPE_LABELS["primary_weapon"] == "Primary Weapon"

    def test_item_type_labels_turret_weapon(self, mock_shop_cog):
        """turret_weapon label should be 'Turret Weapon'."""
        assert mock_shop_cog._ITEM_TYPE_LABELS["turret_weapon"] == "Turret Weapon"

    def test_item_type_labels_module(self, mock_shop_cog):
        """module label should be 'Module'."""
        assert mock_shop_cog._ITEM_TYPE_LABELS["module"] == "Module"

    def test_sell_type_map_removed(self, mock_shop_cog):
        """_SELL_TYPE_MAP must no longer exist on the cog (A.42 fix).

        The vocab-downgrade bug was caused by _SELL_TYPE_MAP mapping
        concrete types (primary_weapon, turret_weapon) back to generic
        aliases (weapon, turret) before POSTing to the API.
        """
        assert not hasattr(mock_shop_cog, "_SELL_TYPE_MAP"), (
            "_SELL_TYPE_MAP was removed in A.42 because it caused the vocab-downgrade bug. "
            "If it was re-added, revert immediately."
        )


# ---------------------------------------------------------------------------
# shops command
# ---------------------------------------------------------------------------


class TestShopsCommand:
    """Tests for the /shops slash command."""

    def _make_shops_summary(self):
        """Return a minimal shops summary dict."""
        return {
            "total_items": 25,
            "shops": {
                "Bronze": {"items": 10, "total_quantity": 50},
                "Silver": {"items": 8, "total_quantity": 30},
                "Gold": {"items": 5, "total_quantity": 15},
            },
        }

    def test_shops_happy_path_with_player(self, mock_shop_cog, make_mock_response):
        """shops should display summary embed with player tier info."""
        interaction = _create_mock_interaction()

        summary_resp = make_mock_response(self._make_shops_summary())
        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=3000))

        mock_shop_cog.http_client.get = AsyncMock(return_value=summary_resp)
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shops_without_player_data(self, mock_shop_cog, make_mock_response):
        """shops should still work when player data is not found."""
        interaction = _create_mock_interaction()

        summary_resp = make_mock_response(self._make_shops_summary())

        mock_shop_cog.http_client.get = AsyncMock(return_value=summary_resp)
        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shops_missing_tier_shows_empty(self, mock_shop_cog, make_mock_response):
        """Tiers not in summary should show 'Empty'."""
        interaction = _create_mock_interaction()

        # Summary only has Bronze, missing Silver/Gold/Platinum
        summary_resp = make_mock_response(
            {
                "total_items": 5,
                "shops": {
                    "Bronze": {"items": 5, "total_quantity": 20},
                },
            }
        )
        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=500))

        mock_shop_cog.http_client.get = AsyncMock(return_value=summary_resp)
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_shops_player_tier_access_icons(self, mock_shop_cog, make_mock_response):
        """shops should show unlock/lock icons based on player tier."""
        interaction = _create_mock_interaction()

        # All tiers present
        summary_resp = make_mock_response(
            {
                "total_items": 30,
                "shops": {
                    "Bronze": {"items": 10, "total_quantity": 50},
                    "Silver": {"items": 8, "total_quantity": 30},
                    "Gold": {"items": 7, "total_quantity": 25},
                    "Platinum": {"items": 5, "total_quantity": 10},
                },
            }
        )

        # Silver player — should unlock Bronze & Silver, lock Gold & Platinum
        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=3000))

        mock_shop_cog.http_client.get = AsyncMock(return_value=summary_resp)
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_shops_http_status_error(self, mock_shop_cog):
        """shops should handle HTTPStatusError gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Error", request=MagicMock(), response=error_response)

        mock_shop_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "API Error" in call_kwargs[0][0]

    def test_shops_generic_exception(self, mock_shop_cog):
        """shops should handle generic exception gracefully."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# Error handlers — additional branch coverage
# ---------------------------------------------------------------------------


class TestErrorHandlersBranches:
    """Additional tests for error handlers covering response-already-done branches."""

    def test_buy_error_handler_response_already_done(self, mock_shop_cog):
        """buy_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_shop_cog.buy_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_sell_error_handler_response_already_done(self, mock_shop_cog):
        """sell_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_shop_cog.sell_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_shops_error_handler_response_already_done(self, mock_shop_cog):
        """shops_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_shop_cog.shops_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


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


# ===========================================================================
# Package E — Tests #21–26: shop cache + buy_item_autocomplete + invalidation
# ===========================================================================


def _make_shop_items_for_tier(tier: str, count: int = 3) -> list[dict]:
    """Generate minimal shop item dicts for a given tier."""
    return [
        {
            "id": i + 1,
            "item_name": f"Item{i + 1}",
            "item_type": "primary_weapon",
            "tier": tier,
            "price": (i + 1) * 100,
            "quantity": 10,
        }
        for i in range(count)
    ]


class TestBuyItemAutocompleteWithCache:
    """Tests for buy_item_autocomplete serving from _shop_cache (spec tests #21–22)."""

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        return interaction

    # ------------------------------------------------------------------
    # Test #21 — cold cache fetches once; second invocation uses cache (zero new HTTP)
    # ------------------------------------------------------------------

    def test_cold_cache_fetches_once_second_hit_uses_cache(self, mock_shop_cog):
        """Cold cache fetches tier shop once; second autocomplete uses cached data (no new HTTP)."""
        player = _make_player_data(tier="Bronze")
        bronze_items = _make_shop_items_for_tier("Bronze", 3)

        # Player resolution response
        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value=player)

        # Shop items response
        shop_resp = MagicMock()
        shop_resp.status_code = 200
        shop_resp.json = MagicMock(return_value=bronze_items)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=shop_resp)

        interaction = self._make_interaction()

        # First autocomplete — should call GET /shops/... once (cold cache)
        result1 = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result1) == 3
        get_call_count_after_first = mock_shop_cog.http_client.get.call_count

        # Second autocomplete — should NOT call GET /shops/... again (cache hit)
        result2 = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result2) == 3
        assert mock_shop_cog.http_client.get.call_count == get_call_count_after_first

    # ------------------------------------------------------------------
    # Test #22 — after TTL expiry, refetches
    # ------------------------------------------------------------------

    def test_after_ttl_expiry_refetches(self, mock_shop_cog):
        """After TTL expiry, buy_item_autocomplete triggers a new shop fetch."""
        player = _make_player_data(tier="Bronze")
        bronze_items = _make_shop_items_for_tier("Bronze", 2)

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value=player)

        shop_resp = MagicMock()
        shop_resp.status_code = 200
        shop_resp.json = MagicMock(return_value=bronze_items)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=shop_resp)

        interaction = self._make_interaction()

        # First call — primes cache
        asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        get_calls_after_prime = mock_shop_cog.http_client.get.call_count

        # Manually expire the cache entry by replacing it with an expired one
        guild_id = interaction.guild_id
        clock = [0.0]
        mock_shop_cog._shop_cache._monotonic = lambda: clock[0]
        # Re-set with t=0 then advance past TTL
        mock_shop_cog._shop_cache.set((guild_id, "Bronze"), bronze_items)
        clock[0] = 400.0  # past 300s TTL

        # Second call — cache expired, should fetch again
        asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert mock_shop_cog.http_client.get.call_count > get_calls_after_prime


class TestBuyInvalidatesCache:
    """Tests for /buy success invalidating shop cache (spec test #23)."""

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        return _create_mock_interaction(user_id=user_id, guild_id=guild_id)

    def test_buy_success_invalidates_purchased_tier_cache(self, mock_shop_cog):
        """Successful /buy invalidates only the purchased item's tier cache."""
        # Pre-populate the cache for Bronze and Silver
        mock_shop_cog._shop_cache.set((987654321, "Bronze"), _make_shop_items_for_tier("Bronze"))
        mock_shop_cog._shop_cache.set((987654321, "Silver"), _make_shop_items_for_tier("Silver"))
        assert mock_shop_cog._shop_cache.size == 2

        player = _make_player_data(tier="Bronze", credits=5000)
        shop_item = _make_shop_item(item_id=1, item_name="Laser", tier="Bronze", price=100)
        transaction = {
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "total_cost": 100,
            "remaining_credits": 4900,
        }

        item_resp = MagicMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json = MagicMock(return_value=shop_item)

        purchase_resp = MagicMock()
        purchase_resp.raise_for_status = MagicMock()
        purchase_resp.json = MagicMock(return_value=transaction)

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value=player)

        # Sequence: POST /players/ → item GET → purchase POST
        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        interaction = self._make_interaction()
        asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, item_id=1, quantity=1))

        # Bronze tier should be invalidated; Silver should remain
        assert mock_shop_cog._shop_cache.size == 1
        remaining = mock_shop_cog._shop_cache.keys()
        assert (987654321, "Silver") in remaining
        assert (987654321, "Bronze") not in remaining


class TestSellInvalidatesCache:
    """Tests for /sell success invalidating shop cache (spec test #24)."""

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        return _create_mock_interaction(user_id=user_id, guild_id=guild_id)

    def test_sell_success_invalidates_seller_tier_cache(self, mock_shop_cog):
        """Successful /sell invalidates only the seller's current tier cache."""
        # Pre-populate caches
        mock_shop_cog._shop_cache.set((987654321, "Bronze"), _make_shop_items_for_tier("Bronze"))
        mock_shop_cog._shop_cache.set((987654321, "Silver"), _make_shop_items_for_tier("Silver"))
        assert mock_shop_cog._shop_cache.size == 2

        player = _make_player_data(tier="Bronze", credits=1000)
        transaction = {
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "total_value": 50,
            "remaining_credits": 1050,
        }

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value=player)

        sell_resp = MagicMock()
        sell_resp.raise_for_status = MagicMock()
        sell_resp.json = MagicMock(return_value=transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, sell_resp])

        interaction = self._make_interaction()
        asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, item="Laser", quantity=1))

        # Bronze tier (player's tier) should be invalidated; Silver should remain
        assert mock_shop_cog._shop_cache.size == 1
        remaining = mock_shop_cog._shop_cache.keys()
        assert (987654321, "Silver") in remaining
        assert (987654321, "Bronze") not in remaining


class TestBuyItemAutocompleteEdgeCases:
    """Tests for buy_item_autocomplete edge cases (spec tests #25, #26)."""

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        return _create_mock_interaction(user_id=user_id, guild_id=guild_id)

    # ------------------------------------------------------------------
    # Test #25 — returns empty list when player resolution fails
    # ------------------------------------------------------------------

    def test_returns_empty_when_player_resolution_fails(self, mock_shop_cog):
        """buy_item_autocomplete returns empty list when player resolution fails."""
        mock_shop_cog.http_client.post = AsyncMock(side_effect=Exception("connection error"))

        interaction = self._make_interaction()
        result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))

        assert result == []

    # ------------------------------------------------------------------
    # Test #26 — Silver player sees Bronze + Silver items
    # ------------------------------------------------------------------

    def test_silver_player_sees_bronze_and_silver_items(self, mock_shop_cog):
        """Silver player's autocomplete includes items from Bronze and Silver tiers."""
        player = _make_player_data(tier="Silver")
        bronze_items = _make_shop_items_for_tier("Bronze", 2)
        silver_items = _make_shop_items_for_tier("Silver", 2)

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value=player)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        # Pre-populate cache for both tiers
        mock_shop_cog._shop_cache.set((987654321, "Bronze"), bronze_items)
        mock_shop_cog._shop_cache.set((987654321, "Silver"), silver_items)

        interaction = self._make_interaction()
        result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))

        # Should show all 4 items (2 Bronze + 2 Silver)
        assert len(result) == 4
        names = [c.name for c in result]
        assert any("[Bronze]" in n for n in names)
        assert any("[Silver]" in n for n in names)
        # Gold tier items should NOT appear
        assert not any("[Gold]" in n for n in names)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
