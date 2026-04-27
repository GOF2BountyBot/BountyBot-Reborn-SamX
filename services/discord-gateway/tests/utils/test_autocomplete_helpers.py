"""Tests for the shared autocomplete helpers.

These tests exercise the helpers via their real public interface.
discord and its app_commands module are imported directly so the produced
Choice objects are real (not mocked); the only mocked surface is
``httpx.AsyncClient`` which supplies controlled HTTP responses.
"""

import asyncio
import logging
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Inject mock shared.bblogger BEFORE importing the module under test
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import utils.autocomplete_helpers as _autocomplete_helpers_mod
from utils.autocomplete_helpers import (
    _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES,
    player_equippable_autocomplete,
    player_equipped_autocomplete,
    player_inventory_autocomplete,
    player_ships_autocomplete,
    resolve_player_id,
)

API_BASE = "http://bot-core:8000/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_data, status_code=200):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    return resp


def _make_interaction(user_id=111, guild_id=222):
    inter = MagicMock()
    inter.user = MagicMock()
    inter.user.id = user_id
    inter.guild_id = guild_id
    return inter


# ---------------------------------------------------------------------------
# resolve_player_id
# ---------------------------------------------------------------------------


class TestResolvePlayerId:
    """Unit tests for resolve_player_id."""

    def test_returns_none_on_error(self):
        """resolve_player_id swallows all exceptions and returns None."""
        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("network down"))

        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))

        assert result is None

    def test_returns_id_on_success(self):
        """resolve_player_id returns the player ID on successful response."""
        client = MagicMock()
        client.post = AsyncMock(return_value=_make_response({"id": 42}))

        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))

        assert result == 42


# ---------------------------------------------------------------------------
# player_ships_autocomplete
# ---------------------------------------------------------------------------


class TestPlayerShipsAutocomplete:
    """Unit tests for player_ships_autocomplete."""

    def test_filters_by_current_and_formats_label(self):
        """Filter is accent-insensitive substring match and label uses 🟢 for active."""
        ships = [
            {"id": 1, "ship_name": "Behén", "nickname": None, "is_active": True},
            {"id": 2, "ship_name": "Mako", "nickname": "StarHunter", "is_active": False},
            {"id": 3, "ship_name": "Viper", "nickname": None, "is_active": False},
        ]
        client = MagicMock()
        client.post = AsyncMock(return_value=_make_response({"id": 7}))
        client.get = AsyncMock(return_value=_make_response(ships))

        # Accent-insensitive: "behen" should match "Behén"
        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "behen"))

        assert len(choices) == 1
        assert choices[0].value == "1"
        assert choices[0].name.startswith("🟢 ")
        assert "Behén" in choices[0].name

        # "Ma" matches "Mako (StarHunter)"; active prefix must NOT be present for inactive ship
        choices_ma = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "Ma"))
        # Because interaction http responses are reused per-test, client.get is called again
        # and the full ship list is returned; only "Mako" matches "ma".
        matching = [c for c in choices_ma if "Mako" in c.name]
        assert len(matching) == 1
        assert matching[0].value == "2"
        assert "StarHunter" in matching[0].name
        assert not matching[0].name.startswith("🟢")

    def test_exclude_active_omits_active_ship(self):
        """exclude_active=True drops the active ship from results."""
        ships = [
            {"id": 1, "ship_name": "Active Ship", "nickname": None, "is_active": True},
            {"id": 2, "ship_name": "Backup Ship", "nickname": None, "is_active": False},
        ]
        client = MagicMock()
        client.post = AsyncMock(return_value=_make_response({"id": 7}))
        client.get = AsyncMock(return_value=_make_response(ships))

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "", exclude_active=True))

        values = [c.value for c in choices]
        assert "1" not in values
        assert "2" in values

    def test_returns_empty_on_player_resolution_failure(self):
        """If player resolution fails, autocomplete returns []."""
        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("network"))
        client.get = AsyncMock()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))

        assert choices == []
        client.get.assert_not_called()


# ---------------------------------------------------------------------------
# player_inventory_autocomplete
# ---------------------------------------------------------------------------


class TestPlayerInventoryAutocomplete:
    """Unit tests for player_inventory_autocomplete."""

    def test_filters_by_type_and_formats_label_with_quantity(self):
        """item_type_filter restricts results; quantity >1 is shown in the label."""
        items = [
            {"item_name": "Pulse Laser", "item_type": "weapon", "quantity": 3},
            {"item_name": "Shield Mk1", "item_type": "module", "quantity": 1},
            {"item_name": "Plasma Turret", "item_type": "turret", "quantity": 2},
        ]
        client = MagicMock()
        client.post = AsyncMock(return_value=_make_response({"id": 7}))
        client.get = AsyncMock(return_value=_make_response(items))

        choices = asyncio.run(
            player_inventory_autocomplete(client, API_BASE, _make_interaction(), "", item_type_filter="weapon")
        )

        assert len(choices) == 1
        assert choices[0].value == "Pulse Laser"
        # Label should include type and quantity for qty > 1
        assert "Weapon" in choices[0].name
        assert "x3" in choices[0].name

    def test_returns_empty_on_error(self):
        """Returns [] if inventory fetch raises."""
        client = MagicMock()
        client.post = AsyncMock(return_value=_make_response({"id": 7}))
        client.get = AsyncMock(side_effect=RuntimeError("boom"))

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))

        assert choices == []


class TestPlayerEquippableAutocomplete:
    """Tests for player_equippable_autocomplete (A.37 new helper)."""

    def test_excludes_equipped_items(self):
        """Items already equipped on the active ship are filtered out."""
        interaction = _make_interaction()
        client = AsyncMock()

        inv_resp = _make_response(
            [
                {"item_name": "Pulse Laser", "item_type": "primary_weapon", "quantity": 1},
                {"item_name": "Shield Gen", "item_type": "module", "quantity": 1},
                {"item_name": "Big Cannon", "item_type": "primary_weapon", "quantity": 1},
            ]
        )
        ships_resp = _make_response(
            [
                {
                    "id": 1,
                    "ship_name": "Betty",
                    "is_active": True,
                    "weapons": ["Pulse Laser"],  # already equipped
                    "modules": [],
                    "turrets": [],
                    "secondary_weapons": [],
                }
            ]
        )
        player_resp = _make_response({"id": 1})

        client.post = AsyncMock(return_value=player_resp)
        client.get = AsyncMock(side_effect=[inv_resp, ships_resp])

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, interaction, ""))

        names = {c.value for c in choices}
        assert "Pulse Laser" not in names, "equipped item should be excluded"
        assert "Shield Gen" in names
        assert "Big Cannon" in names

    def test_excludes_secondary_weapon_today(self):
        """Items with item_type='secondary_weapon' are filtered out by _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES."""
        interaction = _make_interaction()
        client = AsyncMock()

        inv_resp = _make_response(
            [
                {"item_name": "Primary Gun", "item_type": "primary_weapon", "quantity": 1},
                {"item_name": "Seeker Missile", "item_type": "secondary_weapon", "quantity": 1},
            ]
        )
        ships_resp = _make_response(
            [
                {
                    "id": 1,
                    "ship_name": "Betty",
                    "is_active": True,
                    "weapons": [],
                    "modules": [],
                    "turrets": [],
                    "secondary_weapons": [],
                }
            ]
        )
        player_resp = _make_response({"id": 1})
        client.post = AsyncMock(return_value=player_resp)
        client.get = AsyncMock(side_effect=[inv_resp, ships_resp])

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, interaction, ""))

        names = {c.value for c in choices}
        assert "Seeker Missile" not in names, "secondary_weapon must be excluded today"
        assert "Primary Gun" in names

    def test_returns_empty_on_api_error(self):
        """Returns [] on any API failure."""
        interaction = _make_interaction()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=RuntimeError("boom"))

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, interaction, ""))
        assert choices == []

    def test_constants_exclude_secondary_weapon(self):
        """_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES must not contain 'secondary_weapon' today."""
        assert "secondary_weapon" not in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        # ship is also excluded from equippable (has its own slot flow)
        assert "ship" not in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        # These should be present:
        assert "primary_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "turret_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "module" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES


class TestPlayerEquippedAutocomplete:
    """Tests for player_equipped_autocomplete (A.37 new helper)."""

    def test_includes_all_slots(self):
        """Equipped items from weapons, modules, turrets, secondary_weapons all returned."""
        interaction = _make_interaction()
        client = AsyncMock()

        player_resp = _make_response({"id": 1})
        ships_resp = _make_response(
            [
                {
                    "id": 1,
                    "ship_name": "Betty",
                    "is_active": True,
                    "weapons": ["Pulse Laser"],
                    "modules": ["Shield Gen"],
                    "turrets": ["Beam Turret"],
                    "secondary_weapons": [],
                }
            ]
        )
        client.post = AsyncMock(return_value=player_resp)
        client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, interaction, ""))

        names = {c.value for c in choices}
        assert "Pulse Laser" in names
        assert "Shield Gen" in names
        assert "Beam Turret" in names

    def test_returns_empty_when_no_active_ship(self):
        """Returns [] when player has no active ship."""
        interaction = _make_interaction()
        client = AsyncMock()

        player_resp = _make_response({"id": 1})
        ships_resp = _make_response(
            [
                {
                    "id": 1,
                    "ship_name": "Betty",
                    "is_active": False,
                    "weapons": [],
                    "modules": [],
                    "turrets": [],
                    "secondary_weapons": [],
                }
            ]
        )
        client.post = AsyncMock(return_value=player_resp)
        client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, interaction, ""))
        assert choices == []

    def test_returns_empty_on_api_error(self):
        """Returns [] on any API failure."""
        interaction = _make_interaction()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=RuntimeError("boom"))

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, interaction, ""))
        assert choices == []

    def test_filters_by_current_input(self):
        """Only items matching the current search term are returned."""
        interaction = _make_interaction()
        client = AsyncMock()

        player_resp = _make_response({"id": 1})
        ships_resp = _make_response(
            [
                {
                    "id": 1,
                    "ship_name": "Betty",
                    "is_active": True,
                    "weapons": ["Pulse Laser", "Micro Gun"],
                    "modules": ["Shield Gen"],
                    "turrets": [],
                    "secondary_weapons": [],
                }
            ]
        )
        client.post = AsyncMock(return_value=player_resp)
        client.get = AsyncMock(return_value=ships_resp)

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, interaction, "Pulse"))

        names = {c.value for c in choices}
        assert "Pulse Laser" in names
        assert "Micro Gun" not in names
        assert "Shield Gen" not in names


# ---------------------------------------------------------------------------
# Diagnostic logging tests (O.1 fix)
#
# The module-level `logger` is a MagicMock (because bblogger is mocked at
# import time).  To make pytest's caplog fixture capture real log records,
# each test below replaces `utils.autocomplete_helpers.logger` with a real
# logging.Logger for the duration of the test, then restores it.
# ---------------------------------------------------------------------------


class TestAutocompleteExceptionLogging:
    """Verify that each helper emits a WARNING log when an exception occurs."""

    def test_resolve_player_id_logs_warning_on_exception(self, caplog):
        """resolve_player_id logs WARNING with exc_info when an exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
        ):
            client = MagicMock()
            client.post = AsyncMock(side_effect=RuntimeError("network down"))
            result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))

        assert result is None
        assert any("resolve_player_id" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records), (
            f"Expected WARNING log from resolve_player_id; got: {[r.message for r in caplog.records]}"
        )

    def test_player_ships_autocomplete_logs_warning_on_exception(self, caplog):
        """player_ships_autocomplete logs WARNING with exc_info when an exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
        ):
            client = MagicMock()
            # Player resolves OK, but ship fetch raises
            client.post = AsyncMock(return_value=_make_response({"id": 7}))
            client.get = AsyncMock(side_effect=RuntimeError("ships API down"))
            choices = asyncio.run(
                player_ships_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_ships_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        ), f"Expected WARNING log from player_ships_autocomplete; got: {[r.message for r in caplog.records]}"

    def test_player_inventory_autocomplete_logs_warning_on_exception(self, caplog):
        """player_inventory_autocomplete logs WARNING with exc_info when an exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
        ):
            client = MagicMock()
            client.post = AsyncMock(return_value=_make_response({"id": 7}))
            client.get = AsyncMock(side_effect=RuntimeError("inventory API down"))
            choices = asyncio.run(
                player_inventory_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_inventory_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        ), f"Expected WARNING log from player_inventory_autocomplete; got: {[r.message for r in caplog.records]}"

    def test_player_equippable_autocomplete_logs_warning_on_exception(self, caplog):
        """player_equippable_autocomplete logs WARNING with exc_info when an exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=_make_response({"id": 7}))
            client.get = AsyncMock(side_effect=RuntimeError("equippable API down"))
            choices = asyncio.run(
                player_equippable_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_equippable_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        ), f"Expected WARNING log from player_equippable_autocomplete; got: {[r.message for r in caplog.records]}"

    def test_player_equipped_autocomplete_logs_warning_on_exception(self, caplog):
        """player_equipped_autocomplete logs WARNING with exc_info when an exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=_make_response({"id": 7}))
            client.get = AsyncMock(side_effect=RuntimeError("equipped API down"))
            choices = asyncio.run(
                player_equipped_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_equipped_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        ), f"Expected WARNING log from player_equipped_autocomplete; got: {[r.message for r in caplog.records]}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
