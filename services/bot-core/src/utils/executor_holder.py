"""Module-level executor holder.

Provides singleton references to the running ProcessPoolExecutor and
ThreadPoolExecutor instances so that CPU-bound and IO-bound work can be
offloaded from the async event loop without each call site needing to
construct its own pool.

Pools are SET EXACTLY ONCE in the FastAPI lifespan, before any reader
runs; single event loop, set-once — no locking needed.

Usage
-----
In main.py lifespan, after creating the pools:

    from utils.executor_holder import set_process_pool, set_thread_pool
    set_process_pool(process_pool)
    set_thread_pool(thread_pool)

In modules that need to submit CPU-bound work:

    from utils.executor_holder import get_process_pool
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(get_process_pool(), fn, *args)

In modules that need to submit IO-bound work:

    from utils.executor_holder import get_thread_pool
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(get_thread_pool(), fn, *args)
"""

from __future__ import annotations

import concurrent.futures

_process_pool: concurrent.futures.ProcessPoolExecutor | None = None
_thread_pool: concurrent.futures.ThreadPoolExecutor | None = None


def set_process_pool(pool: concurrent.futures.ProcessPoolExecutor) -> None:
    """Store the active ProcessPoolExecutor instance.  Called once from main.py lifespan."""
    global _process_pool
    _process_pool = pool


def get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    """Return the active ProcessPoolExecutor instance.

    Raises
    ------
    RuntimeError
        If called before set_process_pool(); the pool must be set once in
        the FastAPI lifespan before any reader runs.
    """
    if _process_pool is None:
        raise RuntimeError(
            "ProcessPoolExecutor has not been initialised. "
            "Call set_process_pool() exactly once in the FastAPI lifespan before any reader runs."
        )
    return _process_pool


def set_thread_pool(pool: concurrent.futures.ThreadPoolExecutor) -> None:
    """Store the active ThreadPoolExecutor instance.  Called once from main.py lifespan."""
    global _thread_pool
    _thread_pool = pool


def get_thread_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Return the active ThreadPoolExecutor instance.

    Raises
    ------
    RuntimeError
        If called before set_thread_pool(); the pool must be set once in
        the FastAPI lifespan before any reader runs.
    """
    if _thread_pool is None:
        raise RuntimeError(
            "ThreadPoolExecutor has not been initialised. "
            "Call set_thread_pool() exactly once in the FastAPI lifespan before any reader runs."
        )
    return _thread_pool
