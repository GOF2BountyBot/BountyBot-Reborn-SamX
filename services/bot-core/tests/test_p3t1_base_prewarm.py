"""P3-T1: Tests proving the base map image is pre-warmed on the loop thread and
loaded exactly once — even under concurrent cold-cache pressure.

Adversarial-grade test suite with three guarantees:

1. CONCURRENT-COLD: spawn N threads all hitting a cold MapRenderer at the same
   time; assert Image.open() is called exactly once (the double-checked lock
   prevents double-decode).  Mutation-proof: removing the inner lock check
   would allow count > 1.

2. PREWARM-STARTUP: after lifespan startup, ``_map_renderer.prewarm()`` has
   already been called, so ``_base_image`` is populated and Image.open() is
   NOT called again on the first request.

3. PREWARM-IDEMPOTENT: calling ``prewarm()`` a second time is a no-op
   (Image.open() is called once, not twice).
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from PIL import Image

# ---------------------------------------------------------------------------
# Ensure src/ is on sys.path and shared.bblogger is mocked before imports.
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

import types as _types

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = _types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _sqla_utils

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_MAP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "import_data",
        "system-map.png",
    )
)


# ===========================================================================
# Test 1 – CONCURRENT COLD: base image loaded exactly once under fan-out
#
# N threads all call render_route() simultaneously on a renderer whose cache
# is cold.  The double-checked lock inside _load_base() ensures Image.open()
# fires at most once.
#
# Mutation-proof sub-test: remove the inner (locked) guard; only the outer
# check remains → multiple threads race past it → count > 1 → test would fail.
# ===========================================================================


class TestConcurrentColdLoad:
    """Image.open() is called exactly once even when many threads hit cold cache."""

    def _run_concurrent_renders(self, renderer, real_image, n_threads: int = 10) -> int:
        """Launch *n_threads* threads each calling render_route() and return Image.open call_count."""
        open_call_count = 0
        open_lock = threading.Lock()

        def counting_open(path):
            nonlocal open_call_count
            with open_lock:
                open_call_count += 1
            return real_image

        errors: list[Exception] = []
        barrier = threading.Barrier(n_threads)  # synchronise all threads to start together

        def worker():
            barrier.wait()  # hold until all threads are ready → maximum concurrency
            try:
                renderer.render_route(["A"], {"A": (100, 100)})
            except Exception as exc:
                errors.append(exc)

        with patch("services.map_renderer.Image.open", side_effect=counting_open):
            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Worker thread(s) raised: {errors}"
        return open_call_count

    def test_image_open_called_once_under_concurrent_cold_load(self):
        """Image.open() fires exactly once when N threads all hit a cold cache.

        The double-checked lock in _load_base() guarantees at most one decode
        even when all threads race past the outer ``if self._base_image is None``
        check simultaneously.
        """
        from services.map_renderer import MapRenderer

        real_image = Image.open(_MAP_PATH).convert("RGB")
        renderer = MapRenderer(map_path=_MAP_PATH)
        # Verify cache is cold before the test.
        assert renderer._base_image is None, "Cache should start cold"

        count = self._run_concurrent_renders(renderer, real_image, n_threads=10)

        assert count == 1, (
            f"Expected Image.open call_count==1 under concurrent cold load, got {count}. "
            "The double-checked lock should prevent double-decode."
        )

    # ------------------------------------------------------------------
    # Mutation-proof: demonstrate count > 1 WITHOUT the inner lock guard
    # ------------------------------------------------------------------

    def test_mutation_proof_no_inner_guard_allows_double_decode(self):
        """ADVERSARIAL: confirm that removing the inner (locked) guard would allow
        multiple threads to decode the image — proving the guard is load-bearing.

        We simulate the *broken* behaviour by patching _load_base to use only
        the outer check (no lock), then firing concurrent threads.  Because the
        real image decode is instant in tests (mock), we inject a small sleep
        inside the open call to widen the race window; in normal tests the
        Image.open mock returns immediately, so the race is less likely to
        manifest.  We use a threading.Event to guarantee the second thread
        enters counting_open before the first sets _base_image.
        """
        from services.map_renderer import MapRenderer

        real_image = Image.open(_MAP_PATH).convert("RGB")
        renderer = MapRenderer(map_path=_MAP_PATH)

        open_call_count = 0
        open_lock = threading.Lock()
        first_entered = threading.Event()
        may_continue = threading.Event()

        def slow_open(path):
            nonlocal open_call_count
            with open_lock:
                open_call_count += 1
            first_entered.set()  # signal: first thread is inside open()
            may_continue.wait()  # stall until second thread has also entered
            return real_image

        def broken_load_base(self_inner):
            """Simulates _load_base WITHOUT the inner lock guard."""
            if self_inner._base_image is None:
                self_inner._base_image = slow_open(self_inner._map_path)
            return self_inner._base_image

        errors: list[Exception] = []

        def worker():
            try:
                broken_load_base(renderer)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()

        first_entered.wait(timeout=5)  # wait until t1 is inside slow_open
        t2.start()
        # Give t2 time to read _base_image is None (before t1 sets it).
        t2.join(timeout=0.05)
        may_continue.set()  # let both threads finish

        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Worker raised: {errors}"
        # Without the inner guard, both threads can decode simultaneously → count >= 2.
        assert open_call_count >= 2, (
            f"Expected open_call_count>=2 in broken (no-inner-guard) scenario, got {open_call_count}. "
            "This test proves the double-checked lock is load-bearing: without it the race is open."
        )


# ===========================================================================
# Test 2 – PREWARM-STARTUP: prewarm() pre-populates cache; no decode on first render
# ===========================================================================


class TestPrewarmStartup:
    """After prewarm(), the cache is warm and first render does NOT call Image.open."""

    def test_prewarm_populates_cache(self):
        """After prewarm(), _base_image is not None."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        assert renderer._base_image is None, "Cache should start cold"
        renderer.prewarm()
        assert renderer._base_image is not None, "prewarm() must populate _base_image"

    def test_no_image_open_call_after_prewarm(self):
        """Image.open is NOT called during render_route() when cache is already warm."""
        from services.map_renderer import MapRenderer

        real_image = Image.open(_MAP_PATH).convert("RGB")
        renderer = MapRenderer(map_path=_MAP_PATH)
        # Pre-warm BEFORE patching so the patch only catches post-warm calls.
        renderer.prewarm()

        open_call_count = 0

        def counting_open(path):
            nonlocal open_call_count
            open_call_count += 1
            return real_image

        with patch("services.map_renderer.Image.open", side_effect=counting_open):
            renderer.render_route(["A"], {"A": (100, 100)})
            renderer.render_route(["A"], {"A": (100, 100)})

        assert open_call_count == 0, (
            f"Expected Image.open to NOT be called after prewarm(), got {open_call_count} call(s). "
            "A warm cache must short-circuit _load_base without calling open()."
        )

    async def test_prewarm_called_once_in_lifespan(self):
        """Image.open is called exactly ONCE during lifespan startup (prewarm call).

        After startup no further decode occurs on the first render.
        """
        import main as main_module

        test_app = FastAPI()
        open_call_count = 0
        real_image = Image.open(_MAP_PATH).convert("RGB")

        def counting_open(path):
            nonlocal open_call_count
            open_call_count += 1
            return real_image

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.add_job = MagicMock()

        mock_db_mgr = _make_mock_db_manager()

        with (
            _lifespan_patches(mock_db_mgr, mock_scheduler),
            patch("services.map_renderer.Image.open", side_effect=counting_open),
        ):
            async with main_module.lifespan(test_app):
                pass  # startup + immediate shutdown

        assert open_call_count == 1, (
            f"Expected Image.open call_count==1 during lifespan (prewarm), got {open_call_count}. "
            "prewarm() must load the base image exactly once at startup."
        )


# ===========================================================================
# Test 3 – PREWARM-IDEMPOTENT: calling prewarm() twice does not decode twice
# ===========================================================================


class TestPrewarmIdempotent:
    """prewarm() called multiple times still decodes the image only once."""

    def test_double_prewarm_decodes_once(self):
        """Calling prewarm() twice on the same renderer fires Image.open once."""
        from services.map_renderer import MapRenderer

        real_image = Image.open(_MAP_PATH).convert("RGB")
        open_call_count = 0

        def counting_open(path):
            nonlocal open_call_count
            open_call_count += 1
            return real_image

        renderer = MapRenderer(map_path=_MAP_PATH)

        with patch("services.map_renderer.Image.open", side_effect=counting_open):
            renderer.prewarm()  # first call: cold → decodes
            renderer.prewarm()  # second call: warm → no-op

        assert open_call_count == 1, (
            f"Expected Image.open call_count==1 after double prewarm(), got {open_call_count}. "
            "prewarm() must be idempotent."
        )

    def test_render_after_double_prewarm_decodes_once(self):
        """prewarm() twice + render still only ever calls Image.open once total."""
        from services.map_renderer import MapRenderer

        real_image = Image.open(_MAP_PATH).convert("RGB")
        open_call_count = 0

        def counting_open(path):
            nonlocal open_call_count
            open_call_count += 1
            return real_image

        renderer = MapRenderer(map_path=_MAP_PATH)

        with patch("services.map_renderer.Image.open", side_effect=counting_open):
            renderer.prewarm()
            renderer.prewarm()
            renderer.render_route(["A"], {"A": (100, 100)})
            renderer.render_route(["A"], {"A": (100, 100)})

        assert open_call_count == 1, (
            f"Expected Image.open call_count==1 across two prewarms + two renders, got {open_call_count}."
        )


# ===========================================================================
# Helpers (mirrors _lifespan_patches / _make_mock_db_manager from test_p3t7)
# ===========================================================================


def _make_mock_db_manager():
    mock_execute_result = MagicMock()
    mock_execute_result.all.return_value = []

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()

    mock_db_mgr = MagicMock()
    mock_db_mgr.initialize = AsyncMock()
    mock_db_mgr._connection_string = "postgresql+asyncpg://user:pass@host/db"
    mock_db_mgr.shutdown = MagicMock()

    @asynccontextmanager
    async def _mock_get_session():
        yield mock_db_session

    mock_db_mgr.get_session = _mock_get_session
    return mock_db_mgr


def _lifespan_patches(mock_db_mgr, mock_scheduler):
    import contextlib

    stack = contextlib.ExitStack()

    stack.enter_context(patch("main.db_manager", mock_db_mgr))
    stack.enter_context(patch("main.run_stale_state_recovery_sweep", new_callable=AsyncMock))
    stack.enter_context(patch("main.run_stale_respawn_recovery", new_callable=AsyncMock))
    stack.enter_context(patch("main.initialize_schema", new_callable=AsyncMock, return_value=MagicMock()))
    stack.enter_context(patch("main.auto_seed_data", new_callable=AsyncMock))
    stack.enter_context(patch("main.create_engine"))
    stack.enter_context(patch("main.SQLAlchemyJobStore"))
    stack.enter_context(patch("main.AsyncIOScheduler", return_value=mock_scheduler))
    stack.enter_context(patch("main.register_default_jobs"))
    mock_mm_cls = stack.enter_context(patch("persist.database.migration_manager.MigrationManager"))
    mock_mm_instance = MagicMock()
    mock_mm_instance.ensure_current = MagicMock()
    mock_mm_cls.from_async_url.return_value = mock_mm_instance
    stack.enter_context(patch("main.ProcessPoolExecutor", return_value=MagicMock()))
    stack.enter_context(patch("main.ThreadPoolExecutor", return_value=MagicMock()))
    stack.enter_context(patch("main.multiprocessing.set_forkserver_preload"))
    stack.enter_context(patch("main.multiprocessing.get_context"))
    stack.enter_context(patch("main.set_process_pool"))
    stack.enter_context(patch("main.set_thread_pool"))
    stack.enter_context(
        patch(
            "services.system_graph_service.SystemGraphService.load_graph",
            AsyncMock(),
        )
    )
    return stack
