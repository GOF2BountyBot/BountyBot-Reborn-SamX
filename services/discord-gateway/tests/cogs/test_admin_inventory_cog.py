"""
Tests for new admin inventory management commands in adminCog:
  - /admin_give_item
  - /admin_remove_item
  - /admin_give_ship
  - /admin_remove_ship
  - item_name_autocomplete
  - game_ship_autocomplete
  - player_ship_autocomplete
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

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
    logger.critical = MagicMock()
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
# Phase-4 autocomplete_state cache helpers for admin_inventory_cog tests.
# adminCog imports resolve_player_id LAZILY, so we only need to populate
# sys.modules["utils.autocomplete_state"].player_cache.
# ---------------------------------------------------------------------------


def _ac_get_state_admin_inv():
    """Return the autocomplete_state that adminCog's lazy import will use."""
    return sys.modules.get("utils.autocomplete_state")


def _ac_init_player_cache_admin_inv():
    """Create a real (no-HTTP) player_cache on the current autocomplete_state.

    If the module is not yet in sys.modules (was evicted), import it first.
    """
    from cogs._shared.autocomplete_cache import AutocompleteCache

    ac = _ac_get_state_admin_inv()
    if ac is None:
        import utils.autocomplete_state as _ac_mod

        ac = _ac_mod
    if ac.player_cache is None:
        ac.player_cache = AutocompleteCache(ttl_seconds=900, name="player")
        ac._initialized = True
    return ac


def _ac_reset_admin_inv_player_cache():
    """Clear all player_cache entries."""
    ac = _ac_get_state_admin_inv()
    if ac is not None and ac.player_cache is not None:
        ac.player_cache.clear()


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _close_coro(coro):
    """Close a coroutine to prevent 'never awaited' RuntimeWarning."""
    if hasattr(coro, "close"):
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


def _create_mock_interaction(guild_id: int = 987654321, user_id: int = 999888777):
    interaction = DiscordMockUtils.create_mock_interaction(user_id=user_id)
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.guild.icon = None
    return interaction


def _create_mock_user(user_id: int = 111222333, name: str = "TargetUser"):
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.id = user_id
    return user


def _make_http_resp(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data or {})
    return resp


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture(scope="module")
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# Tests: /admin_give_item
# -------------------------------------------------------------------------


class TestAdminGiveItem:
    """Tests for the /admin_give_item command.

    B.80: item_type parameter removed from the slash command.
    The server now resolves the concrete item type from the item name.
    """

    def test_give_item_success(self, mock_admin_cog):
        """/admin_give_item should give item and show success embed.

        B.80: no item_type in command signature; the server resolves it and returns
        it in the response (item_type is shown in the embed from the API response).
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "item_name": "Pulse Laser",
                "item_type": "primary_weapon",
                "new_total_quantity": 2,
                "message": "Gave 1x Pulse Laser to player 10",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_give_item_payload_has_no_item_type(self, mock_admin_cog):
        """/admin_give_item must NOT send item_type in the HTTP payload (B.80)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "item_name": "Pulse Laser",
                "item_type": "primary_weapon",
                "new_total_quantity": 2,
                "message": "Gave 1x Pulse Laser to player 10",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        # Verify the HTTP POST payload does NOT contain item_type (B.80 key assertion)
        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        sent_json = call_kwargs.get("json", {})
        assert "item_type" not in sent_json, (
            f"B.80: /admin_give_item must not send item_type in the payload, got: {sent_json}"
        )
        # Verify the required fields ARE present
        assert sent_json.get("item_name") == "Pulse Laser"
        assert sent_json.get("quantity") == 1

    def test_give_item_not_found_response(self, mock_admin_cog):
        """/admin_give_item should show error message on 404."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(404, {"detail": "Player not found"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        # Should be ephemeral error
        assert call_args[1].get("ephemeral") is True

    def test_give_item_bad_request_response(self, mock_admin_cog):
        """/admin_give_item should show error on 400."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(400, {"detail": "Item does not exist"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="FakeItem",
                quantity=1,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_item_api_exception(self, mock_admin_cog):
        """/admin_give_item should handle unexpected exceptions gracefully."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Connection error"))

        asyncio.run(
            mock_admin_cog.admin_give_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: /admin_remove_item
# -------------------------------------------------------------------------


class TestAdminRemoveItem:
    """Tests for the /admin_remove_item command.

    B.80-style: item_type parameter removed from the slash command.
    The server now resolves the concrete type from the player's inventory by item_name.
    """

    def test_remove_item_success(self, mock_admin_cog):
        """/admin_remove_item should remove item and show success embed.

        B.80-style: item_type removed from slash command — not passed in callback.
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "item_name": "Pulse Laser",
                "item_type": "primary_weapon",
                "quantity_removed": 1,
                "new_quantity": 0,
                "message": "Removed 1x Pulse Laser from player 10",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_remove_item_payload_has_no_item_type(self, mock_admin_cog):
        """/admin_remove_item must NOT send item_type in the HTTP payload (B.80-style)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "item_name": "Pulse Laser",
                "item_type": "primary_weapon",
                "quantity_removed": 1,
                "new_quantity": 0,
                "message": "Removed 1x Pulse Laser from player 10",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        sent_json = call_kwargs.get("json", {})
        assert "item_type" not in sent_json, (
            f"B.80-style: /admin_remove_item must not send item_type in the payload, got: {sent_json}"
        )
        assert sent_json.get("item_name") == "Pulse Laser"
        assert sent_json.get("quantity") == 1

    def test_remove_item_not_found(self, mock_admin_cog):
        """/admin_remove_item should show error on 404."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(404, {"detail": "Player does not have Pulse Laser"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=1,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_remove_item_bad_request(self, mock_admin_cog):
        """/admin_remove_item should show error on 400."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(400, {"detail": "Insufficient quantity"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="Pulse Laser",
                quantity=999,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_remove_item_rejects_placeholder_sentinel(self, mock_admin_cog):
        """/admin_remove_item should respond with an ephemeral error when the placeholder sentinel value is submitted.

        If a user somehow submits '__select_user_first__' (the autocomplete placeholder),
        the command must respond with '❌ Please select a user first.' and return early
        without making any API calls.
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock()

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                item_name="__select_user_first__",
                quantity=1,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        # Must be ephemeral
        assert call_args[1].get("ephemeral") is True
        # Must contain the expected error message
        sent_message = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "Please select a user first" in sent_message
        # Must NOT have called the API
        mock_admin_cog.http_client.post.assert_not_awaited()


# -------------------------------------------------------------------------
# Tests: /admin_give_ship
# -------------------------------------------------------------------------


class TestAdminGiveShip:
    """Tests for the /admin_give_ship command."""

    def test_give_ship_success(self, mock_admin_cog):
        """/admin_give_ship should create ship and show success embed."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "ship_id": 42,
                "ship_name": "Sidewinder",
                "is_active": False,
                "message": "Gave ship 'Sidewinder' to player 10",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="Sidewinder",
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_give_ship_invalid_ship(self, mock_admin_cog):
        """/admin_give_ship shows error when ship doesn't exist in game data."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(404, {"detail": "Ship 'FakeShip' does not exist in game data"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_give_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="FakeShip",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_ship_exception(self, mock_admin_cog):
        """/admin_give_ship handles unexpected exceptions."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Network error"))

        asyncio.run(
            mock_admin_cog.admin_give_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="Sidewinder",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: /admin_remove_ship
# -------------------------------------------------------------------------


class TestAdminRemoveShip:
    """Tests for the /admin_remove_ship command."""

    def test_remove_ship_success(self, mock_admin_cog):
        """/admin_remove_ship should remove ship and show success embed."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(
            200,
            {
                "player_id": 10,
                "ship_id": 42,
                "ship_name": "Sidewinder",
                "items_returned_to_inventory": ["Pulse Laser", "Shield Gen"],
                "message": "Removed ship 'Sidewinder' from player 10. 2 item(s) returned.",
            },
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="Sidewinder",
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_remove_ship_not_found(self, mock_admin_cog):
        """/admin_remove_ship shows error when player doesn't own the ship."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(404, {"detail": "Player does not own a ship named 'VenomStrike'"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="VenomStrike",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_remove_ship_only_active_ship(self, mock_admin_cog):
        """/admin_remove_ship shows error when trying to remove only active ship."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        resp = _make_http_resp(400, {"detail": "Cannot remove the player's only active ship"})
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_remove_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="Sidewinder",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_remove_ship_exception(self, mock_admin_cog):
        """/admin_remove_ship handles unexpected exceptions."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=999888777)
        target_user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Network error"))

        asyncio.run(
            mock_admin_cog.admin_remove_ship.callback(
                mock_admin_cog,
                interaction,
                user=target_user,
                ship_name="Sidewinder",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: Autocomplete functions
# -------------------------------------------------------------------------


class TestAdminAutocomplete:
    """Tests for admin cog autocomplete functions."""

    def test_item_name_autocomplete_returns_choices(self, mock_admin_cog):
        """item_name_autocomplete returns app_commands.Choice list."""
        interaction = _create_mock_interaction()

        weapon_resp = _make_http_resp(200, [{"name": "Pulse Laser"}, {"name": "Scatter Gun"}])
        mock_admin_cog.http_client.get = AsyncMock(return_value=weapon_resp)

        result = asyncio.run(mock_admin_cog.item_name_autocomplete(interaction, "pulse"))
        # Should have filtered to matching items
        assert isinstance(result, list)

    def test_item_name_autocomplete_handles_error(self, mock_admin_cog):
        """item_name_autocomplete returns empty list on network error.

        Phase 3: _item_catalog now has a refresh_fn, so clear() the cache first to
        force the cold-fill path to hit the (raising) http_client and exercise the
        error degrade — otherwise a prior test's warm catalog would be served.
        """
        interaction = _create_mock_interaction()
        mock_admin_cog._item_catalog.clear()
        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Network error"))

        result = asyncio.run(mock_admin_cog.item_name_autocomplete(interaction, "pulse"))
        assert result == []

    def test_game_ship_autocomplete_returns_choices(self, mock_admin_cog):
        """game_ship_autocomplete returns choices from game data."""
        interaction = _create_mock_interaction()

        ships_resp = _make_http_resp(200, [{"name": "Sidewinder"}, {"name": "VenomStrike"}])
        mock_admin_cog.http_client.get = AsyncMock(return_value=ships_resp)

        result = asyncio.run(mock_admin_cog.game_ship_autocomplete(interaction, "side"))
        assert isinstance(result, list)

    def test_game_ship_autocomplete_handles_error(self, mock_admin_cog):
        """game_ship_autocomplete returns empty list on error.

        Phase 3: _ship_catalog now has a refresh_fn; clear() first so the cold-fill
        hits the raising http_client (otherwise a prior test's warm catalog is served).
        """
        interaction = _create_mock_interaction()
        mock_admin_cog._ship_catalog.clear()
        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Network error"))

        result = asyncio.run(mock_admin_cog.game_ship_autocomplete(interaction, ""))
        assert result == []

    def test_player_ship_autocomplete_returns_choices(self, mock_admin_cog):
        """player_ship_autocomplete returns choices from game data."""
        interaction = _create_mock_interaction()

        ships_resp = _make_http_resp(200, [{"name": "Sidewinder"}, {"name": "VenomStrike"}])
        mock_admin_cog.http_client.get = AsyncMock(return_value=ships_resp)

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))
        assert isinstance(result, list)

    def test_player_ship_autocomplete_handles_error(self, mock_admin_cog):
        """player_ship_autocomplete returns empty list on error.

        With no namespace.user selected the handler degrades to the _ship_catalog
        fallback; Phase 3 gave that catalog a refresh_fn, so clear() first to force
        the raising http_client path (otherwise a prior test's warm catalog is served).
        """
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.user = None
        mock_admin_cog._ship_catalog.clear()
        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Network error"))

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))
        assert result == []

    def test_remove_item_autocomplete_returns_placeholder_when_no_user(self, mock_admin_cog):
        """remove_item_autocomplete returns a single placeholder choice when no user is selected yet.

        When interaction.namespace.user is None (user param not yet filled in),
        the autocomplete must return exactly one choice with the sentinel value
        '__select_user_first__' instead of falling back to the full game catalog.
        This makes it visually clear in the Discord UI that the user field must
        be filled before the item dropdown is useful.
        """
        interaction = _create_mock_interaction()
        # No user selected yet
        interaction.namespace = MagicMock()
        interaction.namespace.user = None

        result = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, "pulse"))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "— Select a user first —"
        assert result[0].value == "__select_user_first__"

    def test_remove_item_autocomplete_fetches_inventory_when_user_selected(self, mock_admin_cog):
        """remove_item_autocomplete shows target user's inventory from cache, zero HTTP.

        Phase 6: Both player_cache and inventory_cache are pre-populated.
        The admin function reads from cache — no HTTP calls on the hot path.
        """
        import utils.autocomplete_state as _ac_mod
        from utils.autocomplete_utils import normalize_for_search as nfs

        interaction = _create_mock_interaction()
        target_user = _create_mock_user(user_id=111222333)
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # Phase 6: Pre-populate player_cache and inventory_cache
        ac = _ac_init_player_cache_admin_inv()
        if ac is not None:
            from cogs._shared.autocomplete_cache import AutocompleteCache

            ac.player_cache.set((987654321, 111222333), {"id": 10, "discord_id": 111222333})
            if ac.inventory_cache is None:
                ac.inventory_cache = AutocompleteCache(ttl_seconds=600, name="inventory")
            raw_items = [
                {"item_name": "Pulse Laser", "item_type": "primary_weapon", "quantity": 1},
                {"item_name": "Shield Gen", "item_type": "module", "quantity": 2},
            ]
            inv_choices = []
            for item in raw_items:
                label = f"{item['item_name']} ({item['item_type'].replace('_', ' ').title()})"
                inv_choices.append(
                    _ac_mod.NormalizedChoice(label=label, value=item["item_name"], norm=nfs(label), raw=item)
                )
            ac.inventory_cache.set((987654321, 10), inv_choices)

        # HTTP must not be called — data comes from cache
        mock_admin_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))
        assert isinstance(result, list)
        # Both inventory items should appear
        names = [c.value for c in result]
        assert "Pulse Laser" in names
        assert "Shield Gen" in names

        # Cleanup
        _ac_reset_admin_inv_player_cache()

    def test_remove_item_autocomplete_filters_by_current(self, mock_admin_cog):
        """remove_item_autocomplete filters inventory items by current text (Phase 6: cache)."""
        import utils.autocomplete_state as _ac_mod
        from utils.autocomplete_utils import normalize_for_search as nfs

        interaction = _create_mock_interaction()
        target_user = _create_mock_user(user_id=111222333)
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # Phase 6: Pre-populate player_cache and inventory_cache
        ac = _ac_init_player_cache_admin_inv()
        if ac is not None:
            from cogs._shared.autocomplete_cache import AutocompleteCache

            ac.player_cache.set((987654321, 111222333), {"id": 10, "discord_id": 111222333})
            if ac.inventory_cache is None:
                ac.inventory_cache = AutocompleteCache(ttl_seconds=600, name="inventory")
            raw_items = [
                {"item_name": "Pulse Laser", "item_type": "primary_weapon", "quantity": 1},
                {"item_name": "Shield Gen", "item_type": "module", "quantity": 1},
            ]
            inv_choices = []
            for item in raw_items:
                label = f"{item['item_name']} ({item['item_type'].replace('_', ' ').title()})"
                inv_choices.append(
                    _ac_mod.NormalizedChoice(label=label, value=item["item_name"], norm=nfs(label), raw=item)
                )
            ac.inventory_cache.set((987654321, 10), inv_choices)

        # HTTP must not be called
        mock_admin_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, "pulse"))
        names = [c.value for c in result]
        assert "Pulse Laser" in names
        assert "Shield Gen" not in names

        # Cleanup
        _ac_reset_admin_inv_player_cache()

    def test_remove_item_autocomplete_falls_back_on_api_failure(self, mock_admin_cog):
        """remove_item_autocomplete falls back to catalog when API call fails."""
        interaction = _create_mock_interaction()
        target_user = _create_mock_user(user_id=111222333)
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # Player resolution fails
        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        # Pre-populate catalog (set is synchronous)
        mock_admin_cog._item_catalog.set("primary_weapon", ["Pulse Laser"])
        mock_admin_cog._item_catalog.set("secondary_weapon", [])
        mock_admin_cog._item_catalog.set("turret_weapon", [])
        mock_admin_cog._item_catalog.set("module", [])

        result = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))
        assert isinstance(result, list)
        # Fallback to catalog
        names = [c.value for c in result]
        assert "Pulse Laser" in names

    def test_remove_item_autocomplete_returns_placeholder_when_namespace_is_none(self, mock_admin_cog):
        """remove_item_autocomplete returns the placeholder choice when namespace is None.

        With the refactored code, ``getattr(None, 'user', None)`` returns None,
        which is treated the same as 'no user selected yet' — the placeholder choice
        is returned rather than an empty list or a raised exception.
        Autocomplete must never raise regardless of namespace state.
        """
        interaction = _create_mock_interaction()
        # Simulate a missing/None namespace
        interaction.namespace = None

        result = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].value == "__select_user_first__"


class TestAdminCogA46Choices:
    """A.46/B.80: validates item_type parameter behaviour on admin inventory commands."""

    def test_admin_give_item_has_no_item_type_parameter(self, mock_admin_cog):
        """B.80: /admin_give_item must NOT have an item_type parameter.

        The item_type was removed from the slash command (B.80). The server now
        resolves the concrete type from the item catalog by name. Introspects the
        'admin_give_item' command's parameters and asserts item_type is absent.
        Mock budget: 0.
        """
        give_item_cmd = None
        for cmd in mock_admin_cog.__cog_app_commands__:
            if cmd.name == "admin_give_item":
                give_item_cmd = cmd
                break
        assert give_item_cmd is not None, "Could not find 'admin_give_item' command on AdminCog"

        param_names = {param.name for param in give_item_cmd.parameters}
        assert "item_type" not in param_names, (
            f"B.80: /admin_give_item must NOT have an 'item_type' parameter (server resolves it). "
            f"Found parameters: {param_names}"
        )
        # Required parameters must still be present
        assert "user" in param_names, "Missing 'user' parameter on /admin_give_item"
        assert "item_name" in param_names, "Missing 'item_name' parameter on /admin_give_item"

    def test_admin_remove_item_has_no_item_type_parameter(self, mock_admin_cog):
        """B.80-style: /admin_remove_item must NOT have an item_type parameter.

        The item_type parameter was removed from the slash command (same as B.80 for
        /admin_give_item). The server now resolves the concrete type from the player's
        inventory by item_name. Introspects the command's parameters and asserts
        item_type is absent. Mock budget: 0.
        """
        remove_item_cmd = None
        for cmd in mock_admin_cog.__cog_app_commands__:
            if cmd.name == "admin_remove_item":
                remove_item_cmd = cmd
                break
        assert remove_item_cmd is not None, "Could not find 'admin_remove_item' command on AdminCog"

        param_names = {param.name for param in remove_item_cmd.parameters}
        assert "item_type" not in param_names, (
            f"B.80-style: /admin_remove_item must NOT have an 'item_type' parameter (server resolves it). "
            f"Found parameters: {param_names}"
        )
        # Required parameters must still be present
        assert "user" in param_names, "Missing 'user' parameter on /admin_remove_item"
        assert "item_name" in param_names, "Missing 'item_name' parameter on /admin_remove_item"
