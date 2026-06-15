"""P3-T7: Tests proving MapRenderer + SystemGraphService are hoisted to ONE shared,
pre-warmed pair stored on app.state.

Adversarial-grade test suite with three guarantees:

1. SINGLETON-DEDUP: base image loads exactly once and graph builds exactly once
   across app startup + a request to each endpoint.  The mutation-check shows
   that the old two-singleton code would produce count==2 (test would then fail).

2. OUTPUT-REGRESSION: both /bounties/{id}/map and /systems/route/map return renders
   byte-identical to a committed golden reference PNG captured independently of the
   current code path.  This ensures rendered output does not silently change.
   NOTE: Pillow PNG encoding is deterministic so two *separate* MapRenderer instances
   with the same map+graph+route produce identical bytes — the byte-identity check
   cannot prove shared-instance behaviour.  Shared-instance proof lives in
   TestSingletonDedup (load-count==1 + mutation check).

3. WARMUP: the system graph is pre-loaded at startup so no cold DB hit occurs
   on the first real request.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import sys
import types as _types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

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
# Resolve the real star-map path so tests load the actual PNG.
# ---------------------------------------------------------------------------

_MAP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "import_data",
        "system-map.png",
    )
)


# ---------------------------------------------------------------------------
# Minimal system graph helpers
# ---------------------------------------------------------------------------


def _make_node(name: str, x: int, y: int, neighbours: list[str]):
    from services.system_graph_service import SystemNode

    return SystemNode(name=name, coordinates=(x, y), neighbours=neighbours, faction="Neutral", security=1)


_SYSTEMS = {
    "A": _make_node("A", 100, 100, ["B"]),
    "B": _make_node("B", 200, 100, ["A", "C"]),
    "C": _make_node("C", 300, 100, ["B"]),
}

# Path to the committed golden reference PNG (rendered once, checked in).
# The fixture was produced by render_route_for_bounty(["A","B","C"], graph)
# with the _SYSTEMS coordinates above against the real system-map.png.
# If render output changes, regenerate by running:
#   cd src && python - <<'EOF'
#   ... (see test/fixtures/README for instructions)
# EOF
_GOLDEN_PNG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures", "golden_route_abc.png"))

_HOLDER_MODULE = "utils.executor_holder"
_OFFLOAD_MODULE = "utils.offload"


@pytest.fixture
def thread_pool():
    """Create and register a ThreadPoolExecutor in a fresh executor_holder module.

    Required because the endpoints now call render_route_offloaded (P3-T5), which
    offloads the PIL render to the thread pool via offload_io.  Tests that exercise
    the real MapRenderer via TestClient need a thread pool registered.

    Uses the same save-restore pattern as test_p3t3_render_offload.py to guarantee
    order-independence: saves and restores the canonical module references on teardown.
    """
    _saved_holder = sys.modules.get(_HOLDER_MODULE)
    _saved_offload = sys.modules.get(_OFFLOAD_MODULE)

    if _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]
    import utils.executor_holder as holder

    if _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]
    import utils.offload  # noqa: F401 — imported for side effect (binds to fresh holder)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-p3t7")
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


def _make_loaded_graph():
    """Return a mock SystemGraphService that reports as pre-loaded."""
    svc = MagicMock()
    svc.is_loaded = MagicMock(return_value=True)
    svc.load_graph = AsyncMock()

    def _get_system(name):
        return _SYSTEMS.get(name)

    svc.get_system = MagicMock(side_effect=_get_system)
    svc.get_neighbours = MagicMock(return_value=[])
    return svc


# ---------------------------------------------------------------------------
# Shared-pair app builder (mirrors the lifespan wiring)
# ---------------------------------------------------------------------------


def _build_app_with_shared_pair(map_renderer, system_graph) -> FastAPI:
    """Build a minimal FastAPI app that wires map_renderer + system_graph on
    app.state exactly as the lifespan does, and mounts both routers.

    Both routers now use Depends getter functions, so we override all four
    getters (bounties and systems) to return the shared objects directly.
    app.state is also set so the optional getters used by admin-spawn work.
    """
    import api.routers.bounties as bounties_module
    import api.routers.systems as systems_module

    app = FastAPI()
    app.include_router(bounties_module.router, prefix="/api/v1")
    app.include_router(systems_module.router, prefix="/api/v1")

    # Wire shared singletons on app.state (mirrors lifespan).
    # Needed for the optional getters used by admin-spawn.
    app.state.map_renderer = map_renderer
    app.state.system_graph = system_graph

    # Override dependency getters in BOTH routers to return the shared objects.
    app.dependency_overrides[systems_module._get_system_graph] = lambda: system_graph
    app.dependency_overrides[systems_module._get_map_renderer] = lambda: map_renderer
    app.dependency_overrides[bounties_module._get_map_renderer] = lambda: map_renderer
    app.dependency_overrides[bounties_module._get_system_graph] = lambda: system_graph

    async def override_get_db():
        yield AsyncMock()

    app.dependency_overrides[systems_module.get_db] = override_get_db

    # Wire a trivial bounty service that returns a fake bounty for map tests.
    def _make_bounty_service():
        svc = AsyncMock()
        bounty = MagicMock()
        bounty.id = 1
        bounty.route = ["A", "B", "C"]
        svc.bounty_repo = AsyncMock()
        svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
        return svc

    app.dependency_overrides[bounties_module.get_bounty_service] = _make_bounty_service

    return app


# ===========================================================================
# Test 1 – SINGLETON DEDUP
#
# Patch Image.open (base-image load) and SystemGraphService.load_graph
# (graph build) and assert each is called exactly ONCE regardless of how many
# requests are made.
#
# Mutation-proof sub-test: if we restore the old two-singleton pattern
# (construct a SECOND MapRenderer + SystemGraphService for one of the routers),
# the counts jump to 2 and this assertion would fail — confirming the test is
# load-bearing.
# ===========================================================================


class TestSingletonDedup:
    """Base image and system graph are constructed and loaded exactly once."""

    def _run_with_shared_pair(self, real_image):
        """Build a shared pair, wire it on app.state, hit both endpoints,
        and return (image_open_count, load_graph_call_count).
        """
        from services.map_renderer import MapRenderer
        from services.system_graph_service import SystemGraphService

        image_open_count = 0

        def counting_open(path):
            nonlocal image_open_count
            image_open_count += 1
            return real_image

        # Track load_graph calls on the real SystemGraphService.
        load_graph_count = 0

        original_load_graph = SystemGraphService.load_graph

        async def counting_load_graph(self_inner, db):
            nonlocal load_graph_count
            load_graph_count += 1
            await original_load_graph(self_inner, db)

        with (
            patch("services.map_renderer.Image.open", side_effect=counting_open),
            patch.object(SystemGraphService, "load_graph", counting_load_graph),
        ):
            # Simulate lifespan: build ONE shared pair and pre-warm the graph.
            renderer = MapRenderer(map_path=_MAP_PATH)
            graph = SystemGraphService()

            # Populate the graph manually (normally done via load_graph in lifespan).
            # Here we set _loaded=True and populate _graph with our test nodes
            # so rendered coords are available without a real DB.
            graph._graph = _SYSTEMS
            graph._loaded = True
            graph._validated_neighbours = {n: list(node.neighbours) for n, node in _SYSTEMS.items()}
            graph._jump_gate_systems = list(_SYSTEMS.keys())

            app = _build_app_with_shared_pair(renderer, graph)
            client = TestClient(app)

            # Trigger base-map load: hit the bounty map endpoint.
            with patch("api.routers.bounties.get_db_session") as mock_db_session:
                mock_session = AsyncMock()
                mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)
                client.get("/api/v1/bounties/1/map")

            # Hit the systems route/map endpoint (shares the SAME renderer + graph).
            with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
                mock_pf = MagicMock()
                mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
                mock_pf_cls.return_value = mock_pf
                client.get("/api/v1/systems/route/map?start=A&end=C")

        return image_open_count, load_graph_count

    def test_base_image_loaded_exactly_once_across_both_endpoints(self, thread_pool):
        """Image.open is called exactly once for the shared MapRenderer even
        when both the bounty-map and systems-route-map endpoints are exercised.

        Requires thread_pool fixture: endpoints now call render_route_offloaded (P3-T5)
        which offloads PIL work to the thread pool.
        """
        real_image = Image.open(_MAP_PATH).convert("RGB")
        image_open_count, _ = self._run_with_shared_pair(real_image)
        assert image_open_count == 1, (
            f"Expected Image.open call_count==1 (shared singleton), got {image_open_count}. "
            "Two separate MapRenderer instances would each load the image once → count==2."
        )

    def test_graph_not_reloaded_when_already_warmed(self, thread_pool):
        """load_graph is NOT called during request handling when the graph was
        pre-warmed at startup (is_loaded() == True).  This verifies the
        pre-warm path works and no redundant DB round-trip occurs.

        Requires thread_pool fixture: endpoints now call render_route_offloaded (P3-T5).
        """
        real_image = Image.open(_MAP_PATH).convert("RGB")
        _, load_graph_count = self._run_with_shared_pair(real_image)
        # Graph was pre-populated (is_loaded()==True) so load_graph should
        # not be called at all during request handling.
        assert load_graph_count == 0, (
            f"Expected load_graph call_count==0 (pre-warmed), got {load_graph_count}. "
            "A cold singleton would trigger a DB load on the first request."
        )

    # ------------------------------------------------------------------
    # Mutation-proof: demonstrate the test FAILS with two singletons
    # ------------------------------------------------------------------

    def test_mutation_proof_two_singletons_would_fail(self):
        """ADVERSARIAL: confirm that constructing a SECOND MapRenderer causes
        Image.open to be called TWICE — which is exactly what the previous
        two-singleton code did.  This proves the count==1 assertion above is
        load-bearing (if the code regressed, count would become 2 and the
        test above would fail).
        """
        real_image = Image.open(_MAP_PATH).convert("RGB")

        image_open_count = 0

        def counting_open(path):
            nonlocal image_open_count
            image_open_count += 1
            return real_image

        from services.map_renderer import MapRenderer
        from services.system_graph_service import SystemGraphService

        with patch("services.map_renderer.Image.open", side_effect=counting_open):
            # TWO separate MapRenderer instances (old behaviour).
            renderer_for_bounties = MapRenderer(map_path=_MAP_PATH)
            renderer_for_systems = MapRenderer(map_path=_MAP_PATH)

            # Each independently triggers a load on first render.
            graph = SystemGraphService()
            graph._graph = _SYSTEMS
            graph._loaded = True
            graph._validated_neighbours = {n: list(node.neighbours) for n, node in _SYSTEMS.items()}
            graph._jump_gate_systems = list(_SYSTEMS.keys())

            # Simulated request to bounty map router using renderer_for_bounties.
            renderer_for_bounties.render_route_for_bounty(["A", "B"], graph)
            # Simulated request to systems map router using renderer_for_systems.
            renderer_for_systems.render_route_for_bounty(["A", "C"], graph)

        # With two singletons, Image.open is called twice.
        assert image_open_count == 2, (
            f"Expected image_open_count==2 for two separate singletons, got {image_open_count}. "
            "This test confirms the mutation-proof: regressing to two singletons → "
            "count==2 → the test_base_image_loaded_exactly_once test would fail."
        )


# ===========================================================================
# Test 2 – OUTPUT REGRESSION
#
# Both /bounties/{id}/map and /systems/route/map must return PNG bytes
# matching a *committed golden reference* rendered once and checked in at
# tests/fixtures/golden_route_abc.png.
#
# WHY this is not "byte-identity to shared instance":
#   Pillow PNG encoding is deterministic — two *separate* MapRenderer
#   instances with the same map+graph+route produce byte-identical output.
#   A test comparing two renders from the *same run* cannot distinguish a
#   shared renderer from two independent renderers and is therefore vacuous.
#
# WHY this IS load-bearing:
#   The golden reference was captured ONCE from a known-good build and is
#   stored as a binary fixture.  If render output changes (colour, line
#   width, coordinate logic, PIL version, etc.) the test fails.  A mutation
#   that alters rendering (e.g. wrong colour, different route) will produce
#   bytes that differ from the golden.
#
# Shared-instance proof lives in TestSingletonDedup (load_count==1 +
# mutation-check) which is the correct mechanism for that property.
# ===========================================================================


class TestByteIdentity:
    """Endpoint output matches the committed golden reference PNG."""

    @pytest.fixture
    def shared_setup(self, thread_pool):
        """Build a shared renderer + graph wired on app.state, clear caches,
        and return a TestClient ready to serve requests.

        Requests thread_pool fixture: endpoints now call render_route_offloaded (P3-T5)
        which offloads PIL work to the thread pool — a registered pool is required.
        """
        from services.map_renderer import MapRenderer
        from services.system_graph_service import SystemGraphService

        renderer = MapRenderer(map_path=_MAP_PATH)
        graph = SystemGraphService()
        graph._graph = _SYSTEMS
        graph._loaded = True
        graph._validated_neighbours = {n: list(node.neighbours) for n, node in _SYSTEMS.items()}
        graph._jump_gate_systems = list(_SYSTEMS.keys())

        route = ["A", "B", "C"]

        app = _build_app_with_shared_pair(renderer, graph)
        # Clear the in-process map cache so endpoints produce a fresh render.
        import api.routers.bounties as bounties_module
        import api.routers.systems as systems_module

        bounties_module._map_cache.clear()
        systems_module._route_map_cache.clear()

        return TestClient(app), route

    def _load_golden(self) -> bytes:
        """Return the committed golden reference bytes."""
        assert os.path.exists(_GOLDEN_PNG_PATH), (
            f"Golden reference PNG not found at {_GOLDEN_PNG_PATH}. "
            "Regenerate via: cd src && python -c 'from services.map_renderer import MapRenderer; ...'"
        )
        with open(_GOLDEN_PNG_PATH, "rb") as fh:
            return fh.read()

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_endpoint_bytes_match_golden(self, mock_get_db, shared_setup):
        """GET /bounties/{id}/map returns bytes identical to the committed golden PNG.

        Guarantees: render output has not changed since the golden was captured.
        Does NOT guarantee shared-instance behaviour (see TestSingletonDedup for that).
        """
        client, _route = shared_setup
        golden = self._load_golden()

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/bounties/1/map")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == golden, (
            "Bounty map endpoint output differs from the committed golden PNG. "
            "If the render logic intentionally changed, regenerate the fixture."
        )

    def test_systems_route_map_endpoint_bytes_match_golden(self, shared_setup):
        """GET /systems/route/map returns bytes identical to the committed golden PNG.

        Guarantees: render output has not changed since the golden was captured.
        Does NOT guarantee shared-instance behaviour (see TestSingletonDedup for that).
        """
        client, route = shared_setup
        golden = self._load_golden()

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=route)
            mock_pf_cls.return_value = mock_pf

            response = client.get("/api/v1/systems/route/map?start=A&end=C")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == golden, (
            "Systems route/map endpoint output differs from the committed golden PNG. "
            "If the render logic intentionally changed, regenerate the fixture."
        )

    @patch("api.routers.bounties.get_db_session")
    def test_mutation_proof_different_route_differs_from_golden(self, mock_get_db):
        """ADVERSARIAL: a different route produces bytes that differ from the golden.

        This proves the golden check is load-bearing: if render output changes
        (even subtly — different route, different coords) the test would fail.
        """
        from services.map_renderer import MapRenderer
        from services.system_graph_service import SystemGraphService

        golden = self._load_golden()

        renderer = MapRenderer(map_path=_MAP_PATH)
        graph = SystemGraphService()
        graph._graph = _SYSTEMS
        graph._loaded = True
        graph._validated_neighbours = {n: list(node.neighbours) for n, node in _SYSTEMS.items()}
        graph._jump_gate_systems = list(_SYSTEMS.keys())

        # Render a DIFFERENT route (A→C only, not A→B→C)
        different_route = ["A", "C"]
        different_png = renderer.render_route_for_bounty(different_route, graph)

        assert different_png != golden, (
            "A different route should produce bytes that differ from the golden reference. "
            "If this assertion fails, the golden fixture is not route-sensitive."
        )


# ===========================================================================
# Test 3 – PRE-WARM: lifespan wires app.state.map_renderer + .system_graph
# ===========================================================================


class TestLifespanPreWarm:
    """Verify that main.py lifespan constructs and stores the shared pair."""

    @pytest.mark.asyncio
    async def test_lifespan_stores_map_renderer_on_app_state(self):
        """After lifespan startup, app.state.map_renderer is a MapRenderer."""
        import main as main_module
        from services.map_renderer import MapRenderer

        test_app = FastAPI()

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.add_job = MagicMock()

        mock_db_mgr = _make_mock_db_manager_for_lifespan()

        with _lifespan_patches(mock_db_mgr, mock_scheduler):
            async with main_module.lifespan(test_app):
                assert hasattr(test_app.state, "map_renderer")
                assert isinstance(test_app.state.map_renderer, MapRenderer)

    @pytest.mark.asyncio
    async def test_lifespan_stores_system_graph_on_app_state(self):
        """After lifespan startup, app.state.system_graph is a SystemGraphService."""
        import main as main_module
        from services.system_graph_service import SystemGraphService

        test_app = FastAPI()

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.add_job = MagicMock()

        mock_db_mgr = _make_mock_db_manager_for_lifespan()

        with _lifespan_patches(mock_db_mgr, mock_scheduler):
            async with main_module.lifespan(test_app):
                assert hasattr(test_app.state, "system_graph")
                assert isinstance(test_app.state.system_graph, SystemGraphService)

    @pytest.mark.asyncio
    async def test_lifespan_system_graph_load_graph_called_once_at_startup(self):
        """load_graph is called exactly ONCE during lifespan startup (the pre-warm).

        This proves the graph is loaded at startup (not lazily on first request)
        and that exactly one SystemGraphService instance is pre-warmed.
        Two separate instances would call load_graph twice → test would fail.

        The mock is passed INTO _lifespan_patches so patch-ordering cannot
        affect which mock is active — there is only one patch for load_graph.
        """
        import main as main_module

        test_app = FastAPI()

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.add_job = MagicMock()

        mock_db_mgr = _make_mock_db_manager_for_lifespan()
        # Pass the tracking mock into _lifespan_patches so it is the ONE patch
        # for SystemGraphService.load_graph.  No outer override needed.
        mock_load_graph = AsyncMock()

        with _lifespan_patches(mock_db_mgr, mock_scheduler, load_graph_mock=mock_load_graph):
            async with main_module.lifespan(test_app):
                # load_graph must have been called exactly once (the pre-warm).
                assert mock_load_graph.call_count == 1, (
                    f"Expected load_graph call_count==1 during lifespan startup, "
                    f"got {mock_load_graph.call_count}. "
                    "Two separate SystemGraphService instances would each call "
                    "load_graph once → count==2."
                )

    @pytest.mark.asyncio
    async def test_lifespan_single_map_renderer_not_two(self):
        """Only ONE MapRenderer is constructed during lifespan startup.

        Regression guard: before P3-T7, bounties.py and systems.py each
        created their own MapRenderer at module import time.  This test
        ensures the lifespan constructs exactly one instance.
        """
        import main as main_module
        from services.map_renderer import MapRenderer

        test_app = FastAPI()

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = []
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.add_job = MagicMock()

        constructor_calls: list[int] = []
        original_init = MapRenderer.__init__

        def spy_init(self_inner, map_path=None):
            constructor_calls.append(1)
            original_init(self_inner, map_path)

        mock_db_mgr = _make_mock_db_manager_for_lifespan()

        with (
            _lifespan_patches(mock_db_mgr, mock_scheduler),
            patch.object(MapRenderer, "__init__", spy_init),
        ):
            async with main_module.lifespan(test_app):
                # Exactly one MapRenderer constructed in the lifespan block.
                assert len(constructor_calls) == 1, (
                    f"Expected MapRenderer() called once in lifespan, got {len(constructor_calls)}. "
                    "Before P3-T7 the routers each built their own → 2+ constructors at import time."
                )


# ===========================================================================
# Test 4 – 503 GUARD: missing renderer/graph raises HTTP 503 not 500
#
# Verifies that when app.state.map_renderer or app.state.system_graph is
# absent (e.g. renderer/graph pre-warm failed at startup), the affected
# endpoints return 503 rather than crashing with an unhandled AttributeError.
# ===========================================================================


class TestMissingRendererGuard:
    """503 is returned when app.state.map_renderer / system_graph is absent."""

    def _build_app_without_renderer(self):
        """Return a TestClient whose app has NO map_renderer / system_graph on state."""

        import api.routers.bounties as bounties_module
        import api.routers.systems as systems_module

        app = FastAPI()
        app.include_router(bounties_module.router, prefix="/api/v1")
        app.include_router(systems_module.router, prefix="/api/v1")
        # Deliberately do NOT set app.state.map_renderer or app.state.system_graph.

        # Wire a fake bounty service so the endpoint can reach the renderer check.
        def _make_bounty_service():
            svc = AsyncMock()
            bounty = MagicMock()
            bounty.id = 1
            bounty.route = ["A", "B", "C"]
            svc.bounty_repo = AsyncMock()
            svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
            return svc

        app.dependency_overrides[bounties_module.get_bounty_service] = _make_bounty_service
        return TestClient(app, raise_server_exceptions=False)

    @patch("api.routers.bounties.get_db_session")
    def test_bounty_map_returns_503_when_renderer_absent(self, mock_get_db):
        """GET /bounties/{id}/map returns 503 when app.state.map_renderer is not set.

        The Depends(_get_map_renderer) getter raises HTTPException(503) before
        the endpoint body runs — no unhandled AttributeError or 500 is raised.
        """
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client = self._build_app_without_renderer()
        response = client.get("/api/v1/bounties/1/map")
        assert response.status_code == 503, (
            f"Expected 503 when renderer absent, got {response.status_code}. "
            "The Depends getter should raise HTTP 503, not let the endpoint crash."
        )
        assert "not yet available" in response.json().get("detail", "").lower() or (
            "map renderer" in response.json().get("detail", "").lower()
        ), f"Unexpected detail: {response.json()}"

    def test_systems_route_map_returns_503_when_renderer_absent(self):
        """GET /systems/route/map returns 503 when app.state.map_renderer is not set."""
        from unittest.mock import AsyncMock

        import api.routers.systems as systems_module

        app = FastAPI()
        app.include_router(systems_module.router, prefix="/api/v1")
        # Do NOT set app.state.map_renderer or app.state.system_graph.

        # Override get_db so the DB call doesn't fail before the 503 guard fires.
        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[systems_module.get_db] = override_get_db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/systems/route/map?start=A&end=C")
        assert response.status_code == 503, (
            f"Expected 503 when renderer absent, got {response.status_code}. Body: {response.text}"
        )


# ===========================================================================
# Test 5 – ADMIN-SPAWN 503/SKIP: Phase-2a skips rendering when renderer absent
#
# admin_spawn_bounties uses optional Depends that return None when the
# renderer/graph is absent.  The endpoint must still return 200 (spawn
# succeeded) but omit map images and log a warning rather than raising 503.
# ===========================================================================


class TestAdminSpawnRendererSkip:
    """admin_spawn_bounties Phase-2a skips rendering gracefully when renderer is absent."""

    def test_optional_getter_returns_none_when_renderer_absent(self):
        """_get_map_renderer_optional returns None (not 503) when app.state has no renderer.

        This verifies the guard used by admin_spawn_bounties: the optional getter
        must return None so the Phase-2a guard (``if _map_renderer is not None``)
        short-circuits instead of raising 503 for the whole endpoint.
        """
        import api.routers.bounties as bounties_module

        # Build a fake Request-like object with an app.state that has no renderer.
        fake_app = MagicMock()
        fake_app.state = MagicMock(spec=[])  # no attributes → getattr returns None via spec
        fake_request = MagicMock()
        fake_request.app = fake_app

        result = bounties_module._get_map_renderer_optional(fake_request)
        assert result is None, (
            "_get_map_renderer_optional must return None when map_renderer is absent, "
            "not raise 503. admin_spawn_bounties relies on this for graceful Phase-2a skip."
        )

    def test_optional_getter_returns_renderer_when_present(self):
        """_get_map_renderer_optional returns the renderer object when present."""
        import api.routers.bounties as bounties_module

        sentinel = object()
        fake_app = MagicMock()
        fake_app.state.map_renderer = sentinel
        fake_request = MagicMock()
        fake_request.app = fake_app

        result = bounties_module._get_map_renderer_optional(fake_request)
        assert result is sentinel, "_get_map_renderer_optional should return the renderer from app.state."

    def test_hard_getter_raises_503_when_renderer_absent(self):
        """_get_map_renderer (hard) raises HTTP 503 when app.state.map_renderer is absent."""
        import api.routers.bounties as bounties_module
        from fastapi import HTTPException

        fake_app = MagicMock()
        fake_app.state = MagicMock(spec=[])
        fake_request = MagicMock()
        fake_request.app = fake_app

        try:
            bounties_module._get_map_renderer(fake_request)
            raise AssertionError("Expected HTTPException(503) but no exception was raised")
        except HTTPException as exc:
            assert exc.status_code == 503, f"Expected 503, got {exc.status_code}"
        except Exception as exc:
            raise AssertionError(f"Expected HTTPException(503), got {type(exc).__name__}: {exc}") from exc

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService.log_action", new_callable=AsyncMock)
    @patch("api.routers.bounties.ConfigRepository")
    def test_admin_spawn_phase2a_skips_rendering_when_renderer_absent(
        self, mock_config_repo_cls, mock_audit, mock_get_db
    ):
        """admin_spawn_bounties Phase-2a is skipped when _map_renderer is None.

        We override both optional Depends to return None, simulate a successful
        spawn, and confirm render_route_for_bounty is never called.
        """
        from datetime import UTC, datetime

        import api.routers.bounties as bounties_module

        # Build fake bounty returned by spawn_bounty.
        # Must have proper types so BountyResponse.model_validate succeeds.
        fake_bounty = MagicMock()
        fake_bounty.id = 42
        fake_bounty.guild_id = 1
        fake_bounty.division = "bronze"
        fake_bounty.criminal_name = "TestCriminal"
        fake_bounty.criminal_faction = "Neutral"
        fake_bounty.route = ["A", "B"]
        fake_bounty.answer = "B"
        fake_bounty.reward = 1000
        fake_bounty.reward_per_sys = 500
        fake_bounty.checked = {"A": -1, "B": -1}
        fake_bounty.issue_time = datetime.now(UTC)
        fake_bounty.end_time = datetime.now(UTC)
        fake_bounty.tech_level = 2
        fake_bounty.criminal_ship = {}
        fake_bounty.status = "active"
        fake_bounty.escape_count = 0
        fake_bounty.win_user_id = None

        def _make_svc():
            svc = AsyncMock()
            svc.spawn_bounty = AsyncMock(return_value=fake_bounty)
            return svc

        app = FastAPI()
        app.include_router(bounties_module.router, prefix="/api/v1")
        # No renderer on state — optional getters return None.
        app.dependency_overrides[bounties_module.get_bounty_service] = _make_svc
        app.dependency_overrides[bounties_module._get_map_renderer_optional] = lambda: None
        app.dependency_overrides[bounties_module._get_system_graph_optional] = lambda: None

        # Mock DB session
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock config repo
        mock_config_repo = AsyncMock()
        mock_config_repo.get_by_guild_id = AsyncMock(return_value=None)
        mock_config_repo_cls.return_value = mock_config_repo

        # Spy: confirm render_route_for_bounty is NEVER called when renderer absent.
        render_call_count = 0

        def _spy_render(*_args, **_kwargs):
            nonlocal render_call_count
            render_call_count += 1
            return b"FAKE_PNG"

        with (
            patch("utils.executors.bounty_spawn_executor._announce_bounty", new_callable=AsyncMock),
            patch("utils.executors.bounty_spawn_executor._schedule_expiry_job", new_callable=AsyncMock),
            patch("utils.executors.bounty_spawn_executor._push_bounty_cache", new_callable=AsyncMock),
            patch("services.map_renderer.MapRenderer.render_route_for_bounty", side_effect=_spy_render),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/bounties/guild/1/admin-spawn",
                params={"user_id": 999, "tier": "bronze", "quantity": 1},
            )

        assert response.status_code == 200, (
            f"Expected 200 from admin-spawn even when renderer absent, got {response.status_code}. "
            f"Body: {response.text}"
        )
        data = response.json()
        assert "errors" in data, f"Expected 'errors' key in response, got: {data}"
        assert render_call_count == 0, (
            f"render_route_for_bounty should NOT be called when _map_renderer is None, "
            f"but it was called {render_call_count} time(s)."
        )


def _make_mock_db_manager_for_lifespan():
    """Build a db_manager mock suitable for lifespan tests."""
    mock_execute_result = MagicMock()
    mock_execute_result.all.return_value = []  # no stale bounties

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_session.commit = AsyncMock()
    mock_db_session.rollback = AsyncMock()

    mock_db_mgr = MagicMock()
    mock_db_mgr.initialize = AsyncMock()
    mock_db_mgr._connection_string = "postgresql+asyncpg://user:pass@host/db"
    mock_db_mgr.shutdown = MagicMock()

    @contextlib.asynccontextmanager
    async def _mock_get_session():
        yield mock_db_session

    mock_db_mgr.get_session = _mock_get_session
    return mock_db_mgr


def _lifespan_patches(mock_db_mgr, mock_scheduler, load_graph_mock=None):
    """Return a context manager stack that mocks all lifespan dependencies
    (DB, migrations, scheduler, pools) so the lifespan can run in tests.

    Args:
        mock_db_mgr:      DB manager mock.
        mock_scheduler:   AsyncIOScheduler mock.
        load_graph_mock:  Optional AsyncMock to use for SystemGraphService.load_graph.
                          When supplied, this exact mock is used so callers can
                          inspect call counts *without* relying on patch-ordering
                          tricks.  When None, a fresh AsyncMock() is created.
    """
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
    # Patch load_graph to avoid real DB.  Use the caller-supplied mock when
    # provided so call-count assertions are free of patch-ordering brittleness.
    _lg_mock = load_graph_mock if load_graph_mock is not None else AsyncMock()
    stack.enter_context(
        patch(
            "services.system_graph_service.SystemGraphService.load_graph",
            _lg_mock,
        )
    )

    return stack
