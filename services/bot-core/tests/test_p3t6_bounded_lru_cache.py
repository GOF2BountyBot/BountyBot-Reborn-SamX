"""P3-T6: Tests for bounded LRU cache on _map_cache and _route_map_cache.

Adversarial-grade test suite covering four guarantees:

1. BOUNDED SIZE: driving more than _MAP_CACHE_MAX / _ROUTE_MAP_CACHE_MAX distinct
   renders through the endpoints never causes the cache to exceed the cap.

2. LRU EVICTION CORRECTNESS: the entry evicted on overflow is the LEAST-recently-
   used one — not the most-recently-used or the most-recently-written.  Confirmed
   by accessing an old entry (bumping it to MRU) and then overflowing; the bumped
   entry must survive while the true LRU entry is evicted.

3. RE-RENDER AFTER EVICTION: requesting a previously-evicted key re-renders and
   returns BYTE-IDENTICAL bytes (correctness preserved across eviction cycles).

4. CACHE HIT STILL WORKS + WRITES LOOP-ONLY:
   - A hit on a fresh entry returns the cached bytes with no re-render.
   - The bounded cache's internal mutations (write, move-to-end, evict) happen
     exclusively on the event-loop thread — reuses the P3-T5 _TrackedDict pattern
     with OrderedDict as the base so move_to_end is available.

Applied to BOTH _map_cache (bounties router) and _route_map_cache (systems router).
"""

from __future__ import annotations

import os
import sys
import threading
from collections import OrderedDict
from types import ModuleType
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
# Helpers
# ---------------------------------------------------------------------------


def _make_unique_png(tag: int) -> bytes:
    """Produce a minimal distinct PNG-like byte sequence for each tag.

    These are not real PNGs but are byte-unique per tag, which is sufficient
    for cache-correctness tests that don't need to decode the image.
    """
    return b"\x89PNG\r\n\x1a\n" + tag.to_bytes(4, "big") + b"\x00" * 8


# ===========================================================================
# Bounded-cache unit tests: _map_cache_get / _map_cache_set helpers
# ===========================================================================


class TestBountiesLruHelpers:
    """Unit tests for the _map_cache_get / _map_cache_set helper functions.

    These tests exercise the helpers in isolation, bypassing the HTTP layer.
    """

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Clear and reset _map_cache to a fresh OrderedDict before each test."""
        import api.routers.bounties as m

        original = m._map_cache
        m._map_cache = OrderedDict()
        yield
        m._map_cache = original

    def test_get_returns_none_on_miss(self):
        from api.routers.bounties import _map_cache_get

        assert _map_cache_get((1, ("A", "B"))) is None

    def test_get_returns_default_on_miss(self):
        from api.routers.bounties import _map_cache_get

        sentinel = b"default"
        assert _map_cache_get((1, ("A", "B")), default=sentinel) is sentinel

    def test_set_then_get_returns_value(self):
        from api.routers.bounties import _map_cache_get, _map_cache_set

        key = (42, ("X", "Y", "Z"))
        value = _make_unique_png(42)
        _map_cache_set(key, value)
        assert _map_cache_get(key) == value

    def test_set_overflow_evicts_lru(self):
        """Filling to cap+1 evicts the oldest (LRU) entry."""
        import api.routers.bounties as m
        from api.routers.bounties import _map_cache_set

        cap = m._MAP_CACHE_MAX
        # Fill cache to capacity.
        keys = [(i, (f"Sys{i}",)) for i in range(cap)]
        for i, key in enumerate(keys):
            _map_cache_set(key, _make_unique_png(i))

        assert len(m._map_cache) == cap

        # Insert one more — the oldest (keys[0]) must be evicted.
        new_key = (cap, (f"Sys{cap}",))
        _map_cache_set(new_key, _make_unique_png(cap))

        assert len(m._map_cache) == cap, f"Cache grew beyond cap ({cap}); got {len(m._map_cache)}"
        assert keys[0] not in m._map_cache, "LRU (first inserted) entry was not evicted on overflow"
        assert new_key in m._map_cache, "Newly inserted entry missing after overflow"

    def test_get_promotes_to_mru(self):
        """Accessing an entry via _map_cache_get bumps it to MRU position."""
        import api.routers.bounties as m
        from api.routers.bounties import _map_cache_get, _map_cache_set

        cap = m._MAP_CACHE_MAX
        # Fill cache to capacity.
        keys = [(i, (f"S{i}",)) for i in range(cap)]
        for i, key in enumerate(keys):
            _map_cache_set(key, _make_unique_png(i))

        # Access keys[0] — moves it from LRU to MRU.
        _map_cache_get(keys[0])

        # Now overflow: the new true-LRU is keys[1] (keys[0] was promoted).
        new_key = (cap, (f"S{cap}",))
        _map_cache_set(new_key, _make_unique_png(cap))

        assert keys[0] in m._map_cache, "Promoted (accessed) entry was incorrectly evicted"
        assert keys[1] not in m._map_cache, "True-LRU entry (keys[1]) was not evicted"
        assert len(m._map_cache) == cap

    def test_set_existing_key_promotes_to_mru(self):
        """Re-setting an existing key updates the value and promotes it to MRU."""
        import api.routers.bounties as m
        from api.routers.bounties import _map_cache_get, _map_cache_set

        cap = m._MAP_CACHE_MAX
        keys = [(i, (f"T{i}",)) for i in range(cap)]
        for i, key in enumerate(keys):
            _map_cache_set(key, _make_unique_png(i))

        # Re-set keys[0] with new bytes — promotes it to MRU.
        new_bytes = b"updated_value"
        _map_cache_set(keys[0], new_bytes)

        # Overflow: keys[1] (now LRU) should be evicted.
        overflow_key = (cap, (f"T{cap}",))
        _map_cache_set(overflow_key, _make_unique_png(cap))

        assert keys[0] in m._map_cache, "Re-set key was incorrectly evicted"
        assert _map_cache_get(keys[0]) == new_bytes, "Re-set value not stored correctly"
        assert keys[1] not in m._map_cache, "True-LRU (keys[1]) was not evicted after re-set"

    def test_cache_never_exceeds_cap(self):
        """Inserting 3× cap entries never causes cache to exceed cap."""
        import api.routers.bounties as m
        from api.routers.bounties import _map_cache_set

        cap = m._MAP_CACHE_MAX
        total = cap * 3
        for i in range(total):
            key = (i, (f"U{i}",))
            _map_cache_set(key, _make_unique_png(i))
            assert len(m._map_cache) <= cap, (
                f"Cache size {len(m._map_cache)} exceeded cap {cap} after {i + 1} insertions"
            )

    def test_rerender_after_eviction_byte_identical(self):
        """After a key is evicted and re-inserted, the returned bytes are correct."""
        import api.routers.bounties as m
        from api.routers.bounties import _map_cache_get, _map_cache_set

        cap = m._MAP_CACHE_MAX
        victim_key = (0, ("V0",))
        original_bytes = _make_unique_png(999)

        # Insert victim at LRU position.
        _map_cache_set(victim_key, original_bytes)
        # Fill rest of cache — victim stays LRU.
        for i in range(1, cap):
            _map_cache_set((i, (f"V{i}",)), _make_unique_png(i))

        # Overflow: victim should be evicted.
        _map_cache_set((cap, (f"V{cap}",)), _make_unique_png(cap))
        assert victim_key not in m._map_cache, "Victim key was not evicted as expected"

        # Re-insert with identical bytes (simulating re-render).
        _map_cache_set(victim_key, original_bytes)
        result = _map_cache_get(victim_key)
        assert result == original_bytes, (
            f"Re-inserted value differs from original: got {result!r}, expected {original_bytes!r}"
        )


class TestSystemsLruHelpers:
    """Unit tests for the _route_map_cache_get / _route_map_cache_set helpers."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        import api.routers.systems as m

        original = m._route_map_cache
        m._route_map_cache = OrderedDict()
        yield
        m._route_map_cache = original

    def test_get_returns_none_on_miss(self):
        from api.routers.systems import _route_map_cache_get

        assert _route_map_cache_get(("A", "B")) is None

    def test_set_then_get_returns_value(self):
        from api.routers.systems import _route_map_cache_get, _route_map_cache_set

        key = ("Sol", "Proxima")
        value = _make_unique_png(7)
        _route_map_cache_set(key, value)
        assert _route_map_cache_get(key) == value

    def test_set_overflow_evicts_lru(self):
        import api.routers.systems as m
        from api.routers.systems import _route_map_cache_set

        cap = m._ROUTE_MAP_CACHE_MAX
        keys = [(f"A{i}", f"B{i}") for i in range(cap)]
        for i, key in enumerate(keys):
            _route_map_cache_set(key, _make_unique_png(i))

        assert len(m._route_map_cache) == cap

        new_key = (f"A{cap}", f"B{cap}")
        _route_map_cache_set(new_key, _make_unique_png(cap))

        assert len(m._route_map_cache) == cap, f"Cache grew beyond cap ({cap})"
        assert keys[0] not in m._route_map_cache, "LRU entry was not evicted on overflow"
        assert new_key in m._route_map_cache, "Newly inserted entry missing after overflow"

    def test_get_promotes_to_mru(self):
        import api.routers.systems as m
        from api.routers.systems import _route_map_cache_get, _route_map_cache_set

        cap = m._ROUTE_MAP_CACHE_MAX
        keys = [(f"C{i}", f"D{i}") for i in range(cap)]
        for i, key in enumerate(keys):
            _route_map_cache_set(key, _make_unique_png(i))

        # Access keys[0] — promotes it from LRU to MRU.
        _route_map_cache_get(keys[0])

        # Overflow: keys[1] (now LRU) must be evicted.
        new_key = (f"C{cap}", f"D{cap}")
        _route_map_cache_set(new_key, _make_unique_png(cap))

        assert keys[0] in m._route_map_cache, "Promoted entry was incorrectly evicted"
        assert keys[1] not in m._route_map_cache, "True-LRU entry (keys[1]) was not evicted"
        assert len(m._route_map_cache) == cap

    def test_cache_never_exceeds_cap(self):
        import api.routers.systems as m
        from api.routers.systems import _route_map_cache_set

        cap = m._ROUTE_MAP_CACHE_MAX
        total = cap * 3
        for i in range(total):
            key = (f"E{i}", f"F{i}")
            _route_map_cache_set(key, _make_unique_png(i))
            assert len(m._route_map_cache) <= cap, (
                f"Cache size {len(m._route_map_cache)} exceeded cap {cap} after {i + 1} insertions"
            )

    def test_rerender_after_eviction_byte_identical(self):
        import api.routers.systems as m
        from api.routers.systems import _route_map_cache_get, _route_map_cache_set

        cap = m._ROUTE_MAP_CACHE_MAX
        victim_key = ("W0", "X0")
        original_bytes = _make_unique_png(888)

        _route_map_cache_set(victim_key, original_bytes)
        for i in range(1, cap):
            _route_map_cache_set((f"W{i}", f"X{i}"), _make_unique_png(i))

        _route_map_cache_set((f"W{cap}", f"X{cap}"), _make_unique_png(cap))
        assert victim_key not in m._route_map_cache, "Victim key was not evicted as expected"

        _route_map_cache_set(victim_key, original_bytes)
        result = _route_map_cache_get(victim_key)
        assert result == original_bytes, (
            f"Re-inserted value differs from original: got {result!r}, expected {original_bytes!r}"
        )


# ===========================================================================
# Loop-only-write tests: bounded-cache mutations stay on the event-loop thread
#
# Reuses the P3-T5 _TrackedDict(OrderedDict) pattern.  Drives a real HTTP
# request through TestClient and asserts the cache-write thread ident ≠
# the render-worker thread ident.  The bounded-cache _map_cache_set / helper
# calls move_to_end + popitem, so _TrackedDict must extend OrderedDict.
# ===========================================================================

_MAP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "import_data",
        "system-map.png",
    )
)

_GOLDEN_PNG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures", "golden_route_abc.png"))


def _make_node(name: str, x: int, y: int, neighbours: list[str]):
    from services.system_graph_service import SystemNode

    return SystemNode(name=name, coordinates=(x, y), neighbours=neighbours, faction="Neutral", security=1)


_SYSTEMS = {
    "A": _make_node("A", 100, 100, ["B"]),
    "B": _make_node("B", 200, 100, ["A", "C"]),
    "C": _make_node("C", 300, 100, ["B"]),
}


class _FakeGraph:
    def get_system(self, name: str):
        return _SYSTEMS.get(name)

    def is_loaded(self) -> bool:
        return True


_HOLDER_MODULE = "utils.executor_holder"
_OFFLOAD_MODULE = "utils.offload"

import concurrent.futures


@pytest.fixture
def thread_pool():
    """Create and register a ThreadPoolExecutor in a fresh executor_holder module."""
    import sys

    _saved_holder = sys.modules.get(_HOLDER_MODULE)
    _saved_offload = sys.modules.get(_OFFLOAD_MODULE)

    if _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]
    import utils.executor_holder as holder

    if _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]
    import utils.offload  # noqa: F401

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-p3t6")
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


def _build_app(renderer, graph, bounty_route=None):
    import api.routers.bounties as bounties_module
    import api.routers.systems as systems_module
    from api.routers.bounties import get_bounty_service
    from api.routers.bounties import router as bounties_router
    from api.routers.systems import get_db
    from api.routers.systems import router as systems_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bounties_router, prefix="/api/v1")
    app.include_router(systems_router, prefix="/api/v1")

    app.state.map_renderer = renderer
    app.state.system_graph = graph

    app.dependency_overrides[systems_module._get_system_graph] = lambda: graph
    app.dependency_overrides[systems_module._get_map_renderer] = lambda: renderer
    app.dependency_overrides[bounties_module._get_map_renderer] = lambda: renderer
    app.dependency_overrides[bounties_module._get_system_graph] = lambda: graph

    async def override_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = override_get_db

    route = bounty_route if bounty_route is not None else ["A", "B", "C"]

    def _make_bounty_service():
        svc = AsyncMock()
        bounty = MagicMock()
        bounty.id = 1
        bounty.route = route
        svc.bounty_repo = AsyncMock()
        svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
        return svc

    app.dependency_overrides[get_bounty_service] = _make_bounty_service
    return app


class TestBoundedCacheLoopOnlyWrites:
    """Bounded-cache mutations (write + LRU bookkeeping) happen on the loop thread.

    Uses the same _TrackedDict(OrderedDict) pattern as P3-T5 to capture idents.
    The tracked dict must extend OrderedDict so move_to_end is available for the
    bounded-cache helpers.
    """

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
    def test_bounty_map_bounded_cache_write_on_loop_thread(self, mock_get_db, thread_pool):
        """Bounded _map_cache write (+ LRU mutation) happens on the loop thread."""
        import api.routers.bounties as bounties_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        write_idents: list[int] = []
        render_idents: list[int] = []

        class _TrackedDict(OrderedDict):
            def __setitem__(self, key, value):
                write_idents.append(threading.get_ident())
                super().__setitem__(key, value)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

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

        original_cache = bounties_module._map_cache
        tracked_cache = _TrackedDict()
        bounties_module._map_cache = tracked_cache
        try:
            response = client.get("/api/v1/bounties/1/map")
        finally:
            bounties_module._map_cache = original_cache

        assert response.status_code == 200
        assert len(render_idents) == 1
        assert len(write_idents) == 1
        assert write_idents[0] != render_idents[0], (
            f"Bounded _map_cache write ident ({write_idents[0]}) == render worker ident "
            f"({render_idents[0]}). Write must happen on the loop thread, not inside the worker."
        )

    def test_systems_route_map_bounded_cache_write_on_loop_thread(self, thread_pool):
        """Bounded _route_map_cache write (+ LRU mutation) happens on the loop thread."""
        import api.routers.systems as systems_module
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        write_idents: list[int] = []
        render_idents: list[int] = []

        class _TrackedDict(OrderedDict):
            def __setitem__(self, key, value):
                write_idents.append(threading.get_ident())
                super().__setitem__(key, value)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        app = _build_app(renderer, graph)
        client = TestClient(app)

        original_cache = systems_module._route_map_cache
        tracked_cache = _TrackedDict()
        systems_module._route_map_cache = tracked_cache
        try:
            with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
                mock_pf = MagicMock()
                mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
                mock_pf_cls.return_value = mock_pf
                response = client.get("/api/v1/systems/route/map?start=A&end=C")
        finally:
            systems_module._route_map_cache = original_cache

        assert response.status_code == 200
        assert len(render_idents) == 1
        assert len(write_idents) == 1
        assert write_idents[0] != render_idents[0], (
            f"Bounded _route_map_cache write ident ({write_idents[0]}) == render worker ident "
            f"({render_idents[0]}). Write must happen on the loop thread, not inside the worker."
        )


# ===========================================================================
# Endpoint-level bounded-size test
#
# Drive more than _MAP_CACHE_MAX / _ROUTE_MAP_CACHE_MAX distinct bounty IDs
# through the get_bounty_map endpoint.  Assert the cache size never exceeds cap.
# ===========================================================================


class TestEndpointBoundedSize:
    """Endpoint-level proof: cache never exceeds the cap regardless of traffic."""

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
    def test_bounty_map_cache_bounded_across_many_distinct_keys(self, mock_get_db, thread_pool):
        """Requesting cap+10 distinct bounty IDs never grows _map_cache beyond the cap."""
        import api.routers.bounties as bounties_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        cap = bounties_module._MAP_CACHE_MAX
        num_requests = cap + 10

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        from api.routers.bounties import get_bounty_service
        from api.routers.bounties import router as bounties_router

        app = FastAPI()
        app.include_router(bounties_router, prefix="/api/v1")
        app.state.map_renderer = renderer
        app.state.system_graph = graph
        app.dependency_overrides[bounties_module._get_map_renderer] = lambda: renderer
        app.dependency_overrides[bounties_module._get_system_graph] = lambda: graph

        def _make_service(bounty_id):
            def factory():
                svc = AsyncMock()
                bounty = MagicMock()
                bounty.id = bounty_id
                bounty.route = ["A", "B", "C"]
                svc.bounty_repo = AsyncMock()
                svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
                return svc

            return factory

        client = TestClient(app)

        for i in range(num_requests):
            # Swap the bounty service to return a different bounty_id each time.
            app.dependency_overrides[get_bounty_service] = _make_service(i)
            response = client.get(f"/api/v1/bounties/{i}/map")
            assert response.status_code == 200, f"Request {i} failed with {response.status_code}"
            assert len(bounties_module._map_cache) <= cap, (
                f"Cache size {len(bounties_module._map_cache)} exceeded cap {cap} after {i + 1} requests"
            )

    def test_systems_route_map_cache_bounded_across_many_distinct_keys(self, thread_pool):
        """Requesting cap+10 distinct (start, end) pairs never grows _route_map_cache beyond the cap."""
        import api.routers.systems as systems_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        cap = systems_module._ROUTE_MAP_CACHE_MAX
        num_requests = cap + 10

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        from api.routers.systems import _get_map_renderer, _get_system_graph, get_db
        from api.routers.systems import router as systems_router

        app = FastAPI()
        app.include_router(systems_router, prefix="/api/v1")
        app.state.map_renderer = renderer
        app.state.system_graph = graph
        app.dependency_overrides[_get_system_graph] = lambda: graph
        app.dependency_overrides[_get_map_renderer] = lambda: renderer

        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)

        for i in range(num_requests):
            # Use distinct start/end labels — pathfinding resolves to same A→B→C route.
            start_label = f"Start{i}"
            end_label = f"End{i}"
            with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
                mock_pf = MagicMock()
                mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
                mock_pf_cls.return_value = mock_pf
                response = client.get(f"/api/v1/systems/route/map?start={start_label}&end={end_label}")

            assert response.status_code == 200, f"Request {i} failed with {response.status_code}"
            assert len(systems_module._route_map_cache) <= cap, (
                f"Cache size {len(systems_module._route_map_cache)} exceeded cap {cap} after {i + 1} requests"
            )


# ===========================================================================
# Endpoint-level cache hit test
#
# First request renders; second request for same key is a cache hit (no re-render).
# ===========================================================================


class TestEndpointCacheHit:
    """Cache hit returns identical bytes without re-rendering."""

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
    def test_bounty_map_second_request_is_cache_hit(self, mock_get_db, thread_pool):
        """Second GET /bounties/{id}/map returns cached bytes; no re-render."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        render_count = 0
        orig = renderer.render_route

        def spy(route, system_coords):
            nonlocal render_count
            render_count += 1
            return orig(route, system_coords)

        renderer.render_route = spy  # type: ignore[method-assign]

        app = _build_app(renderer, graph)
        client = TestClient(app)

        resp1 = client.get("/api/v1/bounties/1/map")
        assert resp1.status_code == 200
        assert render_count == 1

        resp2 = client.get("/api/v1/bounties/1/map")
        assert resp2.status_code == 200
        assert render_count == 1, "Second request must not trigger a re-render (cache hit expected)"
        assert resp1.content == resp2.content, "Cache hit must return byte-identical content"

    def test_systems_route_map_second_request_is_cache_hit(self, thread_pool):
        """Second GET /systems/route/map returns cached bytes; no re-render."""
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        render_count = 0
        orig = renderer.render_route

        def spy(route, system_coords):
            nonlocal render_count
            render_count += 1
            return orig(route, system_coords)

        renderer.render_route = spy  # type: ignore[method-assign]

        app = _build_app(renderer, graph)
        client = TestClient(app)

        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock()
            mock_pf.make_route = MagicMock(return_value=["A", "B", "C"])
            mock_pf_cls.return_value = mock_pf

            resp1 = client.get("/api/v1/systems/route/map?start=A&end=C")
            assert resp1.status_code == 200
            assert render_count == 1

            resp2 = client.get("/api/v1/systems/route/map?start=A&end=C")
            assert resp2.status_code == 200
            assert render_count == 1, "Second request must not trigger a re-render (cache hit expected)"
            assert resp1.content == resp2.content, "Cache hit must return byte-identical content"


# ===========================================================================
# Re-render after eviction test (endpoint-level)
#
# After evicting a key by overflowing the cache, re-requesting the evicted key
# triggers a fresh render and returns byte-identical bytes.
# ===========================================================================


class TestReRenderAfterEviction:
    """Re-requesting an evicted key produces correct bytes identical to the original."""

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
    def test_bounty_map_rerender_after_eviction_byte_identical(self, mock_get_db, thread_pool):
        """After _map_cache evicts bounty_id=0, re-requesting it returns identical bytes."""
        import api.routers.bounties as bounties_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.map_renderer import MapRenderer

        cap = bounties_module._MAP_CACHE_MAX

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        from api.routers.bounties import get_bounty_service
        from api.routers.bounties import router as bounties_router

        app = FastAPI()
        app.include_router(bounties_router, prefix="/api/v1")
        app.state.map_renderer = renderer
        app.state.system_graph = graph
        app.dependency_overrides[bounties_module._get_map_renderer] = lambda: renderer
        app.dependency_overrides[bounties_module._get_system_graph] = lambda: graph

        def _make_service(bid):
            def factory():
                svc = AsyncMock()
                bounty = MagicMock()
                bounty.id = bid
                bounty.route = ["A", "B", "C"]
                svc.bounty_repo = AsyncMock()
                svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
                return svc

            return factory

        client = TestClient(app)

        # First request for bounty_id=0 — caches it at LRU position.
        app.dependency_overrides[get_bounty_service] = _make_service(0)
        resp_first = client.get("/api/v1/bounties/0/map")
        assert resp_first.status_code == 200
        first_bytes = resp_first.content

        # Fill cache to cap+1 with new bounty IDs — bounty_id=0 must be evicted.
        for i in range(1, cap + 1):
            app.dependency_overrides[get_bounty_service] = _make_service(i)
            r = client.get(f"/api/v1/bounties/{i}/map")
            assert r.status_code == 200

        # Confirm bounty_id=0 was evicted.
        victim_key = (0, tuple(["A", "B", "C"]))
        assert victim_key not in bounties_module._map_cache, "Victim key was not evicted"

        # Re-request bounty_id=0 — must re-render and return identical bytes.
        app.dependency_overrides[get_bounty_service] = _make_service(0)
        resp_rerender = client.get("/api/v1/bounties/0/map")
        assert resp_rerender.status_code == 200
        assert resp_rerender.content == first_bytes, (
            f"Re-rendered bytes ({len(resp_rerender.content)} B) differ from original "
            f"({len(first_bytes)} B). Re-render after eviction must be byte-identical."
        )
