"""Service-level test configuration."""

import concurrent.futures
import os
import sys

import pytest

# Add src and src/services to path so all imports work
src_path = os.path.join(os.path.dirname(__file__), "..", "..", "src")
sys.path.insert(0, src_path)
sys.path.insert(0, os.path.join(src_path, "services"))

# P2-T2 isolation fix: import executor_holder at MODULE-IMPORT time so the
# session fixture below always targets the SAME module object that
# combat_service (and utils.offload.get_process_pool) captured at collection
# time.  A deferred import inside the fixture body would be vulnerable to
# tests/test_offload.py's del-sys.modules teardown swapping in a fresh holder
# object, leaving combat_service.offload_cpu referencing an uninitialised
# holder.  The top-level import pins the canonical object before any test can
# mutate sys.modules.
import utils.executor_holder as _holder  # isort: skip


# ---------------------------------------------------------------------------
# P2-T2: thread-pool fixture for fight_ships-exercising tests
#
# fight_ships now calls offload_cpu(run_fight, ...) which requires a pool to
# be registered in executor_holder.  A ThreadPoolExecutor is used here
# (instead of a real forkserver ProcessPoolExecutor) for speed: offload_cpu
# calls loop.run_in_executor on whatever is registered, and run_fight is
# pure so it runs correctly in a thread.  Real subprocess isolation is
# covered by test_combat_worker.py and the live stack.
#
# Scope: session — one pool shared across all tests in this directory.
# Teardown resets the holder so callers that check the holder is unset
# (test_executor_holder / test_offload) are unaffected (they use
# del-sys.modules to reload the module into a clean state anyway, but they
# must also restore the original entry — see tests/test_offload.py).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _services_thread_pool_for_offload():
    """Register a ThreadPoolExecutor in executor_holder for the test session.

    Covers all fight_ships-exercising tests under tests/services/.
    Uses a thread pool (not forkserver) for speed; run_fight is pure and
    correct in a thread.  Torn down + holder reset after the session.

    Registers against _holder (the module-level canonical import) so that
    the pool is always set on the same object that combat_service.offload_cpu
    → utils.offload.get_process_pool references, regardless of the order in
    which test modules are collected or which tests mutate sys.modules.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-combat")
    _holder.set_process_pool(pool)
    yield
    pool.shutdown(wait=True)
    _holder._process_pool = None
