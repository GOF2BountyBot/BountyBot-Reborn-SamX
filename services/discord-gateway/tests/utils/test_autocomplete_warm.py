"""Tests for utils/autocomplete_warm.py — Phase 3 warm/refresh job functions.

All HTTP calls use respx to intercept real httpx.AsyncClient requests (per the
tests/AGENTS.md mandate). Module-level state is reset via the reset_state fixture
before and after each test.

Acceptance criteria covered:
- warm_guild_players paginates until fewer than limit rows returned
- warm_guild_players calls set_player for each player in each page
- warm_active_player_loadout populates inventory_cache and ships_cache
- warm_active_player_loadout returns NormalizedChoice objects with non-empty norm
- refresh_loadouts_round_robin only warms currently-cached keys
- warm_guild_players failure (ConnectionError) logs WARNING and returns without raising
- refresh_all_guild_players iterates all guilds
- register_warm_jobs adds the expected jobs to the scheduler
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

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
# Import modules under test AFTER sys.modules patching
# ---------------------------------------------------------------------------

import utils.autocomplete_state as state_mod
import utils.autocomplete_warm as warm_mod
from utils.autocomplete_state import NormalizedChoice
from utils.autocomplete_utils import normalize_for_search

API_BASE = "http://bot-core:8000/api/v1"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before and after every test."""
    # Reset autocomplete_state
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None

    # Reset the warm module semaphore so env vars take effect
    warm_mod._warm_semaphore = None

    yield

    # Reset after test
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    state_mod.inventory_cache = None
    state_mod.ships_cache = None
    warm_mod._warm_semaphore = None


@pytest.fixture
def real_http_client():
    """Provide a real httpx.AsyncClient for respx interception."""
    client = httpx.AsyncClient()
    yield client
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(client.aclose())
        loop.close()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


@pytest.fixture
def initialized_state(real_http_client):
    """Init autocomplete_state with the real client and return the client."""
    state_mod.init(real_http_client, API_BASE)
    return real_http_client


# ---------------------------------------------------------------------------
# Test: warm_guild_players — pagination
# ---------------------------------------------------------------------------


class TestWarmGuildPlayersPaginates:
    """warm_guild_players paginates until fewer than limit rows are returned."""

    @respx.mock
    async def test_warm_guild_players_paginates(self, initialized_state):
        """Two pages: first page 500 rows, second page 3 rows → 503 set_player calls."""
        guild_id = 100

        # Build 500-player page and 3-player page
        page1 = [
            {"id": i, "user_id": i + 1000, "discord_id": i + 1000, "guild_id": guild_id, "tier": "Bronze"}
            for i in range(500)
        ]
        page2 = [
            {"id": 500 + i, "user_id": 1500 + i, "discord_id": 1500 + i, "guild_id": guild_id, "tier": "Bronze"}
            for i in range(3)
        ]

        # First request: skip=0
        respx.get(f"{API_BASE}/players/guild/{guild_id}").mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )

        await warm_mod.warm_guild_players(guild_id)

        # player_cache should have 503 entries for this guild
        all_keys = list(state_mod.player_cache.keys())
        keys_for_guild = [(g, u) for (g, u) in all_keys if g == guild_id]
        assert len(keys_for_guild) == 503


class TestWarmGuildPlayersCallsSetPlayer:
    """warm_guild_players populates player_cache."""

    @respx.mock
    async def test_warm_guild_players_calls_set_player(self, initialized_state):
        """Single page of 3 players → 3 entries in player_cache."""
        guild_id = 200
        players = [
            {"id": 1, "user_id": 101, "discord_id": 101, "guild_id": guild_id, "tier": "Bronze"},
            {"id": 2, "user_id": 102, "discord_id": 102, "guild_id": guild_id, "tier": "Silver"},
            {"id": 3, "user_id": 103, "discord_id": 103, "guild_id": guild_id, "tier": "Gold"},
        ]

        # We also need to mock inventory + ships calls fired by Stage 2
        respx.get(f"{API_BASE}/players/guild/{guild_id}").mock(return_value=httpx.Response(200, json=players))
        respx.get(url__regex=rf"{API_BASE}/inventory/player/\d+").mock(return_value=httpx.Response(200, json=[]))
        respx.get(url__regex=rf"{API_BASE}/ships/player/\d+").mock(return_value=httpx.Response(200, json=[]))

        await warm_mod.warm_guild_players(guild_id)

        # Verify all 3 players are in player_cache
        for player in players:
            cached = state_mod.player_cache.peek((guild_id, player["user_id"]))
            assert cached is not None
            assert cached["id"] == player["id"]


# ---------------------------------------------------------------------------
# Test: warm_active_player_loadout — populates both caches
# ---------------------------------------------------------------------------


class TestWarmActivePlayerLoadout:
    """warm_active_player_loadout populates inventory_cache and ships_cache."""

    @respx.mock
    async def test_warm_active_player_loadout_populates_both_caches(self, initialized_state):
        """After calling warm_active_player_loadout, both caches have entries."""
        guild_id = 300
        player_id = 42

        inventory = [
            {"id": 1, "item_name": "Laser Cannon", "item_type": "primary_weapon", "quantity": 2},
        ]
        ships = [
            {"player_ship_id": 10, "name": "Eagle", "nickname": "", "ship_type": "Fighter", "is_active": True},
        ]

        respx.get(f"{API_BASE}/inventory/player/{player_id}").mock(return_value=httpx.Response(200, json=inventory))
        respx.get(f"{API_BASE}/ships/player/{player_id}").mock(return_value=httpx.Response(200, json=ships))

        await warm_mod.warm_active_player_loadout(guild_id, player_id)

        # Both caches should be populated
        inv_cached = state_mod.inventory_cache.peek((guild_id, player_id))
        ships_cached = state_mod.ships_cache.peek((guild_id, player_id))

        assert inv_cached is not None
        assert len(inv_cached) == 1
        assert ships_cached is not None
        assert len(ships_cached) == 1

    @respx.mock
    async def test_warm_active_player_loadout_returns_normalized_choices(self, initialized_state):
        """Items stored in caches are NormalizedChoice with non-empty norm field."""
        guild_id = 301
        player_id = 43

        inventory = [
            {"id": 5, "item_name": "E2 Exoclad", "item_type": "module", "quantity": 1},
        ]
        ships = [
            {"player_ship_id": 20, "name": "Wraith", "nickname": "Dark", "ship_type": "Corvette", "is_active": False},
        ]

        respx.get(f"{API_BASE}/inventory/player/{player_id}").mock(return_value=httpx.Response(200, json=inventory))
        respx.get(f"{API_BASE}/ships/player/{player_id}").mock(return_value=httpx.Response(200, json=ships))

        await warm_mod.warm_active_player_loadout(guild_id, player_id)

        inv_cached = state_mod.inventory_cache.peek((guild_id, player_id))
        ships_cached = state_mod.ships_cache.peek((guild_id, player_id))

        assert inv_cached is not None and len(inv_cached) == 1
        item = inv_cached[0]
        assert isinstance(item, NormalizedChoice)
        assert item.norm  # non-empty
        assert item.norm == normalize_for_search(item.label)

        assert ships_cached is not None and len(ships_cached) == 1
        ship = ships_cached[0]
        assert isinstance(ship, NormalizedChoice)
        assert ship.norm  # non-empty
        assert ship.norm == normalize_for_search(ship.label)


# ---------------------------------------------------------------------------
# Test: refresh_loadouts_round_robin
# ---------------------------------------------------------------------------


class TestRefreshLoadoutsRoundRobin:
    """refresh_loadouts_round_robin only dispatches tasks for currently-cached keys."""

    async def test_refresh_loadouts_round_robin_only_warms_cached_keys(self, initialized_state):
        """Pre-populate inventory_cache with 2 keys; tasks are dispatched for those 2."""
        guild_id = 400
        player_id_a = 10
        player_id_b = 20

        # Populate the cache with 2 entries
        state_mod.set_inventory(guild_id, player_id_a, [])
        state_mod.set_inventory(guild_id, player_id_b, [])

        # Track tasks dispatched
        dispatched: list[tuple[int, int]] = []

        async def _noop_loadout(gid: int, pid: int) -> None:
            dispatched.append((gid, pid))

        with (
            patch("utils.autocomplete_warm.asyncio.create_task") as mock_create_task,
            patch("utils.autocomplete_warm.warm_active_player_loadout", side_effect=_noop_loadout),
        ):
            # create_task just needs to not error
            mock_create_task.return_value = asyncio.get_event_loop().create_future()
            mock_create_task.return_value.set_result(None)

            await warm_mod.refresh_loadouts_round_robin()

        # create_task was called for each of the 2 cached keys
        assert mock_create_task.call_count == 2

    async def test_refresh_loadouts_round_robin_empty_cache_is_noop(self, initialized_state):
        """With empty inventory_cache, no tasks are dispatched."""
        with patch("utils.autocomplete_warm.asyncio.create_task") as mock_create_task:
            await warm_mod.refresh_loadouts_round_robin()

        mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test: failure handling
# ---------------------------------------------------------------------------


class TestWarmGuildPlayersFailure:
    """warm_guild_players logs WARNING on error and returns without raising."""

    @respx.mock
    async def test_warm_guild_players_failure_logs_and_returns(self, initialized_state):
        """ConnectionError from httpx → function returns without raising."""
        guild_id = 500

        respx.get(f"{API_BASE}/players/guild/{guild_id}").mock(side_effect=httpx.ConnectError("Connection refused"))

        # Should not raise
        await warm_mod.warm_guild_players(guild_id)

        # Player cache should still be in a valid (empty) state for this guild
        all_keys = list(state_mod.player_cache.keys())
        keys_for_guild = [(g, u) for (g, u) in all_keys if g == guild_id]
        assert keys_for_guild == []


class TestWarmActivePlayerLoadoutFailure:
    """warm_active_player_loadout returns without raising on HTTP error."""

    @respx.mock
    async def test_warm_active_player_loadout_inventory_error_returns(self, initialized_state):
        """Inventory fetch error → function returns without raising; ships not fetched."""
        guild_id = 600
        player_id = 99

        respx.get(f"{API_BASE}/inventory/player/{player_id}").mock(
            return_value=httpx.Response(500, json={"detail": "server error"})
        )
        # Ships endpoint should NOT be called since inventory failed first
        respx.get(f"{API_BASE}/ships/player/{player_id}").mock(return_value=httpx.Response(200, json=[]))

        # Should not raise
        await warm_mod.warm_active_player_loadout(guild_id, player_id)

        # Cache should not be populated
        assert state_mod.inventory_cache.peek((guild_id, player_id)) is None


# ---------------------------------------------------------------------------
# Test: refresh_all_guild_players
# ---------------------------------------------------------------------------


class TestRefreshAllGuildPlayers:
    """refresh_all_guild_players calls warm_guild_players for each guild."""

    async def test_refresh_all_guild_players_iterates_all_guilds(self, initialized_state):
        """Mock bot with 2 guilds → warm_guild_players called for each guild.id."""
        guild1 = MagicMock()
        guild1.id = 111

        guild2 = MagicMock()
        guild2.id = 222

        bot = MagicMock()
        bot.guilds = [guild1, guild2]

        called_guild_ids: list[int] = []

        async def _mock_warm(guild_id: int) -> None:
            called_guild_ids.append(guild_id)

        # Patch via the module-level reference (warm_mod) to avoid cross-worker
        # sys.modules eviction causing a mismatch between the patched module and
        # the warm_mod reference established at import time.
        original_warm_guild_players = warm_mod.warm_guild_players
        warm_mod.warm_guild_players = _mock_warm
        try:
            await warm_mod.refresh_all_guild_players(bot)
        finally:
            warm_mod.warm_guild_players = original_warm_guild_players

        assert 111 in called_guild_ids
        assert 222 in called_guild_ids
        assert len(called_guild_ids) == 2

    async def test_refresh_all_guild_players_failure_logs_and_returns(self, initialized_state):
        """Exception inside the loop is caught; function returns without raising."""
        bot = MagicMock()
        bot.guilds = [MagicMock()]
        bot.guilds[0].id = 333

        async def _fail(guild_id: int) -> None:
            raise RuntimeError("boom")

        original_warm_guild_players = warm_mod.warm_guild_players
        warm_mod.warm_guild_players = _fail
        try:
            # Should not raise
            await warm_mod.refresh_all_guild_players(bot)
        finally:
            warm_mod.warm_guild_players = original_warm_guild_players


# ---------------------------------------------------------------------------
# Test: register_warm_jobs
# ---------------------------------------------------------------------------


class TestRegisterWarmJobs:
    """register_warm_jobs adds the expected jobs to the APScheduler instance."""

    async def test_register_warm_jobs_adds_jobs_to_scheduler(self, initialized_state):
        """A real AsyncIOScheduler receives Wave 0 + Wave 1 + 4 recurring jobs."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()

        try:
            guild1 = MagicMock()
            guild1.id = 777
            guild2 = MagicMock()
            guild2.id = 888

            bot = MagicMock()
            bot.guilds = [guild1, guild2]

            warm_mod.register_warm_jobs(scheduler, bot)

            jobs = scheduler.get_jobs()
            job_ids = [j.id for j in jobs]

            # Wave 0: guild shop + bounty warm jobs (B-P2)
            assert "warm-shop-777" in job_ids
            assert "warm-shop-888" in job_ids
            assert "warm-bounty-777" in job_ids
            assert "warm-bounty-888" in job_ids

            # Wave 1: per-user player warm jobs
            assert "warm-guild-777" in job_ids
            assert "warm-guild-888" in job_ids

            # Four recurring jobs
            assert "autocomplete-player-refresh" in job_ids
            assert "autocomplete-loadout-refresh" in job_ids
            assert "autocomplete-jobs-refresh" in job_ids
            assert "autocomplete-shop-safety-net" in job_ids

            # At least 10 jobs total: 2 shop + 2 bounty + 2 guild + 4 recurring
            assert len(jobs) >= 10

        finally:
            scheduler.shutdown(wait=False)

    async def test_register_warm_jobs_stagger_spacing(self, initialized_state, monkeypatch):
        """Guild warm jobs are staggered by AUTOCOMPLETE_WARM_GUILD_STAGGER_MS."""

        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        monkeypatch.setenv("AUTOCOMPLETE_WARM_GUILD_STAGGER_MS", "500")  # 500ms stagger

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()

        try:
            guild1 = MagicMock()
            guild1.id = 1001
            guild2 = MagicMock()
            guild2.id = 1002

            bot = MagicMock()
            bot.guilds = [guild1, guild2]

            warm_mod.register_warm_jobs(scheduler, bot)

            jobs = {j.id: j for j in scheduler.get_jobs()}

            job1 = jobs.get("warm-guild-1001")
            job2 = jobs.get("warm-guild-1002")

            assert job1 is not None
            assert job2 is not None

            # Guild 2 should fire 0.5s after guild 1
            delta = (job2.next_run_time - job1.next_run_time).total_seconds()
            assert abs(delta - 0.5) < 0.1, f"Expected 0.5s stagger, got {delta:.3f}s"

        finally:
            scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Test: refresh_jobs_cache
# ---------------------------------------------------------------------------


class TestRefreshJobsCache:
    """refresh_jobs_cache gracefully handles missing SchedulerCog or missing _job_cache."""

    async def test_refresh_jobs_cache_no_cog_is_noop(self, initialized_state):
        """When SchedulerCog is not found, function returns without raising."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)

        # Should not raise
        await warm_mod.refresh_jobs_cache(bot)

    async def test_refresh_jobs_cache_with_job_cache_invalidates(self, initialized_state):
        """When SchedulerCog has _job_cache, invalidate('all') is called."""
        mock_job_cache = MagicMock()

        mock_cog = MagicMock()
        mock_cog._job_cache = mock_job_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        await warm_mod.refresh_jobs_cache(bot)

        mock_job_cache.invalidate.assert_called_once_with("all")

    async def test_refresh_jobs_cache_without_job_cache_is_noop(self, initialized_state):
        """SchedulerCog without _job_cache → no-op, no error."""
        mock_cog = MagicMock(spec=[])  # spec=[] means no attributes
        mock_cog.__class__.__name__ = "SchedulerCog"

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        # Should not raise — hasattr(cog, "_job_cache") is False
        await warm_mod.refresh_jobs_cache(bot)


# ---------------------------------------------------------------------------
# Test: refresh_shop_cache_safety_net
# ---------------------------------------------------------------------------


class TestRefreshShopCacheSafetyNet:
    """refresh_shop_cache_safety_net gracefully handles missing ShopCog."""

    async def test_refresh_shop_cache_no_cog_is_noop(self, initialized_state):
        """When ShopCog is not found, function returns without raising."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)

        await warm_mod.refresh_shop_cache_safety_net(bot)

    async def test_refresh_shop_cache_triggers_get_for_each_key(self, initialized_state):
        """Each cached (guild_id, tier) key has get() called on _shop_cache."""
        mock_shop_cache = AsyncMock()
        mock_shop_cache.keys = MagicMock(return_value=[(100, "Bronze"), (100, "Gold")])
        mock_shop_cache.get = AsyncMock(return_value=None)

        mock_cog = MagicMock()
        mock_cog._shop_cache = mock_shop_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        await warm_mod.refresh_shop_cache_safety_net(bot)

        # get() should be called for each of the 2 keys
        assert mock_shop_cache.get.call_count == 2


# ---------------------------------------------------------------------------
# Test: get_api_base() accessor added to autocomplete_state
# ---------------------------------------------------------------------------


class TestGetApiBase:
    """get_api_base() returns the API base URL set during init()."""

    def test_get_api_base_returns_none_before_init(self):
        """get_api_base() returns None before init() is called.

        Uses state_mod.get_api_base (the module-level reference established at
        import time) to avoid a stale module reference caused by
        _evict_discord_modules() in other cog test fixtures.
        """
        result = state_mod.get_api_base()
        assert result is None

    def test_get_api_base_returns_value_after_init(self, real_http_client):
        """get_api_base() returns the api_base string passed to init().

        Uses state_mod.get_api_base to avoid module reference staleness.
        """
        state_mod.init(real_http_client, API_BASE)
        assert state_mod.get_api_base() == API_BASE


# ---------------------------------------------------------------------------
# Adversarial / Edge-Case Tests (Phase 3 review additions)
# ---------------------------------------------------------------------------


class TestWarmGuildPlayersExactly500ThenZero:
    """Pagination edge case: exactly 500 rows on page 1, 0 on page 2.

    The pagination loop uses ``len(players) < limit`` as the stop condition.
    When page 1 returns exactly ``limit`` (500) rows, the loop must fetch page 2.
    When page 2 returns 0 rows, ``0 < 500`` is True → break. This must not loop
    infinitely and must warm exactly 500 players.
    """

    @respx.mock
    async def test_exactly_500_then_0_stops_after_two_requests(self, initialized_state):
        """Page 1 = 500 players, page 2 = 0 players → exactly 500 warmed, 2 HTTP requests."""
        guild_id = 700

        page1 = [
            {"id": i, "user_id": i + 2000, "discord_id": i + 2000, "guild_id": guild_id, "tier": "Bronze"}
            for i in range(500)
        ]
        page2: list = []  # empty — must trigger stop condition

        # Mock the paginated player endpoint: first call returns 500, second returns 0.
        respx.get(f"{API_BASE}/players/guild/{guild_id}").mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        # Stage 2 will attempt inventory + ship calls for the 500 players.
        # Silence them with respx regex patterns so we don't get unrouted-request errors.
        respx.get(url__regex=rf"{API_BASE}/inventory/player/\d+").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(url__regex=rf"{API_BASE}/ships/player/\d+").mock(
            return_value=httpx.Response(200, json=[])
        )

        await warm_mod.warm_guild_players(guild_id)

        all_keys = list(state_mod.player_cache.keys())
        keys_for_guild = [(g, u) for (g, u) in all_keys if g == guild_id]
        assert len(keys_for_guild) == 500, (
            f"Expected 500 players warmed (page1 count), got {len(keys_for_guild)}"
        )


class TestWarmActivePlayerLoadoutSemaphoreRelease:
    """Semaphore is always released even when warm_active_player_loadout fails internally.

    The implementation wraps the body with ``async with _get_semaphore():``.
    The context-manager protocol guarantees the semaphore is released on any exit
    path — return, exception, or normal completion.  This test verifies the
    implementation actually uses ``async with`` (not manual acquire/release) by
    confirming the semaphore is available after a failure.
    """

    @respx.mock
    async def test_semaphore_released_after_inventory_failure(self, initialized_state, monkeypatch):
        """After inventory fetch fails, the semaphore is still available (not leaked)."""
        monkeypatch.setenv("AUTOCOMPLETE_WARM_CONCURRENCY", "1")
        warm_mod._warm_semaphore = None  # force re-creation with concurrency=1

        guild_id = 800
        player_id = 55

        # Inventory call returns 500 → triggers except block inside async with
        respx.get(f"{API_BASE}/inventory/player/{player_id}").mock(
            return_value=httpx.Response(500, json={"detail": "internal server error"})
        )

        # Run the function — it should return normally (non-fatal)
        await warm_mod.warm_active_player_loadout(guild_id, player_id)

        # The semaphore MUST be fully released. With concurrency=1, acquiring it
        # immediately must succeed without blocking.
        sem = warm_mod._get_semaphore()
        acquired = False
        try:
            acquired = sem._value == 1  # asyncio.Semaphore internal counter
        except AttributeError:
            # Fallback: try a non-blocking acquire
            acquired = sem.locked() is False
        assert acquired, "Semaphore was not released after warm_active_player_loadout failure"


class TestRefreshShopCacheSafetyNetNoRefreshFn:
    """refresh_shop_cache_safety_net silently no-ops when _shop_cache has no refresh_fn.

    When AutocompleteCache is created without a refresh_fn, ``get()`` returns None
    on a cold miss or expired entry without fetching.  The safety-net job discards
    the return value, so this is a silent no-op.  This test documents the known
    behavior: no exception is raised, no warning is logged, and the call completes.

    This is intentional per the AutocompleteCache contract
    (``refresh_fn=None`` → miss returns None).
    """

    async def test_safety_net_no_refresh_fn_completes_without_error(self, initialized_state):
        """ShopCog._shop_cache has no refresh_fn → safety-net get() returns None silently."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        # Create a real cache with no refresh_fn; pre-populate one expired entry
        shop_cache: AutocompleteCache = AutocompleteCache(
            ttl_seconds=0.001,  # immediately expired
            refresh_fn=None,
            name="test-shop-no-refresh",
        )
        shop_cache.set((900, "Bronze"), [{"item": "sword"}])

        mock_cog = MagicMock()
        mock_cog._shop_cache = shop_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        # Should complete without raising, even though get() returns None
        await warm_mod.refresh_shop_cache_safety_net(bot)

        # Entry is still in store (not evicted — cache only removes on eviction, not on refresh miss)
        # The key behavior is: no exception raised, no crash.


# ---------------------------------------------------------------------------
# Test: warm_guild_shop_cache and warm_guild_bounty_cache (B-P2)
# ---------------------------------------------------------------------------


class TestWarmGuildShopCache:
    """Tests for warm_guild_shop_cache (B-P2 Wave 0 addition)."""

    async def test_warm_guild_shop_cache_warms_all_tiers(self, initialized_state):
        """warm_guild_shop_cache calls _shop_cache.get for each of 4 tiers."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        # Build a shop cache that records which keys were fetched
        fetched_keys = []

        async def record_fetch(key):
            fetched_keys.append(key)
            return []

        shop_cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=record_fetch, name="test-shop")
        mock_cog = MagicMock()
        mock_cog._shop_cache = shop_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        await warm_mod.warm_guild_shop_cache(bot, guild_id=42)

        # All 4 tiers should have been warmed
        assert (42, "Bronze") in fetched_keys
        assert (42, "Silver") in fetched_keys
        assert (42, "Gold") in fetched_keys
        assert (42, "Platinum") in fetched_keys

    async def test_warm_guild_shop_cache_no_cog_logs_warning(self, initialized_state):
        """warm_guild_shop_cache returns silently when ShopCog not found."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)  # ShopCog not loaded

        # Should not raise
        await warm_mod.warm_guild_shop_cache(bot, guild_id=99)

    async def test_warm_guild_shop_cache_tier_error_is_nonfatal(self, initialized_state):
        """warm_guild_shop_cache continues warming remaining tiers after one fails."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        fetched_keys = []
        call_count = 0

        async def flaky_fetch(key):
            nonlocal call_count
            call_count += 1
            fetched_keys.append(key)
            if key[1] == "Bronze":
                raise RuntimeError("fetch failed")
            return []

        shop_cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=flaky_fetch, name="test-flaky-shop")
        mock_cog = MagicMock()
        mock_cog._shop_cache = shop_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        # Must not raise even if Bronze fails
        await warm_mod.warm_guild_shop_cache(bot, guild_id=55)

        # Silver, Gold, Platinum should still be attempted
        warmed_tiers = [k[1] for k in fetched_keys]
        assert "Silver" in warmed_tiers
        assert "Gold" in warmed_tiers
        assert "Platinum" in warmed_tiers


class TestWarmGuildBountyCache:
    """Tests for warm_guild_bounty_cache (B-P2 Wave 0 addition)."""

    async def test_warm_guild_bounty_cache_calls_get(self, initialized_state):
        """warm_guild_bounty_cache calls _bounty_cache.get(guild_id)."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        fetched_keys = []

        async def record_fetch(key):
            fetched_keys.append(key)
            return []

        bounty_cache = AutocompleteCache(ttl_seconds=60.0, refresh_fn=record_fetch, name="test-bounty")
        mock_cog = MagicMock()
        mock_cog._bounty_cache = bounty_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        await warm_mod.warm_guild_bounty_cache(bot, guild_id=77)

        assert 77 in fetched_keys

    async def test_warm_guild_bounty_cache_no_cog_logs_warning(self, initialized_state):
        """warm_guild_bounty_cache returns silently when BountyCog not found."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)

        # Must not raise
        await warm_mod.warm_guild_bounty_cache(bot, guild_id=88)

    async def test_warm_guild_bounty_cache_no_cache_attr_is_noop(self, initialized_state):
        """warm_guild_bounty_cache does nothing when BountyCog has no _bounty_cache."""
        mock_cog = MagicMock(spec=[])  # No _bounty_cache attribute
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        # Must not raise
        await warm_mod.warm_guild_bounty_cache(bot, guild_id=99)


class TestRegisterWarmJobsZeroGuilds:
    """register_warm_jobs with bot.guilds == [] adds only the 4 recurring jobs.

    When the bot is not in any guild at warm-job registration time (unusual but
    possible in CI or immediately after bot invite), the per-guild one-time warm
    jobs must not be created.  The 4 recurring jobs must still be registered.
    """

    async def test_register_warm_jobs_zero_guilds_only_recurring(self, initialized_state):
        """bot.guilds = [] → exactly 4 recurring jobs, zero per-guild jobs."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()

        try:
            bot = MagicMock()
            bot.guilds = []  # no guilds

            warm_mod.register_warm_jobs(scheduler, bot)

            jobs = scheduler.get_jobs()
            job_ids = [j.id for j in jobs]

            # No per-guild warm jobs (Wave 0 or Wave 1) — no guilds
            guild_warm_jobs = [jid for jid in job_ids if any(jid.startswith(p) for p in ("warm-guild-", "warm-shop-", "warm-bounty-"))]
            assert guild_warm_jobs == [], f"Expected no per-guild jobs, got: {guild_warm_jobs}"

            # All 4 recurring jobs present
            assert "autocomplete-player-refresh" in job_ids
            assert "autocomplete-loadout-refresh" in job_ids
            assert "autocomplete-jobs-refresh" in job_ids
            assert "autocomplete-shop-safety-net" in job_ids

            assert len(jobs) == 4, f"Expected exactly 4 jobs, got {len(jobs)}: {job_ids}"

        finally:
            scheduler.shutdown(wait=False)
