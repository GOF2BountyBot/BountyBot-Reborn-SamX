"""Tests for the shared autocomplete_state module.

These tests cover init(), set/get/invalidate/clear helpers, and the
background refresh paths via a mocked httpx.AsyncClient (using respx).

IMPORTANT: autocomplete_state has module-level state. Each test that
calls init() must reset the module state afterward via the
``reset_autocomplete_state`` fixture.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# Inject mock shared.bblogger BEFORE any application imports.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Import module under test AFTER sys.modules patching
# ---------------------------------------------------------------------------

import utils.autocomplete_state as state_mod
from utils.autocomplete_utils import normalize_for_search

NormalizedChoice = state_mod.NormalizedChoice
clear_all = state_mod.clear_all
get_http_client = state_mod.get_http_client
get_player = state_mod.get_player
get_player_id = state_mod.get_player_id
invalidate_inventory = state_mod.invalidate_inventory
invalidate_player = state_mod.invalidate_player
invalidate_ships = state_mod.invalidate_ships
set_inventory = state_mod.set_inventory
set_player = state_mod.set_player
set_ships = state_mod.set_ships

API_BASE = "http://bot-core:8000/api/v1"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_autocomplete_state():
    """Reset module-level state before and after every test.

    This ensures tests are fully isolated: a call to init() in one test
    does not bleed into the next.
    """
    # Reset before
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None

    yield

    # Reset after
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None


@pytest.fixture
def real_http_client():
    """Provide a real httpx.AsyncClient for use with respx."""
    import asyncio

    client = httpx.AsyncClient()
    yield client
    # Close synchronously — tests are sync fixtures, so we call run_until_complete.
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(client.aclose())
        loop.close()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


@pytest.fixture
def initialized_state(real_http_client):
    """Init the module state and return the http_client for use in tests."""
    state_mod.init(real_http_client, API_BASE)
    return real_http_client


# ---------------------------------------------------------------------------
# Tests: init() idempotency
# ---------------------------------------------------------------------------


class TestInitIdempotent:
    """init() is idempotent — second call is a no-op."""

    def test_init_is_idempotent(self, real_http_client):
        """Calling init() twice with the same args leaves caches as identical objects."""
        state_mod.init(real_http_client, API_BASE)

        # Capture references to the created cache objects
        player_cache_first = state_mod.player_cache
        inventory_cache_first = state_mod.inventory_cache
        ships_cache_first = state_mod.ships_cache

        # Call init() again — must be a no-op
        state_mod.init(real_http_client, API_BASE)

        # Caches are the same objects (not re-created)
        assert state_mod.player_cache is player_cache_first
        assert state_mod.inventory_cache is inventory_cache_first
        assert state_mod.ships_cache is ships_cache_first

    def test_init_creates_all_three_caches(self, real_http_client):
        """After init(), all three caches are non-None."""
        state_mod.init(real_http_client, API_BASE)

        assert state_mod.player_cache is not None
        assert state_mod.inventory_cache is not None
        assert state_mod.ships_cache is not None

    def test_init_sets_http_client(self, real_http_client):
        """After init(), _http_client is set and get_http_client() returns it."""
        state_mod.init(real_http_client, API_BASE)

        assert get_http_client() is real_http_client

    def test_init_sets_initialized_flag(self, real_http_client):
        """After init(), _initialized is True."""
        assert state_mod._initialized is False
        state_mod.init(real_http_client, API_BASE)
        assert state_mod._initialized is True


# ---------------------------------------------------------------------------
# Tests: set_player / peek
# ---------------------------------------------------------------------------


class TestSetPlayerAndPeek:
    """set_player writes to cache; player_cache.peek() returns it."""

    def test_set_player_and_peek(self, initialized_state):
        """set_player stores a player dict; peek on the same key returns it."""
        player = {"id": 42, "discord_id": 111, "guild_id": 999, "tier": "Bronze"}

        set_player(guild_id=999, user_id=111, player=player)

        cached = state_mod.player_cache.peek((999, 111))
        assert cached is player

    def test_set_player_different_keys(self, initialized_state):
        """Two players with different keys are stored independently."""
        player_a = {"id": 1, "tier": "Bronze"}
        player_b = {"id": 2, "tier": "Gold"}

        set_player(guild_id=100, user_id=10, player=player_a)
        set_player(guild_id=100, user_id=20, player=player_b)

        assert state_mod.player_cache.peek((100, 10)) is player_a
        assert state_mod.player_cache.peek((100, 20)) is player_b

    def test_peek_unknown_key_returns_none(self, initialized_state):
        """peek() on a key that was never set returns None."""
        result = state_mod.player_cache.peek((1, 1))
        assert result is None


# ---------------------------------------------------------------------------
# Tests: invalidate_player
# ---------------------------------------------------------------------------


class TestInvalidatePlayer:
    """invalidate_player drops the cached entry."""

    def test_invalidate_player_clears_entry(self, initialized_state):
        """After set then invalidate, peek returns None."""
        player = {"id": 5, "tier": "Silver"}
        set_player(guild_id=200, user_id=50, player=player)

        # Sanity — entry is there before invalidation
        assert state_mod.player_cache.peek((200, 50)) is not None

        invalidate_player(guild_id=200, user_id=50)

        assert state_mod.player_cache.peek((200, 50)) is None

    def test_invalidate_player_idempotent(self, initialized_state):
        """Invalidating a key that was never set does not raise."""
        # Should not raise
        invalidate_player(guild_id=999, user_id=999)


# ---------------------------------------------------------------------------
# Tests: set_inventory / NormalizedChoice
# ---------------------------------------------------------------------------


class TestSetInventory:
    """set_inventory stores NormalizedChoice list; peek returns it."""

    def test_set_inventory_produces_normalized_choices(self, initialized_state):
        """Verify that a stored NormalizedChoice has a correctly pre-computed norm."""
        label = "Laser Cannon (Primary Weapon) [x2]"
        norm = normalize_for_search(label)
        choice = NormalizedChoice(label=label, value="42", norm=norm, raw={"id": 42})

        set_inventory(guild_id=100, player_id=10, items=[choice])

        cached = state_mod.inventory_cache.peek((100, 10))
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].norm == normalize_for_search(label)
        assert cached[0].label == label
        assert cached[0].value == "42"

    def test_set_inventory_empty_list(self, initialized_state):
        """set_inventory with an empty list caches an empty list (not None)."""
        set_inventory(guild_id=100, player_id=10, items=[])

        cached = state_mod.inventory_cache.peek((100, 10))
        assert cached == []


# ---------------------------------------------------------------------------
# Tests: clear_all
# ---------------------------------------------------------------------------


class TestClearAll:
    """clear_all() wipes all three caches."""

    def test_clear_all_clears_all_three_caches(self, initialized_state):
        """After populating all three caches, clear_all() leaves all as None on peek."""
        player = {"id": 1}
        label = "Ship (Corvette)"
        choice = NormalizedChoice(label=label, value="1", norm=normalize_for_search(label), raw={})

        set_player(guild_id=10, user_id=1, player=player)
        set_inventory(guild_id=10, player_id=1, items=[choice])
        set_ships(guild_id=10, player_id=1, ships=[choice])

        # Sanity
        assert state_mod.player_cache.peek((10, 1)) is not None
        assert state_mod.inventory_cache.peek((10, 1)) is not None
        assert state_mod.ships_cache.peek((10, 1)) is not None

        clear_all()

        assert state_mod.player_cache.peek((10, 1)) is None
        assert state_mod.inventory_cache.peek((10, 1)) is None
        assert state_mod.ships_cache.peek((10, 1)) is None


# ---------------------------------------------------------------------------
# Tests: invalidate_inventory / invalidate_ships
# ---------------------------------------------------------------------------


class TestInvalidateInventoryAndShips:
    """invalidate_inventory and invalidate_ships drop their respective cache entries."""

    def test_invalidate_inventory_clears_entry(self, initialized_state):
        """invalidate_inventory removes the stored entry."""
        label = "Module (Module)"
        choice = NormalizedChoice(label=label, value="5", norm=normalize_for_search(label), raw={})
        set_inventory(guild_id=10, player_id=5, items=[choice])

        invalidate_inventory(guild_id=10, player_id=5)

        assert state_mod.inventory_cache.peek((10, 5)) is None

    def test_invalidate_ships_clears_entry(self, initialized_state):
        """invalidate_ships removes the stored entry."""
        label = "Betty (Corvette)"
        choice = NormalizedChoice(label=label, value="99", norm=normalize_for_search(label), raw={})
        set_ships(guild_id=10, player_id=5, ships=[choice])

        invalidate_ships(guild_id=10, player_id=5)

        assert state_mod.ships_cache.peek((10, 5)) is None


# ---------------------------------------------------------------------------
# Tests: get_player (async, cold miss triggers refresh)
# ---------------------------------------------------------------------------


class TestGetPlayerColdMiss:
    """get_player() on a cold miss triggers a refresh via _refresh_player."""

    @respx.mock
    async def test_get_player_cold_miss_triggers_refresh(self, real_http_client):
        """Cold cache: await get_player() posts to /players/ and returns the mocked value."""
        # Use a real httpx client so respx can intercept
        state_mod.init(real_http_client, API_BASE)

        expected_player = {"id": 77, "user_id": 111, "guild_id": 999, "tier": "Bronze"}

        respx.post(f"{API_BASE}/players/").mock(return_value=httpx.Response(200, json=expected_player))

        result = await get_player(guild_id=999, user_id=111)

        assert result is not None
        assert result["id"] == 77
        assert result["tier"] == "Bronze"

        # Should now be cached
        cached = state_mod.player_cache.peek((999, 111))
        assert cached is not None
        assert cached["id"] == 77

    @respx.mock
    async def test_get_player_id_returns_id_field(self, real_http_client):
        """get_player_id() returns player['id'] from a successful fetch."""
        state_mod.init(real_http_client, API_BASE)

        respx.post(f"{API_BASE}/players/").mock(
            return_value=httpx.Response(200, json={"id": 42, "user_id": 111, "guild_id": 999})
        )

        result = await get_player_id(guild_id=999, user_id=111)

        assert result == 42

    @respx.mock
    async def test_get_player_id_returns_none_on_error(self, real_http_client):
        """get_player_id() returns None when bot-core returns 500."""
        state_mod.init(real_http_client, API_BASE)

        respx.post(f"{API_BASE}/players/").mock(return_value=httpx.Response(500, json={"detail": "internal error"}))

        result = await get_player_id(guild_id=999, user_id=111)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: _refresh_inventory pre-computes norm
# ---------------------------------------------------------------------------


class TestRefreshInventoryPrecomputesNorm:
    """_refresh_inventory builds NormalizedChoice with correct pre-computed norm."""

    async def test_refresh_inventory_pre_computes_norm(self, initialized_state):
        """Mock GET /inventory/player/{id}; verify NormalizedChoice.norm matches normalize_for_search(label).

        Uses respx.mock() as a context manager and references the refresh function via
        ``state_mod`` (the module-level reference established at test-file import time)
        rather than importing it inline with ``from utils.autocomplete_state import ...``.

        Inline imports of ``utils.*`` modules inside a test body can create a new module
        object if another test file in the same xdist worker process has previously called
        ``sys.modules.pop("utils.autocomplete_state", ...)`` via its ``_evict_discord_modules()``
        fixture helper (e.g. ``test_shopCog.py``).  In that case the inline-imported function
        reads from the evicted module's fresh globals (``_http_client = None``) rather than
        the module that ``initialized_state`` correctly configured — causing a spurious
        RuntimeError even though ``initialized_state`` was set up correctly.

        Using ``state_mod._refresh_inventory`` (where ``state_mod`` was imported at the TOP
        of this test file, before any eviction could happen) guarantees that both the fixture
        and the function under test operate on the SAME module object.
        """
        # initialized_state returns the real_http_client used to init the module
        _real_http_client = initialized_state  # kept for clarity; value unused, side-effect is module init

        items_response = [
            {
                "id": 1,
                "item_name": "Laser Cannon",
                "item_type": "primary_weapon",
                "quantity": 2,
            },
            {
                "id": 2,
                "item_name": "E2 Exoclad",
                "item_type": "module",
                "quantity": 1,
            },
        ]

        # Use state_mod._refresh_inventory (the function on the SAME module object that
        # initialized_state configured) rather than a fresh import that could produce a
        # new module object if utils.autocomplete_state was evicted from sys.modules.
        with respx.mock() as mock_router:
            mock_router.get(f"{API_BASE}/inventory/player/10").mock(
                return_value=httpx.Response(200, json=items_response)
            )

            choices = await state_mod._refresh_inventory((100, 10))

        assert len(choices) == 2

        # First item: quantity > 1, gets [x2] suffix
        laser_choice = choices[0]
        expected_label = "Laser Cannon (Primary Weapon) [x2]"
        assert laser_choice.label == expected_label
        assert laser_choice.norm == normalize_for_search(expected_label)
        assert laser_choice.value == "1"

        # Second item: quantity == 1, no suffix
        exoclad_choice = choices[1]
        expected_label_2 = "E2 Exoclad (Module)"
        assert exoclad_choice.label == expected_label_2
        assert exoclad_choice.norm == normalize_for_search(expected_label_2)

    async def test_refresh_ships_pre_computes_norm(self, initialized_state):
        """Mock GET /ships/player/{id}; verify NormalizedChoice.norm is correct.

        Uses ``state_mod._refresh_ships`` for the same reason as
        ``test_refresh_inventory_pre_computes_norm`` above — avoids a stale module
        reference caused by ``_evict_discord_modules()`` in cog test fixtures.
        """
        # initialized_state returns the real_http_client used to init the module
        _real_http_client = initialized_state  # kept for clarity; value unused, side-effect is module init

        ships_response = [
            {
                "player_ship_id": 55,
                "name": "Betty",
                "nickname": "Bette",
                "ship_type": "Corvette",
                "is_active": True,
            },
            {
                "player_ship_id": 66,
                "name": "Wraith",
                "nickname": "",
                "ship_type": "Fighter",
                "is_active": False,
            },
        ]

        with respx.mock() as mock_router:
            mock_router.get(f"{API_BASE}/ships/player/10").mock(return_value=httpx.Response(200, json=ships_response))

            choices = await state_mod._refresh_ships((100, 10))

        assert len(choices) == 2

        # Active ship uses nickname as display, with ⚡ prefix in type
        betty = choices[0]
        expected_label = "Bette (⚡ Corvette)"
        assert betty.label == expected_label
        assert betty.norm == normalize_for_search(expected_label)
        assert betty.value == "55"

        # Inactive ship uses name (no nickname), no ⚡
        wraith = choices[1]
        expected_label_2 = "Wraith (Fighter)"
        assert wraith.label == expected_label_2
        assert wraith.norm == normalize_for_search(expected_label_2)
        assert wraith.value == "66"


# ---------------------------------------------------------------------------
# Adversarial Tests: edge cases for _refresh_inventory and _refresh_ships
# ---------------------------------------------------------------------------


class TestRefreshInventoryAdversarial:
    """Edge-case and adversarial tests for _refresh_inventory and _refresh_ships.

    These tests exercise the boundary between cache-miss (None) and
    legitimately-empty cache ([]). The hot autocomplete path must distinguish
    between "we have never fetched for this player" (None from peek → trigger
    background refresh) and "we fetched but the player has no ships" ([] from
    peek → return empty choices immediately, no round-trip).
    """

    async def test_refresh_inventory_empty_api_response_returns_empty_list(self, initialized_state):
        """_refresh_inventory returns [] (not None) when the API returns an empty list.

        A player with zero inventory items is a valid state. The cache must
        store [] so that peek() returns [] (falsy but not None), preventing a
        spurious background refresh on every subsequent autocomplete keystroke.
        """
        with respx.mock() as mock_router:
            mock_router.get(f"{API_BASE}/inventory/player/42").mock(return_value=httpx.Response(200, json=[]))

            choices = await state_mod._refresh_inventory((100, 42))

        assert choices == []
        assert choices is not None  # [] is falsy but not None — explicit check

    async def test_refresh_ships_empty_api_response_returns_empty_list(self, initialized_state):
        """_refresh_ships returns [] (not None) when the API returns no ships.

        A player who has somehow lost all ships (or has not yet registered) would
        return an empty API list. The ships cache must store [] to distinguish
        the "no ships" state from "not yet cached" (None).
        """
        with respx.mock() as mock_router:
            mock_router.get(f"{API_BASE}/ships/player/42").mock(return_value=httpx.Response(200, json=[]))

            choices = await state_mod._refresh_ships((100, 42))

        assert choices == []
        assert choices is not None  # [] is falsy but not None — explicit check

    def test_none_vs_empty_list_distinction_in_ships_cache(self, initialized_state):
        """A never-fetched player key returns None (cache miss) from peek().

        A player with zero ships (set explicitly) returns [] from peek().
        The hot path distinguishes None → trigger refresh, [] → skip refresh.

        This is the critical invariant for the ships autocomplete: an empty
        list means 'the player genuinely has no ships', not 'we don't know yet'.
        """
        key_never_fetched = (999, 999)
        key_zero_ships = (999, 888)

        # A key that was never set returns None (cache miss)
        assert state_mod.ships_cache.peek(key_never_fetched) is None

        # Set an empty list explicitly (simulating zero-ship player)
        set_ships(guild_id=999, player_id=888, ships=[])

        # Peek on the zero-ship player returns [] — falsy but not None
        result = state_mod.ships_cache.peek(key_zero_ships)
        assert result == []
        assert result is not None, "[] should NOT be treated as a cache miss (only None is a miss)"

    def test_none_vs_empty_list_distinction_in_inventory_cache(self, initialized_state):
        """Same None-vs-[] distinction for the inventory cache.

        A never-fetched player key returns None (cache miss).
        A player with zero inventory items (set explicitly) returns [] — not None.
        """
        key_never_fetched = (888, 777)
        key_zero_items = (888, 666)

        assert state_mod.inventory_cache.peek(key_never_fetched) is None

        set_inventory(guild_id=888, player_id=666, items=[])

        result = state_mod.inventory_cache.peek(key_zero_items)
        assert result == []
        assert result is not None, "[] should NOT be treated as a cache miss (only None is a miss)"

    async def test_refresh_inventory_skips_items_with_empty_name(self, initialized_state):
        """_refresh_inventory skips items where item_name is empty or None.

        Protects against malformed API responses where item_name is missing.
        The production code has: ``if not item_name: continue``.
        """
        items_response = [
            {"id": 1, "item_name": "", "item_type": "module", "quantity": 1},  # empty name → skipped
            {"id": 2, "item_name": None, "item_type": "module", "quantity": 1},  # None name → skipped
            {"id": 3, "item_name": "E2 Exoclad", "item_type": "module", "quantity": 1},  # valid
        ]

        with respx.mock() as mock_router:
            mock_router.get(f"{API_BASE}/inventory/player/10").mock(
                return_value=httpx.Response(200, json=items_response)
            )

            choices = await state_mod._refresh_inventory((100, 10))

        # Only the item with a non-empty name should appear
        assert len(choices) == 1
        assert choices[0].label == "E2 Exoclad (Module)"


# ---------------------------------------------------------------------------
# Tests: uninitialized use raises RuntimeError
# ---------------------------------------------------------------------------


class TestUninitializedRaisesOrDegraces:
    """Calling public functions before init() raises RuntimeError."""

    def test_uninitialized_set_player_raises(self):
        """set_player before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            set_player(guild_id=1, user_id=1, player={"id": 1})

    def test_uninitialized_set_inventory_raises(self):
        """set_inventory before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            set_inventory(guild_id=1, player_id=1, items=[])

    def test_uninitialized_set_ships_raises(self):
        """set_ships before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            set_ships(guild_id=1, player_id=1, ships=[])

    def test_uninitialized_invalidate_player_raises(self):
        """invalidate_player before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            invalidate_player(guild_id=1, user_id=1)

    def test_uninitialized_invalidate_inventory_raises(self):
        """invalidate_inventory before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            invalidate_inventory(guild_id=1, player_id=1)

    def test_uninitialized_invalidate_ships_raises(self):
        """invalidate_ships before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            invalidate_ships(guild_id=1, player_id=1)

    def test_uninitialized_clear_all_raises(self):
        """clear_all before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            clear_all()

    async def test_uninitialized_get_player_raises(self):
        """await get_player() before init() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            await get_player(guild_id=1, user_id=1)

    def test_get_http_client_returns_none_before_init(self):
        """get_http_client() returns None before init() is called."""
        result = get_http_client()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: set_ships / peek
# ---------------------------------------------------------------------------


class TestSetShipsAndPeek:
    """set_ships writes to the ships cache; ships_cache.peek() returns it."""

    def test_set_ships_and_peek(self, initialized_state):
        """set_ships stores ships list; peek on the same key returns it."""
        label = "Betty (Corvette)"
        choice = NormalizedChoice(label=label, value="1", norm=normalize_for_search(label), raw={"is_active": True})

        set_ships(guild_id=100, player_id=10, ships=[choice])

        cached = state_mod.ships_cache.peek((100, 10))
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].label == label

    def test_set_ships_empty_list(self, initialized_state):
        """set_ships with an empty list caches an empty list."""
        set_ships(guild_id=100, player_id=10, ships=[])

        cached = state_mod.ships_cache.peek((100, 10))
        assert cached == []


# ---------------------------------------------------------------------------
# Tests: environment variable TTL/max_entries
# ---------------------------------------------------------------------------


class TestEnvVarConfiguration:
    """init() reads TTL and max_entries from environment variables."""

    def test_custom_player_ttl_from_env(self, real_http_client, monkeypatch):
        """AUTOCOMPLETE_PLAYER_TTL_SECONDS env var sets player_cache TTL."""
        monkeypatch.setenv("AUTOCOMPLETE_PLAYER_TTL_SECONDS", "300")
        state_mod.init(real_http_client, API_BASE)

        assert state_mod.player_cache is not None
        # TTL is stored as _ttl on the cache
        assert state_mod.player_cache._ttl == 300.0

    def test_custom_inventory_max_entries_from_env(self, real_http_client, monkeypatch):
        """AUTOCOMPLETE_INVENTORY_MAX_ENTRIES env var sets inventory_cache max_entries."""
        monkeypatch.setenv("AUTOCOMPLETE_INVENTORY_MAX_ENTRIES", "100")
        state_mod.init(real_http_client, API_BASE)

        assert state_mod.inventory_cache is not None
        assert state_mod.inventory_cache._max_entries == 100

    def test_no_max_entries_when_env_unset(self, real_http_client, monkeypatch):
        """When AUTOCOMPLETE_INVENTORY_MAX_ENTRIES is unset, max_entries is None."""
        monkeypatch.delenv("AUTOCOMPLETE_INVENTORY_MAX_ENTRIES", raising=False)
        state_mod.init(real_http_client, API_BASE)

        assert state_mod.inventory_cache is not None
        assert state_mod.inventory_cache._max_entries is None
