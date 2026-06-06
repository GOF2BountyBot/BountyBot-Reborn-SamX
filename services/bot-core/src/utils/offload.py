"""CPU and IO offload seam.

SEAM RATIONALE
--------------
These two functions are the single chokepoint through which all
CPU-bound and IO-bound work is offloaded from the async event loop.

Today
~~~~~
- ``offload_cpu`` → ``ProcessPoolExecutor`` (pure-Python, GIL-bound work).
  Each call crosses a process boundary, so *all arguments and the return
  value MUST be picklable*.  Non-picklable objects (lambdas, local functions,
  open file handles, …) will raise ``PicklingError`` immediately.

- ``offload_io`` → ``ThreadPoolExecutor`` (GIL-releasing work such as
  ``time.sleep``, ``os.read``, C-extension blocking calls).
  Arguments and return values do NOT need to be picklable.

FUTURE FREE-THREADING SWAP PATH
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When CPython free-threading (no-GIL, PEP 703) becomes production-viable
and the process pool overhead is no longer necessary for CPU isolation,
``offload_cpu`` can be repointed at the thread pool by changing only this
module — every call site stays untouched.  The interface is intentionally
identical to ``offload_io`` to make that swap trivial.

Pool Ownership
~~~~~~~~~~~~~~
Neither function instantiates a pool.  The ``ProcessPoolExecutor`` and
``ThreadPoolExecutor`` are created exactly once in the FastAPI lifespan
and registered via ``set_process_pool`` / ``set_thread_pool``.  Calling
either offload function before the pools are registered raises
``RuntimeError`` (surfaced by the holder getters).
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from utils.executor_holder import get_process_pool, get_thread_pool


async def offload_cpu(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run *fn* on the process pool and await the result.

    Parameters
    ----------
    fn:
        A module-level (picklable) callable.  Lambdas and locally-defined
        functions will raise ``PicklingError`` when the task crosses the
        process boundary.
    *args, **kwargs:
        Forwarded to *fn* via ``functools.partial``.  All values must be
        picklable.

    Returns
    -------
    Any
        Whatever *fn* returns.

    Raises
    ------
    RuntimeError
        If the process pool has not been registered yet.
    Exception
        Any exception raised inside *fn* is re-raised in the calling coroutine
        unchanged (this is a property of ``loop.run_in_executor``).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_process_pool(), functools.partial(fn, *args, **kwargs))


async def offload_io(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run *fn* on the thread pool and await the result.

    Parameters
    ----------
    fn:
        Any callable.  Unlike ``offload_cpu``, neither *fn* nor its
        arguments need to be picklable.
    *args, **kwargs:
        Forwarded to *fn* via ``functools.partial``.

    Returns
    -------
    Any
        Whatever *fn* returns.

    Raises
    ------
    RuntimeError
        If the thread pool has not been registered yet.
    Exception
        Any exception raised inside *fn* is re-raised in the calling coroutine
        unchanged.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_thread_pool(), functools.partial(fn, *args, **kwargs))
