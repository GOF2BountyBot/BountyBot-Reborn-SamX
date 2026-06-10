"""Phase 5a: Cache invalidation wiring tests across 6 cogs.

Tests verify that command success paths call the correct autocomplete_state
invalidation functions. Failures in cache invalidation must NEVER abort a
successful command (AC-ROB-3).
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.exception = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _close_coro(coro):
    """Close a coroutine to prevent event-loop accumulation."""
    coro.close()
    return MagicMock()


def _evict_discord_modules():
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
    interaction = DiscordMockUtils.create_mock_interaction(user_id=user_id, guild_id=guild_id)
    interaction.guild_id = guild_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.jpg"
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_response(json_data, status_code=200):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    return resp


def _make_player(player_id=1, tier="Bronze", credits=1000, user_id=111111111):
    return {
        "id": player_id,
        "discord_id": user_id,
        "guild_id": 987654321,
        "tier": tier,
        "xp": 100,
        "credits": credits,
        "lifetime_credits": credits,
        "prestige_count": 0,
        "systems_checked": 0,
        "created_at": "2024-01-01T00:00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture
def mock_player_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.playerCog import PlayerCog

    cog = PlayerCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_shop_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.shopCog import ShopCog

    cog = ShopCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_inventory_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.inventoryCog import InventoryCog

    cog = InventoryCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_ships_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.shipsCog import ShipsCog

    cog = ShipsCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_duel_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.duelCog import DuelCog

    cog = DuelCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def mock_dev_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.devCog import DevCog

    cog = DevCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# playerCog tests
# ---------------------------------------------------------------------------


class TestPlayerCogCacheInvalidation:
    """Cache invalidation tests for playerCog."""

    def test_profile_success_calls_set_player(self, mock_player_cog):
        """_display_profile: after success, set_player is called with fresh data."""
        interaction = _create_mock_interaction()
        player_data = _make_player()
        stats_data = {
            "bounty_stats": {"bounty_wins": 0},
            "duel_stats": {"wins": 0, "losses": 0, "win_rate": 0.0},
        }

        player_resp = _make_response(player_data)
        stats_resp = _make_response(stats_data)
        config_resp = _make_response({"bounty_hunter_role_id": None, "bronze_role_id": None})
        promo_resp = _make_response({"can_promote": False, "next_tier": "Silver", "xp_threshold_for_next": 500})

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        with patch("utils.autocomplete_state.set_player") as mock_set:
            asyncio.run(mock_player_cog._display_profile(interaction))

        mock_set.assert_called_once_with(interaction.guild_id, interaction.user.id, player_data)

    def test_prestige_success_invalidates_player_inventory_ships(self, mock_player_cog):
        """prestige: after success, invalidates player, inventory, and ships."""
        interaction = _create_mock_interaction()
        player_data = _make_player(tier="Platinum")
        prestige_data = {
            "prestige_count": 1,
            "tier_before": "Platinum",
        }

        from cogs._shared.confirm_view import ConfirmView

        async def mock_wait(self):
            self.result = True

        player_resp = _make_response(player_data)
        prestige_resp = _make_response(prestige_data)
        config_resp = _make_response({})

        mock_player_cog.http_client.post = AsyncMock(side_effect=[player_resp, prestige_resp])
        mock_player_cog.http_client.get = AsyncMock(return_value=config_resp)

        with (
            patch.object(ConfirmView, "wait", mock_wait),
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)
        mock_inv_inv.assert_called_once_with(interaction.guild_id, player_data["id"])
        mock_inv_ships.assert_called_once_with(interaction.guild_id, player_data["id"])

    def test_promote_success_invalidates_player(self, mock_player_cog):
        """promote: after success, invalidates player cache."""
        interaction = _create_mock_interaction()
        player_data = _make_player(tier="Bronze")
        status_data = {"can_promote": True, "next_tier": "Silver", "on_cooldown": False, "xp": 100}
        promote_data = {"new_tier": "Silver", "old_tier": "Bronze", "xp": 100, "eligible_for_next": False}
        config_resp = _make_response({})

        from cogs._shared.confirm_view import ConfirmView

        async def mock_wait(self):
            self.result = True

        player_resp = _make_response(player_data)
        status_resp = _make_response(status_data)
        preflight_resp = _make_response({"verdict": "green", "sims_run": 20, "player_win_rate": 0.8})
        promote_resp = _make_response(promote_data)

        # promote uses POST for player upsert, PUT for /promote, GET for status/preflight/config
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(return_value=promote_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[status_resp, preflight_resp, config_resp])

        with (
            patch.object(ConfirmView, "wait", mock_wait),
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
        ):
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)

    def test_demote_success_invalidates_player(self, mock_player_cog):
        """demote: after success, invalidates player cache."""
        interaction = _create_mock_interaction()
        player_data = _make_player(tier="Silver")
        status_data = {"on_cooldown": False}
        demote_data = {"old_tier": "Silver", "new_tier": "Bronze", "xp": 100}
        config_resp = _make_response({})

        from cogs._shared.confirm_view import ConfirmView

        async def mock_wait(self):
            self.result = True

        player_resp = _make_response(player_data)
        status_resp = _make_response(status_data)
        demote_resp = _make_response(demote_data)

        # demote uses POST for player upsert, PUT for /demote endpoint, GET for status/config
        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(return_value=demote_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[status_resp, config_resp])

        with (
            patch.object(ConfirmView, "wait", mock_wait),
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
        ):
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)

    def test_profile_cache_failure_does_not_fail_command(self, mock_player_cog):
        """AC-ROB-3: cache set_player failure must not abort profile command."""
        interaction = _create_mock_interaction()
        player_data = _make_player()
        stats_data = {
            "bounty_stats": {"bounty_wins": 0},
            "duel_stats": {"wins": 0, "losses": 0, "win_rate": 0.0},
        }

        player_resp = _make_response(player_data)
        stats_resp = _make_response(stats_data)
        promo_resp = _make_response({"can_promote": False, "next_tier": "Silver"})
        config_resp = _make_response({})

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[stats_resp, promo_resp, config_resp])

        with patch("utils.autocomplete_state.set_player", side_effect=RuntimeError("cache down")):
            # Should not raise — warning logged, command succeeds
            asyncio.run(mock_player_cog._display_profile(interaction))

        # followup.send was called (success message), not an error
        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# shopCog tests
# ---------------------------------------------------------------------------


class TestShopCogCacheInvalidation:
    """Cache invalidation tests for shopCog."""

    def test_buy_success_invalidates_player_and_inventory(self, mock_shop_cog):
        """buy: after success, invalidates player and inventory caches."""
        interaction = _create_mock_interaction()
        player_data = _make_player(credits=5000)
        shop_item = {
            "id": 1,
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "tier": "Bronze",
            "price": 500,
            "quantity": 10,
        }
        transaction = {
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "total_cost": 500,
            "remaining_credits": 4500,
        }
        player_resp = _make_response(player_data)
        item_resp = _make_response(shop_item)
        purchase_resp = _make_response(transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        with (
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)
        mock_inv_inv.assert_called_once_with(interaction.guild_id, player_data["id"])
        mock_inv_ships.assert_not_called()  # not a ship purchase

    def test_buy_ship_also_invalidates_ships(self, mock_shop_cog):
        """buy ship: also invalidates ships cache."""
        interaction = _create_mock_interaction()
        player_data = _make_player(credits=50000)
        shop_item = {
            "id": 2,
            "item_name": "Betty",
            "item_type": "ship",
            "tier": "Bronze",
            "price": 10000,
            "quantity": 5,
        }
        transaction = {"item_name": "Betty", "item_type": "ship", "total_cost": 10000, "remaining_credits": 40000}
        player_resp = _make_response(player_data)
        item_resp = _make_response(shop_item)
        purchase_resp = _make_response(transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        with (
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 2, 1))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)
        mock_inv_inv.assert_called_once_with(interaction.guild_id, player_data["id"])
        mock_inv_ships.assert_called_once_with(interaction.guild_id, player_data["id"])

    def test_sell_success_invalidates_player_and_inventory(self, mock_shop_cog):
        """sell: after success, invalidates player and inventory caches."""
        interaction = _create_mock_interaction()
        player_data = _make_player(credits=1000)
        transaction = {"item_type": "primary_weapon", "total_value": 400, "remaining_credits": 1400}

        player_resp = _make_response(player_data)
        sell_resp = _make_response(transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, sell_resp])

        with (
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
        ):
            asyncio.run(mock_shop_cog.sell.callback(mock_shop_cog, interaction, "Laser", 1))

        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)
        mock_inv_inv.assert_called_once_with(interaction.guild_id, player_data["id"])

    def test_buy_cache_invalidation_failure_does_not_fail_command(self, mock_shop_cog):
        """AC-ROB-3: buy cache invalidation failure must not abort command."""
        interaction = _create_mock_interaction()
        player_data = _make_player(credits=5000)
        shop_item = {
            "id": 1,
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "tier": "Bronze",
            "price": 500,
            "quantity": 10,
        }
        transaction = {
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "total_cost": 500,
            "remaining_credits": 4500,
        }

        player_resp = _make_response(player_data)
        item_resp = _make_response(shop_item)
        purchase_resp = _make_response(transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        with patch("utils.autocomplete_state.invalidate_player", side_effect=RuntimeError("cache down")):
            # Must not raise
            asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        # Success embed was sent
        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# inventoryCog tests
# ---------------------------------------------------------------------------


class TestInventoryCogCacheInvalidation:
    """Cache invalidation tests for inventoryCog."""

    def test_equip_success_invalidates_inventory_and_ships(self, mock_inventory_cog):
        """equip (ok path): after success, invalidates inventory and ships caches."""
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        ships_resp = _make_response([active_ship])
        check_resp = _make_response({"status": "ok", "equipment_type": "weapons"})
        equip_resp = _make_response(
            {"id": 10, "ship_name": "Betty", "nickname": None, "weapons": ["Laser"], "modules": [], "turrets": []}
        )

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[player_resp, check_resp, equip_resp])
        mock_inventory_cog.http_client.get = AsyncMock(return_value=ships_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_inventory_cog.equip.callback(mock_inventory_cog, interaction, "Laser"))

        mock_inv_inv.assert_called_once_with(interaction.guild_id, 1)
        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)

    def test_unequip_success_invalidates_inventory_and_ships(self, mock_inventory_cog):
        """unequip (single item): after success, invalidates inventory and ships caches."""
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        ships_resp = _make_response([active_ship])
        unequip_resp = _make_response({"id": 10, "ship_name": "Betty", "nickname": None})

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[player_resp, unequip_resp])
        mock_inventory_cog.http_client.get = AsyncMock(return_value=ships_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_inventory_cog.unequip.callback(mock_inventory_cog, interaction, "Laser"))

        mock_inv_inv.assert_called_once_with(interaction.guild_id, 1)
        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)

    def test_unequip_all_invalidates_inventory_and_ships_once(self, mock_inventory_cog):
        """_unequip_all: invalidates caches once at end, not per-item."""
        interaction = _create_mock_interaction()
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        loadout = {"weapons": ["Laser", "Plasma"], "modules": [], "turrets": [], "secondary_weapons": []}
        loadout_resp = _make_response(loadout)
        unequip_resp = _make_response({"id": 10, "ship_name": "Betty"})

        mock_inventory_cog.http_client.post = AsyncMock(return_value=unequip_resp)
        mock_inventory_cog.http_client.get = AsyncMock(return_value=loadout_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_inventory_cog._unequip_all(interaction, player_id=1, ship_id=10, active_ship=active_ship))

        # Called once total (not once per item)
        mock_inv_inv.assert_called_once_with(interaction.guild_id, 1)
        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)

    def test_give_item_invalidates_both_players_inventory(self, mock_inventory_cog):
        """give item: invalidates inventory for both giver and recipient."""
        import discord as dc

        interaction = _create_mock_interaction(user_id=111)
        target = MagicMock(spec=dc.Member)
        target.id = 222
        target.display_name = "Target"
        target.mention = "<@222>"

        source_player = _make_player(player_id=1, user_id=111)
        target_player = _make_player(player_id=2, user_id=222)

        source_resp = _make_response(source_player)
        target_resp = _make_response(target_player)
        transfer_resp = _make_response({})
        transfer_resp.status_code = 200

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[source_resp, target_resp, transfer_resp])

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(
                mock_inventory_cog.give.callback(
                    mock_inventory_cog,
                    interaction,
                    target,
                    "item",
                    None,
                    "Laser::primary_weapon",
                    None,
                )
            )

        # Both players' inventories should be invalidated
        calls = [call.args for call in mock_inv_inv.call_args_list]
        assert (interaction.guild_id, source_player["id"]) in calls
        assert (interaction.guild_id, target_player["id"]) in calls
        mock_inv_ships.assert_not_called()

    def test_give_credits_invalidates_both_players_player(self, mock_inventory_cog):
        """give credits: invalidates the player cache for BOTH giver and recipient.

        Regression guard for the give-bug fix — the recipient's player cache was
        previously not invalidated, so a stale credit balance could linger.
        """
        import discord as dc

        interaction = _create_mock_interaction(user_id=111)
        target = MagicMock(spec=dc.Member)
        target.id = 222
        target.display_name = "Target"
        target.mention = "<@222>"

        source_player = _make_player(player_id=1, user_id=111, credits=1000)
        target_player = _make_player(player_id=2, user_id=222, credits=0)

        source_resp = _make_response(source_player)
        target_resp = _make_response(target_player)
        transfer_resp = _make_response({})
        transfer_resp.status_code = 200

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[source_resp, target_resp, transfer_resp])

        with patch("utils.autocomplete_state.invalidate_player") as mock_inv_player:
            asyncio.run(
                mock_inventory_cog.give.callback(
                    mock_inventory_cog,
                    interaction,
                    target,
                    "credits",
                    500,
                    None,
                    None,
                )
            )

        # Both players' player caches invalidated (keyed by Discord user id).
        calls = [call.args for call in mock_inv_player.call_args_list]
        assert (interaction.guild_id, interaction.user.id) in calls  # giver
        assert (interaction.guild_id, target.id) in calls  # recipient (the previously-missing side)

    def test_equip_cache_invalidation_failure_does_not_fail_command(self, mock_inventory_cog):
        """AC-ROB-3: equip cache invalidation failure must not abort command."""
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        ships_resp = _make_response([active_ship])
        check_resp = _make_response({"status": "ok", "equipment_type": "weapons"})
        equip_resp = _make_response(
            {"id": 10, "ship_name": "Betty", "nickname": None, "weapons": ["Laser"], "modules": [], "turrets": []}
        )

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[player_resp, check_resp, equip_resp])
        mock_inventory_cog.http_client.get = AsyncMock(return_value=ships_resp)

        with patch("utils.autocomplete_state.invalidate_inventory", side_effect=RuntimeError("cache down")):
            # Should not raise
            asyncio.run(mock_inventory_cog.equip.callback(mock_inventory_cog, interaction, "Laser"))

        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# shipsCog tests
# ---------------------------------------------------------------------------


class TestShipsCogCacheInvalidation:
    """Cache invalidation tests for shipsCog."""

    def test_setactive_invalidates_ships_and_player(self, mock_ships_cog):
        """setactive: after success, invalidates ships and player caches."""
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        ship_data = {"id": 10, "ship_name": "Betty", "nickname": None, "is_active": True}
        ship_resp = _make_response(ship_data)

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=ship_resp)

        with (
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
        ):
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, "10"))

        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)
        mock_inv_player.assert_called_once_with(interaction.guild_id, interaction.user.id)

    def test_nickname_invalidates_ships(self, mock_ships_cog):
        """nickname: after success, invalidates ships cache."""
        interaction = _create_mock_interaction()
        ship_data = {"id": 10, "ship_name": "Betty", "player_id": 1, "is_active": True}
        get_ship_resp = _make_response(ship_data)
        player_resp = _make_response({"id": 1})
        updated_ship = {"ship_name": "Betty", "nickname": "Speedy", "is_active": True}
        nick_resp = _make_response(updated_ship)

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.get = AsyncMock(return_value=get_ship_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=nick_resp)

        with patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships:
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, "10", "Speedy"))

        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)

    def test_setactive_cache_invalidation_failure_does_not_fail_command(self, mock_ships_cog):
        """AC-ROB-3: setactive cache invalidation failure must not abort command."""
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        ship_data = {"id": 10, "ship_name": "Betty", "nickname": None, "is_active": True}
        ship_resp = _make_response(ship_data)

        mock_ships_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_ships_cog.http_client.put = AsyncMock(return_value=ship_resp)

        with patch("utils.autocomplete_state.invalidate_ships", side_effect=RuntimeError("cache down")):
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, "10"))

        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# duelCog tests
# ---------------------------------------------------------------------------


class TestDuelCogCacheInvalidation:
    """Cache invalidation tests for duelCog."""

    def test_duel_cog_has_pending_and_outgoing_caches(self, mock_duel_cog):
        """DuelCog should have _pending_duel_cache and _outgoing_duel_cache."""
        assert hasattr(mock_duel_cog, "_pending_duel_cache")
        assert hasattr(mock_duel_cog, "_outgoing_duel_cache")

    def test_duel_challenge_invalidates_outgoing_and_pending(self, mock_duel_cog):
        """duel_challenge: invalidates challenger's outgoing and target's pending caches."""
        import discord as dc

        interaction = _create_mock_interaction(user_id=111)
        target = MagicMock(spec=dc.User)
        target.id = 222
        target.display_name = "Target"
        target.mention = "<@222>"

        challenger_player_resp = _make_response({"id": 10})
        target_player_resp = _make_response({"id": 20})
        duel_resp = _make_response({"id": 99, "expires_at": None})

        mock_duel_cog.http_client.post = AsyncMock(side_effect=[challenger_player_resp, target_player_resp, duel_resp])

        with (
            patch.object(mock_duel_cog._outgoing_duel_cache, "invalidate") as mock_out,
            patch.object(mock_duel_cog._pending_duel_cache, "invalidate") as mock_pend,
        ):
            asyncio.run(mock_duel_cog.duel_challenge.callback(mock_duel_cog, interaction, target, 0))

        mock_out.assert_called_once_with((interaction.guild_id, 10))
        mock_pend.assert_called_once_with((interaction.guild_id, 20))

    def test_duel_accept_invalidates_pending_and_outgoing(self, mock_duel_cog):
        """duel_accept: invalidates accepter's pending and challenger's outgoing caches."""
        interaction = _create_mock_interaction(user_id=222)
        player_resp = _make_response({"id": 20})
        accept_resp = _make_response(
            {
                "is_stalemate": False,
                "challenger_id": 10,
                "challenger_name": "Challenger",
                "challenger_credits": 1000,
                "challenger_hp": 200,
                "challenger_dps": 50,
                "target_id": 20,
                "target_name": "Target",
                "target_credits": 1200,
                "target_hp": 180,
                "target_dps": 60,
                "credits_transferred": 100,
                "stakes": 100,
            }
        )

        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, accept_resp])

        with (
            patch.object(mock_duel_cog._pending_duel_cache, "invalidate") as mock_pend,
            patch.object(mock_duel_cog._outgoing_duel_cache, "invalidate") as mock_out,
        ):
            asyncio.run(mock_duel_cog.duel_accept.callback(mock_duel_cog, interaction, "42"))

        mock_pend.assert_called_once_with((interaction.guild_id, 20))
        mock_out.assert_called_once_with((interaction.guild_id, 10))

    def test_duel_reject_invalidates_pending_and_outgoing(self, mock_duel_cog):
        """duel_reject: invalidates rejecter's pending and challenger's outgoing caches."""
        interaction = _create_mock_interaction(user_id=222)
        player_resp = _make_response({"id": 20})
        reject_resp = _make_response({"challenger_name": "Challenger", "challenger_id": 10})

        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, reject_resp])

        with (
            patch.object(mock_duel_cog._pending_duel_cache, "invalidate") as mock_pend,
            patch.object(mock_duel_cog._outgoing_duel_cache, "invalidate") as mock_out,
        ):
            asyncio.run(mock_duel_cog.duel_reject.callback(mock_duel_cog, interaction, "42"))

        mock_pend.assert_called_once_with((interaction.guild_id, 20))
        mock_out.assert_called_once_with((interaction.guild_id, 10))

    def test_duel_cancel_invalidates_outgoing_and_target_pending(self, mock_duel_cog):
        """duel_cancel: invalidates canceller's outgoing and target's pending caches."""
        interaction = _create_mock_interaction(user_id=111)
        player_resp = _make_response({"id": 10})
        cancel_resp = _make_response({"target_name": "Target", "target_id": 20})

        mock_duel_cog.http_client.post = AsyncMock(side_effect=[player_resp, cancel_resp])

        with (
            patch.object(mock_duel_cog._outgoing_duel_cache, "invalidate") as mock_out,
            patch.object(mock_duel_cog._pending_duel_cache, "invalidate") as mock_pend,
        ):
            asyncio.run(mock_duel_cog.duel_cancel.callback(mock_duel_cog, interaction, "42"))

        mock_out.assert_called_once_with((interaction.guild_id, 10))
        mock_pend.assert_called_once_with((interaction.guild_id, 20))


# ---------------------------------------------------------------------------
# adminCog tests
# ---------------------------------------------------------------------------


class TestAdminCogCacheInvalidation:
    """Cache invalidation tests for adminCog."""

    def test_admin_give_item_invalidates_target_inventory(self, mock_admin_cog):
        """admin_give_item: after success, invalidates target player's inventory cache."""
        import discord as dc

        interaction = _create_mock_interaction()
        # Admin permission check: user has administrator permission
        interaction.user.guild_permissions.administrator = True

        target_user = MagicMock(spec=dc.User)
        target_user.id = 999
        target_user.display_name = "Target"
        target_user.mention = "<@999>"

        give_resp = _make_response(
            {
                "message": "Item given",
                "item_type": "primary_weapon",
                "new_total_quantity": 2,
                "player_id": 42,
            }
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=give_resp)

        with patch("utils.autocomplete_state.invalidate_inventory") as mock_inv:
            asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, target_user, "Laser", 1))

        mock_inv.assert_called_once_with(interaction.guild_id, 42)

    def test_admin_remove_item_invalidates_target_inventory(self, mock_admin_cog):
        """admin_remove_item: after success, invalidates target player's inventory cache."""
        import discord as dc

        interaction = _create_mock_interaction()
        interaction.user.guild_permissions.administrator = True

        target_user = MagicMock(spec=dc.User)
        target_user.id = 999
        target_user.display_name = "Target"
        target_user.mention = "<@999>"

        remove_resp = _make_response(
            {
                "message": "Item removed",
                "item_type": "primary_weapon",
                "new_quantity": 0,
                "player_id": 42,
            }
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=remove_resp)

        with patch("utils.autocomplete_state.invalidate_inventory") as mock_inv:
            asyncio.run(mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, target_user, "Laser", 1))

        mock_inv.assert_called_once_with(interaction.guild_id, 42)

    def test_admin_give_ship_invalidates_ships_and_player(self, mock_admin_cog):
        """admin_give_ship: after success, invalidates target player's ships and player caches."""
        import discord as dc

        interaction = _create_mock_interaction()
        interaction.user.guild_permissions.administrator = True

        target_user = MagicMock(spec=dc.User)
        target_user.id = 999
        target_user.display_name = "Target"
        target_user.mention = "<@999>"

        give_resp = _make_response(
            {
                "message": "Ship given",
                "ship_id": 55,
                "player_id": 42,
            }
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=give_resp)

        with (
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
        ):
            asyncio.run(mock_admin_cog.admin_give_ship.callback(mock_admin_cog, interaction, target_user, "Betty"))

        mock_inv_ships.assert_called_once_with(interaction.guild_id, 42)
        mock_inv_player.assert_called_once_with(interaction.guild_id, target_user.id)

    def test_admin_player_set_credits_invalidates_player(self, mock_admin_cog):
        """admin_player set_credits: after success, invalidates target player cache."""
        import discord as dc

        interaction = _create_mock_interaction()
        interaction.user.guild_permissions.administrator = True

        target_user = MagicMock(spec=dc.User)
        target_user.id = 999
        target_user.display_name = "Target"
        target_user.__str__ = MagicMock(return_value="Target#0001")
        target_user.display_avatar = MagicMock()

        player_resp = _make_response(_make_player())
        credits_resp = _make_response({"old_credits": 100, "new_credits": 5000})

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=credits_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=_make_response({"bounty_stats": {}, "duel_stats": {}}))

        with patch("utils.autocomplete_state.invalidate_player") as mock_inv:
            asyncio.run(
                mock_admin_cog.admin_player.callback(
                    mock_admin_cog, interaction, target_user, "set_credits", 5000, None
                )
            )

        mock_inv.assert_called_once_with(interaction.guild_id, target_user.id)

    def test_admin_give_item_cache_failure_does_not_fail_command(self, mock_admin_cog):
        """AC-ROB-3: admin_give_item cache failure must not abort command."""
        import discord as dc

        interaction = _create_mock_interaction()
        interaction.user.guild_permissions.administrator = True

        target_user = MagicMock(spec=dc.User)
        target_user.id = 999
        target_user.display_name = "Target"
        target_user.mention = "<@999>"

        give_resp = _make_response(
            {
                "message": "Item given",
                "item_type": "primary_weapon",
                "new_total_quantity": 2,
                "player_id": 42,
            }
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=give_resp)

        with patch("utils.autocomplete_state.invalidate_inventory", side_effect=RuntimeError("cache down")):
            asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, target_user, "Laser", 1))

        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# devCog tests
# ---------------------------------------------------------------------------


class TestDevCogCacheInvalidation:
    """Cache invalidation tests for devCog."""

    def test_reload_autocomplete_calls_clear_all(self, mock_dev_cog):
        """reload_autocomplete: calls autocomplete_state.clear_all() as first statement."""
        interaction = _create_mock_interaction()
        mock_dev_cog._categories = ["ship", "module"]

        import os as _os

        with (
            patch.dict(_os.environ, {"DEVELOPERS": str(interaction.user.id)}),
            patch("utils.autocomplete_state.clear_all") as mock_clear_all,
        ):
            asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        mock_clear_all.assert_called_once()

    def test_reload_autocomplete_clear_all_failure_is_non_fatal(self, mock_dev_cog):
        """AC-ROB-3: clear_all failure in reload_autocomplete must be non-fatal."""
        interaction = _create_mock_interaction()
        mock_dev_cog._categories = []

        import os as _os

        with (
            patch.dict(_os.environ, {"DEVELOPERS": str(interaction.user.id)}),
            patch("utils.autocomplete_state.clear_all", side_effect=RuntimeError("cache not init")),
        ):
            # Should not raise
            asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# Adversarial edge case tests
# ---------------------------------------------------------------------------


class TestAdversarialEdgeCases:
    """Edge case and adversarial invalidation tests not covered in primary suites."""

    def test_give_ship_invalidates_both_players_ships_and_player(self, mock_inventory_cog):
        """give ship: invalidates ships AND player caches for BOTH giver and recipient.

        The production code makes 6 invalidation calls:
          - invalidate_ships(guild, source_player_id)
          - invalidate_ships(guild, target_player_id)
          - invalidate_inventory(guild, source_player_id)
          - invalidate_inventory(guild, target_player_id)
          - invalidate_player(guild, interaction.user.id)   [Discord user ID]
          - invalidate_player(guild, target.id)              [Discord user ID]

        This test pins the 2x ships and 2x player requirement explicitly.
        """
        import discord as dc

        interaction = _create_mock_interaction(user_id=111)
        target = MagicMock(spec=dc.Member)
        target.id = 222
        target.display_name = "Target"
        target.mention = "<@222>"

        source_player = _make_player(player_id=1, user_id=111)
        target_player = _make_player(player_id=2, user_id=222)

        source_resp = _make_response(source_player)
        target_resp = _make_response(target_player)
        # transfer endpoint returns result with ship info
        transfer_resp = _make_response({"ship_name": "Betty", "items_returned_to_source": []})
        transfer_resp.status_code = 200

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[source_resp, target_resp, transfer_resp])

        with (
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_player") as mock_inv_player,
        ):
            asyncio.run(
                mock_inventory_cog.give.callback(
                    mock_inventory_cog,
                    interaction,
                    target,
                    "ship",
                    None,
                    None,
                    str(99),  # ship_id as string
                )
            )

        # Verify 2x invalidate_ships: one for source, one for target (by player_id)
        ships_calls = [call.args for call in mock_inv_ships.call_args_list]
        assert (interaction.guild_id, source_player["id"]) in ships_calls, (
            f"Expected source player (id={source_player['id']}) ships invalidated. Got: {ships_calls}"
        )
        assert (interaction.guild_id, target_player["id"]) in ships_calls, (
            f"Expected target player (id={target_player['id']}) ships invalidated. Got: {ships_calls}"
        )
        assert len(ships_calls) == 2, f"Expected exactly 2 invalidate_ships calls, got {len(ships_calls)}"

        # Verify 2x invalidate_player: one for source discord user, one for target discord user
        player_calls = [call.args for call in mock_inv_player.call_args_list]
        assert (interaction.guild_id, interaction.user.id) in player_calls, (
            f"Expected source discord user (id={interaction.user.id}) player invalidated. Got: {player_calls}"
        )
        assert (interaction.guild_id, target.id) in player_calls, (
            f"Expected target discord user (id={target.id}) player invalidated. Got: {player_calls}"
        )
        assert len(player_calls) == 2, f"Expected exactly 2 invalidate_player calls, got {len(player_calls)}"

        # Verify 2x invalidate_inventory (also tested here)
        inv_calls = [call.args for call in mock_inv_inv.call_args_list]
        assert (interaction.guild_id, source_player["id"]) in inv_calls
        assert (interaction.guild_id, target_player["id"]) in inv_calls

    def test_unequip_all_partial_failure_still_invalidates(self, mock_inventory_cog):
        """_unequip_all partial failure: cache invalidation runs even when some items fail.

        The guard is 'if succeeded:' — if at least one item was unequipped, the cache
        must be invalidated even if other items failed. This ensures partial unequip
        doesn't leave stale cache data.
        """
        interaction = _create_mock_interaction()
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        loadout = {"weapons": ["Laser", "Plasma"], "modules": [], "turrets": [], "secondary_weapons": []}
        loadout_resp = _make_response(loadout)

        # Laser succeeds, Plasma fails
        success_resp = _make_response({"id": 10, "ship_name": "Betty"})
        fail_exc = Exception("API error")

        call_count = 0

        async def side_effect_unequip(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return success_resp  # Laser succeeds
            raise fail_exc  # Plasma fails

        mock_inventory_cog.http_client.post = side_effect_unequip
        mock_inventory_cog.http_client.get = AsyncMock(return_value=loadout_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_inventory_cog._unequip_all(interaction, player_id=1, ship_id=10, active_ship=active_ship))

        # Despite partial failure, invalidation must still run (succeeded=['Laser'])
        mock_inv_inv.assert_called_once_with(interaction.guild_id, 1)
        mock_inv_ships.assert_called_once_with(interaction.guild_id, 1)

        # Verify the partial success embed was sent (not an error embed)
        send_calls = interaction.followup.send.call_args_list
        assert len(send_calls) >= 1, "Expected followup.send to be called with partial result embed"

    def test_unequip_all_all_items_fail_no_invalidation(self, mock_inventory_cog):
        """_unequip_all all items fail: if succeeded is empty, no invalidation should run.

        The guard is 'if succeeded:' — if zero items were unequipped (all failed),
        the cache should NOT be invalidated (nothing changed in the DB).
        """
        interaction = _create_mock_interaction()
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        loadout = {"weapons": ["Laser"], "modules": [], "turrets": [], "secondary_weapons": []}
        loadout_resp = _make_response(loadout)

        async def always_fail(*args, **kwargs):
            raise Exception("API error")

        mock_inventory_cog.http_client.post = always_fail
        mock_inventory_cog.http_client.get = AsyncMock(return_value=loadout_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_inv_ships,
        ):
            asyncio.run(mock_inventory_cog._unequip_all(interaction, player_id=1, ship_id=10, active_ship=active_ship))

        # If nothing succeeded, no invalidation should occur
        mock_inv_inv.assert_not_called()
        mock_inv_ships.assert_not_called()

    def test_ac_cohere_1_buy_calls_set_player_and_invalidate_inventory(self, mock_shop_cog):
        """AC-COHERE-1: after /buy, next equip autocomplete reflects updated cargo.

        Verifies that both invalidate_player (credits changed → set_player context) AND
        invalidate_inventory (cargo grew) are called on buy success, so subsequent
        equip autocomplete immediately reflects the new item in cargo.
        """
        interaction = _create_mock_interaction()
        player_data = _make_player(credits=5000)
        shop_item = {
            "id": 1,
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "tier": "Bronze",
            "price": 500,
            "quantity": 10,
        }
        transaction = {
            "item_name": "Laser",
            "item_type": "primary_weapon",
            "total_cost": 500,
            "remaining_credits": 4500,
        }

        player_resp = _make_response(player_data)
        item_resp = _make_response(shop_item)
        purchase_resp = _make_response(transaction)

        mock_shop_cog.http_client.post = AsyncMock(side_effect=[player_resp, purchase_resp])
        mock_shop_cog.http_client.get = AsyncMock(return_value=item_resp)

        with (
            patch("utils.autocomplete_state.invalidate_player") as mock_player,
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv,
        ):
            asyncio.run(mock_shop_cog.buy.callback(mock_shop_cog, interaction, 1, 1))

        # AC-COHERE-1: both must be invalidated for equip autocomplete to reflect new cargo
        mock_player.assert_called_once()
        mock_inv.assert_called_once()

    def test_ac_cohere_3_equip_invalidates_inventory_and_ships(self, mock_inventory_cog):
        """AC-COHERE-3: after /equip, both equip and unequip reflect new loadout.

        After equipping, the equip autocomplete (reads inventory cache) and
        unequip autocomplete (reads ships cache) must both be invalidated.
        """
        interaction = _create_mock_interaction()
        player_resp = _make_response({"id": 1})
        active_ship = {"id": 10, "is_active": True, "ship_name": "Betty"}
        ships_resp = _make_response([active_ship])
        check_resp = _make_response({"status": "ok", "equipment_type": "weapons"})
        equip_resp = _make_response(
            {"id": 10, "ship_name": "Betty", "nickname": None, "weapons": ["Laser"], "modules": [], "turrets": []}
        )

        mock_inventory_cog.http_client.post = AsyncMock(side_effect=[player_resp, check_resp, equip_resp])
        mock_inventory_cog.http_client.get = AsyncMock(return_value=ships_resp)

        with (
            patch("utils.autocomplete_state.invalidate_inventory") as mock_inv,
            patch("utils.autocomplete_state.invalidate_ships") as mock_ships,
        ):
            asyncio.run(mock_inventory_cog.equip.callback(mock_inventory_cog, interaction, "Laser"))

        # AC-COHERE-3: BOTH must be invalidated
        mock_inv.assert_called_once()
        mock_ships.assert_called_once()

    def test_ac_cohere_5_promote_invalidates_player(self, mock_player_cog):
        """AC-COHERE-5: after /promote, /buy autocomplete reflects new tier.

        invalidate_player must be called so autocomplete_state re-fetches player data
        (including tier) on the next keystroke in /buy.
        """
        interaction = _create_mock_interaction()
        player_data = _make_player(tier="Bronze")
        status_data = {"can_promote": True, "next_tier": "Silver", "on_cooldown": False, "xp": 100}
        promote_data = {"new_tier": "Silver", "old_tier": "Bronze", "xp": 100, "eligible_for_next": False}
        config_resp = _make_response({})

        from cogs._shared.confirm_view import ConfirmView

        async def mock_wait(self):
            self.result = True

        player_resp = _make_response(player_data)
        status_resp = _make_response(status_data)
        preflight_resp = _make_response({"verdict": "green", "sims_run": 20, "player_win_rate": 0.8})
        promote_resp = _make_response(promote_data)

        mock_player_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_player_cog.http_client.put = AsyncMock(return_value=promote_resp)
        mock_player_cog.http_client.get = AsyncMock(side_effect=[status_resp, preflight_resp, config_resp])

        with (
            patch.object(ConfirmView, "wait", mock_wait),
            patch("utils.autocomplete_state.invalidate_player") as mock_inv,
        ):
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # AC-COHERE-5: player cache must be invalidated
        mock_inv.assert_called_once_with(interaction.guild_id, interaction.user.id)
