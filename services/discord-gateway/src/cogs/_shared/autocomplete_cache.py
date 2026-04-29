"""AutocompleteCache — in-memory key→value cache with optional TTL and refresh callable.

Designed for Discord autocomplete handlers that need to serve from in-memory state
rather than issuing HTTP calls on every keystroke.

Typical uses:
  - Static preload (TTL=None, refresh_fn=None): set(...) at startup;
    get(...) reads forever; invalidate / clear available for manual reload.
  - Lazy TTL cache (TTL=300, refresh_fn=async_loader): get(...) returns
    a fresh value on miss/expiry by awaiting refresh_fn(key); subsequent
    hits within the TTL window are O(1) with no HTTP.
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
        _monotonic: Monotonic clock callable. Override in tests to control
            time without sleeping or external dependencies.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        refresh_fn: Callable[[K], Awaitable[V]] | None = None,
        name: str = "autocomplete-cache",
        _monotonic: Callable[[], float] = monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._refresh_fn = refresh_fn
        self._name = name
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
        """
        self._store[key] = _Entry(value=value, stored_at=self._monotonic())

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
