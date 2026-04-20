"""
Tests for the /give command and autocomplete in inventoryCog.
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

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _close_coro(coro):
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


def _create_mock_interaction(user_id: int = 111111111, guild_id: int = 987654321):
    interaction = DiscordMockUtils.create_mock_interaction(user_id=user_id)
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    return interaction


def _create_mock_member(user_id: int = 222222222, name: str = "TargetUser"):
    member = MagicMock()
    member.id = user_id
    member.display_name = name
    member.mention = f"<@{user_id}>"
    return member


def _make_http_resp(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data or {})
    return resp


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture
def inventory_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.inventoryCog import InventoryCog

    cog = InventoryCog(mock_bot)
    return cog


# Shared player creation helper
def _player_resp(player_id: int = 10, credits: int = 5000):
    return _make_http_resp(
        200,
        {"id": player_id, "credits": credits, "guild_id": 987654321, "tier": "Bronze", "xp": 0},
    )


# -------------------------------------------------------------------------
# Tests: /give credits
# -------------------------------------------------------------------------


class TestGiveCredits:
    """Tests for /give with type=credits."""

    def test_give_credits_success(self, inventory_cog):
        """/give credits should transfer credits and confirm success."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        # source player has enough credits
        source_player_resp = _player_resp(player_id=10, credits=5000)
        target_player_resp = _player_resp(player_id=20, credits=100)
        transfer_resp = _make_http_resp(200, {"amount": 500, "source_remaining_credits": 4500})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="credits",
                amount=500,
                item=None,
                ship=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_give_credits_to_self_blocked(self, inventory_cog):
        """/give should reject giving to self."""
        interaction = _create_mock_interaction(user_id=111111111)
        # Target same user
        target = _create_mock_member(user_id=111111111)

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="credits",
                amount=500,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True
        content = interaction.followup.send.call_args[0][0] if interaction.followup.send.call_args[0] else ""
        assert "yourself" in content.lower() or "yourself" in str(call_kwargs)

    def test_give_credits_insufficient_balance(self, inventory_cog):
        """/give credits should reject if source has insufficient credits."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        # Source player only has 100 credits, trying to give 500
        source_player_resp = _player_resp(player_id=10, credits=100)
        target_player_resp = _player_resp(player_id=20, credits=0)

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="credits",
                amount=500,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_credits_no_amount_provided(self, inventory_cog):
        """/give credits without amount shows error."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10, credits=5000)
        target_player_resp = _player_resp(player_id=20, credits=100)

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="credits",
                amount=None,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_credits_transfer_api_error(self, inventory_cog):
        """/give credits handles 400 from transfer API."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10, credits=5000)
        target_player_resp = _player_resp(player_id=20, credits=100)
        transfer_resp = _make_http_resp(400, {"detail": "Transfer failed"})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="credits",
                amount=500,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: /give item
# -------------------------------------------------------------------------


class TestGiveItem:
    """Tests for /give with type=item."""

    def test_give_item_success(self, inventory_cog):
        """/give item should transfer item and confirm success."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)
        transfer_resp = _make_http_resp(200, {"from_player_id": 10, "to_player_id": 20, "item_name": "Pulse Laser"})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="item",
                amount=None,
                item="Pulse Laser::weapon",
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_give_item_no_item_selected(self, inventory_cog):
        """/give item without selecting item shows error."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="item",
                amount=None,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_item_transfer_error(self, inventory_cog):
        """/give item handles 400 from transfer API."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)
        transfer_resp = _make_http_resp(400, {"detail": "Player does not have item"})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="item",
                amount=None,
                item="FakeItem::weapon",
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: /give ship
# -------------------------------------------------------------------------


class TestGiveShip:
    """Tests for /give with type=ship."""

    def test_give_ship_success(self, inventory_cog):
        """/give ship should transfer ship and confirm success."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)
        transfer_resp = _make_http_resp(
            200,
            {
                "ship_id": 42,
                "ship_name": "Sidewinder",
                "from_player_id": 10,
                "to_player_id": 20,
                "items_returned_to_source": ["Pulse Laser"],
                "message": "Ship transferred",
            },
        )

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="ship",
                amount=None,
                item=None,
                ship="42",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_give_ship_no_ship_selected(self, inventory_cog):
        """/give ship without selecting ship shows error."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="ship",
                amount=None,
                item=None,
                ship=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_ship_active_ship_blocked(self, inventory_cog):
        """/give ship shows error when trying to give active ship."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)
        transfer_resp = _make_http_resp(400, {"detail": "Cannot transfer the active ship"})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="ship",
                amount=None,
                item=None,
                ship="42",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_ship_invalid_ship_id(self, inventory_cog):
        """/give ship with invalid ship ID string shows error."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="ship",
                amount=None,
                item=None,
                ship="not-a-number",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    def test_give_ship_404_from_api(self, inventory_cog):
        """/give ship shows error on 404 from API."""
        interaction = _create_mock_interaction(user_id=111111111)
        target = _create_mock_member(user_id=222222222)

        source_player_resp = _player_resp(player_id=10)
        target_player_resp = _player_resp(player_id=20)
        transfer_resp = _make_http_resp(404, {"detail": "Ship 42 not found"})

        inventory_cog.http_client.post = AsyncMock(side_effect=[source_player_resp, target_player_resp, transfer_resp])

        asyncio.run(
            inventory_cog.give.callback(
                inventory_cog,
                interaction,
                target=target,
                give_type="ship",
                amount=None,
                item=None,
                ship="42",
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# -------------------------------------------------------------------------
# Tests: Autocomplete functions
# -------------------------------------------------------------------------


class TestGiveAutocomplete:
    """Tests for /give autocomplete helpers."""

    def test_give_item_autocomplete_success(self, inventory_cog):
        """give_item_autocomplete returns choices from player inventory."""
        interaction = _create_mock_interaction(user_id=111111111)

        player_resp = _make_http_resp(200, {"id": 10})
        inv_resp = _make_http_resp(
            200,
            [
                {"item_name": "Pulse Laser", "item_type": "weapon", "quantity": 1},
                {"item_name": "Shield Gen", "item_type": "module", "quantity": 2},
            ],
        )
        inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        inventory_cog.http_client.get = AsyncMock(return_value=inv_resp)

        result = asyncio.run(inventory_cog.give_item_autocomplete(interaction, "pulse"))
        assert isinstance(result, list)
        # Should find "Pulse Laser"
        names = [c.name for c in result]
        assert any("Pulse Laser" in n for n in names)

    def test_give_item_autocomplete_handles_error(self, inventory_cog):
        """give_item_autocomplete returns [] on error."""
        interaction = _create_mock_interaction(user_id=111111111)
        inventory_cog.http_client.post = AsyncMock(side_effect=Exception("Error"))

        result = asyncio.run(inventory_cog.give_item_autocomplete(interaction, ""))
        assert result == []

    def test_give_ship_autocomplete_success(self, inventory_cog):
        """give_ship_autocomplete returns non-active ships."""
        interaction = _create_mock_interaction(user_id=111111111)

        player_resp = _make_http_resp(200, {"id": 10})
        ships_resp = _make_http_resp(
            200,
            [
                {"id": 42, "ship_name": "Sidewinder", "is_active": False, "nickname": None},
                {"id": 43, "ship_name": "VenomStrike", "is_active": True, "nickname": None},  # active — excluded
            ],
        )
        inventory_cog.http_client.post = AsyncMock(return_value=player_resp)
        inventory_cog.http_client.get = AsyncMock(return_value=ships_resp)

        result = asyncio.run(inventory_cog.give_ship_autocomplete(interaction, ""))
        # Active ship should be excluded
        values = [c.value for c in result]
        assert "43" not in values
        assert "42" in values

    def test_give_ship_autocomplete_handles_error(self, inventory_cog):
        """give_ship_autocomplete returns [] on error."""
        interaction = _create_mock_interaction(user_id=111111111)
        inventory_cog.http_client.post = AsyncMock(side_effect=Exception("Error"))

        result = asyncio.run(inventory_cog.give_ship_autocomplete(interaction, ""))
        assert result == []
