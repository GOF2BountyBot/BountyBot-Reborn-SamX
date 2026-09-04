"""Tests for events cache warm/refresh jobs in autocomplete_warm.py.

Mirrors test_autocomplete_warm.py style. Covers:
- warm_guild_events_cache registered in Wave 0 with id warm-events-{guild}
- refresh_events_cache interval job registered as events-cache-refresh
- refresh_events_cache iterates all guilds
- warm_guild_events_cache is non-fatal when EventsCog not found
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger and shared.http_retry BEFORE any application imports.
# Loading the real http_retry via path is container-layout-sensitive; mock it
# instead since these tests exercise warm/refresh logic, not HTTP retry details.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

# Minimal http_retry stub: with_transient_retry just awaits the first arg.
_mock_http_retry = types.ModuleType("shared.http_retry")

async def _passthrough(fn, *args, **kwargs):
    return await fn(*args, **kwargs)

_mock_http_retry.with_transient_retry = _passthrough
sys.modules.setdefault("shared.http_retry", _mock_http_retry)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import utils.autocomplete_state as state_mod
import utils.autocomplete_warm as warm_mod
from cogs._shared.autocomplete_cache import AutocompleteCache


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


# ---------------------------------------------------------------------------
# warm_guild_events_cache
# ---------------------------------------------------------------------------


class TestWarmGuildEventsCache:
    async def test_calls_events_cache_get(self, reset_state):
        """warm_guild_events_cache calls _events_cache.get(guild_id)."""
        called_with = []

        async def record_fetch(key):
            called_with.append(key)
            return []

        mock_cog = MagicMock()
        events_cache = AutocompleteCache(ttl_seconds=60.0, refresh_fn=record_fetch, name="test-events")
        mock_cog._events_cache = events_cache

        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)

        await warm_mod.warm_guild_events_cache(bot, guild_id=42)
        assert 42 in called_with

    async def test_no_cog_logs_warning(self, reset_state):
        """warm_guild_events_cache returns silently when EventsCog not found."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)
        # Should not raise
        await warm_mod.warm_guild_events_cache(bot, guild_id=99)

    async def test_no_cache_attr_is_noop(self, reset_state):
        """warm_guild_events_cache does nothing when EventsCog has no _events_cache."""
        mock_cog = MagicMock(spec=[])  # no _events_cache attribute
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)
        # Should not raise
        await warm_mod.warm_guild_events_cache(bot, guild_id=77)


# ---------------------------------------------------------------------------
# refresh_events_cache
# ---------------------------------------------------------------------------


class TestRefreshEventsCache:
    async def test_iterates_all_guilds(self, reset_state):
        """refresh_events_cache calls _events_cache.get for each guild."""
        refreshed = []

        async def record_fetch(guild_id):
            refreshed.append(guild_id)
            return []

        mock_cog = MagicMock()
        cache = AutocompleteCache(ttl_seconds=60.0, refresh_fn=record_fetch, name="test-events-r")
        mock_cog._events_cache = cache

        guild1 = MagicMock()
        guild1.id = 100
        guild2 = MagicMock()
        guild2.id = 200
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=mock_cog)
        bot.guilds = [guild1, guild2]

        await warm_mod.refresh_events_cache(bot)
        assert set(refreshed) == {100, 200}

    async def test_no_cog_skips_gracefully(self, reset_state):
        """refresh_events_cache returns without error when EventsCog not found."""
        bot = MagicMock()
        bot.get_cog = MagicMock(return_value=None)
        bot.guilds = []
        await warm_mod.refresh_events_cache(bot)


# ---------------------------------------------------------------------------
# register_warm_jobs includes warm-events-* and events-cache-refresh
# ---------------------------------------------------------------------------


class TestRegisterWarmJobsEvents:
    async def test_wave0_events_warm_jobs_registered(self, reset_state):
        """register_warm_jobs adds warm-events-{guild} jobs in Wave 0."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()
        try:
            guild1 = MagicMock()
            guild1.id = 111
            guild2 = MagicMock()
            guild2.id = 222
            bot = MagicMock()
            bot.guilds = [guild1, guild2]

            warm_mod.register_warm_jobs(scheduler, bot)

            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "warm-events-111" in job_ids, f"warm-events-111 not in {job_ids}"
            assert "warm-events-222" in job_ids, f"warm-events-222 not in {job_ids}"
        finally:
            scheduler.shutdown(wait=False)

    async def test_events_cache_refresh_job_registered(self, reset_state):
        """register_warm_jobs includes events-cache-refresh recurring job."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()
        try:
            bot = MagicMock()
            bot.guilds = []
            warm_mod.register_warm_jobs(scheduler, bot)
            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "events-cache-refresh" in job_ids, f"events-cache-refresh not in {job_ids}"
        finally:
            scheduler.shutdown(wait=False)

    async def test_events_cache_refresh_has_10_min_interval(self, reset_state):
        """events-cache-refresh must fire every 10 minutes (default)."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="UTC",
        )
        scheduler.start()
        try:
            bot = MagicMock()
            bot.guilds = []
            warm_mod.register_warm_jobs(scheduler, bot)
            jobs = {j.id: j for j in scheduler.get_jobs()}
            job = jobs.get("events-cache-refresh")
            assert job is not None
            assert job.trigger.interval.total_seconds() == 600
        finally:
            scheduler.shutdown(wait=False)
