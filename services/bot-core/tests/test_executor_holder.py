"""Tests for utils.executor_holder.

Covers:
  - set_process_pool / get_process_pool round-trip returns the same object
  - set_thread_pool / get_thread_pool round-trip returns the same object
  - get_process_pool() before set raises RuntimeError with a clear message
  - get_thread_pool() before set raises RuntimeError with a clear message
"""

from __future__ import annotations

import concurrent.futures
import sys

import pytest


def _fresh_module():
    """Return a freshly-imported executor_holder with clean global state."""
    mod_name = "utils.executor_holder"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import utils.executor_holder as m

    return m


# ---------------------------------------------------------------------------
# get_* before set_* — must raise RuntimeError
# ---------------------------------------------------------------------------


def test_get_process_pool_before_set_raises():
    m = _fresh_module()
    with pytest.raises(RuntimeError, match="ProcessPoolExecutor has not been initialised"):
        m.get_process_pool()


def test_get_thread_pool_before_set_raises():
    m = _fresh_module()
    with pytest.raises(RuntimeError, match="ThreadPoolExecutor has not been initialised"):
        m.get_thread_pool()


# ---------------------------------------------------------------------------
# set_* / get_* round-trips — returns the same object
# ---------------------------------------------------------------------------


def test_process_pool_round_trip():
    m = _fresh_module()
    pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    try:
        m.set_process_pool(pool)
        assert m.get_process_pool() is pool
    finally:
        pool.shutdown(wait=False)


def test_thread_pool_round_trip():
    m = _fresh_module()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        m.set_thread_pool(pool)
        assert m.get_thread_pool() is pool
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Setting one pool does not affect the other
# ---------------------------------------------------------------------------


def test_process_pool_set_does_not_affect_thread_pool():
    m = _fresh_module()
    pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    try:
        m.set_process_pool(pool)
        with pytest.raises(RuntimeError, match="ThreadPoolExecutor has not been initialised"):
            m.get_thread_pool()
    finally:
        pool.shutdown(wait=False)


def test_thread_pool_set_does_not_affect_process_pool():
    m = _fresh_module()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        m.set_thread_pool(pool)
        with pytest.raises(RuntimeError, match="ProcessPoolExecutor has not been initialised"):
            m.get_process_pool()
    finally:
        pool.shutdown(wait=False)
