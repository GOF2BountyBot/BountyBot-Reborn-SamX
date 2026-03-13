"""
Unit tests for PathfindingService (A* pathfinding).

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root).

Test graph topology:

    A(0,0) --- B(10,0) --- C(20,0)
    |                      |
    D(0,10) --- E(10,10) --- F(20,10)
                |
                G(10,20)  ← isolated (no connections to main graph)

Named connections (bidirectional):
    A ↔ B, B ↔ C, A ↔ D, C ↔ F, D ↔ E, E ↔ F

G has no neighbours and cannot be reached from the rest.

Shortest paths:
    A → A  : ["A"]                          (same system)
    A → B  : ["A", "B"]                    (1 hop)
    A → C  : ["A", "B", "C"]               (2 hops)
    A → D  : ["A", "D"]                    (1 hop)
    A → F  : ["A", "B", "C", "F"]
             or ["A", "D", "E", "F"]       (3 hops, two equally short paths)
    A → G  : PathfindingError.NO_ROUTE_FOUND (isolated)
    A → Z  : PathfindingError.NO_ROUTE_FOUND (not in graph)
"""

from __future__ import annotations

import math
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger before importing any service code
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services.pathfinding_service import MAX_ROUTE_LENGTH, PathfindingError, PathfindingService  # noqa: I001
from services.system_graph_service import SystemGraphService, SystemNode


# ---------------------------------------------------------------------------
# Helpers — build a mock SystemGraphService from a plain dict
# ---------------------------------------------------------------------------


def _build_graph_service(
    systems: dict[str, tuple[tuple[int, int], list[str]]],
) -> SystemGraphService:
    """Return a SystemGraphService pre-populated from *systems*.

    *systems* maps ``name → (coordinates, neighbours)`` where neighbours is a
    list of system names (already filtered to graph members — mirrors what
    ``get_neighbours`` would return for a fully connected graph).
    """
    svc = SystemGraphService.__new__(SystemGraphService)
    svc._graph = {}
    svc._loaded = True

    for name, (coords, neighbours) in systems.items():
        svc._graph[name] = SystemNode(
            name=name,
            coordinates=coords,
            neighbours=neighbours,
            faction="",
            security=1,
        )

    return svc


# ---------------------------------------------------------------------------
# Shared fixture — the main 6-node test topology
# ---------------------------------------------------------------------------

#
# Graph layout (coordinates reflect a 10-unit grid):
#
#   A(0,0) --- B(10,0) --- C(20,0)
#   |                      |
#   D(0,10) -- E(10,10) -- F(20,10)
#
# G(10,20) is completely isolated — no neighbours.
#

_MAIN_GRAPH: dict[str, tuple[tuple[int, int], list[str]]] = {
    "A": ((0, 0),   ["B", "D"]),
    "B": ((10, 0),  ["A", "C"]),
    "C": ((20, 0),  ["B", "F"]),
    "D": ((0, 10),  ["A", "E"]),
    "E": ((10, 10), ["D", "F"]),
    "F": ((20, 10), ["C", "E"]),
    "G": ((10, 20), []),  # isolated
}


@pytest.fixture()
def graph_svc() -> SystemGraphService:
    """Provide the main 7-node test graph."""
    return _build_graph_service(_MAIN_GRAPH)


@pytest.fixture()
def svc(graph_svc: SystemGraphService) -> PathfindingService:
    """Provide a PathfindingService backed by the main test graph."""
    return PathfindingService(graph_svc)


# ---------------------------------------------------------------------------
# Helper: assert route is valid (each consecutive pair are neighbours)
# ---------------------------------------------------------------------------


def _assert_route_valid(route: list[str], graph_svc: SystemGraphService) -> None:
    """Verify every consecutive pair of systems in *route* are neighbours."""
    for i in range(len(route) - 1):
        current = route[i]
        nxt = route[i + 1]
        neighbours = graph_svc.get_neighbours(current)
        assert nxt in neighbours, (
            f"Route hop {current!r} → {nxt!r} is not a valid edge. "
            f"Neighbours of {current!r}: {neighbours}"
        )


# ===========================================================================
# TestMakeRoute
# ===========================================================================


class TestMakeRoute:
    """Tests for PathfindingService.make_route."""

    def test_same_system_returns_single_element_list(self, svc: PathfindingService) -> None:
        result = svc.make_route("A", "A")
        assert result == ["A"]

    def test_adjacent_systems_one_hop(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        result = svc.make_route("A", "B")
        assert isinstance(result, list)
        assert result[0] == "A"
        assert result[-1] == "B"
        assert len(result) == 2, f"Expected 2 hops, got {len(result)}: {result}"
        _assert_route_valid(result, graph_svc)

    def test_adjacent_other_direction(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        result = svc.make_route("A", "D")
        assert isinstance(result, list)
        assert result == ["A", "D"]
        _assert_route_valid(result, graph_svc)

    def test_two_hop_path(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        result = svc.make_route("A", "C")
        assert isinstance(result, list)
        assert result[0] == "A"
        assert result[-1] == "C"
        assert len(result) == 3, f"Expected 3-system route, got {len(result)}: {result}"
        _assert_route_valid(result, graph_svc)

    def test_multi_hop_path_length(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        """A → F is 3 hops; verify the route length and validity."""
        result = svc.make_route("A", "F")
        assert isinstance(result, list)
        assert result[0] == "A"
        assert result[-1] == "F"
        assert len(result) == 4, f"Expected 4-system route (3 hops), got {len(result)}: {result}"
        _assert_route_valid(result, graph_svc)

    def test_route_is_traversable(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        """Every hop in the returned route must use a real graph edge."""
        result = svc.make_route("B", "E")
        assert isinstance(result, list)
        _assert_route_valid(result, graph_svc)

    def test_reverse_route_same_length(self, svc: PathfindingService, graph_svc: SystemGraphService) -> None:
        """Reverse path should have the same length (undirected graph)."""
        forward = svc.make_route("A", "F")
        reverse = svc.make_route("F", "A")
        assert isinstance(forward, list)
        assert isinstance(reverse, list)
        assert len(forward) == len(reverse)

    def test_disconnected_system_no_route(self, svc: PathfindingService) -> None:
        """G is isolated — no path should exist from A."""
        result = svc.make_route("A", "G")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_start_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("Z", "A")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_end_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("A", "Z")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_both_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("X", "Z")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_returns_list_of_strings(self, svc: PathfindingService) -> None:
        result = svc.make_route("A", "C")
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_start_and_end_in_result(self, svc: PathfindingService) -> None:
        result = svc.make_route("D", "C")
        assert isinstance(result, list)
        assert result[0] == "D"
        assert result[-1] == "C"


# ===========================================================================
# TestMaxRouteLength
# ===========================================================================


class TestMaxRouteLength:
    """Tests for the MAX_ROUTE_LENGTH limit."""

    def test_max_route_length_constant(self) -> None:
        assert MAX_ROUTE_LENGTH == 50

    def test_long_chain_returns_max_length_reached(self) -> None:
        """A 60-system linear chain should trigger MAX_LENGTH_REACHED."""
        n = 60
        systems: dict[str, tuple[tuple[int, int], list[str]]] = {}
        for i in range(n):
            name = f"S{i}"
            neighbours: list[str] = []
            if i > 0:
                neighbours.append(f"S{i - 1}")
            if i < n - 1:
                neighbours.append(f"S{i + 1}")
            systems[name] = ((i * 10, 0), neighbours)

        long_graph = _build_graph_service(systems)
        long_svc = PathfindingService(long_graph)

        result = long_svc.make_route("S0", f"S{n - 1}")
        assert result is PathfindingError.MAX_LENGTH_REACHED

    def test_just_under_limit_finds_route(self) -> None:
        """A 49-hop chain (50 systems) should succeed (hop counter < 50)."""
        n = 49  # 49 hops → hop_counter reaches 49 < 50
        systems: dict[str, tuple[tuple[int, int], list[str]]] = {}
        for i in range(n):
            name = f"T{i}"
            neighbours: list[str] = []
            if i > 0:
                neighbours.append(f"T{i - 1}")
            if i < n - 1:
                neighbours.append(f"T{i + 1}")
            systems[name] = ((i * 10, 0), neighbours)

        medium_graph = _build_graph_service(systems)
        medium_svc = PathfindingService(medium_graph)

        result = medium_svc.make_route("T0", f"T{n - 1}")
        assert isinstance(result, list), f"Expected list, got {result}"
        assert result[0] == "T0"
        assert result[-1] == f"T{n - 1}"


# ===========================================================================
# TestHeuristic
# ===========================================================================


class TestHeuristic:
    """Tests for PathfindingService._heuristic (static method)."""

    def _make_node(self, x: int, y: int) -> SystemNode:
        return SystemNode(name="test", coordinates=(x, y), neighbours=[], faction="", security=1)

    def test_3_4_5_triangle(self) -> None:
        """Euclidean distance of (0,0)→(3,4) == 5.0."""
        a = self._make_node(0, 0)
        b = self._make_node(3, 4)
        result = PathfindingService._heuristic(a, b)
        assert math.isclose(result, 5.0)

    def test_same_point_is_zero(self) -> None:
        a = self._make_node(5, 7)
        result = PathfindingService._heuristic(a, a)
        assert math.isclose(result, 0.0)

    def test_horizontal_distance(self) -> None:
        a = self._make_node(0, 0)
        b = self._make_node(10, 0)
        assert math.isclose(PathfindingService._heuristic(a, b), 10.0)

    def test_vertical_distance(self) -> None:
        a = self._make_node(0, 0)
        b = self._make_node(0, 7)
        assert math.isclose(PathfindingService._heuristic(a, b), 7.0)

    def test_symmetry(self) -> None:
        """Heuristic should be symmetric: h(a,b) == h(b,a)."""
        a = self._make_node(1, 2)
        b = self._make_node(4, 6)
        assert math.isclose(
            PathfindingService._heuristic(a, b),
            PathfindingService._heuristic(b, a),
        )

    def test_known_diagonal(self) -> None:
        """Distance from (0,0) to (1,1) == sqrt(2)."""
        a = self._make_node(0, 0)
        b = self._make_node(1, 1)
        assert math.isclose(PathfindingService._heuristic(a, b), math.sqrt(2))


# ===========================================================================
# TestEdgeCases
# ===========================================================================


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_start_system_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("MISSING_START", "A")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_end_system_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("A", "MISSING_END")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_both_systems_not_in_graph(self, svc: PathfindingService) -> None:
        result = svc.make_route("MISSING_START", "MISSING_END")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_isolated_node_to_itself(self, svc: PathfindingService) -> None:
        """G is isolated; G → G is still a valid same-system route."""
        result = svc.make_route("G", "G")
        assert result == ["G"]

    def test_empty_graph_no_route(self) -> None:
        empty_graph = _build_graph_service({})
        empty_svc = PathfindingService(empty_graph)
        result = empty_svc.make_route("A", "B")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_single_node_graph_same_system(self) -> None:
        single_graph = _build_graph_service({"SOLO": ((0, 0), [])})
        single_svc = PathfindingService(single_graph)
        result = single_svc.make_route("SOLO", "SOLO")
        assert result == ["SOLO"]

    def test_single_node_graph_no_route_to_other(self) -> None:
        single_graph = _build_graph_service({"SOLO": ((0, 0), [])})
        single_svc = PathfindingService(single_graph)
        result = single_svc.make_route("SOLO", "OTHER")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_two_connected_nodes(self) -> None:
        two_graph = _build_graph_service({
            "P": ((0, 0), ["Q"]),
            "Q": ((1, 0), ["P"]),
        })
        two_svc = PathfindingService(two_graph)
        assert two_svc.make_route("P", "Q") == ["P", "Q"]
        assert two_svc.make_route("Q", "P") == ["Q", "P"]

    def test_pathfinding_error_values(self) -> None:
        """Ensure enum members have the expected string values."""
        assert PathfindingError.MAX_LENGTH_REACHED.value == "max_length_reached"
        assert PathfindingError.NO_ROUTE_FOUND.value == "no_route_found"
