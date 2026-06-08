"""D9-T1 — Unit tests for shared/http_retry.py transient-retry helper.

Adversarial coverage (per D9-T1 tester spec):
  1. Transient errors (500, 503, timeout, connect) ARE retried.
  2. Non-transient 4xx errors (400, 401, 403, 404, 422) are NOT retried.
  3. 429 IS retried (special transient rate-limit).
  4. Success after a transient blip (1 failure then success) converges.
  5. Jitter is present — delays vary across retry states; no two equal.
  6. Helper never exceeds 3 total attempts (retry cap, no storm).
  7. Non-HTTP exceptions (ValueError, etc.) are NOT retried.

Design notes
------------
- Uses real tenacity (no mock of tenacity internals) with a mocked httpx
  response / exception to drive retry decisions.
- The wait strategy is injected as ``wait_none()`` in tests so they run
  instantly without actual sleeps.
- Verifies ``_is_transient`` directly + through ``with_transient_retry``.
- Max 2 mocks per test (per repo convention).
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest
from tenacity import wait_none

# ---------------------------------------------------------------------------
# Path + shared stub setup
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# conftest.py installs a flat mock 'shared' module (not a real package) to
# suppress bblogger import errors.  We must load shared.http_retry directly
# from the filesystem so Python doesn't try to find it inside the mock object.
if "shared.http_retry" not in sys.modules:
    _spec = _ilu.spec_from_file_location("shared.http_retry", os.path.join(_SRC_DIR, "shared", "http_retry.py"))
    _http_retry_mod = _ilu.module_from_spec(_spec)
    sys.modules["shared.http_retry"] = _http_retry_mod
    _spec.loader.exec_module(_http_retry_mod)

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

from shared.http_retry import (
    _is_transient,
    make_transient_retry,
    with_transient_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build a minimal HTTPStatusError for a given status code."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_req = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=mock_req,
        response=mock_resp,
    )


# ---------------------------------------------------------------------------
# D9-T1-1: _is_transient predicate — transient statuses ARE retried
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_is_transient_retries_5xx_and_429(status: int):
    exc = _http_status_error(status)
    assert _is_transient(exc) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_is_transient_does_not_retry_4xx(status: int):
    exc = _http_status_error(status)
    assert _is_transient(exc) is False


# ---------------------------------------------------------------------------
# D9-T1-2: _is_transient — connection / timeout errors ARE transient
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_type",
    [
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        httpx.NetworkError,
    ],
)
def test_is_transient_retries_network_errors(exc_type):
    # Construct a bare instance (no full args needed for predicate test)
    if exc_type in (httpx.RemoteProtocolError, httpx.NetworkError):
        exc = exc_type("test error", request=MagicMock())
    else:
        exc = exc_type("test error")
    assert _is_transient(exc) is True


def test_is_transient_does_not_retry_non_http_exception():
    """Non-HTTP exceptions (ValueError, RuntimeError, etc.) should NOT be retried."""
    assert _is_transient(ValueError("bad value")) is False
    assert _is_transient(RuntimeError("something broke")) is False


# ---------------------------------------------------------------------------
# D9-T1-3: with_transient_retry — success after a transient blip
# ---------------------------------------------------------------------------


async def test_with_transient_retry_success_after_blip():
    """A single 503 followed by success converges within 3 attempts."""
    call_count = 0

    async def flaky_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _http_status_error(503)
        # Return a mock response for the successful second call
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        return mock_resp

    # Use wait_none() to skip actual sleeping
    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()):
        result = await with_transient_retry(flaky_call, "http://example.com")

    assert call_count == 2
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# D9-T1-4: with_transient_retry — 4xx is NOT retried, raises on first attempt
# ---------------------------------------------------------------------------


async def test_with_transient_retry_404_not_retried():
    """A 404 HTTPStatusError is raised immediately with only 1 call."""
    call_count = 0

    async def always_404(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _http_status_error(404)

    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()), pytest.raises(httpx.HTTPStatusError) as exc_info:
        await with_transient_retry(always_404, "http://example.com")

    assert call_count == 1  # Never retried
    assert exc_info.value.response.status_code == 404


@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_with_transient_retry_non_transient_4xx_not_retried(status: int):
    call_count = 0

    async def always_err(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _http_status_error(status)

    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()), pytest.raises(httpx.HTTPStatusError):
        await with_transient_retry(always_err, "http://example.com")

    assert call_count == 1  # Only 1 attempt — not retried


# ---------------------------------------------------------------------------
# D9-T1-5: with_transient_retry — cap at 3 total attempts (no storm)
# ---------------------------------------------------------------------------


async def test_with_transient_retry_caps_at_3_attempts():
    """Persistent 503 exhausts retries after exactly 3 total attempts."""
    call_count = 0

    async def always_503(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _http_status_error(503)

    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()), pytest.raises(httpx.HTTPStatusError) as exc_info:
        await with_transient_retry(always_503, "http://example.com")

    assert call_count == 3  # 1 initial + 2 retries = 3 total
    assert exc_info.value.response.status_code == 503


async def test_with_transient_retry_timeout_caps_at_3_attempts():
    """Persistent ConnectTimeout also caps at 3 total attempts."""
    call_count = 0

    async def always_timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectTimeout("timed out")

    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()), pytest.raises(httpx.ConnectTimeout):
        await with_transient_retry(always_timeout, "http://example.com")

    assert call_count == 3


# ---------------------------------------------------------------------------
# D9-T1-6: Jitter is present — wait values vary across different retry states
# ---------------------------------------------------------------------------


def test_transient_wait_produces_jitter():
    """wait_random_exponential (Full Jitter) must produce different values.

    Two retry states at the same attempt number should produce different
    wait values with very high probability (Full Jitter = uniform(0, cap)).
    We draw 20 samples and assert they are not all identical.
    """
    from unittest.mock import MagicMock as _MM

    from shared.http_retry import TRANSIENT_WAIT
    from tenacity import RetryCallState

    samples = []
    for _ in range(20):
        rs = _MM(spec=RetryCallState)
        rs.attempt_number = 2  # Fixed attempt — any variance is purely jitter
        samples.append(TRANSIENT_WAIT(rs))

    # All 20 values being identical would be astronomically unlikely with Full Jitter
    assert len(set(samples)) > 1, "Wait values show no jitter — expected variance"


def test_transient_wait_respects_cap():
    """All wait values must be <= 10 seconds (the configured max)."""
    from unittest.mock import MagicMock as _MM

    from shared.http_retry import TRANSIENT_WAIT
    from tenacity import RetryCallState

    for attempt in range(1, 20):  # Test many attempts including far beyond cap
        rs = _MM(spec=RetryCallState)
        rs.attempt_number = attempt
        wait_val = TRANSIENT_WAIT(rs)
        assert wait_val <= 10.0, f"Wait value {wait_val:.3f}s exceeds 10s cap at attempt {attempt}"
        assert wait_val >= 0.0, f"Wait value {wait_val:.3f}s is negative"


# ---------------------------------------------------------------------------
# D9-T1-7: make_transient_retry is configurable
# ---------------------------------------------------------------------------


async def test_make_transient_retry_is_async_retrying():
    """make_transient_retry() returns an AsyncRetrying with reraise=True."""
    from tenacity import AsyncRetrying

    retrying = make_transient_retry()
    assert isinstance(retrying, AsyncRetrying)
    assert retrying.reraise is True


async def test_make_transient_retry_respects_reraise():
    """With reraise=True, the original exception (not RetryError) is re-raised."""
    call_count = 0

    async def always_503(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _http_status_error(503)

    # Patch the module-level TRANSIENT_WAIT to avoid real sleeps
    with patch("shared.http_retry.TRANSIENT_WAIT", wait_none()), pytest.raises(httpx.HTTPStatusError) as exc_info:
        await with_transient_retry(always_503)

    assert exc_info.value.response.status_code == 503
    assert call_count == 3
