"""Unit tests for AutocompleteCache (Layer 1 — spec tests #1–12).

All tests use real AutocompleteCache objects.  The only mock allowed per the
max-2-mocks rule is the refresh_fn itself (AsyncMock/regular coroutine).
Time is controlled via the _monotonic injection parameter so no sleeps or
third-party dependencies are needed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Module-level mock: inject shared.bblogger before importing the module under test
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from cogs._shared.autocomplete_cache import AutocompleteCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_no_ttl(**kwargs) -> AutocompleteCache:
    """Return a cache with TTL=None (static mode)."""
    return AutocompleteCache(ttl_seconds=None, name="test-static", **kwargs)


def _cache_with_ttl(ttl: float = 300.0, *, mono: float = 0.0, **kwargs) -> tuple[AutocompleteCache, list]:
    """Return a (cache, clock) pair where clock[0] is the current monotonic value."""
    clock = [mono]
    return AutocompleteCache(ttl_seconds=ttl, _monotonic=lambda: clock[0], name="test-ttl", **kwargs), clock


# ---------------------------------------------------------------------------
# Test 1: set then get returns the value
# ---------------------------------------------------------------------------


class TestSetAndGet:
    def test_set_then_get_returns_value(self):
        """Basic round-trip: set a value and get it back."""
        cache = _cache_no_ttl()
        cache.set("key1", ["a", "b", "c"])
        result = asyncio.run(cache.get("key1"))
        assert result == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Test 2: get on cold cache with no refresh_fn returns None
# ---------------------------------------------------------------------------


class TestColdMissNoRefreshFn:
    def test_cold_miss_no_refresh_fn_returns_none(self):
        """Cache miss with no refresh_fn returns None."""
        cache = _cache_no_ttl()
        result = asyncio.run(cache.get("missing"))
        assert result is None


# ---------------------------------------------------------------------------
# Test 3: get on cold cache with refresh_fn invokes it and stores result
# ---------------------------------------------------------------------------


class TestColdMissWithRefreshFn:
    def test_cold_miss_calls_refresh_fn_and_stores(self):
        """Cold cache with refresh_fn invokes fn and caches the result."""
        refresh = AsyncMock(return_value=["item1", "item2"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test")

        result = asyncio.run(cache.get("mykey"))

        assert result == ["item1", "item2"]
        refresh.assert_awaited_once_with("mykey")
        # Verify the value was stored (next get should not call refresh again)
        assert cache.size == 1


# ---------------------------------------------------------------------------
# Test 4: second get within TTL does not call refresh_fn
# ---------------------------------------------------------------------------


class TestHitWithinTTL:
    def test_second_get_within_ttl_does_not_refresh(self):
        """Second get within TTL window hits cache; refresh_fn is called once only."""
        refresh = AsyncMock(return_value=["data"])
        cache, _clock = _cache_with_ttl(ttl=300.0, refresh_fn=refresh)

        asyncio.run(cache.get("key"))
        asyncio.run(cache.get("key"))

        refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5: get after TTL expiry calls refresh_fn again
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_get_after_ttl_expiry_calls_refresh_fn_again(self):
        """After the TTL window passes, get() triggers another refresh."""
        call_count = 0

        async def counting_refresh(key):
            nonlocal call_count
            call_count += 1
            return [f"result-{call_count}"]

        cache, clock = _cache_with_ttl(ttl=300.0, mono=0.0, refresh_fn=counting_refresh)

        asyncio.run(cache.get("key"))  # cold load, count=1
        assert call_count == 1

        clock[0] = 301.0  # advance simulated time past TTL
        asyncio.run(cache.get("key"))  # should refresh again, count=2
        assert call_count == 2


# ---------------------------------------------------------------------------
# Test 6: invalidate drops only that key; others remain
# ---------------------------------------------------------------------------


class TestInvalidate:
    def test_invalidate_drops_only_specified_key(self):
        """invalidate(key) removes only that key; sibling keys are unaffected."""
        cache = _cache_no_ttl()
        cache.set("a", [1, 2])
        cache.set("b", [3, 4])

        cache.invalidate("a")

        assert asyncio.run(cache.get("a")) is None
        assert asyncio.run(cache.get("b")) == [3, 4]
        assert cache.size == 1

    def test_invalidate_is_idempotent(self):
        """invalidate on a missing key does not raise."""
        cache = _cache_no_ttl()
        cache.invalidate("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Test 7: clear() drops all keys
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_drops_all_keys(self):
        """clear() empties the entire cache."""
        cache = _cache_no_ttl()
        cache.set("x", [1])
        cache.set("y", [2])
        cache.set("z", [3])

        cache.clear()

        assert cache.size == 0
        assert asyncio.run(cache.get("x")) is None
        assert asyncio.run(cache.get("y")) is None


# ---------------------------------------------------------------------------
# Test 8: refresh_fn raises with prior value cached → returns stale, logs WARNING
# ---------------------------------------------------------------------------


class TestStaleOnError:
    def test_refresh_fn_raises_returns_stale_value(self):
        """If refresh_fn raises and a prior value exists, return the stale value."""
        call_count = 0

        async def flaky_refresh(key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ["stale-data"]
            raise RuntimeError("upstream down")

        cache, clock = _cache_with_ttl(ttl=300.0, mono=0.0, refresh_fn=flaky_refresh)

        # Prime the cache
        result1 = asyncio.run(cache.get("key"))
        assert result1 == ["stale-data"]

        # Advance time to expire the entry
        clock[0] = 301.0

        # Next get should hit refresh_fn which now raises; stale value returned
        result2 = asyncio.run(cache.get("key"))
        assert result2 == ["stale-data"]

    def test_refresh_fn_raises_stale_logs_warning(self):
        """Stale-on-error path logs a WARNING."""
        mock_logger = MagicMock()
        _mock_bblogger.get_logger = MagicMock(return_value=mock_logger)

        call_count = 0

        async def flaky_refresh(key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ["ok"]
            raise ValueError("oops")

        cache, clock = _cache_with_ttl(ttl=10.0, mono=0.0, refresh_fn=flaky_refresh)
        cache._log = mock_logger  # inject logger directly

        asyncio.run(cache.get("k"))  # prime
        clock[0] = 11.0  # expire
        asyncio.run(cache.get("k"))  # stale path

        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Test 9: refresh_fn raises with no prior value → returns None
# ---------------------------------------------------------------------------


class TestHardMissOnError:
    def test_refresh_fn_raises_no_prior_returns_none(self):
        """If refresh_fn raises with no prior value cached, return None."""

        async def always_fail(key):
            raise ConnectionError("network down")

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=always_fail, name="test-fail")

        result = asyncio.run(cache.get("key"))
        assert result is None


# ---------------------------------------------------------------------------
# Test 10: TTL=None entries never expire even after long elapsed time
# ---------------------------------------------------------------------------


class TestNoTTLNeverExpires:
    def test_static_cache_never_expires(self):
        """With TTL=None, entries are never expired regardless of time."""
        very_large_time = [0.0]
        cache = AutocompleteCache(
            ttl_seconds=None,
            _monotonic=lambda: very_large_time[0],
            name="static-test",
        )
        cache.set("catalog", ["ship-alpha", "ship-beta"])

        # Advance time massively
        very_large_time[0] = 1_000_000.0

        result = asyncio.run(cache.get("catalog"))
        assert result == ["ship-alpha", "ship-beta"]


# ---------------------------------------------------------------------------
# Test 11: Concurrent get(key) on cold cache invokes refresh_fn exactly once
# ---------------------------------------------------------------------------


class TestConcurrentGetLock:
    def test_concurrent_get_invokes_refresh_fn_once(self):
        """Two concurrent gets on a cold cache call refresh_fn exactly once."""
        call_count = 0

        async def slow_refresh(key):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)  # yield to allow other coroutine to reach the lock
            return ["result"]

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=slow_refresh, name="lock-test")

        async def run():
            results = await asyncio.gather(
                cache.get("mykey"),
                cache.get("mykey"),
            )
            return results

        results = asyncio.run(run())

        assert call_count == 1, f"Expected refresh_fn called once, got {call_count}"
        assert all(r == ["result"] for r in results)


# ---------------------------------------------------------------------------
# Test 12: keys() and size reflect current state
# ---------------------------------------------------------------------------


class TestObservability:
    def test_keys_and_size_reflect_state(self):
        """keys() and size accurately reflect current cache contents."""
        cache = _cache_no_ttl()
        assert cache.size == 0
        assert cache.keys() == []

        cache.set("alpha", [1])
        cache.set("beta", [2])
        assert cache.size == 2
        assert set(cache.keys()) == {"alpha", "beta"}

        cache.invalidate("alpha")
        assert cache.size == 1
        assert cache.keys() == ["beta"]

        cache.clear()
        assert cache.size == 0
        assert cache.keys() == []
