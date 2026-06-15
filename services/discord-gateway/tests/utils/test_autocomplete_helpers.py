"""Tests for the shared autocomplete helpers — Phase 4 rewired version.

Phase 4: helpers read from ``autocomplete_state`` shared caches instead of
making HTTP calls per keystroke.  Tests pre-populate the cache via
``autocomplete_state.set_player``, ``set_inventory``, ``set_ships`` and verify
that the HTTP client is NEVER called on the warm path.

Backward-compat tests that previously mocked HTTP are updated to pre-populate
the cache instead.  The public function signatures are unchanged (AC-COMPAT-1).
"""

import asyncio
import logging
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Inject mock shared.bblogger BEFORE importing any application modules.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Now import application modules (after bblogger mock is in place).
# ---------------------------------------------------------------------------

import utils.autocomplete_helpers as _autocomplete_helpers_mod
import utils.autocomplete_state as autocomplete_state
from utils.autocomplete_utils import normalize_for_search

_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES = _autocomplete_helpers_mod._CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
player_equippable_autocomplete = _autocomplete_helpers_mod.player_equippable_autocomplete
player_equipped_autocomplete = _autocomplete_helpers_mod.player_equipped_autocomplete
player_inventory_autocomplete = _autocomplete_helpers_mod.player_inventory_autocomplete
player_ships_autocomplete = _autocomplete_helpers_mod.player_ships_autocomplete
resolve_player_id = _autocomplete_helpers_mod.resolve_player_id
NormalizedChoice = autocomplete_state.NormalizedChoice

API_BASE = "http://bot-core:8000/api/v1"

# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------


def _make_interaction(user_id=111, guild_id=222):
    inter = MagicMock()
    inter.user = MagicMock()
    inter.user.id = user_id
    inter.guild_id = guild_id
    return inter


def _make_player_nc(player_id: int, guild_id: int = 222, user_id: int = 111) -> dict:
    """Return a minimal player dict for player_cache."""
    return {"id": player_id, "guild_id": guild_id, "discord_id": user_id, "tier": "bronze", "credits": 100}


def _make_ship_nc(
    ship_id: int,
    ship_name: str,
    is_active: bool = False,
    nickname: str = "",
    weapons: list | None = None,
    modules: list | None = None,
    turrets: list | None = None,
    secondary_weapons: list | None = None,
) -> NormalizedChoice:
    """Build a NormalizedChoice for a ship entry."""
    raw = {
        "id": ship_id,
        "ship_name": ship_name,
        "is_active": is_active,
        "nickname": nickname,
        "weapons": weapons or [],
        "modules": modules or [],
        "turrets": turrets or [],
        "secondary_weapons": secondary_weapons or [],
    }
    label = f"{ship_name} ({nickname})" if nickname else ship_name
    if is_active:
        label = f"🟢 {label}"
    return NormalizedChoice(label=label, value=str(ship_id), norm=normalize_for_search(label), raw=raw)


def _make_inv_nc(
    item_name: str,
    item_type: str,
    quantity: int = 1,
    item_id: int = 0,
) -> NormalizedChoice:
    """Build a NormalizedChoice for an inventory item."""
    raw = {
        "id": item_id or hash(item_name),
        "item_name": item_name,
        "item_type": item_type,
        "quantity": quantity,
    }
    qty_suffix = f" x{quantity}" if quantity > 1 else ""
    label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
    return NormalizedChoice(label=label, value=item_name, norm=normalize_for_search(label), raw=raw)


def _init_state_with_real_caches():
    """Initialize autocomplete_state with minimal real caches (no HTTP client needed)."""
    from cogs._shared.autocomplete_cache import AutocompleteCache

    # Reset module state
    autocomplete_state._initialized = False
    autocomplete_state._http_client = None
    autocomplete_state._api_base = None

    # Create real caches (no refresh_fn so schedule_refresh is a no-op)
    autocomplete_state.player_cache = AutocompleteCache(ttl_seconds=900, name="player")
    autocomplete_state.inventory_cache = AutocompleteCache(ttl_seconds=600, name="inventory")
    autocomplete_state.ships_cache = AutocompleteCache(ttl_seconds=600, name="ships")
    autocomplete_state._initialized = True


def _reset_state():
    """Reset autocomplete_state to uninitialized."""
    autocomplete_state._initialized = False
    autocomplete_state._http_client = None
    autocomplete_state._api_base = None
    autocomplete_state.player_cache = None
    autocomplete_state.inventory_cache = None
    autocomplete_state.ships_cache = None


def _make_raising_client():
    """Return a mock HTTP client that raises if any method is called."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=AssertionError("HTTP client must NOT be called in Phase 4"))
    client.get = AsyncMock(side_effect=AssertionError("HTTP client must NOT be called in Phase 4"))
    client.put = AsyncMock(side_effect=AssertionError("HTTP client must NOT be called in Phase 4"))
    return client


# ---------------------------------------------------------------------------
# resolve_player_id — Phase 4 cache-based tests
# ---------------------------------------------------------------------------


class TestResolvePlayerId:
    """Phase 4 tests for resolve_player_id."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def test_resolve_player_id_warm_returns_id(self):
        """Warm cache hit returns player id immediately without HTTP."""
        player = _make_player_nc(42)
        autocomplete_state.player_cache.set((222, 111), player)

        client = _make_raising_client()
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result == 42

    def test_resolve_player_id_cold_returns_none_and_schedules_refresh(self):
        """Cold miss returns None and fires schedule_refresh (does not HTTP)."""
        client = _make_raising_client()
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result is None

    def test_returns_none_when_state_not_initialized(self):
        """Returns None gracefully if autocomplete_state.player_cache is None."""
        _reset_state()  # player_cache is None
        client = _make_raising_client()
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result is None

    def test_cold_miss_does_not_call_http(self):
        """On cold miss, the HTTP client is never invoked."""
        client = _make_raising_client()
        # Should NOT raise even though client raises on any call
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result is None
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_warm_hit_does_not_call_http(self):
        """On warm hit, the HTTP client is never invoked."""
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        client = _make_raising_client()
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result == 7
        client.post.assert_not_called()
        client.get.assert_not_called()


# ---------------------------------------------------------------------------
# player_ships_autocomplete — Phase 4 cache-based tests
# ---------------------------------------------------------------------------


class TestPlayerShipsAutocomplete:
    """Phase 4 tests for player_ships_autocomplete."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def _populate(self, player_id=7, ships=None):
        autocomplete_state.player_cache.set((222, 111), {"id": player_id})
        if ships is not None:
            autocomplete_state.ships_cache.set((222, player_id), ships)

    def test_player_ships_warm_returns_choices(self):
        """Warm cache returns matching ships without HTTP."""
        ships = [
            _make_ship_nc(1, "Behén", is_active=True),
            _make_ship_nc(2, "Mako", nickname="StarHunter"),
            _make_ship_nc(3, "Viper"),
        ]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "behen"))
        assert len(choices) == 1
        assert choices[0].value == "1"
        assert choices[0].name.startswith("🟢 ")
        assert "Behén" in choices[0].name

    def test_player_ships_excludes_active_when_flag_set(self):
        """exclude_active=True drops active ship from results."""
        ships = [
            _make_ship_nc(1, "Active Ship", is_active=True),
            _make_ship_nc(2, "Backup Ship"),
        ]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "", exclude_active=True))
        values = [c.value for c in choices]
        assert "1" not in values
        assert "2" in values

    def test_show_active_indicator_false(self):
        """show_active_indicator=False omits 🟢 prefix even for active ship."""
        ships = [_make_ship_nc(1, "Eagle", is_active=True)]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(
            player_ships_autocomplete(client, API_BASE, _make_interaction(), "", show_active_indicator=False)
        )
        assert len(choices) == 1
        assert not choices[0].name.startswith("🟢 ")

    def test_returns_empty_on_player_cache_miss(self):
        """If player is not in player_cache, returns []."""
        client = _make_raising_client()
        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_returns_empty_on_ships_cache_miss(self):
        """If ships cache empty, returns [] and schedules refresh."""
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        client = _make_raising_client()
        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_cold_miss_does_not_call_http(self):
        """Cold miss on player_cache does not call HTTP."""
        client = _make_raising_client()
        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_warm_cache_does_not_call_http(self):
        """Warm cache does not call HTTP."""
        ships = [_make_ship_nc(1, "Eagle", is_active=True)]
        self._populate(ships=ships)
        client = _make_raising_client()

        asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_nickname_included_in_label(self):
        """Nickname is included in the label when present."""
        ships = [_make_ship_nc(2, "Mako", nickname="StarHunter")]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert len(choices) == 1
        assert "StarHunter" in choices[0].name

    def test_accent_insensitive_filter(self):
        """Search is accent-insensitive: 'behen' matches 'Behén'."""
        ships = [_make_ship_nc(1, "Behén"), _make_ship_nc(2, "Mako")]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), "behen"))
        assert len(choices) == 1
        assert "Behén" in choices[0].name

    def test_empty_current_returns_all(self):
        """Empty current string returns all ships (up to 25)."""
        ships = [_make_ship_nc(i + 1, f"Ship{i + 1}") for i in range(5)]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert len(choices) == 5


# ---------------------------------------------------------------------------
# player_inventory_autocomplete — Phase 4 cache-based tests
# ---------------------------------------------------------------------------


class TestPlayerInventoryAutocomplete:
    """Phase 4 tests for player_inventory_autocomplete."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def _populate(self, player_id=7, items=None):
        autocomplete_state.player_cache.set((222, 111), {"id": player_id})
        if items is not None:
            autocomplete_state.inventory_cache.set((222, player_id), items)

    def test_player_inventory_item_type_filter(self):
        """item_type_filter restricts results to matching items only."""
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=3),
            _make_inv_nc("Shield Mk1", "module", quantity=1),
            _make_inv_nc("Plasma Turret", "turret_weapon", quantity=2),
        ]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(
            player_inventory_autocomplete(client, API_BASE, _make_interaction(), "", item_type_filter="primary_weapon")
        )
        assert len(choices) == 1
        assert choices[0].value == "Pulse Laser"
        assert "Primary Weapon" in choices[0].name
        assert "x3" in choices[0].name

    def test_no_filter_returns_all(self):
        """No item_type_filter returns all inventory items."""
        items = [
            _make_inv_nc("Gun", "primary_weapon"),
            _make_inv_nc("Shield", "module"),
        ]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert len(choices) == 2

    def test_quantity_suffix_shown_for_qty_gt_1(self):
        """Quantity suffix appears for quantity > 1."""
        items = [_make_inv_nc("Cannon", "primary_weapon", quantity=5)]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert "x5" in choices[0].name

    def test_no_quantity_suffix_for_qty_1(self):
        """No quantity suffix for quantity = 1."""
        items = [_make_inv_nc("Cannon", "primary_weapon", quantity=1)]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert "x1" not in choices[0].name

    def test_returns_empty_on_player_miss(self):
        """Returns [] when player not in cache."""
        client = _make_raising_client()
        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_returns_empty_on_inventory_miss(self):
        """Returns [] when inventory not cached yet."""
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        client = _make_raising_client()
        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_cold_miss_does_not_call_http(self):
        """Cold miss never calls HTTP client."""
        client = _make_raising_client()
        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_warm_cache_does_not_call_http(self):
        """Warm cache never calls HTTP client."""
        items = [_make_inv_nc("Gun", "primary_weapon")]
        self._populate(items=items)
        client = _make_raising_client()

        asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_search_filter(self):
        """Current string filters by substring match."""
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon"),
            _make_inv_nc("Micro Gun", "primary_weapon"),
        ]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), "Pulse"))
        assert len(choices) == 1
        assert choices[0].value == "Pulse Laser"

    def test_deduplicates_item_names(self):
        """Duplicate item_names are deduplicated (shows only first occurrence)."""
        items = [
            _make_inv_nc("Cannon", "primary_weapon", quantity=1),
            _make_inv_nc("Cannon", "primary_weapon", quantity=2),  # duplicate name
        ]
        self._populate(items=items)
        client = _make_raising_client()

        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = [c.value for c in choices]
        assert names.count("Cannon") == 1


# ---------------------------------------------------------------------------
# player_equippable_autocomplete — Phase 4 cache-based tests
# ---------------------------------------------------------------------------


class TestPlayerEquippableAutocomplete:
    """Phase 4 tests for player_equippable_autocomplete."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def _populate(self, player_id=7, items=None, ships=None):
        autocomplete_state.player_cache.set((222, 111), {"id": player_id})
        if items is not None:
            autocomplete_state.inventory_cache.set((222, player_id), items)
        if ships is not None:
            autocomplete_state.ships_cache.set((222, player_id), ships)

    def test_player_equippable_excludes_zero_quantity_items(self):
        """Items with quantity <= 0 (no cargo copies) are excluded.

        B.41: player_inventories.quantity is CARGO-ONLY. The gate is quantity <= 0,
        not an equipped-names check. An item with quantity=1 and also equipped on
        the ship still appears here because there is a cargo copy available.
        """
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=1),  # has cargo copy
            _make_inv_nc("Shield Gen", "module", quantity=0),  # no cargo copy
            _make_inv_nc("Big Cannon", "primary_weapon", quantity=2),  # has cargo copies
        ]
        # ships_cache no longer needed for equippable filter — only inventory quantity matters
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}
        assert "Shield Gen" not in names, "quantity=0 — must be excluded"
        assert "Pulse Laser" in names, "quantity=1 — cargo copy available, must appear"
        assert "Big Cannon" in names, "quantity=2 — cargo copies available, must appear"

    def test_player_equippable_shows_item_even_when_equipped_if_cargo_copy_exists(self):
        """An item that is equipped on the ship AND has quantity>=1 cargo copy still appears.

        This is the canonical B.41 bug scenario: the player has 1 copy equipped and
        1 copy in cargo. The old code excluded it via the equipped-names set; the
        correct behavior per B.41 is to show it because cargo quantity > 0.
        """
        # Simulate: "Pulse Laser" is equipped on the ship AND has 1 cargo copy
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=1),
        ]
        # Note: we only populate player_cache and inventory_cache now
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}
        assert "Pulse Laser" in names, "cargo copy available even though equipped — must appear"

    def test_player_equippable_includes_secondary_weapon_type(self):
        """secondary_weapon items are included in /equip autocomplete (CI-23).

        Secondaries are now buyable (CI-5) and equippable (CI-16).  They must appear
        in the /equip dropdown alongside primary_weapon, turret_weapon, and module.
        'ship' items remain excluded (never /equip-able via that surface).
        """
        items = [
            _make_inv_nc("Primary Gun", "primary_weapon"),
            _make_inv_nc("Seeker Missile", "secondary_weapon"),
            _make_inv_nc("Old Freighter", "ship"),  # ship — always excluded
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}
        assert "Seeker Missile" in names, "secondary_weapon with quantity>0 must appear in /equip autocomplete"
        assert "Primary Gun" in names
        assert "Old Freighter" not in names, "ship type must never appear in /equip autocomplete"

    def test_cold_miss_does_not_call_http(self):
        """Cold miss on player cache does not call HTTP."""
        client = _make_raising_client()
        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_warm_cache_does_not_call_http(self):
        """Inventory cache warm — HTTP never called (ships_cache no longer needed)."""
        items = [_make_inv_nc("Cannon", "primary_weapon")]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_returns_empty_when_inventory_miss(self):
        """Returns [] when inventory not cached."""
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        client = _make_raising_client()
        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_ships_cache_miss_does_not_block_results(self):
        """ships_cache is no longer consulted — a miss does not block equippable results.

        Pre-fix, a cold ships_cache caused an early return []. Post-fix, only
        inventory_cache matters. This test verifies the fix holds.
        """
        items = [_make_inv_nc("Cannon", "primary_weapon", quantity=1)]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        # Deliberately do NOT populate ships_cache
        client = _make_raising_client()
        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        # Should return the item since quantity>0 and ships_cache is no longer required
        names = {c.value for c in choices}
        assert "Cannon" in names

    def test_returns_all_equippable_types_regardless_of_active_ship(self):
        """All equippable-type items with quantity>0 are returned; no ship state needed."""
        items = [
            _make_inv_nc("Cannon", "primary_weapon"),
            _make_inv_nc("Shield", "module"),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}
        assert "Cannon" in names
        assert "Shield" in names

    def test_search_filter(self):
        """Current string filters equippable items."""
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon"),
            _make_inv_nc("Shield Gen", "module"),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), "Pulse"))
        assert len(choices) == 1
        assert choices[0].value == "Pulse Laser"

    def test_constants_include_secondary_weapon(self):
        """_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES MUST contain 'secondary_weapon' (CI-23).

        Secondaries are now buyable (CI-5) and equippable (CI-16), so they must appear
        in /equip autocomplete alongside primary_weapon, turret_weapon, and module.
        'ship' is never equippable via /equip and must remain excluded.
        """
        assert "secondary_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "ship" not in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "primary_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "turret_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES
        assert "module" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES

    def test_excludes_items_with_zero_quantity(self):
        """Items with quantity=0 (no cargo copies) are excluded regardless of slot.

        Post-fix: the gate is quantity <= 0. Items with quantity > 0 appear even
        if they are also in the ship loadout (B.41).
        """
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=0),
            _make_inv_nc("Shield", "module", quantity=0),
            _make_inv_nc("Turret Mk1", "turret_weapon", quantity=0),
        ]
        # Only inventory_cache needed — no ships_cache population
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == [], "all items have quantity=0 — must be excluded"


# ---------------------------------------------------------------------------
# player_equipped_autocomplete — Phase 4 cache-based tests
# ---------------------------------------------------------------------------


class TestPlayerEquippedAutocomplete:
    """Phase 4 tests for player_equipped_autocomplete."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def _populate(self, player_id=7, ships=None):
        autocomplete_state.player_cache.set((222, 111), {"id": player_id})
        if ships is not None:
            autocomplete_state.ships_cache.set((222, player_id), ships)

    def test_player_equipped_returns_loadout_items(self):
        """Returns items from all loadout slots of the active ship."""
        ships = [
            _make_ship_nc(
                1,
                "Betty",
                is_active=True,
                weapons=["Pulse Laser"],
                modules=["Shield Gen"],
                turrets=["Beam Turret"],
            )
        ]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}
        assert "Pulse Laser" in names
        assert "Shield Gen" in names
        assert "Beam Turret" in names

    def test_returns_empty_when_no_active_ship(self):
        """Returns [] when no active ship is found."""
        ships = [_make_ship_nc(1, "Betty", is_active=False)]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []

    def test_cold_miss_does_not_call_http(self):
        """Cold miss on player cache never calls HTTP."""
        client = _make_raising_client()
        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_warm_cache_does_not_call_http(self):
        """Warm caches never calls HTTP."""
        ships = [_make_ship_nc(1, "Betty", is_active=True, weapons=["Gun"])]
        self._populate(ships=ships)
        client = _make_raising_client()

        asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_filters_by_current_input(self):
        """Only equipped items matching current are returned."""
        ships = [
            _make_ship_nc(
                1,
                "Betty",
                is_active=True,
                weapons=["Pulse Laser", "Micro Gun"],
                modules=["Shield Gen"],
            )
        ]
        self._populate(ships=ships)
        client = _make_raising_client()

        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), "Pulse"))
        names = {c.value for c in choices}
        assert "Pulse Laser" in names
        assert "Micro Gun" not in names
        assert "Shield Gen" not in names

    def test_returns_empty_on_ships_miss(self):
        """Returns [] when ships_cache miss."""
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        client = _make_raising_client()
        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []


# ---------------------------------------------------------------------------
# Cross-cutting: test_cold_miss_does_not_call_http for all helpers
# ---------------------------------------------------------------------------


class TestColdMissNeverCallsHttp:
    """Verify that cold misses across ALL helpers never issue HTTP calls."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def test_resolve_player_id_cold(self):
        client = _make_raising_client()
        result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        assert result is None
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_player_ships_cold(self):
        client = _make_raising_client()
        choices = asyncio.run(player_ships_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_player_inventory_cold(self):
        client = _make_raising_client()
        choices = asyncio.run(player_inventory_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_player_equippable_cold(self):
        client = _make_raising_client()
        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    def test_player_equipped_cold(self):
        client = _make_raising_client()
        choices = asyncio.run(player_equipped_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Diagnostic logging tests (O.1 fix — preserved from original test suite)
#
# In Phase 4 these test the warning paths that fire when the autocomplete_state
# itself raises (e.g. unexpected attribute errors). The module-level logger
# mock is replaced with a real logger for the duration of each test.
# ---------------------------------------------------------------------------


class TestAutocompleteExceptionLogging:
    """Verify that each helper emits a WARNING log when an unexpected exception occurs."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def test_resolve_player_id_logs_warning_on_exception(self, caplog):
        """resolve_player_id logs WARNING when an unexpected exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        # Simulate unexpected exception by corrupting the player_cache peek method
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
            patch.object(autocomplete_state.player_cache, "peek", side_effect=RuntimeError("unexpected")),
        ):
            client = MagicMock()
            result = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))

        assert result is None
        assert any("resolve_player_id" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records)

    def test_player_ships_autocomplete_logs_warning_on_exception(self, caplog):
        """player_ships_autocomplete logs WARNING when an unexpected exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        # Populate player cache, then corrupt ships_cache peek
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
            patch.object(autocomplete_state.ships_cache, "peek", side_effect=RuntimeError("unexpected")),
        ):
            client = MagicMock()
            choices = asyncio.run(
                player_ships_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_ships_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        )

    def test_player_inventory_autocomplete_logs_warning_on_exception(self, caplog):
        """player_inventory_autocomplete logs WARNING when an unexpected exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
            patch.object(autocomplete_state.inventory_cache, "peek", side_effect=RuntimeError("unexpected")),
        ):
            client = MagicMock()
            choices = asyncio.run(
                player_inventory_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_inventory_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        )

    def test_player_equippable_autocomplete_logs_warning_on_exception(self, caplog):
        """player_equippable_autocomplete logs WARNING when an unexpected exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
            patch.object(autocomplete_state.inventory_cache, "peek", side_effect=RuntimeError("unexpected")),
        ):
            client = MagicMock()
            choices = asyncio.run(
                player_equippable_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_equippable_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        )

    def test_player_equipped_autocomplete_logs_warning_on_exception(self, caplog):
        """player_equipped_autocomplete logs WARNING when an unexpected exception is swallowed."""
        real_logger = logging.getLogger("discord-gateway-autocomplete-helpers")
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        with (
            patch.object(_autocomplete_helpers_mod, "logger", real_logger),
            caplog.at_level(logging.WARNING, logger=real_logger.name),
            patch.object(autocomplete_state.ships_cache, "peek", side_effect=RuntimeError("unexpected")),
        ):
            client = MagicMock()
            choices = asyncio.run(
                player_equipped_autocomplete(client, API_BASE, _make_interaction(user_id=111, guild_id=222), "")
            )

        assert choices == []
        assert any(
            "player_equipped_autocomplete" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        )


# ---------------------------------------------------------------------------
# Adversarial edge case tests (added by QA Reviewer, Phase 4 review)
# ---------------------------------------------------------------------------


class TestAdversarialEdgeCases:
    """Adversarial and boundary tests not covered by the original 51-test suite."""

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    # ------------------------------------------------------------------
    # Filter persistence: empty-string item_type_filter behaves as "no filter"
    # ------------------------------------------------------------------

    def test_inventory_empty_string_filter_returns_all(self):
        """item_type_filter='' (empty string) is falsy → treated as no filter → returns all items.

        The production guard is ``if item_type_filter and item_type != item_type_filter``.
        An empty string is falsy, so the filter is skipped and all items pass through.
        This is the documented "no filter" behaviour for falsy values.
        """
        items = [
            _make_inv_nc("Pulse Laser", "primary_weapon"),
            _make_inv_nc("Shield Mk1", "module"),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        # Empty string → falsy → no filtering applied → all items returned
        choices = asyncio.run(
            player_inventory_autocomplete(client, API_BASE, _make_interaction(), "", item_type_filter="")
        )
        assert len(choices) == 2, "empty-string filter must return ALL items (no filtering)"
        values = {c.value for c in choices}
        assert "Pulse Laser" in values
        assert "Shield Mk1" in values

    def test_inventory_none_filter_returns_all(self):
        """item_type_filter=None (default) returns all items without filtering.

        This explicitly documents the None-as-no-filter contract that is relied
        on by callers that omit the argument.
        """
        items = [
            _make_inv_nc("Gun", "primary_weapon"),
            _make_inv_nc("Turret", "turret_weapon"),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(
            player_inventory_autocomplete(client, API_BASE, _make_interaction(), "", item_type_filter=None)
        )
        assert len(choices) == 2

    # ------------------------------------------------------------------
    # Cold key called twice: schedule_refresh must not double-fire HTTP
    # ------------------------------------------------------------------

    def test_cold_key_called_twice_does_not_double_schedule(self):
        """Calling any helper twice for the same cold key triggers schedule_refresh twice
        on the cache, but since no refresh_fn is configured in tests the calls are
        no-ops.  This test confirms that the helper itself does not call the HTTP client
        on either invocation — the guard is stateless (peek → None → schedule → return []).

        The coalescing-to-one-HTTP-call guarantee is enforced inside AutocompleteCache.get()
        via its asyncio.Lock double-check pattern (tested separately in
        test_autocomplete_cache.py::test_schedule_refresh_concurrent_calls_coalesce).
        """
        client = _make_raising_client()
        # First call — cold miss
        result1 = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))
        # Second call — still cold (no refresh_fn wired → cache still empty)
        result2 = asyncio.run(resolve_player_id(client, API_BASE, 111, 222))

        assert result1 is None
        assert result2 is None
        # Neither call should have touched the HTTP client
        client.post.assert_not_called()
        client.get.assert_not_called()

    # ------------------------------------------------------------------
    # player_ships_autocomplete: show_active_indicator=True — verify 🟢 label
    # ------------------------------------------------------------------

    def test_show_active_indicator_true_shows_green_circle(self):
        """show_active_indicator=True (default) prefixes the active ship label with '🟢 '.

        Explicitly named adversarial test to pin the exact indicator character used.
        """
        ships = [_make_ship_nc(10, "Flagship", is_active=True)]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.ships_cache.set((222, 7), ships)
        client = _make_raising_client()

        choices = asyncio.run(
            player_ships_autocomplete(client, API_BASE, _make_interaction(), "", show_active_indicator=True)
        )
        assert len(choices) == 1
        # Must use exactly 🟢 (green circle) — not ⚡ or any other character
        assert choices[0].name.startswith("🟢 "), f"Active ship indicator must be '🟢 ' but got: {choices[0].name!r}"

    # ------------------------------------------------------------------
    # player_equippable_autocomplete: cold miss on player_id
    # ------------------------------------------------------------------

    def test_equippable_cold_miss_player_cache_schedules_refresh_returns_empty(self):
        """When player_cache misses, equippable cannot resolve player_id.

        The function schedules a player refresh (via schedule_refresh) and returns [].
        HTTP client must not be called.
        """
        # player_cache is initialized but empty (cold)
        client = _make_raising_client()
        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert choices == []
        client.post.assert_not_called()
        client.get.assert_not_called()

    # ------------------------------------------------------------------
    # player_equippable_autocomplete: quantity-based filtering (B.41)
    # ------------------------------------------------------------------

    def test_equippable_uses_quantity_gate_not_equipped_names(self):
        """Equippable filter uses quantity <= 0 gate (B.41), not an equipped-names set.

        Equippable-type items with quantity > 0 appear regardless of whether they
        are also in the ship loadout. Items with quantity <= 0 are excluded.
        Only inventory_cache is needed — ships_cache is not consulted.

        Note: secondary_weapon IS now in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES (CI-23),
        so a secondary with quantity > 0 appears in the dropdown.  Only 'ship' type
        and items with quantity=0 are excluded.
        """
        items = [
            _make_inv_nc("Cannon", "primary_weapon"),  # quantity=1 (default)
            _make_inv_nc("Turret Alpha", "turret_weapon"),
            _make_inv_nc("ShieldV2", "module"),
            _make_inv_nc("Seeker", "secondary_weapon"),  # equippable since CI-23
            _make_inv_nc("EmptyGun", "primary_weapon", quantity=0),  # no cargo copy
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}

        # All equippable types with quantity > 0 returned (secondary_weapon now included — CI-23)
        assert "Cannon" in names
        assert "Turret Alpha" in names
        assert "ShieldV2" in names
        assert "Seeker" in names, "secondary_weapon with quantity>0 must appear since CI-23"
        # quantity=0 → excluded regardless of type
        assert "EmptyGun" not in names


# ---------------------------------------------------------------------------
# CI-23 tests: secondary_weapon now appears in /equip autocomplete
# ---------------------------------------------------------------------------


class TestCI23SecondaryWeaponEquippable:
    """CI-23: secondary_weapon is now equippable and must appear in /equip autocomplete.

    Previously gated out; CI-5 (buyable) + CI-16 (equippable wiring) made secondaries
    fully supported.  CI-23 removes the surface gate in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES.
    """

    def setup_method(self):
        _init_state_with_real_caches()

    def teardown_method(self):
        _reset_state()

    def test_secondary_weapon_in_equippable_types_constant(self):
        """_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES contains 'secondary_weapon' (CI-23)."""
        assert "secondary_weapon" in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES

    def test_secondary_weapon_in_cargo_appears_in_equip_autocomplete(self):
        """A secondary_weapon with quantity>0 in cargo shows up in /equip autocomplete.

        This is the canonical CI-23 regression test: before the fix the item was
        silently omitted from the dropdown; after the fix it appears alongside
        primary_weapon / turret_weapon / module items.
        """
        items = [
            _make_inv_nc("Seeker Missile", "secondary_weapon", quantity=2),
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=1),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}

        assert "Seeker Missile" in names, (
            "secondary_weapon with quantity>0 must appear in /equip autocomplete after CI-23"
        )
        assert "Pulse Laser" in names

    def test_secondary_weapon_zero_quantity_excluded(self):
        """secondary_weapon with quantity=0 (no cargo copy) is still excluded."""
        items = [
            _make_inv_nc("Seeker Missile", "secondary_weapon", quantity=0),
            _make_inv_nc("Pulse Laser", "primary_weapon", quantity=1),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}

        assert "Seeker Missile" not in names, "quantity=0 must be excluded regardless of type"
        assert "Pulse Laser" in names

    def test_ship_type_still_excluded(self):
        """'ship' item_type is never equippable via /equip regardless of CI-23."""
        items = [
            _make_inv_nc("Old Freighter", "ship", quantity=1),
            _make_inv_nc("Seeker Missile", "secondary_weapon", quantity=1),
        ]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        names = {c.value for c in choices}

        assert "Old Freighter" not in names, "ship type must never appear in /equip autocomplete"
        assert "Seeker Missile" in names

    def test_secondary_weapon_label_format(self):
        """secondary_weapon choice label uses 'Secondary Weapon' (title-cased from type)."""
        items = [_make_inv_nc("EMP Drone", "secondary_weapon", quantity=3)]
        autocomplete_state.player_cache.set((222, 111), {"id": 7})
        autocomplete_state.inventory_cache.set((222, 7), items)
        client = _make_raising_client()

        choices = asyncio.run(player_equippable_autocomplete(client, API_BASE, _make_interaction(), ""))
        assert len(choices) == 1
        assert "Secondary Weapon" in choices[0].name
        assert "x3" in choices[0].name
        assert choices[0].value == "EMP Drone"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
