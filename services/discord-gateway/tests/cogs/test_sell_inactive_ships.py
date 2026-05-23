"""Unit tests for Sub-task C: /sell command includes inactive ships.

Acceptance criteria:
- sell_item_autocomplete includes inactive ships from ships_cache
- Active ships are excluded (is_active=True ships do not appear)
- Ship choices are labeled "Name (inactive ship)"
- Ship choice values are encoded as "ship:<player_ship_id>"
- /sell routes ship choices to /shops/sell-ship endpoint
- /sell regular items still work normally
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    to_evict = [
        k
        for k in sys.modules
        if k == "discord"
        or k.startswith("discord.")
        or k in ("api", "bot")
        or k.startswith("api.")
        or k.startswith("cogs.")
        # Note: we do NOT evict utils.* here so that autocomplete_state
        # stays the same module object before and after cog creation.
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


def _make_cog():
    """Return a ShopCog instance with mocked dependencies."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(return_value=None)

    from cogs.shopCog import ShopCog

    cog = ShopCog(bot)
    cog.http_client = MagicMock()
    return cog


def _make_normalized_choice(label, value, norm, raw):
    """Return a mock NormalizedChoice object."""
    choice = MagicMock()
    choice.label = label
    choice.value = value
    choice.norm = norm
    choice.raw = raw
    return choice


def _make_player_cache(player_id=42, tier="Bronze", guild_id=999):
    """Return a mock AutocompleteCache for player_cache."""
    cache = MagicMock()
    cache.peek = MagicMock(return_value={"id": player_id, "tier": tier})
    cache.schedule_refresh = MagicMock()
    return cache


def _make_inventory_cache_with_item(item_name="Ridil Blaster", item_type="primary_weapon"):
    """Return a mock AutocompleteCache for inventory_cache with one item."""
    from utils.autocomplete_utils import normalize_for_search

    raw = {"item_name": item_name, "item_type": item_type}
    label = f"{item_name} (Primary Weapon)"
    choice = _make_normalized_choice(label, item_name, normalize_for_search(label), raw)
    cache = MagicMock()
    cache.peek = MagicMock(return_value=[choice])
    cache.schedule_refresh = MagicMock()
    return cache


def _make_ships_cache(ships):
    """Return a mock AutocompleteCache for ships_cache."""
    cache = MagicMock()
    cache.peek = MagicMock(return_value=ships)
    cache.schedule_refresh = MagicMock()
    return cache


def _make_interaction(user_id=111, guild_id=999):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


class TestSellAutocompleteIncludesInactiveShips:
    """Tests that sell_item_autocomplete includes inactive ships from ships_cache."""

    @pytest.mark.asyncio
    async def test_inactive_ship_appears_in_autocomplete(self):
        """Inactive ship (is_active=False) appears in sell autocomplete."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        # Set up player cache
        ac_state.player_cache = _make_player_cache(player_id=42)

        # Set up inventory cache (empty for simplicity)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        # Set up ships cache with one inactive ship
        inactive_ship_raw = {
            "is_active": False,
            "ship_name": "Niode",
            "nickname": "My Niode",
            "player_ship_id": 77,
        }
        from utils.autocomplete_utils import normalize_for_search as nfs

        ship_choice = _make_normalized_choice(
            "My Niode (Niode)",
            "77",
            nfs("My Niode (Niode)"),
            inactive_ship_raw,
        )
        ac_state.ships_cache = _make_ships_cache([ship_choice])

        choices = await cog.sell_item_autocomplete(interaction, "")
        choice_values = [c.value for c in choices]
        assert any(v.startswith("ship:") for v in choice_values), (
            "Expected at least one ship: choice in sell autocomplete"
        )

    @pytest.mark.asyncio
    async def test_active_ship_excluded_from_autocomplete(self):
        """Active ship (is_active=True) is excluded from sell autocomplete."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        # Active ship
        active_ship_raw = {
            "is_active": True,
            "ship_name": "Betty",
            "nickname": "",
            "player_ship_id": 1,
        }
        from utils.autocomplete_utils import normalize_for_search as nfs

        ship_choice = _make_normalized_choice("Betty (Betty)", "1", nfs("Betty (Betty)"), active_ship_raw)
        ac_state.ships_cache = _make_ships_cache([ship_choice])

        choices = await cog.sell_item_autocomplete(interaction, "")
        choice_values = [c.value for c in choices]
        assert not any(v.startswith("ship:") for v in choice_values), "Active ship must NOT appear in sell autocomplete"

    @pytest.mark.asyncio
    async def test_ship_choice_value_encoded_correctly(self):
        """Ship choices use 'ship:<player_ship_id>' as value."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        inactive_ship_raw = {
            "is_active": False,
            "ship_name": "Razorback",
            "nickname": "",
            "player_ship_id": 55,
        }
        from utils.autocomplete_utils import normalize_for_search as nfs

        ship_choice = _make_normalized_choice("Razorback (Razorback)", "55", nfs("Razorback"), inactive_ship_raw)
        ac_state.ships_cache = _make_ships_cache([ship_choice])

        choices = await cog.sell_item_autocomplete(interaction, "")
        ship_choices = [c for c in choices if c.value.startswith("ship:")]
        assert len(ship_choices) == 1
        assert ship_choices[0].value == "ship:55"

    @pytest.mark.asyncio
    async def test_ship_choice_label_includes_inactive_marker(self):
        """Ship choices have '(inactive ship)' in their label."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        inactive_ship_raw = {
            "is_active": False,
            "ship_name": "Liberator",
            "nickname": "Freedom",
            "player_ship_id": 88,
        }
        from utils.autocomplete_utils import normalize_for_search as nfs

        ship_choice = _make_normalized_choice("Freedom (Liberator)", "88", nfs("Freedom"), inactive_ship_raw)
        ac_state.ships_cache = _make_ships_cache([ship_choice])

        choices = await cog.sell_item_autocomplete(interaction, "")
        ship_choices = [c for c in choices if c.value.startswith("ship:")]
        assert any("inactive ship" in c.name.lower() for c in ship_choices), (
            "Ship choices should include '(inactive ship)' in label"
        )

    @pytest.mark.asyncio
    async def test_regular_items_still_appear(self):
        """Regular inventory items still appear alongside ships."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)

        # Inventory with one item
        from utils.autocomplete_utils import normalize_for_search as nfs

        inv_raw = {"item_name": "Plasma Cannon", "item_type": "primary_weapon"}
        inv_choice = _make_normalized_choice(
            "Plasma Cannon (Primary Weapon)",
            "Plasma Cannon",
            nfs("Plasma Cannon (Primary Weapon)"),
            inv_raw,
        )
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[inv_choice])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        # Ships cache empty
        ac_state.ships_cache = _make_ships_cache([])

        choices = await cog.sell_item_autocomplete(interaction, "")
        choice_values = [c.value for c in choices]
        assert "Plasma Cannon" in choice_values

    @pytest.mark.asyncio
    async def test_sell_routes_ship_to_sell_ship_endpoint(self):
        """When item starts with 'ship:', /sell posts to /shops/sell-ship."""
        cog = _make_cog()
        interaction = _make_interaction()

        # Mock get_player_data
        cog._get_player_data = AsyncMock(return_value={"id": 42, "tier": "Bronze"})

        # Mock http_client to return a successful sell-ship response
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "player_id": 42,
            "item_type": "ship",
            "item_name": "Niode",
            "quantity": 1,
            "total_value": 8000,
            "remaining_credits": 9000,
            "transaction_type": "ship_sale",
            "items_unequipped_to_inventory": 0,
        }
        cog.http_client.post = AsyncMock(return_value=mock_resp)

        # Mock cache invalidations
        import utils.autocomplete_state as ac_state

        ac_state.invalidate_player = MagicMock()
        ac_state.invalidate_inventory = MagicMock()
        ac_state.invalidate_ships = MagicMock()

        await cog.sell.callback(cog, interaction, item="ship:77", quantity=1)

        # Verify that the sell-ship endpoint was called
        call_args = cog.http_client.post.call_args
        assert "/shops/sell-ship" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_sell_routes_regular_item_to_sell_endpoint(self):
        """When item does NOT start with 'ship:', /sell posts to /shops/sell."""
        cog = _make_cog()
        interaction = _make_interaction()

        cog._get_player_data = AsyncMock(return_value={"id": 42, "tier": "Bronze"})

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "player_id": 42,
            "item_type": "primary_weapon",
            "item_name": "Plasma Cannon",
            "quantity": 1,
            "total_value": 500,
            "remaining_credits": 1500,
            "transaction_type": "sale",
        }
        cog.http_client.post = AsyncMock(return_value=mock_resp)

        import utils.autocomplete_state as ac_state

        ac_state.invalidate_player = MagicMock()
        ac_state.invalidate_inventory = MagicMock()
        ac_state.invalidate_ships = MagicMock()
        cog._shop_cache = MagicMock()
        cog._shop_cache.invalidate = MagicMock()

        await cog.sell.callback(cog, interaction, item="Plasma Cannon", quantity=1)

        call_args = cog.http_client.post.call_args
        assert "/shops/sell" in call_args[0][0]
        assert "/shops/sell-ship" not in call_args[0][0]

    @pytest.mark.asyncio
    async def test_sell_ship_invalid_id_returns_error(self):
        """sell with 'ship:abc' (non-numeric ID) sends error message."""
        cog = _make_cog()
        interaction = _make_interaction()

        cog._get_player_data = AsyncMock(return_value={"id": 42, "tier": "Bronze"})

        await cog.sell.callback(cog, interaction, item="ship:abc", quantity=1)

        # Should have sent an error ephemeral message
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)


class TestSellAutocompleteEdgeCases:
    """Adversarial and edge case tests for sell_item_autocomplete."""

    @pytest.mark.asyncio
    async def test_ships_cache_is_none_does_not_crash(self):
        """When ships_cache is None, autocomplete returns without crashing."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()
        ac_state.ships_cache = None  # No ships cache at all

        # Must not raise
        choices = await cog.sell_item_autocomplete(interaction, "")
        # Should return choices from inventory only (none in this case)
        assert isinstance(choices, list)
        assert not any(v.startswith("ship:") for c in choices for v in [c.value])

    @pytest.mark.asyncio
    async def test_player_cache_miss_returns_empty(self):
        """When player_cache has no entry, autocomplete returns []."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        # Player cache misses
        pc = MagicMock()
        pc.peek = MagicMock(return_value=None)
        pc.schedule_refresh = MagicMock()
        ac_state.player_cache = pc
        ac_state.inventory_cache = MagicMock()
        ac_state.ships_cache = MagicMock()

        choices = await cog.sell_item_autocomplete(interaction, "")
        assert choices == []

    @pytest.mark.asyncio
    async def test_ships_cache_peek_returns_none_handled_gracefully(self):
        """When ships_cache.peek() returns None (cold cache), no ship choices appear."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        # ships_cache exists but peek returns None (cold cache)
        sc = MagicMock()
        sc.peek = MagicMock(return_value=None)
        sc.schedule_refresh = MagicMock()
        ac_state.ships_cache = sc

        choices = await cog.sell_item_autocomplete(interaction, "")
        assert not any(v.startswith("ship:") for c in choices for v in [c.value])

    @pytest.mark.asyncio
    async def test_ship_with_no_player_ship_id_is_skipped(self):
        """Ship choices without a valid player_ship_id are silently skipped."""
        import utils.autocomplete_state as ac_state

        cog = _make_cog()
        interaction = _make_interaction()

        ac_state.player_cache = _make_player_cache(player_id=42)
        ac_state.inventory_cache = MagicMock()
        ac_state.inventory_cache.peek = MagicMock(return_value=[])
        ac_state.inventory_cache.schedule_refresh = MagicMock()

        # Ship with no player_ship_id and no 'id'
        bad_ship_raw = {
            "is_active": False,
            "ship_name": "Ghost",
            "nickname": "Ghost",
            # No player_ship_id and no id key
        }
        from utils.autocomplete_utils import normalize_for_search as nfs

        ship_choice = _make_normalized_choice("Ghost", None, nfs("Ghost"), bad_ship_raw)
        # Also make ship_choice.value return None
        ship_choice.value = None
        sc = _make_ships_cache([ship_choice])
        ac_state.ships_cache = sc

        choices = await cog.sell_item_autocomplete(interaction, "")
        # No ship: choices should appear since ID was missing
        assert not any(v.startswith("ship:") for c in choices for v in [c.value])
