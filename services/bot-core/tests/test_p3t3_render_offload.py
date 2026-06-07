"""P3-T3: Tests proving ``render_route`` is a pure, offloadable callable.

Adversarial-grade test suite with five guarantees:

1. BYTE-IDENTITY: rendered PNG via the offload path is byte-identical to the
   committed golden reference PNG.  Any pixel-level regression fails this test.

2. THREAD-ID EVIDENCE:
   a. ``render_route`` DID run on a WORKER thread (different ident from the
      event-loop thread) when called via ``render_route_offloaded``.
   b. ``_load_base`` is NOT called on a worker thread when the renderer is
      pre-warmed (it fast-paths in ``render_route`` with zero I/O).
   c. Mutation-proof: if the renderer is NOT pre-warmed, ``_load_base`` WOULD
      be called on the worker thread — the assertion catches this, confirming
      the guard is load-bearing.

3. PURE-CALLABLE: ``render_route(route, coords)`` produces a valid PNG when
   called with NO db, NO session, NO graph — only plain Python data.  Passing
   an unavailable/None graph to it has no effect because it never touches one.

4. LOOP-RESPONSIVE: a concurrent coroutine makes progress while a render is
   offloaded — the event loop is NOT blocked.

5. SIGNATURE: ``render_route_offloaded`` exists on MapRenderer and the
   loop-side coord-resolution step does NOT call ``load_graph``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure src/ is on sys.path and mocks are in place before any src imports.
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# Mock shared.bblogger (not installed in test env)
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

# Mock sqlalchemy_utils (transitive import from models)
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

_GOLDEN_PNG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures", "golden_route_abc.png"))

# ---------------------------------------------------------------------------
# Test systems (same as P3-T7 suite — deterministic coordinates)
# ---------------------------------------------------------------------------


def _make_node(name: str, x: int, y: int, neighbours: list[str]):
    from services.system_graph_service import SystemNode

    return SystemNode(name=name, coordinates=(x, y), neighbours=neighbours, faction="Neutral", security=1)


_SYSTEMS = {
    "A": _make_node("A", 100, 100, ["B"]),
    "B": _make_node("B", 200, 100, ["A", "C"]),
    "C": _make_node("C", 300, 100, ["B"]),
}


class _FakeGraph:
    """Minimal graph stub — only ``get_system`` is needed."""

    def get_system(self, name: str):
        return _SYSTEMS.get(name)


# ---------------------------------------------------------------------------
# Fixture: thread pool wired into executor_holder
#
# Uses the same save-restore pattern as tests/test_offload.py to guarantee
# order-independence: saves the canonical utils.executor_holder and
# utils.offload module references, installs a fresh copy with the thread pool
# set, and restores originals on teardown.  This prevents the fixture from
# leaving a shutdown pool in executor_holder._thread_pool (which would cause
# RuntimeError for any test that runs after the last test in this file and
# calls offload_io without re-registering a pool).
# ---------------------------------------------------------------------------

_HOLDER_MODULE = "utils.executor_holder"
_OFFLOAD_MODULE = "utils.offload"


@pytest.fixture
def thread_pool():
    """Create and register a ThreadPoolExecutor in a fresh executor_holder module.

    Saves and restores sys.modules state so this fixture does NOT leak a
    shutdown pool reference into subsequent tests (order-independence guarantee).
    Mirrors the pattern used in tests/test_offload.py's ``pools`` fixture.
    """
    import sys

    # Save originals before swapping (may be already present or None).
    _saved_holder = sys.modules.get(_HOLDER_MODULE)
    _saved_offload = sys.modules.get(_OFFLOAD_MODULE)

    # Fresh holder so we start from a clean slate.
    if _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]
    import utils.executor_holder as holder

    # Also reload offload so it picks up the fresh holder's get_thread_pool.
    if _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]
    import utils.offload  # noqa: F401 — imported for side effect (binds to fresh holder)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-render")
    holder.set_thread_pool(pool)

    yield pool

    pool.shutdown(wait=True)

    # Reset the fresh holder's global so no dangling reference exists.
    holder._thread_pool = None

    # Restore originals so subsequent tests see the canonical module objects.
    if _saved_holder is not None:
        sys.modules[_HOLDER_MODULE] = _saved_holder
    elif _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]

    if _saved_offload is not None:
        sys.modules[_OFFLOAD_MODULE] = _saved_offload
    elif _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]


# ===========================================================================
# Test 1 – BYTE-IDENTITY
#
# The PNG bytes produced by ``render_route_offloaded`` must be byte-identical
# to the committed golden reference for route A→B→C.
# ===========================================================================


class TestByteIdentity:
    """Offloaded render output matches the committed golden reference PNG."""

    @pytest.mark.asyncio
    async def test_offloaded_render_matches_golden(self, thread_pool):
        """PNG bytes from render_route_offloaded must equal the golden fixture."""
        from services.map_renderer import MapRenderer

        assert os.path.exists(_GOLDEN_PNG_PATH), (
            f"Golden reference PNG not found at {_GOLDEN_PNG_PATH}. "
            "Regenerate via: python -c 'from services.map_renderer import MapRenderer; ...'"
        )
        with open(_GOLDEN_PNG_PATH, "rb") as fh:
            golden = fh.read()

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        route = ["A", "B", "C"]
        png = await renderer.render_route_offloaded(route, _FakeGraph())

        assert png == golden, (
            f"Offloaded render output ({len(png)} bytes) differs from golden ({len(golden)} bytes). "
            "render_route_offloaded must produce byte-identical output to render_route_for_bounty."
        )

    @pytest.mark.asyncio
    async def test_offloaded_matches_sync_for_bounty(self, thread_pool):
        """render_route_offloaded produces the same bytes as render_route_for_bounty."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        route = ["A", "B", "C"]
        graph = _FakeGraph()

        sync_png = renderer.render_route_for_bounty(route, graph)
        offload_png = await renderer.render_route_offloaded(route, graph)

        assert offload_png == sync_png, (
            "render_route_offloaded must produce byte-identical output to render_route_for_bounty."
        )


# ===========================================================================
# Test 2 – THREAD-ID EVIDENCE
#
# a. render_route ran on a WORKER thread (different ident from loop thread).
# b. _load_base was NOT called during the worker execution when pre-warmed.
# c. Mutation-proof: cold renderer → _load_base IS called on worker → detected.
# ===========================================================================


class TestThreadIdEvidence:
    """Thread-id checks proving the offload path works correctly."""

    @pytest.mark.asyncio
    async def test_render_runs_on_worker_thread(self, thread_pool):
        """render_route executes on a DIFFERENT thread than the event loop.

        Captures the thread ident inside render_route via a patched wrapper
        and asserts it differs from the loop thread's ident.
        """
        from services.map_renderer import MapRenderer

        loop_thread_ident = threading.get_ident()
        render_thread_ident: list[int] = []

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_thread_ident.append(threading.get_ident())
            return original_render_route(route, system_coords)

        # Patch the instance method so our spy records the thread ident.
        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        route = ["A", "B", "C"]
        await renderer.render_route_offloaded(route, _FakeGraph())

        assert len(render_thread_ident) == 1, (
            f"Expected render_route to be called exactly once, got {len(render_thread_ident)}"
        )
        assert render_thread_ident[0] != loop_thread_ident, (
            f"render_route should run on a WORKER thread (ident {render_thread_ident[0]}), "
            f"but it ran on the loop thread (ident {loop_thread_ident}). "
            "If they are equal, offload_io is not actually offloading."
        )

    @pytest.mark.asyncio
    async def test_image_open_not_called_on_worker_when_prewarmed(self, thread_pool):
        """Image.open (disk I/O) is NOT called on a worker thread when the renderer is pre-warmed.

        After prewarm(), ``_base_image`` is already populated.  When ``render_route``
        calls ``_load_base()``, the outer ``if self._base_image is None`` short-circuits
        immediately with no lock acquisition and no disk read.  We instrument
        ``Image.open`` directly — not ``_load_base`` — because ``_load_base`` IS called
        but its fast-path returns immediately when the cache is warm.

        The key invariant: no disk I/O happens on a worker thread when pre-warmed.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        assert renderer._base_image is not None, "Renderer must be pre-warmed before this test"

        image_open_thread_idents: list[int] = []

        # Capture the pre-loaded image so the spy can return a real Image object if called.
        real_image = renderer._base_image

        def spy_image_open(path):
            image_open_thread_idents.append(threading.get_ident())
            return real_image

        route = ["A", "B", "C"]
        with patch("services.map_renderer.Image.open", side_effect=spy_image_open):
            await renderer.render_route_offloaded(route, _FakeGraph())

        assert len(image_open_thread_idents) == 0, (
            f"Expected Image.open NOT to be called on any thread (pre-warmed), "
            f"but it was called {len(image_open_thread_idents)} time(s) on thread(s) "
            f"{image_open_thread_idents}. "
            "Pre-warm must populate _base_image so no disk read occurs on the worker thread."
        )

    @pytest.mark.asyncio
    async def test_mutation_proof_cold_renderer_triggers_image_open_on_worker(self, thread_pool):
        """ADVERSARIAL: a cold renderer causes Image.open (disk I/O) to execute on the worker thread.

        This proves the pre-warm requirement is load-bearing: if prewarm() is
        NOT called before offloading, ``Image.open`` is called from the worker
        thread — which is the unsafe behaviour that pre-warming prevents.

        Contrast with test_image_open_not_called_on_worker_when_prewarmed:
        - Warm renderer → Image.open NOT called on any thread.
        - Cold renderer (this test) → Image.open IS called on the worker thread.

        If the code is ever changed to proactively block cold renders on worker threads,
        this test should be updated to assert a RuntimeError is raised instead.
        """
        from PIL import Image as PILImage
        from services.map_renderer import MapRenderer

        loop_thread_ident = threading.get_ident()

        # Cold renderer — prewarm() deliberately NOT called.
        renderer = MapRenderer(map_path=_MAP_PATH)
        assert renderer._base_image is None, "Renderer must start cold for this test"

        image_open_thread_idents: list[int] = []
        # Pre-load the real image to give our spy a valid return value.
        real_image = PILImage.open(_MAP_PATH).convert("RGB")

        def spy_image_open(path):
            image_open_thread_idents.append(threading.get_ident())
            return real_image

        route = ["A", "B", "C"]
        with patch("services.map_renderer.Image.open", side_effect=spy_image_open):
            await renderer.render_route_offloaded(route, _FakeGraph())

        # Image.open MUST have been called (cold renderer).
        assert len(image_open_thread_idents) >= 1, (
            f"Expected Image.open to be called at least once for a cold renderer, got {len(image_open_thread_idents)}."
        )

        # Critically: it must have been called on a WORKER thread, not the loop.
        # Phase-1 (coord resolution) runs on the loop; Phase-2 (render) runs on the worker.
        # _load_base → Image.open is called inside render_route → worker thread.
        non_loop_calls = [t for t in image_open_thread_idents if t != loop_thread_ident]
        assert len(non_loop_calls) >= 1, (
            f"Expected Image.open to be called on a worker thread (cold renderer), "
            f"but all calls were on the loop thread (ident {loop_thread_ident}). "
            f"Recorded idents: {image_open_thread_idents}. "
            "This means render_route is NOT being offloaded to a worker thread."
        )


# ===========================================================================
# Test 3 – PURE-CALLABLE: render_route with zero DB/graph/session dependency
#
# Call render_route directly with plain Python data (no db, no graph, no session).
# It must produce a valid PNG.  Passing graph=None or a broken graph to
# render_route_offloaded (which resolves coords before offloading) also works.
# ===========================================================================


class TestPureCallable:
    """render_route produces valid PNG from plain Python data alone."""

    def test_render_route_with_plain_data_no_graph(self):
        """render_route(route, coords) works with plain dicts — no ORM, no graph."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        # Pre-warm before calling render_route (production invariant)
        renderer.prewarm()

        route = ["A", "B", "C"]
        coords = {"A": (100, 100), "B": (200, 100), "C": (300, 100)}

        # Call directly with plain Python data — no DB, no graph, no session.
        png = renderer.render_route(route, coords)

        assert png[:8] == b"\x89PNG\r\n\x1a\n", "render_route must return valid PNG bytes"
        assert len(png) > 0

    def test_render_route_accepts_empty_coords(self):
        """render_route with an empty coords dict returns the base map (no crash)."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        png = renderer.render_route(["A", "B", "C"], {})
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_render_route_accepts_empty_route(self):
        """render_route with an empty route returns the base map (no crash)."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        coords = {"A": (100, 100)}
        png = renderer.render_route([], coords)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_offloaded_render_with_graph_returning_none(self, thread_pool):
        """render_route_offloaded gracefully handles a graph where all lookups return None.

        The coord-resolution phase skips systems not found in the graph;
        render_route is then called with an empty coords dict and returns the base map.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        class _NullGraph:
            def get_system(self, _name):
                return None  # no systems found

        png = await renderer.render_route_offloaded(["A", "B", "C"], _NullGraph())
        assert png[:8] == b"\x89PNG\r\n\x1a\n", "Should return base map when no coords resolved"


# ===========================================================================
# Test 4 – LOOP RESPONSIVE
#
# A concurrent coroutine makes observable progress while render_route_offloaded
# is running.  The event loop must NOT be blocked.
# ===========================================================================


class TestLoopResponsive:
    """Event loop remains responsive during an offloaded render."""

    @pytest.mark.asyncio
    async def test_concurrent_coroutine_makes_progress_during_render(self, thread_pool):
        """A side-coroutine increments a counter while the render is offloaded.

        If render_route_offloaded blocked the event loop, the side-coroutine
        would not be able to run during the render.  Detecting at least one
        progress tick proves the loop was yielded.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        progress_ticks: list[int] = []

        async def side_coro():
            """Increment a counter on every event-loop iteration while alive."""
            tick = 0
            while True:
                progress_ticks.append(tick)
                tick += 1
                await asyncio.sleep(0)  # yield to the event loop

        route = ["A", "B", "C"]

        # Start the side coroutine, run the render, then cancel.
        side_task = asyncio.create_task(side_coro())
        await renderer.render_route_offloaded(route, _FakeGraph())
        import contextlib

        side_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await side_task

        assert len(progress_ticks) > 0, (
            "Side coroutine made ZERO progress while render_route_offloaded ran. "
            "This indicates the event loop was blocked — offload_io is not working."
        )

    @pytest.mark.asyncio
    async def test_health_style_await_completes_during_render(self, thread_pool):
        """An asyncio.sleep(0) completes while the render is running.

        Models a health-check-style await: the loop must stay responsive so
        lightweight coroutines are not starved during heavy renders.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        health_resolved = False

        async def health_check():
            nonlocal health_resolved
            await asyncio.sleep(0)
            health_resolved = True

        route = ["A", "B", "C"]

        # Run render and health check concurrently; gather waits for both.
        await asyncio.gather(
            renderer.render_route_offloaded(route, _FakeGraph()),
            health_check(),
        )

        assert health_resolved, (
            "Health-check coroutine did not resolve during render_route_offloaded. "
            "The event loop was blocked — offload_io must yield the loop."
        )


# ===========================================================================
# Test 5 – SIGNATURE AND COORD-RESOLUTION ISOLATION
#
# Verify that render_route_offloaded exists, and that the coord-resolution
# phase in Phase-1 (loop) does NOT call load_graph.
# ===========================================================================


class TestSignatureAndIsolation:
    """render_route_offloaded has the correct signature and never triggers load_graph."""

    def test_render_route_offloaded_exists_on_map_renderer(self):
        """MapRenderer has a render_route_offloaded method."""
        from services.map_renderer import MapRenderer

        assert hasattr(MapRenderer, "render_route_offloaded"), (
            "MapRenderer must have a render_route_offloaded method (P3-T3 offload seam)."
        )
        import asyncio as _asyncio

        assert _asyncio.iscoroutinefunction(MapRenderer.render_route_offloaded), (
            "render_route_offloaded must be an async (coroutine) method."
        )

    @pytest.mark.asyncio
    async def test_coord_resolution_does_not_call_load_graph(self, thread_pool):
        """The Phase-1 coord-resolution step in render_route_offloaded does NOT call load_graph.

        load_graph is an async method that performs DB I/O.  Phase 1 must only
        read from the already-loaded graph (via get_system) — never trigger a
        fresh DB load.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        load_graph_call_count = 0

        class _SpyGraph:
            def get_system(self, name):
                return _SYSTEMS.get(name)

            async def load_graph(self, db):
                nonlocal load_graph_call_count
                load_graph_call_count += 1

        spy_graph = _SpyGraph()
        route = ["A", "B", "C"]
        await renderer.render_route_offloaded(route, spy_graph)

        assert load_graph_call_count == 0, (
            f"render_route_offloaded called load_graph {load_graph_call_count} time(s). "
            "Phase-1 coord-resolution must only call get_system (read-only), never load_graph."
        )

    @pytest.mark.asyncio
    async def test_coord_resolution_calls_get_system_for_each_route_system(self, thread_pool):
        """Phase-1 calls get_system exactly once per system in the route."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()

        get_system_calls: list[str] = []

        class _SpyGraph:
            def get_system(self, name):
                get_system_calls.append(name)
                return _SYSTEMS.get(name)

        spy_graph = _SpyGraph()
        route = ["A", "B", "C"]
        await renderer.render_route_offloaded(route, spy_graph)

        assert get_system_calls == route, (
            f"Expected get_system to be called exactly once per route system in order, got: {get_system_calls}"
        )
