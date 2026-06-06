"""Tests for utils.offload.

Covers:
  - offload_cpu runs fn in a SEPARATE process (returned pid != current pid)
  - offload_cpu returns the correct value
  - offload_cpu propagates exceptions from fn to the awaiter
  - offload_cpu raises (not hangs) when given a non-picklable argument
  - offload_io runs fn and returns the correct result
  - offload_io propagates exceptions from fn to the awaiter
  - Calling either function without a registered pool raises RuntimeError

Module-level helpers are required for process-pool tests because lambdas
and locally-defined functions are NOT picklable and cannot cross the
process boundary.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import multiprocessing
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Module-level picklable helpers (MUST be at top level for process pool)
# ---------------------------------------------------------------------------


def _return_pid() -> int:
    """Return the PID of the process executing this function."""
    return os.getpid()


def _add(a: int, b: int) -> int:
    """Return a + b."""
    return a + b


def _raises() -> None:
    """Always raise a ValueError with a known message."""
    raise ValueError("deliberate-error-from-worker")


def _identity(value):
    """Return value unchanged (used to test that args round-trip correctly)."""
    return value


def _multiply_sum(a: int, b: int, multiplier: int = 1) -> int:
    """Return (a + b) * multiplier — tests that kwargs survive the process boundary."""
    return (a + b) * multiplier


# ---------------------------------------------------------------------------
# Fixture: real pools registered in the holder, torn down after each test
# ---------------------------------------------------------------------------


@pytest.fixture()
def pools():
    """Create real ProcessPoolExecutor and ThreadPoolExecutor, register them
    in the holder, yield, then shut them down and reset the holder globals so
    no state leaks into subsequent tests."""
    # Fresh holder module so we start from a clean slate
    holder_name = "utils.executor_holder"
    if holder_name in sys.modules:
        del sys.modules[holder_name]
    import utils.executor_holder as holder  # noqa: PLC0415

    # Also reload offload so it picks up the fresh holder
    offload_name = "utils.offload"
    if offload_name in sys.modules:
        del sys.modules[offload_name]
    import utils.offload as offload  # noqa: PLC0415

    process_pool = concurrent.futures.ProcessPoolExecutor(
        mp_context=multiprocessing.get_context("forkserver"),
        max_workers=2,
    )
    thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    holder.set_process_pool(process_pool)
    holder.set_thread_pool(thread_pool)

    yield offload

    process_pool.shutdown(wait=True)
    thread_pool.shutdown(wait=True)

    # Reset globals so the next test sees an uninitialised holder
    holder._process_pool = None
    holder._thread_pool = None


# ---------------------------------------------------------------------------
# offload_cpu tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offload_cpu_runs_in_separate_process(pools):
    """offload_cpu must execute fn in a different process."""
    worker_pid = await pools.offload_cpu(_return_pid)
    assert worker_pid != os.getpid(), (
        f"Expected worker PID to differ from test PID {os.getpid()}, got {worker_pid}"
    )


@pytest.mark.asyncio
async def test_offload_cpu_returns_correct_value(pools):
    """offload_cpu must return the value produced by fn."""
    result = await pools.offload_cpu(_add, 7, 5)
    assert result == 12


@pytest.mark.asyncio
async def test_offload_cpu_propagates_exception(pools):
    """Exceptions raised inside fn must propagate unchanged to the awaiter."""
    with pytest.raises(ValueError, match="deliberate-error-from-worker"):
        await pools.offload_cpu(_raises)


@pytest.mark.asyncio
async def test_offload_cpu_forwards_kwargs(pools):
    """offload_cpu must forward keyword arguments across the process boundary."""
    result = await pools.offload_cpu(_multiply_sum, 3, 4, multiplier=5)
    assert result == 35, f"Expected 35, got {result}"


@pytest.mark.asyncio
async def test_offload_cpu_non_picklable_arg_raises_loudly(pools):
    """Passing a non-picklable argument (a lambda) to offload_cpu must raise,
    not hang.  The process boundary requires all args to be picklable."""
    non_picklable = lambda x: x  # noqa: E731 — intentionally non-picklable

    with pytest.raises(Exception):  # PicklingError or AttributeError from multiprocessing
        await pools.offload_cpu(_identity, non_picklable)


# ---------------------------------------------------------------------------
# offload_io tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offload_io_returns_correct_value(pools):
    """offload_io must return the value produced by fn."""
    result = await pools.offload_io(_add, 3, 4)
    assert result == 7


@pytest.mark.asyncio
async def test_offload_io_propagates_exception(pools):
    """Exceptions raised inside fn must propagate unchanged to the awaiter."""
    with pytest.raises(ValueError, match="deliberate-error-from-worker"):
        await pools.offload_io(_raises)


@pytest.mark.asyncio
async def test_offload_io_accepts_non_picklable_fn(pools):
    """offload_io runs in a thread, so non-picklable callables are fine."""
    non_picklable_fn = lambda: 42  # noqa: E731

    result = await pools.offload_io(non_picklable_fn)
    assert result == 42


# ---------------------------------------------------------------------------
# Holder not set — must surface clearly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offload_cpu_without_pool_raises():
    """Calling offload_cpu before the process pool is registered must raise RuntimeError."""
    holder_name = "utils.executor_holder"
    if holder_name in sys.modules:
        del sys.modules[holder_name]
    import utils.executor_holder as holder  # noqa: PLC0415

    offload_name = "utils.offload"
    if offload_name in sys.modules:
        del sys.modules[offload_name]
    import utils.offload as offload  # noqa: PLC0415

    # Neither pool is set on the fresh module
    with pytest.raises(RuntimeError, match="ProcessPoolExecutor has not been initialised"):
        await offload.offload_cpu(_add, 1, 2)

    # Clean up
    holder._process_pool = None
    holder._thread_pool = None


@pytest.mark.asyncio
async def test_offload_io_without_pool_raises():
    """Calling offload_io before the thread pool is registered must raise RuntimeError."""
    holder_name = "utils.executor_holder"
    if holder_name in sys.modules:
        del sys.modules[holder_name]
    import utils.executor_holder as holder  # noqa: PLC0415

    offload_name = "utils.offload"
    if offload_name in sys.modules:
        del sys.modules[offload_name]
    import utils.offload as offload  # noqa: PLC0415

    # Neither pool is set on the fresh module
    with pytest.raises(RuntimeError, match="ThreadPoolExecutor has not been initialised"):
        await offload.offload_io(_add, 1, 2)

    # Clean up
    holder._process_pool = None
    holder._thread_pool = None
