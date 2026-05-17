"""AutocompleteCache — in-memory key→value cache with optional TTL and refresh callable.

Designed for Discord autocomplete handlers that need to serve from in-memory state
rather than issuing HTTP calls on every keystroke.

Typical uses:
  - Static preload (TTL=None, refresh_fn=None): set(...) at startup;
    get(...) reads forever; invalidate / clear available for manual reload.
  - Lazy TTL cache (TTL=300, refresh_fn=async_loader): get(...) returns
    a fresh value on miss/expiry by awaiting refresh_fn(key); subsequent
    hits within the TTL window are O(1) with no HTTP.

Phase-1 additions (purely additive, no behaviour changes to existing API):
  - ``peek(key)``: synchronous non-blocking read; never triggers a refresh.
  - ``schedule_refresh(key)``: fire-and-forget background refresh task.
  - ``max_entries`` constructor param: LRU-style eviction by oldest stored_at.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from shared import bblogger


@dataclass
class _Entry[V]:
    """Internal storage unit for a single cached value."""

    value: V
    stored_at: float  # monotonic seconds


class AutocompleteCache[K, V]:
    """In-memory key→value cache with optional TTL and refresh callable.

    Args:
        ttl_seconds: Expiry window in seconds. ``None`` means entries never
            expire (suitable for static game catalog data).
        refresh_fn: Async callable ``(key) -> value`` invoked on cache miss
            or expiry.  When ``None``, a miss simply returns ``None``.
        name: Identifier used in log messages.
            Logger name: ``discord-gateway-AutocompleteCache.<name>``.
        max_entries: Maximum number of entries to store. When set, adding a new
            entry that pushes the count over this limit evicts the entry with the
            oldest ``stored_at`` timestamp. ``None`` disables eviction.
        _monotonic: Monotonic clock callable. Override in tests to control
            time without sleeping or external dependencies.

    Phase-1 additions:
        peek(key): Synchronous non-blocking read; never triggers a refresh and
            never acquires the lock. Safe to call from autocomplete hot paths.
        schedule_refresh(key): Fire-and-forget background refresh via
            ``asyncio.create_task``; no-op when ``refresh_fn`` is ``None``.
        max_entries: LRU-style eviction; oldest-inserted entry is dropped on
            overflow.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        refresh_fn: Callable[[K], Awaitable[V]] | None = None,
        name: str = "autocomplete-cache",
        max_entries: int | None = None,
        _monotonic: Callable[[], float] = monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._refresh_fn = refresh_fn
        self._name = name
        self._max_entries = max_entries
        self._monotonic = _monotonic
        self._store: dict[K, _Entry[V]] = {}
        self._lock = asyncio.Lock()
        self._log = bblogger.get_logger(f"discord-gateway-AutocompleteCache.{name}")

    # ------------------------------------------------------------------
    # Public read/write API
    # ------------------------------------------------------------------

    async def get(self, key: K) -> V | None:
        """Return the cached value, refreshing via refresh_fn if missing or expired.

        Stale-on-error policy: if refresh_fn raises and a previously-cached value
        exists, return that stale value and log a WARNING.  Better stale than
        empty for autocomplete UX.

        Returns None if:
        - No value is cached for ``key`` and no refresh_fn is configured.
        - refresh_fn raises and no prior value was cached.

        Concurrency / TOCTOU note (E.1):
        The initial TTL-expiry check (fast-path) runs outside the lock to avoid
        lock contention on every hot-path cache hit.  Because asyncio uses
        cooperative multitasking (a single event loop; no coroutine is interrupted
        between two non-await statements), there is no OS-thread race between the
        fast-path check and the lock acquisition below.  The double-check inside
        the lock handles the case where two concurrent coroutines both see an
        expired entry on the fast-path: the first one acquires the lock and
        refreshes; the second acquires the lock afterward, re-checks, finds the
        entry fresh, and returns the cached value without issuing a second
        refresh_fn call.  Only one refresh fires — guaranteed by the double-check.
        Thread-safety assumption: single asyncio event loop, no thread-pool
        executor mixing with set().  If that assumption ever changes, move the
        fast-path check inside the lock.
        """
        entry = self._store.get(key)
        expired = entry is not None and self._ttl is not None and (self._monotonic() - entry.stored_at) > self._ttl

        if entry is not None and not expired:
            # Fast-path: valid cache hit, no lock needed.
            return entry.value

        # Miss or expired — need to refresh.
        if self._refresh_fn is None:
            return None

        # Serialize concurrent refreshes for the same key via a single instance lock.
        # After acquiring the lock, re-check in case another coroutine already filled it.
        async with self._lock:
            entry = self._store.get(key)
            expired = entry is not None and self._ttl is not None and (self._monotonic() - entry.stored_at) > self._ttl
            if entry is not None and not expired:
                return entry.value

            # Still a miss/expired inside the lock — we are the refresh winner.
            try:
                value = await self._refresh_fn(key)
                self.set(key, value)
                return value
            except Exception as exc:  # pylint: disable=broad-exception-caught
                prior = self._store.get(key)
                if prior is not None:
                    self._log.warning(
                        f"refresh_fn raised for key={key!r}; returning stale value. Error: {type(exc).__name__}: {exc}"
                    )
                    return prior.value
                self._log.warning(
                    f"refresh_fn raised for key={key!r} with no prior value; returning None. "
                    f"Error: {type(exc).__name__}: {exc}"
                )
                return None

    def set(self, key: K, value: V) -> None:
        """Explicitly set a value.  Resets the per-entry timestamp.

        Used by startup preloads and by post-transaction refresh paths.
        Synchronous because it performs no I/O.

        If ``max_entries`` is set and the store exceeds that limit after writing,
        the entry with the smallest ``stored_at`` timestamp is evicted.
        """
        self._store[key] = _Entry(value=value, stored_at=self._monotonic())
        if self._max_entries is not None and len(self._store) > self._max_entries:
            oldest_key = min(self._store, key=lambda k: self._store[k].stored_at)
            del self._store[oldest_key]

    def peek(self, key: K) -> V | None:
        """Return cached value without triggering a refresh. Synchronous.

        Returns None if key is absent or the entry has expired (TTL check).
        Never acquires the lock; safe to call from autocomplete hot paths.
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._ttl is not None and (self._monotonic() - entry.stored_at) > self._ttl:
            return None
        return entry.value

    def schedule_refresh(self, key: K) -> None:
        """Schedule a background refresh for key without blocking.

        No-op if refresh_fn is None. The existing lock in get() coalesces
        duplicate concurrent refresh tasks (AC-ROB-2).
        """
        if self._refresh_fn is None:
            return
        asyncio.create_task(self.get(key), name=f"warm-{self._name}-{key}")  # noqa: RUF006

    async def get_with_timeout(self, key: K, *, timeout: float) -> V | None:
        """Return cached value, with a time-bounded cold-path wait.

        Fast path: if ``peek(key)`` returns a value, return it immediately
        (zero I/O, no lock acquired).

        Cold path: ``await asyncio.shield(self.get(key))`` with a
        ``timeout`` deadline.  ``asyncio.shield`` ensures the inner
        ``get()`` call — which may trigger ``refresh_fn`` — continues
        running even if this coroutine times out.  The next keystroke will
        therefore find the cache already populated.

        On ``asyncio.TimeoutError``: log WARNING and return ``None``.
        The exception is NOT re-raised so autocomplete callers receive
        an empty list instead of a crash.

        On any other exception: log WARNING and return ``None``.

        Args:
            key: Cache key.
            timeout: Maximum seconds to wait for the cold-path ``get()``.

        Returns:
            Cached value, or ``None`` on miss / timeout / error.
        """
        # Fast path — synchronous peek, no I/O.
        value = self.peek(key)
        if value is not None:
            return value

        # Cold path — shield so the inner get() survives a timeout cancel.
        try:
            return await asyncio.wait_for(asyncio.shield(self.get(key)), timeout=timeout)
        except asyncio.TimeoutError:
            self._log.warning(
                f"get_with_timeout: timed out after {timeout}s waiting for key={key!r} in cache {self._name!r}"
            )
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._log.warning(
                f"get_with_timeout: exception for key={key!r} in cache {self._name!r}: {type(exc).__name__}: {exc}"
            )
            return None

    def invalidate(self, key: K) -> None:
        """Drop a single key.  Idempotent.

        Used by /buy and /sell post-success hooks and by the manual
        /reload_autocomplete path.
        """
        self._store.pop(key, None)

    def clear(self) -> None:
        """Drop all entries.  Used by /reload_autocomplete to force full reload."""
        self._store.clear()

    def keys(self) -> list[K]:
        """Return a snapshot of current keys (for debugging / health endpoints)."""
        return list(self._store.keys())

    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._store)
