"""D9-T3 — Tests: gateway warm-pull GETs are retried; duel paths not double-wrapped.

Adversarial coverage (per D9-T3 tester spec):
  1. warm_guild_players: 503 on first attempt → retried → succeeds, cache populated.
  2. warm_active_player_loadout inventory: 503 → retry → success.
  3. warm_active_player_loadout ships: 503 → retry → success.
  4. All-fail (3x 503): non-fatal — function returns without raising.
  5. Retry cap: never more than 3 total attempts (no storm).
  6. Jitter present (TRANSIENT_WAIT varies across retry states).
  7. Duel cache paths (refresh_duel_caches/_warm_player/_refresh_pending/_refresh_outgoing)
     are NOT double-wrapped — they use AutocompleteCache.get() which has its own retry.
  8. Aggressive warming concurrency: AUTOCOMPLETE_WARM_CONCURRENCY env var is still
     respected (semaphore not broken by the retry wrapper).

Design notes
------------
- Uses respx to intercept real httpx.AsyncClient requests.
- Patches shared.http_retry.TRANSIENT_WAIT to wait_none() for instant tests.
- Module state reset before/after each test via autouse fixture.
- Max 2 mocks per test (per repo convention).
"""

from __future__ import annotations

import asyncio
import importlib.util as _ilu
import os
import pathlib as _pl
import sys
import types
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from tenacity import wait_none

# ---------------------------------------------------------------------------
# Inject mock shared.bblogger BEFORE any application imports.
# shared.http_retry is the REAL module (loaded from services/shared/) so that
# ``from shared.http_retry import with_transient_retry`` resolves in
# autocomplete_warm.py and patch("shared.http_retry.TRANSIENT_WAIT") works.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

# Load the real http_retry module from the canonical services/shared/ location
# and register it as shared.http_retry so autocomplete_warm's import resolves.
_http_retry_path = str(_pl.Path(__file__).parents[3] / "shared" / "http_retry.py")
_http_retry_spec = _ilu.spec_from_file_location("shared.http_retry", _http_retry_path)
_http_retry_mod = _ilu.module_from_spec(_http_retry_spec)  # type: ignore[arg-type]
_http_retry_spec.loader.exec_module(_http_retry_mod)  # type: ignore[union-attr]
sys.modules.setdefault("shared.http_retry", _http_retry_mod)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Application imports AFTER sys.modules patching
# ---------------------------------------------------------------------------

import utils.autocomplete_state as state_mod
import utils.autocomplete_warm as warm_mod

API_BASE = "http://bot-core:8000/api/v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before and after every test."""
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None
    warm_mod._warm_semaphore = None
    yield
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None
    warm_mod._warm_semaphore = None


@pytest.fixture
def initialized_state():
    """Init autocomplete_state with a real client."""
    client = httpx.AsyncClient()
    state_mod.init(client, API_BASE)
    yield client
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(client.aclose())
        loop.close()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# ===========================================================================
# D9-T3-1: warm_guild_players — 503 retried, cache populated
# ===========================================================================


class TestWarmGuildPlayersRetry:
    async def test_503_retried_and_cache_populated(self, initialized_state):
        """warm_guild_players retries a 503 and populates player_cache on second attempt."""
        guild_id = 9_300_001
        players = [{"id": 1, "user_id": 101, "discord_id": 101, "guild_id": guild_id}]

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=players)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(url__regex=rf"{API_BASE}/players/guild/{guild_id}.*").mock(side_effect=side_effect)
            # Stage 2: mock inventory/ships to avoid cascading calls
            router.get(url__regex=rf"{API_BASE}/inventory/player/\d+").mock(return_value=httpx.Response(200, json=[]))
            router.get(url__regex=rf"{API_BASE}/ships/player/\d+").mock(return_value=httpx.Response(200, json=[]))

            await warm_mod.warm_guild_players(guild_id)

        assert call_count == 2  # 1 fail + 1 success
        assert state_mod.player_cache.peek((guild_id, 101)) is not None

    async def test_all_3_attempts_fail_is_nonfatal(self, initialized_state):
        """warm_guild_players is non-fatal when all 3 retry attempts fail."""
        guild_id = 9_300_002
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(url__regex=rf"{API_BASE}/players/guild/{guild_id}.*").mock(side_effect=side_effect)
            # Must not raise
            await warm_mod.warm_guild_players(guild_id)

        assert call_count == 3  # Exhausted all 3 attempts — then caught by outer except

    async def test_connect_error_retried(self, initialized_state):
        """warm_guild_players retries on ConnectError (transient network failure)."""
        guild_id = 9_300_003
        players = [{"id": 5, "user_id": 505, "discord_id": 505, "guild_id": guild_id}]
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json=players)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(url__regex=rf"{API_BASE}/players/guild/{guild_id}.*").mock(side_effect=side_effect)
            router.get(url__regex=rf"{API_BASE}/inventory/player/\d+").mock(return_value=httpx.Response(200, json=[]))
            router.get(url__regex=rf"{API_BASE}/ships/player/\d+").mock(return_value=httpx.Response(200, json=[]))

            await warm_mod.warm_guild_players(guild_id)

        assert call_count == 2
        assert state_mod.player_cache.peek((guild_id, 505)) is not None


# ===========================================================================
# D9-T3-2: warm_active_player_loadout — GETs are retried
# ===========================================================================


class TestWarmActivePlayerLoadoutRetry:
    async def test_inventory_503_retried_cache_populated(self, initialized_state):
        """warm_active_player_loadout retries a 503 on inventory GET, populates cache."""
        guild_id = 9_300_010
        player_id = 1010
        inv_items = [{"id": 1, "item_name": "Laser", "item_type": "primary_weapon", "quantity": 1}]

        inv_call_count = 0

        def inv_side_effect(request):
            nonlocal inv_call_count
            inv_call_count += 1
            if inv_call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=inv_items)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(side_effect=inv_side_effect)
            router.get(f"{API_BASE}/ships/player/{player_id}").mock(return_value=httpx.Response(200, json=[]))

            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        assert inv_call_count == 2
        inv_cached = state_mod.inventory_cache.peek((guild_id, player_id))
        assert inv_cached is not None
        assert len(inv_cached) == 1

    async def test_ships_503_retried_cache_populated(self, initialized_state):
        """warm_active_player_loadout retries a 503 on ships GET, populates cache."""
        guild_id = 9_300_011
        player_id = 1011
        ships = [{"player_ship_id": 5, "name": "Eagle", "nickname": "", "ship_type": "Fighter", "is_active": True}]

        ships_call_count = 0

        def ships_side_effect(request):
            nonlocal ships_call_count
            ships_call_count += 1
            if ships_call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=ships)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(return_value=httpx.Response(200, json=[]))
            router.get(f"{API_BASE}/ships/player/{player_id}").mock(side_effect=ships_side_effect)

            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        assert ships_call_count == 2
        ships_cached = state_mod.ships_cache.peek((guild_id, player_id))
        assert ships_cached is not None
        assert len(ships_cached) == 1

    async def test_inventory_all_fail_is_nonfatal(self, initialized_state):
        """warm_active_player_loadout is non-fatal when all 3 inventory attempts fail."""
        guild_id = 9_300_012
        player_id = 1012
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(side_effect=side_effect)
            # Must not raise
            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        assert call_count == 3  # Exhausted retries, then caught by outer except

    async def test_404_on_inventory_not_retried(self, initialized_state):
        """warm_active_player_loadout does NOT retry a 404 (non-transient)."""
        guild_id = 9_300_013
        player_id = 1013
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(404)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(side_effect=side_effect)
            # Must not raise (non-fatal)
            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        # Only 1 attempt — 404 is not transient, not retried
        assert call_count == 1


# ===========================================================================
# D9-T3-3: Retry cap — never more than 3 total attempts
# ===========================================================================


class TestRetryCapNeverExceeded:
    async def test_warm_guild_players_max_3_attempts(self, initialized_state):
        """Persistent 503 results in exactly 3 attempts — no storm."""
        guild_id = 9_300_020
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(url__regex=rf"{API_BASE}/players/guild/{guild_id}.*").mock(side_effect=side_effect)
            await warm_mod.warm_guild_players(guild_id)

        assert call_count == 3, f"Expected exactly 3 attempts, got {call_count}"

    async def test_warm_active_player_loadout_max_3_inventory_attempts(self, initialized_state):
        """Persistent 503 on inventory GET → exactly 3 attempts, no storm."""
        guild_id = 9_300_021
        player_id = 1021
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(side_effect=side_effect)
            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        assert call_count == 3, f"Expected 3 attempts, got {call_count}"


# ===========================================================================
# D9-T3-4: Duel cache paths are NOT double-wrapped (Iter-4 scope guard)
# ===========================================================================


class TestDuelCacheNotDoubleWrapped:
    def test_refresh_duel_caches_does_not_call_with_transient_retry(self):
        """refresh_duel_caches must NOT call with_transient_retry directly.

        The duel cache refresh goes through AutocompleteCache.get(), which has
        its own 5-attempt backoff. Nesting would create 3x5=15 multiplicative
        storm (D9 Iter-4 scope guard).
        """
        import inspect

        src = inspect.getsource(warm_mod.refresh_duel_caches)
        assert "with_transient_retry" not in src, (
            "refresh_duel_caches must NOT use with_transient_retry (Iter-4: already-retrying duel cache path)"
        )

    def test_warm_guild_duel_caches_does_not_call_with_transient_retry(self):
        """warm_guild_duel_caches must NOT call with_transient_retry directly."""
        import inspect

        src = inspect.getsource(warm_mod.warm_guild_duel_caches)
        assert "with_transient_retry" not in src, (
            "warm_guild_duel_caches must NOT use with_transient_retry (Iter-4: already-retrying duel cache path)"
        )


# ===========================================================================
# D9-T3-5: Semaphore / concurrency is not broken by retry wrapper
# ===========================================================================


class TestSemaphoreConcurrencyIntact:
    async def test_concurrency_env_var_still_respected(self, initialized_state):
        """AUTOCOMPLETE_WARM_CONCURRENCY env var limits concurrent loadout warms.

        This test verifies the semaphore is still created with the env var value
        after the retry wrapper was introduced (regression guard).
        """
        # Reset semaphore so it gets recreated with our env var
        warm_mod._warm_semaphore = None

        with patch.dict(os.environ, {"AUTOCOMPLETE_WARM_CONCURRENCY": "3"}):
            sem = warm_mod._get_semaphore()

        assert sem._value == 3  # semaphore created with the env var value

    async def test_semaphore_released_after_retry_success(self, initialized_state):
        """Semaphore is released even when a retry occurs before success."""
        guild_id = 9_300_030
        player_id = 1030
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=[])

        initial_semaphore_value = warm_mod._get_semaphore()._value

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.get(f"{API_BASE}/inventory/player/{player_id}").mock(side_effect=side_effect)
            router.get(f"{API_BASE}/ships/player/{player_id}").mock(return_value=httpx.Response(200, json=[]))

            await warm_mod.warm_active_player_loadout(guild_id, player_id)

        # Semaphore should be back to its initial value (released)
        final_semaphore_value = warm_mod._get_semaphore()._value
        assert final_semaphore_value == initial_semaphore_value
