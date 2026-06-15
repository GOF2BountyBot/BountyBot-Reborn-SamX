"""Shared transient-only HTTP retry helper — exponential back-off with Full Jitter.

Design
------
Uses ``tenacity`` (v9+) for retry orchestration.  Selected over
``httpx-retries`` because tenacity supports both status-code-based and
exception-based predicates in a single composable policy.

Back-off algorithm: *Full Jitter* — ``uniform(0, min(max_wait, multiplier *
2^attempt))``.  This matches the AWS Builders Library recommendation for
inter-service retries and is implemented by ``tenacity.wait_random_exponential``.

Reference URLs (verified 2026-06-07):
  - https://tenacity.readthedocs.io/en/latest/api.html
    (wait_random_exponential = Full-Jitter; stop_after_attempt; retry_if_exception_type)
  - https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    (AWS Full-Jitter canonical algorithm)
  - https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
    (AWS Builders Library — cap retries, always jitter, never retry non-idempotent)

Retry policy (LOCKED — see X5 constraint in PLAN_OF_ACTION_TASKS.md):
  - 3 total attempts (1 initial + 2 retries).
  - Retries ONLY on: connection errors, timeouts, 5xx responses, 429 responses.
  - NEVER retries: other 4xx (400/401/403/404/409/422 etc.) — non-transient.
  - Wait: Full-Jitter exponential — uniform(0, min(10s, 1 * 2^attempt)).
    Attempt 1 → uniform(0, 2s), attempt 2 → uniform(0, 4s), cap at 10s.

Usage
-----
Wrap any async callable that makes a single idempotent HTTP call::

    resp = await with_transient_retry(client.post, url, json=body, timeout=5.0)
    resp.raise_for_status()

Or use the context-manager form for existing response objects::

    async for attempt in transient_retry():
        with attempt:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()

IMPORTANT — X5 constraint: apply ONLY to idempotent cache-set POSTs and warm
GETs.  NEVER wrap announce/upload POSTs — retrying those double-posts to Discord.
"""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# ---------------------------------------------------------------------------
# Transient-failure predicate
# ---------------------------------------------------------------------------

#: Status codes that are safe to retry (transient server errors + rate-limit).
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_transient(exc: BaseException) -> bool:
    """Return True when *exc* represents a transient HTTP failure.

    Rules:
    - ``httpx.ConnectError``, ``httpx.ConnectTimeout``, ``httpx.ReadTimeout``,
      ``httpx.WriteTimeout``, ``httpx.PoolTimeout``, ``httpx.RemoteProtocolError``:
      all transient network/transport errors → retry.
    - ``httpx.HTTPStatusError`` with status in ``_RETRYABLE_STATUSES`` (429/5xx) → retry.
    - ``httpx.HTTPStatusError`` with any other status (400, 401, 403, 404…) → DO NOT retry.
    - Any other exception type → DO NOT retry (conservative default).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUSES
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
        ),
    )


# ---------------------------------------------------------------------------
# Tenacity policy
# ---------------------------------------------------------------------------

#: Shared wait strategy: Full-Jitter exponential, cap at 10 s.
#: attempt 1 → uniform(0, 2 s), attempt 2 → uniform(0, 4 s), capped at 10 s.
TRANSIENT_WAIT = wait_random_exponential(multiplier=1, max=10)

#: Maximum 3 total attempts (1 initial + 2 retries).
TRANSIENT_STOP = stop_after_attempt(3)

#: Retry only on transient failures; raise immediately for non-transient 4xx.
TRANSIENT_RETRY = retry_if_exception(_is_transient)


def make_transient_retry(**kwargs) -> AsyncRetrying:
    """Build an ``AsyncRetrying`` instance with the standard transient policy.

    Extra kwargs (e.g. ``before_sleep``) are forwarded to ``AsyncRetrying``.
    """
    return AsyncRetrying(
        wait=TRANSIENT_WAIT,
        stop=TRANSIENT_STOP,
        retry=TRANSIENT_RETRY,
        reraise=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Convenience wrapper: call an async callable with transient retry
# ---------------------------------------------------------------------------


async def with_transient_retry(fn, *args, **kwargs):
    """Call ``await fn(*args, **kwargs)`` with the standard transient retry policy.

    Calls ``raise_for_status()`` on ``httpx.Response`` return values so that
    HTTP 5xx and 429 status codes are treated as retryable errors — not just
    connection-level failures. This makes the retry predicate work uniformly for
    both network errors and bad-status responses.

    On a ``RetryError`` the last exception is re-raised (``reraise=True``).

    Example::

        # resp is guaranteed 2xx here; raise_for_status() already called internally.
        resp = await with_transient_retry(client.post, url, json=body, timeout=5.0)

    If the final attempt succeeds (2xx), the response object is returned.
    If all attempts fail, the last exception (HTTPStatusError or network exc) is re-raised.
    """
    async for attempt in make_transient_retry():
        with attempt:
            result = await fn(*args, **kwargs)
            # Raise on HTTP error status so the retry predicate can evaluate it.
            # This covers 5xx / 429 responses that don't raise natively.
            if hasattr(result, "raise_for_status"):
                result.raise_for_status()
            return result
    # Unreachable in practice: AsyncRetrying(reraise=True) either returns from
    # the loop body or re-raises the last exception. Explicit for clarity and to
    # satisfy static analysis (all code paths return).
    return None


# Re-export so callers can ``from shared.http_retry import retry_if_exception_type``
# if they need to build custom policies.
__all__ = [
    "TRANSIENT_RETRY",
    "TRANSIENT_STOP",
    "TRANSIENT_WAIT",
    "_RETRYABLE_STATUSES",
    "_is_transient",
    "make_transient_retry",
    "retry_if_exception_type",
    "with_transient_retry",
]
