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


# ---------------------------------------------------------------------------
# Test 13: Concurrent gets at the moment of TTL expiry — only one refresh fires
# (E.1 — TOCTOU double-check correctness)
# ---------------------------------------------------------------------------


class TestConcurrentExpiryLock:
    def test_only_one_refresh_fires_when_multiple_gets_hit_expiry(self):
        """E.1: Two concurrent gets on an expired entry call refresh_fn exactly once.

        This validates the double-check locking pattern inside get(): both
        coroutines see the entry as expired on the fast-path, both queue up for
        the lock, but only the first acquires it and refreshes; the second
        re-checks inside the lock and sees the fresh entry.
        """
        call_count = 0

        clock = [0.0]

        async def expiry_refresh(key):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)  # yield so the second coroutine can reach the lock
            return [f"refreshed-{call_count}"]

        cache = AutocompleteCache(
            ttl_seconds=300.0,
            refresh_fn=expiry_refresh,
            _monotonic=lambda: clock[0],
            name="expiry-lock-test",
        )

        # Prime the cache at t=0
        asyncio.run(cache.get("key"))
        assert call_count == 1

        # Advance time past TTL — both concurrent gets will see the entry as expired
        clock[0] = 301.0

        async def run_concurrent():
            results = await asyncio.gather(
                cache.get("key"),
                cache.get("key"),
            )
            return results

        results = asyncio.run(run_concurrent())

        # Only one refresh should have fired despite two concurrent gets at expiry
        assert call_count == 2, f"Expected exactly 2 total calls (1 prime + 1 refresh), got {call_count}"
        # Both gets return the same (new) value
        assert all(r == ["refreshed-2"] for r in results)


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


# ---------------------------------------------------------------------------
# TestPeek — Phase-1 addition
# ---------------------------------------------------------------------------


class TestPeek:
    def test_peek_returns_none_on_missing_key(self):
        """peek() returns None when key is not in cache."""
        cache = _cache_no_ttl()
        assert cache.peek("nonexistent") is None

    def test_peek_returns_value_on_warm_cache(self):
        """peek() returns the cached value when key is present and not expired."""
        cache = _cache_no_ttl()
        cache.set("hello", ["world"])
        assert cache.peek("hello") == ["world"]

    def test_peek_returns_none_on_expired_entry(self):
        """peek() returns None for an entry that has passed its TTL."""
        cache, clock = _cache_with_ttl(ttl=300.0, mono=0.0)
        cache.set("mykey", ["data"])
        # Advance clock past TTL
        clock[0] = 301.0
        assert cache.peek("mykey") is None

    def test_peek_never_calls_refresh_fn(self):
        """peek() must not invoke refresh_fn even on a cache miss."""

        async def bad_refresh(key):
            raise AssertionError("peek() must not call refresh_fn")

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=bad_refresh, name="test-peek-no-refresh")
        # Does not raise — refresh_fn is never called
        result = cache.peek("missing")
        assert result is None

    def test_peek_does_not_hold_lock(self):
        """peek() must never acquire self._lock — it is synchronous and lock-free.

        Strategy: verify that peek() is a regular (non-coroutine) function,
        that it returns the correct value synchronously, and that it does NOT
        await or touch self._lock in any observable way.  Because peek() is a
        plain def (not async def), it cannot await and therefore cannot block
        on lock acquisition.

        Note: the cog conftest patches asyncio.create_task globally to prevent
        background-task accumulation.  This test is therefore kept synchronous
        to avoid dependency on the patched create_task.
        """
        import inspect

        cache = _cache_no_ttl()
        cache.set("key", ["val"])

        # peek() must be a plain function, not a coroutine function
        assert not inspect.iscoroutinefunction(cache.peek), "peek() must not be async"

        # Direct synchronous call — no event loop, no lock acquisition needed
        result = cache.peek("key")
        assert result == ["val"]

        # Calling peek() on a missing key must also return synchronously
        assert cache.peek("absent") is None


# ---------------------------------------------------------------------------
# TestScheduleRefresh — Phase-1 addition
# ---------------------------------------------------------------------------


class TestScheduleRefresh:
    def test_schedule_refresh_noop_when_no_refresh_fn(self):
        """schedule_refresh() is a no-op and does not raise when refresh_fn is None."""
        cache = AutocompleteCache(name="test-no-refresh-fn")
        # Must not raise — no refresh_fn configured
        cache.schedule_refresh("anykey")

    async def test_schedule_refresh_populates_cache(self):
        """schedule_refresh() schedules a get() task; after the task completes the cache is warm.

        Note: the cog conftest patches asyncio.create_task globally.  We restore
        real task creation inside this test using unittest.mock.patch to override
        the global patch for the duration of the test only.
        """
        import asyncio as _asyncio
        from unittest.mock import patch

        refresh = AsyncMock(return_value=["loaded"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test-schedule")

        # Restore real create_task for this test scope
        with patch("asyncio.create_task", side_effect=_asyncio.get_event_loop().create_task):
            cache.schedule_refresh("mykey")
            # Yield control so the background task runs
            await _asyncio.sleep(0)
            await _asyncio.sleep(0)  # two yields to handle lock acquisition

        assert cache.peek("mykey") == ["loaded"]
        refresh.assert_awaited_once_with("mykey")

    async def test_schedule_refresh_concurrent_calls_coalesce(self):
        """Two concurrent schedule_refresh calls trigger refresh_fn at most once.

        The get() lock ensures only one refresh fires even when both tasks race
        to the lock simultaneously (AC-ROB-2).
        """
        import asyncio as _asyncio
        from unittest.mock import patch

        call_count = 0

        async def counting_refresh(key):
            nonlocal call_count
            call_count += 1
            await _asyncio.sleep(0)  # yield so second task can reach the lock
            return ["result"]

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=counting_refresh, name="test-coalesce")

        # Restore real create_task for this test scope
        with patch("asyncio.create_task", side_effect=_asyncio.get_event_loop().create_task):
            # Schedule two refreshes for the same key
            cache.schedule_refresh("shared-key")
            cache.schedule_refresh("shared-key")

            # Allow both background tasks to fully complete
            await _asyncio.sleep(0)
            await _asyncio.sleep(0)
            await _asyncio.sleep(0)

        assert call_count <= 1, f"Expected refresh_fn called at most once, got {call_count}"
        assert cache.peek("shared-key") == ["result"]


# ---------------------------------------------------------------------------
# TestMaxEntries — Phase-1 addition
# ---------------------------------------------------------------------------


class TestMaxEntries:
    def test_max_entries_evicts_oldest_on_overflow(self):
        """Setting max_entries=2 and inserting 3 keys evicts the oldest one."""
        clock = [0.0]
        cache = AutocompleteCache(
            ttl_seconds=None,
            max_entries=2,
            _monotonic=lambda: clock[0],
            name="test-max-entries",
        )

        clock[0] = 1.0
        cache.set("oldest", ["a"])

        clock[0] = 2.0
        cache.set("middle", ["b"])

        clock[0] = 3.0
        cache.set("newest", ["c"])  # should evict "oldest"

        assert cache.size == 2
        assert cache.peek("oldest") is None  # evicted
        assert cache.peek("middle") == ["b"]
        assert cache.peek("newest") == ["c"]

    def test_max_entries_none_disables_eviction(self):
        """With max_entries=None (default), any number of entries can be stored."""
        cache = AutocompleteCache(ttl_seconds=None, name="test-unlimited")
        for i in range(100):
            cache.set(f"key-{i}", [i])
        assert cache.size == 100

    def test_max_entries_eviction_order_stable(self):
        """Eviction always removes the entry with the smallest stored_at, not insertion order."""
        clock = [0.0]
        cache = AutocompleteCache(
            ttl_seconds=None,
            max_entries=3,
            _monotonic=lambda: clock[0],
            name="test-eviction-order",
        )

        # Insert keys with explicit different timestamps
        clock[0] = 10.0
        cache.set("t10", "ten")

        clock[0] = 5.0
        cache.set("t5", "five")  # earlier timestamp despite later insertion

        clock[0] = 20.0
        cache.set("t20", "twenty")

        # All three fit — no eviction yet
        assert cache.size == 3

        # Adding a 4th entry should evict "t5" (smallest stored_at = 5.0)
        clock[0] = 30.0
        cache.set("t30", "thirty")

        assert cache.size == 3
        assert cache.peek("t5") is None  # smallest stored_at — evicted
        assert cache.peek("t10") == "ten"
        assert cache.peek("t20") == "twenty"
        assert cache.peek("t30") == "thirty"


# ---------------------------------------------------------------------------
# TestPeekAdversarial — additional edge cases for peek()
# ---------------------------------------------------------------------------


class TestPeekAdversarial:
    def test_peek_ttl_none_large_clock_advance_still_returns_value(self):
        """peek() with TTL=None always returns the value regardless of elapsed time.

        Mirrors TestNoTTLNeverExpires for get() but validates peek() specifically.
        With no TTL, the TTL branch in peek() is never entered; the entry lives
        forever.
        """
        clock = [0.0]
        cache = AutocompleteCache(
            ttl_seconds=None,
            _monotonic=lambda: clock[0],
            name="peek-no-ttl-test",
        )
        cache.set("catalog", ["ship-alpha", "ship-beta"])

        # Advance simulated time by one million seconds
        clock[0] = 1_000_000.0

        assert cache.peek("catalog") == ["ship-alpha", "ship-beta"]

    def test_peek_is_readonly_never_mutates_store(self):
        """peek() must not mutate _store, keys(), or size in any observable way.

        Calls peek() 20 times on both present and absent keys; verifies that
        cache.size and cache.keys() are identical before and after.  peek() is
        documented as read-only so any mutation would be a correctness defect.
        """
        cache = _cache_no_ttl()
        cache.set("a", [1, 2, 3])
        cache.set("b", [4, 5, 6])

        size_before = cache.size
        keys_before = sorted(cache.keys())

        for _ in range(20):
            cache.peek("a")
            cache.peek("b")
            cache.peek("nonexistent")

        assert cache.size == size_before
        assert sorted(cache.keys()) == keys_before


# ---------------------------------------------------------------------------
# TestScheduleRefreshAdversarial — additional edge cases for schedule_refresh()
# ---------------------------------------------------------------------------


class TestScheduleRefreshAdversarial:
    def test_schedule_refresh_no_event_loop_raises_runtime_error(self):
        """schedule_refresh() propagates RuntimeError when no event loop is running.

        Design decision: schedule_refresh() does NOT swallow the RuntimeError
        from asyncio.create_task when called outside an event-loop context.
        Callers are responsible for ensuring a running loop exists (or calling
        schedule_refresh() only from within async code).

        This test simulates the no-event-loop condition by patching
        asyncio.create_task at the module level (bypassing the cog conftest's
        global patch) to close the coroutine and raise RuntimeError, then
        asserting propagation.

        Note: Python evaluates ``self.get(key)`` (creating the coroutine) *before*
        passing it to asyncio.create_task.  The side_effect receives the coroutine
        as its argument; we explicitly close it to prevent the
        "coroutine never awaited" RuntimeWarning that would otherwise appear during
        GC.
        """
        import pytest
        from unittest.mock import patch

        refresh = AsyncMock(return_value=["data"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test-no-loop")

        def _raise_and_close(coro, **kwargs):
            coro.close()  # prevent "never awaited" GC warning
            raise RuntimeError("no running event loop")

        with patch(
            "cogs._shared.autocomplete_cache.asyncio.create_task",
            side_effect=_raise_and_close,
        ):
            with pytest.raises(RuntimeError, match="no running event loop"):
                cache.schedule_refresh("key")

    async def test_schedule_refresh_on_warm_key_does_not_call_refresh_fn(self):
        """schedule_refresh() on a warm (non-expired) key creates a task, but refresh_fn
        is never called because get() fast-paths on a valid cache hit.

        This validates the "lock in get() suppresses duplicate refreshes" design
        from AC-ROB-2: the suppression happens inside get() — the same get() that
        the background task (from schedule_refresh) would execute.

        Design comment: schedule_refresh() itself does NOT check warmth; it always
        calls asyncio.create_task(self.get(key)).  The suppression is entirely in
        get()'s fast-path (entry is not None and not expired → return without lock).
        We verify this by calling get() directly while the entry is warm, confirming
        that get() returns the cached value without calling refresh_fn.
        """
        refresh = AsyncMock(return_value=["original"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test-warm-key")

        # Prime the cache — refresh_fn called once on cold miss
        result_cold = await cache.get("mykey")
        assert result_cold == ["original"]
        assert refresh.await_count == 1

        # Call get() again while entry is warm.  This is the exact code path that
        # the asyncio.Task scheduled by schedule_refresh() would execute.
        # get() must fast-path (no lock acquisition, no refresh_fn call).
        result_warm = await cache.get("mykey")
        assert result_warm == ["original"]

        # Critical assertion: refresh_fn NOT called on the warm hit
        assert refresh.await_count == 1, (
            f"refresh_fn called {refresh.await_count} times; expected 1 "
            "(warm get() must suppress refresh via fast-path)"
        )
        # Cache state is unchanged
        assert cache.peek("mykey") == ["original"]


# ---------------------------------------------------------------------------
# TestMaxEntriesAdversarial — additional edge cases for max_entries
# ---------------------------------------------------------------------------


class TestMaxEntriesAdversarial:
    def test_max_entries_one_only_newest_survives(self):
        """max_entries=1: after setting two keys the SECOND (newest) survives and
        the FIRST (oldest) is evicted.

        This is the minimal limit case and verifies that eviction correctly
        targets the oldest stored_at, not an arbitrary victim.
        """
        clock = [0.0]
        cache = AutocompleteCache(
            ttl_seconds=None,
            max_entries=1,
            _monotonic=lambda: clock[0],
            name="test-max-one",
        )

        clock[0] = 1.0
        cache.set("first", "alpha")

        clock[0] = 2.0
        cache.set("second", "beta")  # should evict "first"

        assert cache.size == 1
        assert cache.peek("first") is None, "oldest entry must be evicted when max_entries=1"
        assert cache.peek("second") == "beta", "newest entry must survive"

    def test_max_entries_zero_always_evicts_immediately(self):
        """max_entries=0 causes every set() call to immediately evict the entry
        it just wrote, leaving the cache permanently empty.

        DEF-0001-001 (Medium): This is an unintended edge case in the current
        implementation.  The eviction condition ``len(self._store) > max_entries``
        evaluates to ``1 > 0 == True`` after writing the first (and only) entry,
        so the newly-written key is evicted before the caller can read it.  The
        result is a cache that silently discards all writes.

        Expected fix (developer): validate ``max_entries > 0`` in ``__init__`` and
        raise ``ValueError`` for non-positive values, OR treat ``0`` as equivalent
        to ``None`` (no limit).

        This test documents the current (defective) behaviour so that fixing the
        production code causes this test to fail and prompts an update.
        """
        cache = AutocompleteCache(
            ttl_seconds=None,
            max_entries=0,
            name="test-max-zero",
        )

        cache.set("key", "value")

        # Document defective behaviour: entry is evicted immediately after write
        assert cache.size == 0, (
            "DEF-0001-001: max_entries=0 silently evicts all entries — "
            "if this assertion fails the production code was fixed; update this test"
        )
        assert cache.peek("key") is None, (
            "DEF-0001-001: peek after set with max_entries=0 should return None "
            "(entry was self-evicted)"
        )


# ---------------------------------------------------------------------------
# TestGetWithTimeout — B-P0 addition
# ---------------------------------------------------------------------------


class TestGetWithTimeout:
    """Tests for AutocompleteCache.get_with_timeout (B-P0).

    Three paths:
      - Happy path (cache hit): fast-path peek returns immediately, no I/O.
      - Cold path (cache miss): awaits get() which calls refresh_fn.
      - Timeout path: returns None without re-raising asyncio.TimeoutError.
    """

    async def test_cache_hit_returns_immediately(self):
        """Fast path: warm cache → peek succeeds → return value without awaiting get()."""
        refresh = AsyncMock(return_value=["new-data"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test-gwt-hit")
        cache.set("mykey", ["warm-data"])

        result = await cache.get_with_timeout("mykey", timeout=1.0)

        assert result == ["warm-data"]
        # refresh_fn must NOT be called for a warm hit
        refresh.assert_not_awaited()

    async def test_cold_miss_awaits_refresh_fn(self):
        """Cold path: cache miss → get_with_timeout awaits get() → calls refresh_fn."""
        refresh = AsyncMock(return_value=["fetched"])
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=refresh, name="test-gwt-cold")

        result = await cache.get_with_timeout("mykey", timeout=2.0)

        assert result == ["fetched"]
        refresh.assert_awaited_once_with("mykey")
        # Value must be persisted in cache after cold-path fill
        assert cache.peek("mykey") == ["fetched"]

    async def test_timeout_returns_none_does_not_raise(self):
        """Timeout path: get_with_timeout returns None, does NOT re-raise TimeoutError."""
        import asyncio as _asyncio

        async def slow_refresh(key):
            # Sleep longer than the timeout so the wait_for fires
            await _asyncio.sleep(10)
            return ["too-late"]

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=slow_refresh, name="test-gwt-timeout")

        # Very short timeout so it fires immediately
        result = await cache.get_with_timeout("mykey", timeout=0.01)

        # Must return None — no exception raised
        assert result is None

    async def test_timeout_logs_warning(self):
        """Timeout path logs a WARNING with key and timeout info."""
        import asyncio as _asyncio

        async def slow_refresh(key):
            await _asyncio.sleep(10)
            return ["never"]

        mock_logger = MagicMock()
        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=slow_refresh, name="test-gwt-warn")
        cache._log = mock_logger

        await cache.get_with_timeout("mykey", timeout=0.01)

        mock_logger.warning.assert_called()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "mykey" in warning_msg or "timeout" in warning_msg.lower()

    async def test_non_timeout_exception_returns_none(self):
        """Non-asyncio.TimeoutError exception from get() → return None, log WARNING."""
        async def bad_refresh(key):
            raise RuntimeError("network down")

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=bad_refresh, name="test-gwt-exc")
        mock_logger = MagicMock()
        cache._log = mock_logger

        result = await cache.get_with_timeout("mykey", timeout=2.0)

        assert result is None
        mock_logger.warning.assert_called()

    async def test_shield_allows_inner_get_to_complete_after_timeout(self):
        """asyncio.shield ensures the inner get() completes even after timeout fires.

        After a timeout, the cache entry must eventually be written so that the
        next peek() call finds it populated.
        """
        import asyncio as _asyncio

        fill_event = _asyncio.Event()

        async def delayed_refresh(key):
            await _asyncio.sleep(0.05)  # slightly longer than timeout
            fill_event.set()
            return ["delayed-value"]

        cache = AutocompleteCache(ttl_seconds=300.0, refresh_fn=delayed_refresh, name="test-gwt-shield")

        # Timeout fires before refresh completes — result is None
        result = await cache.get_with_timeout("mykey", timeout=0.01)
        assert result is None

        # Wait for the shielded inner get() to finish
        await fill_event.wait()
        await _asyncio.sleep(0)  # yield to allow task to store result

        # After the inner get() completes, the cache should be populated
        assert cache.peek("mykey") == ["delayed-value"]
