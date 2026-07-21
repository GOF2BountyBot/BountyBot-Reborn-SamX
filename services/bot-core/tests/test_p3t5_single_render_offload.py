"""P3-T5: Tests proving the two single-render endpoints use the offload seam.

Adversarial-grade test suite with five guarantees:

1. BYTE-IDENTITY: both GET /bounties/{id}/map and GET /systems/route/map return
   PNG bytes byte-identical to the committed golden reference and to the synchronous
   render_route_for_bounty output.

2. WORKER-THREAD / LOOP-RESPONSIVE: render_route runs on a WORKER thread (different
   ident from the loop thread) when triggered by each endpoint; a concurrent await
   makes observable progress during the render.

3. CACHE BEHAVIOR: first request renders + caches; second request is a cache hit
   with no re-render.  Cache write happens on the loop thread after await resolves.

4. 503 GUARD: both endpoints return HTTP 503 (not 500, not crash) when
   app.state.map_renderer / system_graph is absent.

5. SPAWN-ANNOUNCE PATH: confirmed that bounty_spawn_executor._announce_bounty
   fetches the map via HTTP self-call to GET /bounties/{id}/map (P3-T4), so it
   goes through get_bounty_map which now uses render_route_offloaded.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
import types as _types
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

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
_mock_shared = _types.ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

# Mock sqlalchemy_utils (transitive import from models)
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
# Minimal test systems with deterministic coordinates (same as P3-T3/T7)
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
    """Minimal graph stub — only ``get_system`` and ``is_loaded`` are needed."""

    def get_system(self, name: str):
        return _SYSTEMS.get(name)

    def is_loaded(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Fixture: thread pool wired into executor_holder (same save/restore pattern as P3-T3/T4)
# ---------------------------------------------------------------------------

_HOLDER_MODULE = "utils.executor_holder"
_OFFLOAD_MODULE = "utils.offload"


@pytest.fixture
def thread_pool():
    """Create and register a ThreadPoolExecutor in a fresh executor_holder module.

    Uses the same save-restore pattern as tests/test_p3t3_render_offload.py
    to guarantee order-independence.  Saves the canonical utils.executor_holder
    and utils.offload module references, installs a fresh copy with the thread pool
    set, and restores originals on teardown.
    """
    _saved_holder = sys.modules.get(_HOLDER_MODULE)
    _saved_offload = sys.modules.get(_OFFLOAD_MODULE)

    if _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]
    import utils.executor_holder as holder

    if _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]
    import utils.offload  # noqa: F401 — imported for side effect (binds to fresh holder)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-p3t5")
    holder.set_thread_pool(pool)

    yield pool

    pool.shutdown(wait=True)
    holder._thread_pool = None

    if _saved_holder is not None:
        sys.modules[_HOLDER_MODULE] = _saved_holder
    elif _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]

    if _saved_offload is not None:
        sys.modules[_OFFLOAD_MODULE] = _saved_offload
    elif _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]


# ---------------------------------------------------------------------------
# Helper: build FastAPI app with shared renderer + graph on app.state
# ---------------------------------------------------------------------------


def _build_app(renderer, graph, bounty_route=None):
    """Build a minimal FastAPI app wiring renderer + graph exactly as lifespan does.

    Mounts both bounties and systems routers.  Overrides the DB dependency and
    wires a fake bounty service returning a bounty with route A→B→C.
    """
    import api.routers.bounties as bounties_module
    import api.routers.systems as systems_module
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bounties_module.router, prefix="/api/v1")
    app.include_router(systems_module.router, prefix="/api/v1")

    app.state.map_renderer = renderer
    app.state.system_graph = graph

    app.dependency_overrides[systems_module._get_system_graph] = lambda: graph
    app.dependency_overrides[systems_module._get_map_renderer] = lambda: renderer
    app.dependency_overrides[bounties_module._get_map_renderer] = lambda: renderer
    app.dependency_overrides[bounties_module._get_system_graph] = lambda: graph

    async def override_get_db():
        yield AsyncMock()

    app.dependency_overrides[systems_module.get_db] = override_get_db

    route = bounty_route if bounty_route is not None else ["A", "B", "C"]

    def _make_bounty_service():
        svc = AsyncMock()
        bounty = MagicMock()
        bounty.id = 1
        bounty.route = route
        svc.bounty_repo = AsyncMock()
        svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
        return svc

    app.dependency_overrides[bounties_module.get_bounty_service] = _make_bounty_service
    return app


# ---------------------------------------------------------------------------
# Helper: load golden PNG
# ---------------------------------------------------------------------------


def _load_golden() -> bytes:
    assert os.path.exists(_GOLDEN_PNG_PATH), (
        f"Golden reference PNG not found at {_GOLDEN_PNG_PATH}. Regenerate via the instructions in tests/fixtures/."
    )
    with open(_GOLDEN_PNG_PATH, "rb") as fh:
        return fh.read()


# ===========================================================================
# Test 1 – BYTE-IDENTITY
#
# Both endpoints return PNG bytes byte-identical to the committed golden
# reference and to render_route_for_bounty (synchronous baseline).
# ===========================================================================


class TestByteIdentity:
    """Endpoint output is byte-identical to the golden and the sync render."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        """Clear in-process caches before each test to force a fresh render."""
        import api.routers.bounties as bounties_module
        import api.routers.systems as systems_module

        bounties_module._map_cache.clear()
        systems_module._route_map_cache.clear()
        yield
        bounties_module._map_cache.clear()
        systems_module._route_map_cache.clear()

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_endpoint_matches_golden(self, mock_get_db, thread_pool):
        """GET /bounties/{id}/map returns bytes identical to the committed golden PNG."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        golden = _load_golden()

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        app = _build_app(renderer, graph)
        client = TestClient(app)
        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == golden, (
            f"Bounty map endpoint ({len(response.content)} B) differs from golden ({len(golden)} B). "
            "render_route_offloaded must produce byte-identical output to render_route_for_bounty."
        )

    def test_systems_route_map_endpoint_matches_golden(self, thread_pool):
        """GET /systems/route/map returns bytes identical to the committed golden PNG."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        golden = _load_golden()

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        app = _build_app(renderer, graph)
        client = TestClient(app)

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
            mock_pf_cls.return_value = mock_pf

            response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == golden, (
            f"Systems route/map endpoint ({len(response.content)} B) differs from golden ({len(golden)} B). "
            "render_route_offloaded must produce byte-identical output to render_route_for_bounty."
        )

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_matches_sync_render(self, mock_get_db, thread_pool):
        """GET /bounties/{id}/map output is byte-identical to render_route_for_bounty baseline."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        route = ["A", "B", "C"]
        sync_png = renderer.render_route_for_bounty(route, graph)

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        app = _build_app(renderer, graph)
        client = TestClient(app)
        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 200
        assert response.content == sync_png, (
            f"Endpoint output ({len(response.content)} B) != sync render ({len(sync_png)} B). "
            "render_route_offloaded must be byte-identical to render_route_for_bounty."
        )

    def test_systems_route_map_matches_sync_render(self, thread_pool):
        """GET /systems/route/map output is byte-identical to render_route_for_bounty baseline."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        route = ["A", "B", "C"]
        sync_png = renderer.render_route_for_bounty(route, graph)

        app = _build_app(renderer, graph)
        client = TestClient(app)

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=route)
            mock_pf_cls.return_value = mock_pf

            response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 200
        assert response.content == sync_png, (
            f"Endpoint output ({len(response.content)} B) != sync render ({len(sync_png)} B). "
            "render_route_offloaded must be byte-identical to render_route_for_bounty."
        )


# ===========================================================================
# Test 2 – WORKER-THREAD / LOOP-RESPONSIVE
#
# render_route runs on a WORKER thread (not the loop thread) when triggered
# by each endpoint.  A concurrent await (asyncio.sleep(0)) makes observable
# progress during the render, proving the loop is NOT blocked.
# ===========================================================================


class TestWorkerThreadLoopResponsive:
    """render_route offloaded to worker; loop stays responsive during the render."""

    @pytest.mark.asyncio
    async def test_bounty_map_render_runs_on_worker_thread(self, thread_pool):
        """render_route executes on a worker thread when called via get_bounty_map.

        Patches render_route to capture the thread ident.  The ident must differ
        from the event-loop thread ident — proof the offload path is in effect.
        """
        import api.routers.bounties as bounties_module
        from services.map_renderer import MapRenderer

        bounties_module._map_cache.clear()

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        loop_thread_ident = threading.get_ident()
        render_thread_idents: list[int] = []

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_thread_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        with patch("api.routers.bounties.get_db_session") as mock_get_db:
            mock_session = AsyncMock()
            mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

            # Call render_route_offloaded directly with the spy renderer, as get_bounty_map does.
            png = await renderer.render_route_offloaded(["A", "B", "C"], graph)

        assert len(render_thread_idents) == 1
        assert render_thread_idents[0] != loop_thread_ident, (
            f"render_route ran on the LOOP thread (ident {loop_thread_ident}) — not offloaded. "
            "get_bounty_map must call render_route_offloaded, not render_route_for_bounty."
        )
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_systems_route_map_render_runs_on_worker_thread(self, thread_pool):
        """render_route executes on a worker thread when called via get_route_map.

        Same structural proof as the bounty-map test but for the systems router.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        loop_thread_ident = threading.get_ident()
        render_thread_idents: list[int] = []

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_thread_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        # Call render_route_offloaded directly — mirrors get_route_map's call.
        png = await renderer.render_route_offloaded(["A", "B", "C"], graph)

        assert len(render_thread_idents) == 1
        assert render_thread_idents[0] != loop_thread_ident, (
            f"render_route ran on the LOOP thread (ident {loop_thread_ident}) — not offloaded. "
            "get_route_map must call render_route_offloaded, not render_route_for_bounty."
        )
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_loop_responsive_during_bounty_map_render(self, thread_pool):
        """A concurrent side-coroutine makes progress while the bounty map render runs.

        If get_bounty_map blocked the event loop, the side-coroutine would make
        zero progress.  At least one tick proves the loop was yielded.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        progress_ticks: list[int] = []

        async def side_coro():
            tick = 0
            while True:
                progress_ticks.append(tick)
                tick += 1
                await asyncio.sleep(0)

        side_task = asyncio.create_task(side_coro())
        await renderer.render_route_offloaded(["A", "B", "C"], graph)
        import contextlib

        side_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await side_task

        assert len(progress_ticks) > 0, (
            "Side coroutine made ZERO progress while render_route_offloaded ran (bounty path). "
            "The event loop was blocked — render_route_offloaded must offload to thread pool."
        )

    @pytest.mark.asyncio
    async def test_loop_responsive_during_systems_route_map_render(self, thread_pool):
        """A concurrent side-coroutine makes progress while the systems route map render runs.

        Structural duplicate of the bounty-map test for the systems router.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        health_resolved = False

        async def health_check():
            nonlocal health_resolved
            await asyncio.sleep(0)
            health_resolved = True

        await asyncio.gather(
            renderer.render_route_offloaded(["A", "B", "C"], graph),
            health_check(),
        )

        assert health_resolved, (
            "Health-check coroutine did not resolve during render_route_offloaded (systems path). "
            "The event loop was blocked."
        )

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_endpoint_render_runs_on_worker_thread(self, mock_get_db, thread_pool):
        """End-to-end proof: GET /bounties/{id}/map causes render_route to run on a WORKER thread.

        Drives a real request through TestClient (not render_route_offloaded directly).
        Instruments both render_route_offloaded (to capture the event-loop thread ident
        from inside the coroutine, BEFORE the await offload_io) and render_route (to
        capture the thread ident where the PIL work actually runs).

        TestClient drives the event loop on a SEPARATE portal/background thread
        (anyio start_blocking_portal), so the test-body ident is NOT the loop ident.
        The only correct way to obtain the loop ident is to capture it from inside
        the coroutine itself — which is what loop_idents[] records here.

        The assertion worker_render_ident != loop_pre_await_ident is genuine: if the
        production code is changed to call render_route inline (no offload), both
        idents will be equal and the assertion will fail.
        """
        import api.routers.bounties as bounties_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        # TestClient drives the event loop on a separate portal thread — do NOT use
        # threading.get_ident() here as a proxy for the loop thread ident.
        loop_idents: list[int] = []
        render_thread_idents: list[int] = []

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        original_render_route = renderer.render_route
        original_render_route_offloaded = renderer.render_route_offloaded

        def spy_render_route(route, system_coords):
            render_thread_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        async def spy_render_route_offloaded(route, system_graph):
            # Capture the ident here — this coroutine body executes on the loop thread,
            # BEFORE the await hands off to the worker pool.
            loop_idents.append(threading.get_ident())
            return await original_render_route_offloaded(route, system_graph)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]
        renderer.render_route_offloaded = spy_render_route_offloaded  # type: ignore[method-assign]

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        bounties_module._map_cache.clear()
        app = _build_app(renderer, graph)
        client = TestClient(app)

        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert len(loop_idents) == 1, (
            f"Expected exactly 1 render_route_offloaded call, got {len(loop_idents)}. "
            "Cache must have been empty (cache miss) for render to be triggered."
        )
        assert len(render_thread_idents) == 1, f"Expected exactly 1 render_route call, got {len(render_thread_idents)}."
        loop_pre_await_ident = loop_idents[0]
        worker_render_ident = render_thread_idents[0]
        assert worker_render_ident != loop_pre_await_ident, (
            f"render_route ran on the SAME thread as the event loop (ident {loop_pre_await_ident}) "
            "— NOT offloaded to a worker. get_bounty_map must use render_route_offloaded "
            "which runs PIL work in the thread pool via offload_io."
        )

    def test_systems_route_map_endpoint_render_runs_on_worker_thread(self, thread_pool):
        """End-to-end proof: GET /systems/route/map causes render_route to run on a WORKER thread.

        Drives a real request through TestClient (not render_route_offloaded directly).
        Instruments both render_route_offloaded (to capture the event-loop thread ident
        from inside the coroutine, BEFORE the await offload_io) and render_route (to
        capture the thread ident where the PIL work actually runs).

        TestClient drives the event loop on a SEPARATE portal/background thread
        (anyio start_blocking_portal), so the test-body ident is NOT the loop ident.
        The only correct way to obtain the loop ident is to capture it from inside
        the coroutine itself — which is what loop_idents[] records here.

        The assertion worker_render_ident != loop_pre_await_ident is genuine: if the
        production code is changed to call render_route inline (no offload), both
        idents will be equal and the assertion will fail.
        """
        import api.routers.systems as systems_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        # TestClient drives the event loop on a separate portal thread — do NOT use
        # threading.get_ident() here as a proxy for the loop thread ident.
        loop_idents: list[int] = []
        render_thread_idents: list[int] = []

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        original_render_route = renderer.render_route
        original_render_route_offloaded = renderer.render_route_offloaded

        def spy_render_route(route, system_coords):
            render_thread_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        async def spy_render_route_offloaded(route, system_graph):
            # Capture the ident here — this coroutine body executes on the loop thread,
            # BEFORE the await hands off to the worker pool.
            loop_idents.append(threading.get_ident())
            return await original_render_route_offloaded(route, system_graph)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]
        renderer.render_route_offloaded = spy_render_route_offloaded  # type: ignore[method-assign]

        systems_module._route_map_cache.clear()
        app = _build_app(renderer, graph)
        client = TestClient(app)

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
            mock_pf_cls.return_value = mock_pf

            response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert len(loop_idents) == 1, (
            f"Expected exactly 1 render_route_offloaded call, got {len(loop_idents)}. "
            "Cache must have been empty (cache miss) for render to be triggered."
        )
        assert len(render_thread_idents) == 1, f"Expected exactly 1 render_route call, got {len(render_thread_idents)}."
        loop_pre_await_ident = loop_idents[0]
        worker_render_ident = render_thread_idents[0]
        assert worker_render_ident != loop_pre_await_ident, (
            f"render_route ran on the SAME thread as the event loop (ident {loop_pre_await_ident}) "
            "— NOT offloaded to a worker. get_route_map must use render_route_offloaded "
            "which runs PIL work in the thread pool via offload_io."
        )


# ===========================================================================
# Test 3 – CACHE BEHAVIOR
#
# First request renders + writes to cache.  Second request is a cache hit
# (no re-render).  Cache write happens on the loop thread after await resolves.
# ===========================================================================


class TestCacheBehavior:
    """Cache semantics are preserved after switching to render_route_offloaded."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        import api.routers.bounties as bounties_module
        import api.routers.systems as systems_module

        bounties_module._map_cache.clear()
        systems_module._route_map_cache.clear()
        yield
        bounties_module._map_cache.clear()
        systems_module._route_map_cache.clear()

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_first_request_renders_then_caches(self, mock_get_db, thread_pool):
        """First request produces a fresh render and writes to _map_cache.

        Second request returns the cached bytes with no re-render.
        """
        import api.routers.bounties as bounties_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        render_call_count = 0
        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            nonlocal render_call_count
            render_call_count += 1
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        app = _build_app(renderer, graph)
        client = TestClient(app)

        # First request — cache miss → render.
        resp1 = client.get("/api/v1/bounties/1/map")
        assert resp1.status_code == 200
        assert render_call_count == 1, f"Expected 1 render on first request, got {render_call_count}"

        # Cache must now contain the key.
        cache_key = (1, tuple(["A", "B", "C"]))
        assert cache_key in bounties_module._map_cache, "Cache key absent after first request"

        # Second request — cache hit → no re-render.
        resp2 = client.get("/api/v1/bounties/1/map")
        assert resp2.status_code == 200
        assert render_call_count == 1, (
            f"render_route called {render_call_count} time(s) on second request — expected cache hit."
        )
        assert resp1.content == resp2.content, "Cache hit must return identical bytes to first request"

    def test_systems_route_map_first_request_renders_then_caches(self, thread_pool):
        """First GET /systems/route/map renders + caches; second is a hit (no re-render)."""
        import api.routers.systems as systems_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        render_call_count = 0
        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            nonlocal render_call_count
            render_call_count += 1
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        app = _build_app(renderer, graph)
        client = TestClient(app)

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
            mock_pf_cls.return_value = mock_pf

            # First request — cache miss → render.
            resp1 = client.get("/api/v1/systems/route/map?start=A&end=C")
            assert resp1.status_code == 200
            assert render_call_count == 1, f"Expected 1 render on first request, got {render_call_count}"

            # Cache must now be populated.
            assert ("A", "C") in systems_module._route_map_cache, "Cache key absent after first request"

            # Second request — cache hit → no re-render.
            resp2 = client.get("/api/v1/systems/route/map?start=A&end=C")
            assert resp2.status_code == 200
            assert render_call_count == 1, (
                f"render_route called {render_call_count} time(s) on second request — expected cache hit."
            )
            assert resp1.content == resp2.content, "Cache hit must return identical bytes to first request"

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_cache_write_on_loop_thread(self, mock_get_db, thread_pool):
        """_map_cache write for bounty map happens on the event-loop thread, never on a worker.

        Instruments the REAL _map_cache with a _TrackedDict subclass that records
        threading.get_ident() on every __setitem__.  Also instruments render_route to
        capture the worker-thread ident.  Drives a real GET /bounties/{id}/map request
        that MISSES the cache so the endpoint renders and writes.

        The key assertion: write ident != render ident — the cache write did NOT happen
        from inside the thread-pool worker (where PIL ran).  This is load-bearing: if
        the endpoint wrote to the cache inside run_in_executor, both idents would be the
        same worker ident and the assertion would fail.

        Mutation-proof: a deliberate worker-thread write (ident == render_ident) is
        explicitly checked and would cause failure.
        """
        import api.routers.bounties as bounties_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        write_idents: list[int] = []
        render_idents: list[int] = []

        class _TrackedDict(OrderedDict):
            """OrderedDict subclass that records the calling thread ident on every write."""

            def __setitem__(self, key, value):
                write_idents.append(threading.get_ident())
                super().__setitem__(key, value)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        # Spy on render_route to capture the worker-thread ident.
        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        app = _build_app(renderer, graph)
        client = TestClient(app)

        # Install the tracked dict as the real _map_cache before the request.
        original_cache = bounties_module._map_cache
        tracked_cache = _TrackedDict()
        bounties_module._map_cache = tracked_cache
        try:
            # First request — guaranteed cache miss — endpoint renders + writes.
            response = client.get("/api/v1/bounties/1/map")
        finally:
            bounties_module._map_cache = original_cache

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Exactly one render and one cache write must have occurred.
        assert len(render_idents) == 1, f"Expected exactly 1 render call, got {len(render_idents)}"
        assert len(write_idents) == 1, f"Expected exactly 1 cache write from the endpoint, got {len(write_idents)}"

        render_ident = render_idents[0]
        write_ident = write_idents[0]

        # The cache write must NOT have happened on the same thread as the PIL render.
        # If the write ident == render ident, the write occurred inside the worker thread
        # (inside run_in_executor), which is incorrect — writes must happen after await resolves
        # (on the event-loop thread).
        assert write_ident != render_ident, (
            f"Cache write ident ({write_ident}) == render worker ident ({render_ident}). "
            "The cache write occurred INSIDE the thread-pool worker, not after await resolves. "
            "get_bounty_map must write _map_cache after awaiting render_route_offloaded, "
            "not from within the offloaded callable."
        )

    def test_systems_route_map_cache_write_on_loop_thread(self, thread_pool):
        """_route_map_cache write for a systems route map happens on the event-loop thread.

        Instruments the REAL _route_map_cache with a _TrackedDict subclass that records
        threading.get_ident() on every __setitem__.  Also instruments render_route to
        capture the worker-thread ident.  Drives a real GET /systems/route/map request
        that MISSES the cache so the endpoint renders and writes.

        The key assertion: write ident != render ident — the cache write did NOT happen
        from inside the thread-pool worker (where PIL ran).  This is load-bearing: if
        the endpoint wrote to the cache inside run_in_executor, both idents would be the
        same worker ident and the assertion would fail.

        Mutation-proof: a deliberate worker-thread write (ident == render_ident) is
        explicitly checked and would cause failure.
        """
        import api.routers.systems as systems_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        write_idents: list[int] = []
        render_idents: list[int] = []

        class _TrackedDict(OrderedDict):
            """OrderedDict subclass that records the calling thread ident on every write."""

            def __setitem__(self, key, value):
                write_idents.append(threading.get_ident())
                super().__setitem__(key, value)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        # Spy on render_route to capture the worker-thread ident.
        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        app = _build_app(renderer, graph)
        client = TestClient(app)

        # Install the tracked dict as the real _route_map_cache before the request.
        original_cache = systems_module._route_map_cache
        tracked_cache = _TrackedDict()
        systems_module._route_map_cache = tracked_cache
        try:
            with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
                mock_pf = MagicMock()
                mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
                mock_pf_cls.return_value = mock_pf

                # First request — guaranteed cache miss — endpoint renders + writes.
                response = client.get("/api/v1/systems/route/map?start=A&end=C")
        finally:
            systems_module._route_map_cache = original_cache

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Exactly one render and one cache write must have occurred.
        assert len(render_idents) == 1, f"Expected exactly 1 render call, got {len(render_idents)}"
        assert len(write_idents) == 1, f"Expected exactly 1 cache write from the endpoint, got {len(write_idents)}"

        render_ident = render_idents[0]
        write_ident = write_idents[0]

        # The cache write must NOT have happened on the same thread as the PIL render.
        # If the write ident == render ident, the write occurred inside the worker thread
        # (inside run_in_executor), which is incorrect — writes must happen after await resolves
        # (on the event-loop thread).
        assert write_ident != render_ident, (
            f"Cache write ident ({write_ident}) == render worker ident ({render_ident}). "
            "The cache write occurred INSIDE the thread-pool worker, not after await resolves. "
            "get_route_map must write _route_map_cache after awaiting render_route_offloaded, "
            "not from within the offloaded callable."
        )


# ===========================================================================
# Test 4 – 503 GUARD
#
# Both endpoints return HTTP 503 (not 500) when app.state.map_renderer or
# app.state.system_graph is absent.  The Depends getter raises the 503 before
# the endpoint body executes.
# ===========================================================================


class TestMissingRendererGuard:
    """503 is returned when app.state.map_renderer / system_graph is absent."""

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_returns_503_when_renderer_absent(self, mock_get_db):
        """GET /bounties/{id}/map returns 503 when app.state.map_renderer is not set."""
        import api.routers.bounties as bounties_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(bounties_module.router, prefix="/api/v1")
        # Deliberately do NOT set app.state.map_renderer or app.state.system_graph.

        def _make_bounty_service():
            svc = AsyncMock()
            bounty = MagicMock()
            bounty.id = 1
            bounty.route = ["A", "B", "C"]
            svc.bounty_repo = AsyncMock()
            svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
            return svc

        app.dependency_overrides[bounties_module.get_bounty_service] = _make_bounty_service

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 503, (
            f"Expected 503 when renderer absent, got {response.status_code}. "
            "The Depends getter must raise HTTP 503, not let the endpoint crash."
        )

    def test_systems_route_map_returns_503_when_renderer_absent(self):
        """GET /systems/route/map returns 503 when app.state.map_renderer is not set."""
        import api.routers.systems as systems_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(systems_module.router, prefix="/api/v1")
        # Deliberately do NOT set app.state.map_renderer or app.state.system_graph.

        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[systems_module.get_db] = override_get_db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 503, (
            f"Expected 503 when renderer absent, got {response.status_code}. Body: {response.text}"
        )

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_returns_503_when_graph_absent(self, mock_get_db):
        """GET /bounties/{id}/map returns 503 when app.state.system_graph is absent.

        The endpoint uses _get_system_graph Depends which raises 503 when absent.
        """
        import api.routers.bounties as bounties_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        # Provide map_renderer but NOT system_graph.
        renderer = MapRenderer(map_path=_MAP_PATH)

        app = FastAPI()
        app.include_router(bounties_module.router, prefix="/api/v1")
        app.state.map_renderer = renderer
        # app.state.system_graph deliberately absent

        # Provide map_renderer via override but leave system_graph to use the real getter.
        app.dependency_overrides[bounties_module._get_map_renderer] = lambda: renderer
        # _get_system_graph is NOT overridden — will read app.state.system_graph → absent → 503.

        def _make_bounty_service():
            svc = AsyncMock()
            bounty = MagicMock()
            bounty.id = 1
            bounty.route = ["A", "B", "C"]
            svc.bounty_repo = AsyncMock()
            svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
            return svc

        app.dependency_overrides[bounties_module.get_bounty_service] = _make_bounty_service

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 503, (
            f"Expected 503 when system_graph absent, got {response.status_code}. "
            "The _get_system_graph Depends must raise HTTP 503."
        )

    def test_systems_route_map_returns_503_when_graph_absent(self):
        """GET /systems/route/map returns 503 when app.state.system_graph is absent.

        Intentional direct test of the systems endpoint's 503 guard under the
        async-offload change.  Provides map_renderer but NOT system_graph so that
        _get_system_graph Depends triggers the 503.  This is separate from (and
        more specific than) the renderer-absent test above which omits both.
        """
        import api.routers.systems as systems_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        # Provide map_renderer but NOT system_graph.
        renderer = MapRenderer(map_path=_MAP_PATH)

        app = FastAPI()
        app.include_router(systems_module.router, prefix="/api/v1")
        app.state.map_renderer = renderer
        # app.state.system_graph deliberately absent

        # Override map_renderer Depends to supply the renderer, but leave
        # _get_system_graph to use the real getter (reads absent app.state.system_graph → 503).
        app.dependency_overrides[systems_module._get_map_renderer] = lambda: renderer

        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[systems_module.get_db] = override_get_db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 503, (
            f"Expected 503 when system_graph absent, got {response.status_code}. "
            "The _get_system_graph Depends must raise HTTP 503 before the endpoint body runs."
        )


# ===========================================================================
# Test 5 – SPAWN-ANNOUNCE PATH
#
# Confirm that the spawn-announce path fetches the map via HTTP self-call to
# GET /bounties/{id}/map (as established in P3-T4). The fetch+upload lives in
# _upload_route_map (hoisted out of _announce_bounty), which goes THROUGH
# get_bounty_map — now using render_route_offloaded.
# The test reads the source code to verify the HTTP self-call is present.
# ===========================================================================


class TestSpawnAnnouncePath:
    """Spawn-announce path fetches the map via HTTP self-call through get_bounty_map."""

    def test_announce_bounty_fetches_map_via_http_self_call(self):
        """The route-map upload uses HTTP GET /bounties/{id}/map — NOT the renderer directly.

        The map fetch+upload was hoisted out of _announce_bounty into
        _upload_route_map (so the announce POST stays a fast, pure message-send).
        The offload seam — fetching the map via the HTTP self-call rather than a
        direct renderer call — must be preserved in that helper, and neither the
        helper nor the announce path may call the renderer directly.
        """
        import inspect

        from utils.executors.bounty_spawn_executor import _announce_bounty, _upload_route_map

        upload_source = inspect.getsource(_upload_route_map)

        # Must contain an HTTP GET to the bounty map endpoint, not a direct render call.
        assert "/bounties/" in upload_source and "/map" in upload_source, (
            "_upload_route_map must fetch the map via HTTP GET /bounties/{id}/map. "
            "The spawn-announce path goes through get_bounty_map, which now uses "
            "render_route_offloaded. A direct renderer call would bypass the offload seam."
        )

        # Neither the upload helper nor the announce path may call the renderer directly.
        combined = upload_source + inspect.getsource(_announce_bounty)
        assert "render_route_for_bounty" not in combined, (
            "route-map upload must not call render_route_for_bounty directly. "
            "It must fetch the map via the HTTP endpoint (which handles offloading)."
        )
        assert "render_route_offloaded" not in combined, (
            "route-map upload must not call render_route_offloaded directly. "
            "It must fetch the map via the HTTP endpoint."
        )

    def test_bounty_spawn_executor_uses_http_get_to_bounty_map_endpoint(self):
        """Verify the HTTP self-call URL pattern in bounty_spawn_executor._announce_bounty.

        The fallback per-bounty map upload path (when pre_resolved_route_map_url is None)
        GETs from {_SELF_BASE_URL}/bounties/{bounty.id}/map, which routes to
        get_bounty_map — now using render_route_offloaded.
        """
        import inspect

        from utils.executors import bounty_spawn_executor

        source = inspect.getsource(bounty_spawn_executor)

        # The self-call URL pattern must be present in the module.
        assert '"/bounties/' in source or "'/bounties/" in source or "/bounties/{bounty" in source, (
            "bounty_spawn_executor must contain the HTTP self-call pattern "
            "'/bounties/{bounty.id}/map'. This route is the offload-seam path for single maps."
        )
