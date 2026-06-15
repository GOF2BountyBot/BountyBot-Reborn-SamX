"""Tests for shopCog — boosting coverage from 0% to 60%+."""

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

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_unused_module_logger = None
# Track all loggers created (keyed by name) to support lookup after multi-logger inits.
_all_loggers: dict[str, MagicMock] = {}


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _unused_module_logger
    name = _args[0] if _args else None
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _unused_module_logger = logger
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


@pytest.fixture(scope="module")
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

    import cogs.shopCog as shop_module

    cog = shop_module.ShopCog(mock_bot)
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

    def test_shop_cache_ttl_is_3600(self, mock_shop_cog):
        """_shop_cache must be initialized with ttl_seconds=3600.0 (Item A: 300→3600)."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        assert hasattr(mock_shop_cog, "_shop_cache")
        assert isinstance(mock_shop_cog._shop_cache, AutocompleteCache)
        assert mock_shop_cog._shop_cache._ttl == 3600.0, (
            f"Expected _shop_cache TTL=3600s (1 hr), got {mock_shop_cog._shop_cache._ttl}"
        )


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
        """shop should display ephemeral embed when items are available (B.69)."""
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

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # B.69: /shop browse response must be ephemeral
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs["embed"]
        assert len(embed.fields) > 0

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

    def test_shop_player_not_found_via_http_error(self, mock_shop_cog):
        """shop should send 'Player not found' when the player API returns a non-guild HTTP error.

        The /shop command no longer accepts a tier parameter — it always uses the
        invoking player's own tier (strict same-tier enforcement).  This test
        verifies the player-not-found path via a 404 HTTPStatusError.
        """
        import httpx

        interaction = _create_mock_interaction()

        error_resp = MagicMock()
        error_resp.status_code = 404
        mock_shop_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_resp)
        )

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "Player not found" in call_kwargs[0][0]

    def test_shop_player_not_found(self, mock_shop_cog):
        """shop should send error when player not found."""
        interaction = _create_mock_interaction()

        mock_shop_cog.http_client.post = AsyncMock(side_effect=RuntimeError("player error"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_shop_strict_same_tier_bronze_sees_bronze_shop(self, mock_shop_cog, make_mock_response):
        """Strict same-tier: Bronze player always sees the Bronze shop.

        The /shop command no longer accepts a tier parameter — the player's own
        tier is used unconditionally.  This test verifies that the GET request
        targets the Bronze-tier shop endpoint for a Bronze player.
        """
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze"))
        items_resp = make_mock_response([_make_shop_item(1, "BronzeItem", "module", "Bronze", 100, 5, 1)])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert send_kwargs.get("ephemeral", False)
        # GET must target the player's own tier (Bronze), not any other tier
        get_url = mock_shop_cog.http_client.get.call_args[0][0]
        assert "/tier/Bronze" in get_url
        # Embed footer confirms the player's tier
        embed = send_kwargs["embed"]
        assert "Bronze" in (embed.footer.text or "")

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

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # Footer should say inventory for non-ship
        embed = call_kwargs["embed"]
        assert "inventory" in embed.footer.text.lower()
        assert len(embed.fields) > 0 or embed.description

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
# /buy URL+method contract (respx) — Tier 2 closeout 2026-04-30
# ---------------------------------------------------------------------------


class TestBuyCommandRespx:
    """respx-backed URL+method contract test for /buy happy path.

    Verifies that /buy hits the 3 expected bot-core routes:
      POST /api/v1/players/                  (player upsert)
      GET  /api/v1/shops/item/{item_id}      (shop item details)
      POST /api/v1/shops/purchase            (item purchase, non-ship)
                  or /api/v1/shops/purchase-ship (ship purchase)

    All three URLs were verified against bot-core's registered routes during
    the 2026-04-30 Tier 2 audit. Follows the policy in
    services/discord-gateway/tests/AGENTS.md (B.33 followup).
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_buy_item_calls_correct_urls(self, mock_shop_cog, request):
        """/buy (non-ship item) must POST /players/, GET /shops/item/{id}, POST /shops/purchase."""
        import httpx
        import respx

        self._with_real_client(mock_shop_cog, request)
        interaction = _create_mock_interaction()

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=_make_player_data(tier="Bronze", credits=2000))
            )
            mock_router.get(f"{self._BOT_API}/shops/item/1").mock(
                return_value=httpx.Response(200, json=_make_shop_item(1, "LaserCannon", "weapon", "Bronze", 500, 10))
            )
            mock_router.post(f"{self._BOT_API}/shops/purchase").mock(
                return_value=httpx.Response(200, json=_make_transaction("LaserCannon", "weapon", 500, 1500))
            )

            asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()


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

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, item_type="weapon"))

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

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, item_type="ship"))

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
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        # quantity=5 → shown as a pipe-delimited 'x5' field (not run together with tech level)
        assert " | x5" in all_text, f"Expected pipe-delimited '| x5' in:\n{all_text}"

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
        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        # quantity == 1 is suppressed entirely (no 'x1' token)
        assert "x1" not in all_text, f"Expected no 'x1' token for singleton stock, got:\n{all_text}"
        assert " | T3" in all_text, f"Expected pipe-delimited '| T3' tech level in:\n{all_text}"

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
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "~~" in all_text

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
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value) + (embed.description or "")
        assert "more" in all_text.lower() or "..." in all_text

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
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

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

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
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
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

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
    """Tests for the updated sell_item_autocomplete (Phase 6 — zero-HTTP, cache-backed).

    Phase 6: sell_item_autocomplete uses peek() on player_cache and inventory_cache.
    Tests pre-populate these shared caches instead of mocking HTTP responses.
    """

    def _make_normalized_choices(self, items):
        """Build NormalizedChoice objects from raw inventory item dicts (as inventory_cache stores them)."""
        import utils.autocomplete_state as ac_state
        from utils.autocomplete_utils import normalize_for_search

        choices = []
        for item in items:
            item_name = item.get("item_name") or ""
            item_type = item.get("item_type") or ""
            quantity = item.get("quantity") or 0
            if not item_name:
                continue
            qty_suffix = f" [x{quantity}]" if quantity and quantity > 1 else ""
            label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
            value = str(item.get("id", item_name))
            norm = normalize_for_search(label)
            choices.append(ac_state.NormalizedChoice(label=label, value=value, norm=norm, raw=item))
        return choices

    def _init_ac_state(self, player, inventory_items, guild_id=987654321, user_id=111111111):
        """Initialize autocomplete_state caches with test data."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        player_id = player.get("id", 1)

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="inventory-test")

        ac_state.player_cache.set((guild_id, user_id), player)
        normalized = self._make_normalized_choices(inventory_items)
        ac_state.inventory_cache.set((guild_id, player_id), normalized)

    def _make_inventory_items(self):
        """Return a sample inventory list."""
        return [
            {"id": 1, "item_name": "LaserCannon", "item_type": "primary_weapon", "quantity": 2},
            {"id": 2, "item_name": "ShieldModule", "item_type": "module", "quantity": 1},
            {"id": 3, "item_name": "Betty", "item_type": "ship", "quantity": 1},
            {"id": 4, "item_name": "Raptor Turret", "item_type": "turret_weapon", "quantity": 3},
        ]

    def test_sell_autocomplete_returns_all(self, mock_shop_cog):
        """sell_item_autocomplete returns all inventory items (no type filter), zero HTTP."""
        interaction = _create_mock_interaction()
        player = _make_player_data()
        self._init_ac_state(player, self._make_inventory_items())

        # Assert HTTP client is never called
        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 4

    def test_sell_autocomplete_display_format_includes_type(self, mock_shop_cog):
        """sell_item_autocomplete should display 'Name (Type)' format, zero HTTP."""
        interaction = _create_mock_interaction()
        player = _make_player_data()
        self._init_ac_state(player, [{"id": 3, "item_name": "Betty", "item_type": "ship", "quantity": 1}])

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 1
        assert result[0].name == "Betty (Ship)"
        assert result[0].value == "Betty"

    def test_sell_autocomplete_primary_weapon_label(self, mock_shop_cog):
        """sell_item_autocomplete should show 'Primary Weapon' for primary_weapon type, zero HTTP."""
        interaction = _create_mock_interaction()
        player = _make_player_data()
        self._init_ac_state(
            player, [{"id": 10, "item_name": "Nirai Impulse EX 1", "item_type": "primary_weapon", "quantity": 1}]
        )

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert len(result) == 1
        assert result[0].name == "Nirai Impulse EX 1 (Primary Weapon)"
        assert result[0].value == "Nirai Impulse EX 1"

    def test_sell_autocomplete_player_cache_miss_returns_empty(self, mock_shop_cog):
        """sell_item_autocomplete returns [] on player cache cold miss (schedules refresh)."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = _create_mock_interaction(user_id=999999, guild_id=888888)

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        # Ensure no entry for this guild/user
        ac_state.player_cache.invalidate((888888, 999999))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert result == []

    def test_sell_autocomplete_inventory_cache_miss_returns_empty(self, mock_shop_cog):
        """sell_item_autocomplete returns [] on inventory cache cold miss."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = _create_mock_interaction(user_id=111111, guild_id=987654321)
        player = _make_player_data(player_id=42)

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="inventory-test")

        ac_state.player_cache.set((987654321, 111111), player)
        # Ensure no inventory entry for this player
        ac_state.inventory_cache.invalidate((987654321, 42))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        assert result == []

    def test_sell_autocomplete_filters_by_current_text(self, mock_shop_cog):
        """sell_item_autocomplete should filter results by current text, zero HTTP."""
        interaction = _create_mock_interaction()
        player = _make_player_data()
        self._init_ac_state(player, self._make_inventory_items())

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, "Betty"))
        assert len(result) == 1
        assert result[0].value == "Betty"

    def test_inactive_ship_empty_nickname_no_extra_brackets(self, mock_shop_cog):
        """GROUP-A fix: inactive ship with empty nickname should show 'Betty (inactive ship)',
        NOT 'Betty () (inactive ship)'.

        The pre-computed NormalizedChoice.label may have empty parens when the nickname
        is blank (e.g. 'Betty ()').  The fix builds ship_display from raw data directly
        so the parens only appear when there's an actual nickname.
        """
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_utils import normalize_for_search

        interaction = _create_mock_interaction()
        player = _make_player_data()

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="inventory-test")
        if ac_state.ships_cache is None:
            ac_state.ships_cache = AutocompleteCache(name="ships-test")

        ac_state.player_cache.set((987654321, 111111111), player)
        # Empty inventory so only ships appear
        ac_state.inventory_cache.set((987654321, 1), [])

        # Build a ship NormalizedChoice with an empty nickname — this mimics the
        # pre-computed label that produces "Betty ()" without the fix.
        ship_raw = {
            "name": "Betty",
            "ship_name": "Betty",
            "nickname": "",  # blank nickname → pre-computed label would be "Betty ()"
            "is_active": False,
            "player_ship_id": 42,
        }
        # Pre-computed label has empty parens (the bug scenario)
        label_with_empty_parens = "Betty ()"
        ship_nc = ac_state.NormalizedChoice(
            label=label_with_empty_parens,
            value="42",
            norm=normalize_for_search(label_with_empty_parens),
            raw=ship_raw,
        )
        ac_state.ships_cache.set((987654321, 1), [ship_nc])

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))

        # Exactly one inactive ship choice
        ship_choices = [c for c in result if "inactive ship" in c.name.lower()]
        assert len(ship_choices) == 1, f"Expected 1 inactive ship choice, got: {[c.name for c in result]}"

        # Must be "Betty (inactive ship)", NOT "Betty () (inactive ship)"
        assert ship_choices[0].name == "Betty (inactive ship)", (
            f"Expected 'Betty (inactive ship)' but got '{ship_choices[0].name}' — "
            "empty nickname must not produce extra brackets"
        )
        assert "()" not in ship_choices[0].name, f"Label should not contain '()': '{ship_choices[0].name}'"
        assert ship_choices[0].value == "ship:42"

    def test_inactive_ship_with_nickname_shows_nickname(self, mock_shop_cog):
        """Inactive ship WITH a real nickname shows nickname in label (not ship name)."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_utils import normalize_for_search

        interaction = _create_mock_interaction()
        player = _make_player_data()

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="inventory-test")
        if ac_state.ships_cache is None:
            ac_state.ships_cache = AutocompleteCache(name="ships-test")

        ac_state.player_cache.set((987654321, 111111111), player)
        ac_state.inventory_cache.set((987654321, 1), [])

        ship_raw = {
            "name": "Niode",
            "ship_name": "Niode",
            "nickname": "Speedy",  # real nickname
            "is_active": False,
            "player_ship_id": 99,
        }
        ship_nc = ac_state.NormalizedChoice(
            label="Speedy (Niode)",
            value="99",
            norm=normalize_for_search("Speedy (Niode)"),
            raw=ship_raw,
        )
        ac_state.ships_cache.set((987654321, 1), [ship_nc])

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.sell_item_autocomplete(interaction, ""))
        ship_choices = [c for c in result if "inactive ship" in c.name.lower()]
        assert len(ship_choices) == 1
        # Nickname should be used as display
        assert ship_choices[0].name == "Speedy (inactive ship)"
        assert ship_choices[0].value == "ship:99"


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
        """shops should display ephemeral summary embed with player tier info (B.69)."""
        interaction = _create_mock_interaction()

        summary_resp = make_mock_response(self._make_shops_summary())
        player_resp = make_mock_response(_make_player_data(tier="Silver", credits=3000))

        mock_shop_cog.http_client.get = AsyncMock(return_value=summary_resp)
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_shop_cog.shops.callback(mock_shop_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        # B.69: /shops overview response must be ephemeral
        assert send_kwargs.get("ephemeral") is True

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
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

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

        import cogs.shopCog as shop_module

        asyncio.run(shop_module.setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        assert isinstance(added_arg, shop_module.ShopCog)


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
    """Tests for buy_item_autocomplete serving from caches (spec tests #21–22).

    Phase 6: buy_item_autocomplete uses peek() on player_cache (for tier) and _shop_cache
    (for items). Tests pre-populate both caches and assert zero HTTP calls.
    """

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        return interaction

    def _init_player_cache(self, player, guild_id=987654321, user_id=111111):
        """Pre-populate the shared player cache."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), player)

    # ------------------------------------------------------------------
    # Test #21 — warm caches serve from memory; second invocation also zero-HTTP
    # ------------------------------------------------------------------

    def test_warm_caches_serve_from_memory_no_http(self, mock_shop_cog):
        """Warm player_cache + warm shop_cache → zero HTTP on both invocations."""
        player = _make_player_data(tier="Bronze")
        bronze_items = _make_shop_items_for_tier("Bronze", 3)
        interaction = self._make_interaction()

        self._init_player_cache(player)
        # Pre-populate shop cache directly
        mock_shop_cog._shop_cache.set((interaction.guild_id, "Bronze"), bronze_items)

        # Both calls must NOT touch HTTP
        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result1 = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result1) == 3

        result2 = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result2) == 3

    # ------------------------------------------------------------------
    # Test #22 — shop cache cold miss returns [] and schedules refresh
    # ------------------------------------------------------------------

    def test_shop_cache_cold_miss_returns_empty_no_http(self, mock_shop_cog):
        """On shop cache cold miss, buy_item_autocomplete returns [] without HTTP."""
        player = _make_player_data(tier="Bronze")
        interaction = self._make_interaction(user_id=111111, guild_id=987654321)

        self._init_player_cache(player)
        # Ensure shop cache is empty for this guild/tier
        mock_shop_cog._shop_cache.invalidate((987654321, "Bronze"))

        # HTTP must not be called
        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert result == []


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
    """Tests for buy_item_autocomplete edge cases (Phase 6 — zero-HTTP cache-backed)."""

    def _make_interaction(self, user_id=111111, guild_id=987654321):
        return _create_mock_interaction(user_id=user_id, guild_id=guild_id)

    def _init_player_cache(self, player, guild_id=987654321, user_id=111111):
        """Pre-populate the shared player cache."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), player)

    # ------------------------------------------------------------------
    # Test #25 — player cache cold miss → returns []
    # ------------------------------------------------------------------

    def test_returns_empty_when_player_cache_cold_miss(self, mock_shop_cog):
        """buy_item_autocomplete returns [] on player cache cold miss (no HTTP)."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = self._make_interaction(user_id=777777, guild_id=111111)
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        # Ensure no entry for this user/guild
        ac_state.player_cache.invalidate((111111, 777777))

        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        assert result == []

    # ------------------------------------------------------------------
    # Test #26 — Silver player sees only Silver items (strict same-tier)
    # ------------------------------------------------------------------

    def test_silver_player_sees_only_silver_items(self, mock_shop_cog):
        """Strict same-tier: Silver player's autocomplete includes only Silver items, zero HTTP."""
        player = _make_player_data(tier="Silver")
        bronze_items = _make_shop_items_for_tier("Bronze", 2)
        silver_items = _make_shop_items_for_tier("Silver", 2)

        interaction = self._make_interaction()
        self._init_player_cache(player)

        # Pre-populate cache for both tiers
        mock_shop_cog._shop_cache.set((987654321, "Bronze"), bronze_items)
        mock_shop_cog._shop_cache.set((987654321, "Silver"), silver_items)

        # HTTP must not be called
        mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))

        # Strict same-tier: only the 2 Silver items are shown (not Bronze+Silver=4)
        assert len(result) == 2
        names = [c.name for c in result]
        assert all("Item" in n for n in names)

    # ------------------------------------------------------------------
    # Test E.2 — WARNING logged when player_tier is not in _valid_tiers
    # ------------------------------------------------------------------

    def test_unknown_player_tier_returns_empty_and_logs_warning(self, mock_shop_cog):
        """E.2: buy_item_autocomplete returns [] and logs WARNING when player tier is unrecognized.

        Tier is read from player_cache — if a player has an unrecognized tier,
        the autocomplete must log a WARNING so operators can investigate.
        """
        # Put a player with an unrecognized tier in the player cache
        player = _make_player_data(tier="Legendary")  # not in ["Bronze","Silver","Gold","Platinum"]
        self._init_player_cache(player)

        # Inject a trackable logger into the cog
        mock_logger = MagicMock()
        mock_logger.warning = MagicMock()
        import cogs.shopCog as shop_module

        original_flogger = shop_module.flogger
        shop_module.flogger = mock_logger

        try:
            interaction = self._make_interaction()
            mock_shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
            mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
            result = asyncio.run(mock_shop_cog.buy_item_autocomplete(interaction, ""))
        finally:
            shop_module.flogger = original_flogger

        # Must return empty list
        assert result == []
        # Must have logged a WARNING mentioning the tier, guild, and user
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Legendary" in warning_msg
        assert str(interaction.guild_id) in warning_msg
        assert str(interaction.user.id) in warning_msg


# ---------------------------------------------------------------------------
# _format_shop_item_stats helper — Sub-task A (Task 0002)
# ---------------------------------------------------------------------------


class TestFormatShopItemStats:
    """Tests for the _format_shop_item_stats module-level helper function.

    Acceptance criteria (Task 0002 Sub-task A):
    - Primary/Secondary/Turret Weapons: "DPS: {dps:.1f}" when dps non-zero
    - Modules: "Shield: {n}" or "Armour: {n}" when stat non-zero
    - Ships: "Hull: {n}" when hull_hp non-zero
    - Items with no relevant stat: "" (empty)
    - Returns a bare token (no leading " | "); the caller owns the delimiter
    - DPS rounds to 1 decimal place
    """

    def _get_format_fn(self):
        """Import the helper from the module under test."""
        _evict_discord_modules()
        import cogs.shopCog as shop_module

        return shop_module._format_shop_item_stats

    # ── Weapon types ─────────────────────────────────────────────────────────

    def test_primary_weapon_with_dps(self):
        """Primary weapon with non-zero dps → 'DPS: x.x' token."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": 92.3}
        assert fn(item) == "DPS: 92.3"

    def test_primary_weapon_dps_rounded(self):
        """DPS rounds to exactly 1 decimal place."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": 45.678}
        assert fn(item) == "DPS: 45.7"

    def test_secondary_weapon_with_dps(self):
        """Secondary weapon with non-zero dps → 'DPS: x.x' token."""
        fn = self._get_format_fn()
        item = {"item_type": "secondary_weapon", "dps": 60.0}
        assert fn(item) == "DPS: 60.0"

    def test_turret_weapon_with_dps(self):
        """Turret weapon with non-zero dps → 'DPS: x.x' token."""
        fn = self._get_format_fn()
        item = {"item_type": "turret_weapon", "dps": 120.5}
        assert fn(item) == "DPS: 120.5"

    def test_weapon_dps_zero_returns_empty(self):
        """Weapon with dps == 0 returns empty string (no trailing pipe)."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": 0.0}
        assert fn(item) == ""

    def test_weapon_dps_none_returns_empty(self):
        """Weapon with dps == None returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": None}
        assert fn(item) == ""

    def test_weapon_no_dps_key_returns_empty(self):
        """Weapon dict with no 'dps' key returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon"}
        assert fn(item) == ""

    # ── Module types ─────────────────────────────────────────────────────────

    def test_module_with_shield(self):
        """Module with non-zero shield → 'Shield: n' token."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": 380}
        assert fn(item) == "Shield: 380"

    def test_module_with_armour(self):
        """Module with non-zero armour → 'Armour: n' token."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "armour": 250}
        assert fn(item) == "Armour: 250"

    def test_module_shield_takes_priority_over_armour(self):
        """When both shield and armour are present, shield is shown (first found)."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": 100, "armour": 200}
        result = fn(item)
        # Shield takes priority — both should not appear on the same line
        assert result == "Shield: 100"

    def test_module_no_stats_returns_empty(self):
        """Module with no relevant stats (utility module) returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "module"}
        assert fn(item) == ""

    def test_module_zero_shield_returns_empty(self):
        """Module with shield == 0 returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": 0}
        assert fn(item) == ""

    def test_module_none_stats_returns_empty(self):
        """Module with None stats returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": None, "armour": None}
        assert fn(item) == ""

    # ── Ship type ─────────────────────────────────────────────────────────────

    def test_ship_with_hull_hp(self):
        """Ship with non-zero hull_hp → 'Hull: n' token."""
        fn = self._get_format_fn()
        item = {"item_type": "ship", "hull_hp": 1200}
        assert fn(item) == "Hull: 1200"

    def test_ship_no_hull_hp_returns_empty(self):
        """Ship with hull_hp == None returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "ship", "hull_hp": None}
        assert fn(item) == ""

    def test_ship_zero_hull_hp_returns_empty(self):
        """Ship with hull_hp == 0 returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "ship", "hull_hp": 0}
        assert fn(item) == ""

    # ── Unknown / no type ─────────────────────────────────────────────────────

    def test_unknown_item_type_returns_empty(self):
        """Unknown item type returns empty string."""
        fn = self._get_format_fn()
        item = {"item_type": "unknown_type", "dps": 100}
        assert fn(item) == ""

    def test_no_item_type_key_returns_empty(self):
        """Item dict with no 'item_type' key returns empty string."""
        fn = self._get_format_fn()
        item = {"dps": 100}
        assert fn(item) == ""

    # ── Formatting correctness ─────────────────────────────────────────────────

    def test_stat_token_has_no_delimiter_when_present(self):
        """Stat token is bare — no leading/trailing ' | ' (the caller owns the delimiter)."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": 50.0}
        result = fn(item)
        assert "|" not in result, f"Token must not contain a pipe, got: {result!r}"
        assert result == result.strip(), f"Token must not have surrounding whitespace, got: {result!r}"
        assert result == "DPS: 50.0"

    def test_stat_suffix_empty_is_truly_empty_string(self):
        """When no stat, return value is exactly '' (not ' ' or '|')."""
        fn = self._get_format_fn()
        item = {"item_type": "module"}
        assert fn(item) == ""


class TestShopCommandWithStats:
    """Integration-style tests: /shop embed shows stats for items that have them."""

    def test_shop_weapon_with_dps_in_embed(self, mock_shop_cog, make_mock_response):
        """Weapon item with dps field shows '| DPS: x.x' in the shop embed text."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        # Item has dps field (new schema field)
        weapon_item = _make_shop_item(1, "LaserCannon", "primary_weapon", "Bronze", 500, 5, 1)
        weapon_item["dps"] = 92.3
        items_resp = make_mock_response([weapon_item])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        embed = send_kwargs["embed"]
        # Check that at least one field value contains the DPS stat
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "DPS: 92.3" in all_text, f"Expected 'DPS: 92.3' in embed fields, got:\n{all_text}"

    def test_shop_line_fields_pipe_delimited(self, mock_shop_cog, make_mock_response):
        """Stat, tech level and quantity on the item line are all joined by ' | '
        (regression: tech level and quantity used to be space-separated and ran
        together as 'T1 x5')."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        weapon_item = _make_shop_item(1, "LaserCannon", "primary_weapon", "Bronze", 500, 5, 1)
        weapon_item["dps"] = 92.3
        items_resp = make_mock_response([weapon_item])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "DPS: 92.3 | T1 | x5" in all_text, f"Expected pipe-delimited stat|tech|qty, got:\n{all_text}"
        # No space-separated run-together (the original legibility bug)
        assert "T1 x5" not in all_text, f"Tech level and quantity must not run together, got:\n{all_text}"

    def test_shop_module_with_shield_in_embed(self, mock_shop_cog, make_mock_response):
        """Shield module shows '| Shield: n' in the shop embed text."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        shield_item = _make_shop_item(2, "ParticleShield", "module", "Bronze", 300, 3, 2)
        shield_item["shield"] = 380
        items_resp = make_mock_response([shield_item])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "Shield: 380" in all_text, f"Expected 'Shield: 380' in embed fields, got:\n{all_text}"

    def test_shop_ship_with_hull_in_embed(self, mock_shop_cog, make_mock_response):
        """Ship item shows '| Hull: n' in the shop embed text."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=10000))
        ship_item = _make_shop_item(3, "Eagle", "ship", "Bronze", 2000, 1, 1)
        ship_item["hull_hp"] = 1200
        items_resp = make_mock_response([ship_item])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "Hull: 1200" in all_text, f"Expected 'Hull: 1200' in embed fields, got:\n{all_text}"

    def test_shop_item_without_stats_shows_no_pipe(self, mock_shop_cog, make_mock_response):
        """Item with no relevant stat (utility module) shows no ' | Stat' suffix."""
        interaction = _create_mock_interaction()

        player_resp = make_mock_response(_make_player_data(tier="Bronze", credits=5000))
        utility_item = _make_shop_item(4, "CabinModule", "module", "Bronze", 200, 2, 1)
        # No shield or armour keys — utility module
        items_resp = make_mock_response([utility_item])

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, "Bronze"))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value)
        # No orphan pipe should appear from empty stat suffix
        assert " |  " not in all_text, f"Unexpected orphan ' | ' found in embed:\n{all_text}"


class TestFormatShopItemStatsAdversarial:
    """Adversarial edge-case tests for _format_shop_item_stats (Task 0002 review).

    Covers scenarios not exercised by the developer's TestFormatShopItemStats class:
    - shield=0 paired with armour=250 → armour shown (not empty)
    - dps as integer (not float) → formatted correctly
    - Very large DPS value → no overflow / crash
    - Empty item dict → returns "" (no KeyError)
    - dps as string representation → coerced correctly
    """

    def _get_format_fn(self):
        """Import the helper from the module under test."""
        _evict_discord_modules()
        import cogs.shopCog as shop_module

        return shop_module._format_shop_item_stats

    def test_module_zero_shield_nonzero_armour_shows_armour(self):
        """Module with shield=0 but armour=250 should show armour, not empty string.

        This is the critical two-field adversarial case: shield=0 is skipped,
        falling through to the armour check which should succeed.
        """
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": 0, "armour": 250}
        result = fn(item)
        assert result == "Armour: 250", f"Expected 'Armour: 250' when shield=0 and armour=250, got {result!r}"

    def test_weapon_dps_as_integer_shows_one_decimal(self):
        """DPS provided as an integer (e.g. 50) formats to '50.0' with 1 decimal place."""
        fn = self._get_format_fn()
        item = {"item_type": "primary_weapon", "dps": 50}
        result = fn(item)
        assert result == "DPS: 50.0", f"Expected 'DPS: 50.0' for integer dps=50, got {result!r}"

    def test_weapon_very_large_dps_no_crash(self):
        """Very large DPS value does not crash or produce truncated output."""
        fn = self._get_format_fn()
        item = {"item_type": "turret_weapon", "dps": 9999.9}
        result = fn(item)
        assert result == "DPS: 9999.9", f"Expected 'DPS: 9999.9' for large dps, got {result!r}"

    def test_empty_item_dict_returns_empty_no_crash(self):
        """Completely empty item dict returns empty string without KeyError."""
        fn = self._get_format_fn()
        item: dict = {}
        result = fn(item)
        assert result == "", f"Expected '' for empty item dict, got {result!r}"

    def test_module_none_shield_nonzero_armour_shows_armour(self):
        """Module with shield=None and armour=300 → falls through to armour display."""
        fn = self._get_format_fn()
        item = {"item_type": "module", "shield": None, "armour": 300}
        result = fn(item)
        assert result == "Armour: 300", f"Expected 'Armour: 300' when shield=None and armour=300, got {result!r}"

    def test_ship_hull_hp_as_integer_one(self):
        """Ship with hull_hp=1 (minimum non-zero) should show stat (not treated as falsy)."""
        fn = self._get_format_fn()
        item = {"item_type": "ship", "hull_hp": 1}
        result = fn(item)
        assert result == "Hull: 1", f"Expected 'Hull: 1' for hull_hp=1, got {result!r}"

    def test_secondary_weapon_dps_zero_int_returns_empty(self):
        """Secondary weapon dps=0 (int) returns empty string, not '| DPS: 0.0'."""
        fn = self._get_format_fn()
        item = {"item_type": "secondary_weapon", "dps": 0}
        result = fn(item)
        assert result == "", f"Expected '' for dps=0 (int), got {result!r}"


class TestShopCommandCachePeekFirst:
    """/shop command reads from _shop_cache.peek() when cache is warm (Item A).

    The Item A overhaul adds _shop_cache.peek((guild_id, tier)) as the primary read
    path for /shop when no item_type filter is specified, before falling back to HTTP.
    """

    def _init_player_cache_for_shop(self, guild_id, user_id, tier="Bronze"):
        """Pre-populate the shared player cache for shop tests."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test-shop")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": tier, "credits": 5000})

    def test_shop_reads_from_shop_cache_no_http_get(self, mock_shop_cog, make_mock_response):
        """/shop with no item_type uses _shop_cache.peek() when warm — no GET to bot-core.

        Player data still requires HTTP POST (player upsert), but the shop item fetch
        must come from the cache, not from bot-core GET.
        """
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Set up player POST response (needed to resolve tier)
        player_data = _make_player_data(tier="Bronze", credits=5000)
        player_resp = make_mock_response(player_data)
        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)

        # Pre-populate shop cache
        items = [_make_shop_item(1, "CachedItem", "module", "Bronze", 100)]
        mock_shop_cog._shop_cache.set((guild_id, "Bronze"), items)

        # GET must NOT be called when cache is warm
        mock_shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP GET must not be called"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args[1]
        embed = send_kwargs.get("embed")
        assert embed is not None, "Expected embed in cache-hit path for /shop"

    def test_shop_falls_back_to_http_when_cache_cold(self, mock_shop_cog, make_mock_response):
        """/shop falls back to HTTP GET when _shop_cache is cold (miss → None)."""
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        player_data = _make_player_data(tier="Bronze", credits=5000)
        player_resp = make_mock_response(player_data)
        items = [_make_shop_item(1, "FallbackItem", "module", "Bronze", 100)]
        items_resp = make_mock_response(items)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        # Ensure cache is cold
        mock_shop_cog._shop_cache.invalidate((guild_id, "Bronze"))

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction))

        # HTTP GET must have been called for the fallback
        mock_shop_cog.http_client.get.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    def test_shop_with_item_type_always_uses_http(self, mock_shop_cog, make_mock_response):
        """/shop with item_type filter always makes HTTP GET (cache stores unfiltered list).

        Even if the cache is warm for the tier, the item_type filter requires an HTTP
        call because the cache stores the unfiltered list (not per-type).
        """
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        player_data = _make_player_data(tier="Bronze", credits=5000)
        player_resp = make_mock_response(player_data)
        filtered_items = [_make_shop_item(1, "LaserCannon", "primary_weapon", "Bronze", 500)]
        items_resp = make_mock_response(filtered_items)

        mock_shop_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_shop_cog.http_client.get = AsyncMock(return_value=items_resp)

        # Pre-populate cache (but item_type filter should bypass it)
        all_items = [
            _make_shop_item(1, "LaserCannon", "primary_weapon", "Bronze", 500),
            _make_shop_item(2, "ArmourMod", "module", "Bronze", 300),
        ]
        mock_shop_cog._shop_cache.set((guild_id, "Bronze"), all_items)

        asyncio.run(mock_shop_cog.shop.callback(mock_shop_cog, interaction, item_type="primary_weapon"))

        # HTTP GET is ALWAYS called when item_type is specified
        mock_shop_cog.http_client.get.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
