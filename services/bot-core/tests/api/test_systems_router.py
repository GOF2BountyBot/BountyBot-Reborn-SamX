"""Tests for the systems API router — GET /api/v1/systems/route.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.pathfinding_service import PathfindingError, PathfindingService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph_service(systems: dict):
    """Return a mock SystemGraphService pre-populated with the given nodes."""

    svc = MagicMock()
    svc.is_loaded = MagicMock(return_value=True)
    svc.load_graph = AsyncMock()

    def _get_system(name):
        return systems.get(name)

    def _get_neighbours(name):
        node = systems.get(name)
        return node.neighbours if node else []

    svc.get_system = MagicMock(side_effect=_get_system)
    svc.get_neighbours = MagicMock(side_effect=_get_neighbours)
    return svc


def _make_node(name: str, x: int, y: int, neighbours: list[str]):
    from services.system_graph_service import SystemNode

    return SystemNode(
        name=name,
        coordinates=(x, y),
        neighbours=neighbours,
        faction="Neutral",
        security=1,
    )


# Shared graph: a linear chain A-B-C-D-E plus an unreachable "Isolated" node.
# The chain gives deterministic multi-hop routes; "Isolated" has no neighbours
# and nothing points to it, so a real search from A can never reach it (exercises
# the genuine NO_ROUTE_FOUND branch without patching the algorithm).
_SYSTEMS = {
    "A": _make_node("A", 0, 0, ["B"]),
    "B": _make_node("B", 10, 0, ["A", "C"]),
    "C": _make_node("C", 20, 0, ["B", "D"]),
    "D": _make_node("D", 30, 0, ["C", "E"]),
    "E": _make_node("E", 40, 0, ["D"]),
    "Isolated": _make_node("Isolated", 500, 500, []),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    return AsyncMock()


@pytest.fixture
def test_app(mock_db_session):
    """Build a minimal FastAPI app with the systems router and db override.

    P3-T7: graph_service and map_renderer are now served via app.state
    (wired by lifespan at startup).  Tests set them on app.state directly
    and override the _get_system_graph / _get_map_renderer dependencies.
    """
    from api.routers.systems import _get_map_renderer, _get_system_graph, get_db
    from api.routers.systems import router as systems_router

    mock_graph = _make_graph_service(_SYSTEMS)
    mock_renderer = MagicMock()

    app = FastAPI()
    app.include_router(systems_router, prefix="/api/v1")

    # Set shared singletons on app.state (mirrors lifespan behaviour).
    app.state.system_graph = mock_graph
    app.state.map_renderer = mock_renderer

    async def override_get_db():
        yield mock_db_session

    # Override the dependency getters so they return the app.state values.
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_system_graph] = lambda: mock_graph
    app.dependency_overrides[_get_map_renderer] = lambda: mock_renderer

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFindRoute:
    """Tests for GET /api/v1/systems/route."""

    def test_route_found_between_two_systems(self, client, test_app):
        """Returns 200 with route list and hop count when a path exists.

        Runs the REAL PathfindingService over the real SystemNode graph — the A*
        search actually computes A→B→C.
        """
        response = client.get("/api/v1/systems/route?start=A&end=C")

        assert response.status_code == 200
        data = response.json()
        assert data["route"] == ["A", "B", "C"]
        assert data["hops"] == 2

    def test_same_start_and_end_returns_single_system(self, client, test_app):
        """Returns 200 with single-element route when start == end (real service)."""
        response = client.get("/api/v1/systems/route?start=A&end=A")

        assert response.status_code == 200
        data = response.json()
        assert data["route"] == ["A"]
        assert data["hops"] == 0

    def test_no_route_found_returns_404(self, client, test_app):
        """Returns 404 when the real search cannot reach an unreachable system.

        "Isolated" exists in the graph but has no neighbours and nothing points to
        it, so the real A* search genuinely exhausts and returns NO_ROUTE_FOUND.
        """
        response = client.get("/api/v1/systems/route?start=A&end=Isolated")

        assert response.status_code == 404
        assert "no route found" in response.json()["detail"].lower()

    def test_invalid_system_name_returns_404(self, client, test_app):
        """Returns 404 when a system name does not exist in the graph.

        The real PathfindingService returns NO_ROUTE_FOUND when an endpoint is not
        present (end_node is None) — no patching needed.
        """
        response = client.get("/api/v1/systems/route?start=DoesNotExist&end=C")

        assert response.status_code == 404

    def test_max_length_reached_returns_400(self, client, test_app):
        """Returns 400 when pathfinding returns MAX_LENGTH_REACHED.

        A >50-hop graph is impractical to construct as a fixture, so this error
        branch alone forces the enum via a spec'd PathfindingService whose
        make_route returns MAX_LENGTH_REACHED (the only patched case; the found /
        same / no-route / hop-count cases all run the real algorithm).
        """
        with patch("api.routers.systems.PathfindingService") as mock_pf_cls:
            mock_pf = MagicMock(spec=PathfindingService)
            mock_pf.make_route = MagicMock(return_value=PathfindingError.MAX_LENGTH_REACHED)
            mock_pf_cls.return_value = mock_pf

            response = client.get("/api/v1/systems/route?start=A&end=E")

        assert response.status_code == 400
        assert "maximum length" in response.json()["detail"].lower()

    def test_missing_start_parameter_returns_422(self, client):
        """Returns 422 when required 'start' query parameter is missing."""
        response = client.get("/api/v1/systems/route?end=B")
        assert response.status_code == 422

    def test_missing_end_parameter_returns_422(self, client):
        """Returns 422 when required 'end' query parameter is missing."""
        response = client.get("/api/v1/systems/route?start=A")
        assert response.status_code == 422

    def test_route_response_has_correct_hop_count(self, client, test_app):
        """Hop count equals len(route) - 1, computed by the real service.

        A→E traverses the full real chain (A,B,C,D,E), so the router's
        hops == len(route) - 1 invariant is verified against a genuine 4-hop path.
        """
        response = client.get("/api/v1/systems/route?start=A&end=E")

        assert response.status_code == 200
        data = response.json()
        assert data["route"] == ["A", "B", "C", "D", "E"]
        assert data["hops"] == len(data["route"]) - 1
        assert data["hops"] == 4
