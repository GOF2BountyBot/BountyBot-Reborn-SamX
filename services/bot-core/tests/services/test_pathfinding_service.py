"""
Unit tests for PathfindingService (zero-heuristic A* / Dijkstra pathfinding).

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

Note (B.77): _heuristic() always returns 0.0 (zero heuristic), which degrades
A* to Dijkstra/BFS and guarantees the shortest hop count. A Euclidean
coordinate heuristic is inadmissible for uniform-hop-cost graphs.
"""

from __future__ import annotations

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

from services.pathfinding_service import MAX_ROUTE_LENGTH, PathfindingError, PathfindingService
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
    "A": ((0, 0), ["B", "D"]),
    "B": ((10, 0), ["A", "C"]),
    "C": ((20, 0), ["B", "F"]),
    "D": ((0, 10), ["A", "E"]),
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
            f"Route hop {current!r} → {nxt!r} is not a valid edge. Neighbours of {current!r}: {neighbours}"
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
    """Tests for PathfindingService._heuristic (static method).

    B.77: The heuristic always returns 0.0 (zero heuristic), degrading A* to
    Dijkstra/BFS to guarantee shortest hop count on a uniform-hop-cost graph.
    A Euclidean coordinate heuristic is inadmissible for this graph type.
    """

    def _make_node(self, x: int, y: int) -> SystemNode:
        return SystemNode(name="test", coordinates=(x, y), neighbours=[], faction="", security=1)

    def test_zero_for_distinct_points(self) -> None:
        """Heuristic always returns 0.0 regardless of coordinates (B.77)."""
        a = self._make_node(0, 0)
        b = self._make_node(3, 4)
        assert PathfindingService._heuristic(a, b) == 0.0

    def test_zero_for_same_point(self) -> None:
        """Heuristic returns 0.0 for identical nodes (B.77)."""
        a = self._make_node(5, 7)
        assert PathfindingService._heuristic(a, a) == 0.0

    def test_zero_for_horizontal_separation(self) -> None:
        """Heuristic returns 0.0 regardless of horizontal separation (B.77)."""
        a = self._make_node(0, 0)
        b = self._make_node(10, 0)
        assert PathfindingService._heuristic(a, b) == 0.0

    def test_zero_for_vertical_separation(self) -> None:
        """Heuristic returns 0.0 regardless of vertical separation (B.77)."""
        a = self._make_node(0, 0)
        b = self._make_node(0, 7)
        assert PathfindingService._heuristic(a, b) == 0.0

    def test_symmetry(self) -> None:
        """Heuristic is symmetric: h(a,b) == h(b,a) == 0.0 (B.77)."""
        a = self._make_node(1, 2)
        b = self._make_node(4, 6)
        assert PathfindingService._heuristic(a, b) == PathfindingService._heuristic(b, a) == 0.0

    def test_returns_float(self) -> None:
        """Heuristic return type is float (B.77)."""
        a = self._make_node(0, 0)
        b = self._make_node(1, 1)
        result = PathfindingService._heuristic(a, b)
        assert isinstance(result, float)
        assert result == 0.0


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
        two_graph = _build_graph_service(
            {
                "P": ((0, 0), ["Q"]),
                "Q": ((1, 0), ["P"]),
            }
        )
        two_svc = PathfindingService(two_graph)
        assert two_svc.make_route("P", "Q") == ["P", "Q"]
        assert two_svc.make_route("Q", "P") == ["Q", "P"]

    def test_pathfinding_error_values(self) -> None:
        """Ensure enum members have the expected string values."""
        assert PathfindingError.MAX_LENGTH_REACHED.value == "max_length_reached"
        assert PathfindingError.NO_ROUTE_FOUND.value == "no_route_found"


# ===========================================================================
# TestLazyDeletion — covers lines 121/123 (stale heap entries)
# ===========================================================================


class TestLazyDeletion:
    """Tests that verify lazy-deletion paths in the A* open-heap (lines 121 and 123).

    Because h=0 (zero heuristic), every node's f equals g (hop count).
    On a uniform-cost graph with zero heuristic, a node can be pushed to
    the heap multiple times before it is expanded.  We force that condition
    by constructing a diamond graph where two paths to the same node are
    discovered before the node is expanded, exercising the stale-entry
    guards on lines 121 and 123.

    Diamond topology (all edges bidirectional):

        START ─── L ─── END
          └───── R ─────┘

    Both L and R are at 1 hop from START.  END is at 2 hops via either
    branch.  Depending on heap ordering, END may be discovered via L
    and then via R (or vice-versa) before being expanded, causing
    duplicate entries for L or R in the open set.
    """

    @staticmethod
    def _diamond_graph() -> SystemGraphService:
        return _build_graph_service(
            {
                "START": ((0, 0), ["L", "R"]),
                "L": ((1, 0), ["START", "END"]),
                "R": ((1, 1), ["START", "END"]),
                "END": ((2, 0), ["L", "R"]),
            }
        )

    def test_diamond_finds_shortest_path(self) -> None:
        """Diamond graph: START → END should be 2 hops (one intermediate node)."""
        graph = self._diamond_graph()
        svc = PathfindingService(graph)
        result = svc.make_route("START", "END")
        assert isinstance(result, list), f"Expected list, got {result!r}"
        assert result[0] == "START"
        assert result[-1] == "END"
        assert len(result) == 3, f"Expected 3 nodes (2 hops), got {len(result)}: {result}"
        _assert_route_valid(result, graph)

    def test_diamond_stale_closed_entry_skipped(self) -> None:
        """On the diamond, expanding one branch may push a stale entry for
        the other branch (line 121: closed_coords guard).  The route must
        still be correct despite stale entries in the heap."""
        graph = self._diamond_graph()
        svc = PathfindingService(graph)
        # Run START → END then END → START to maximise heap churn.
        r1 = svc.make_route("START", "END")
        r2 = svc.make_route("END", "START")
        assert isinstance(r1, list) and isinstance(r2, list)
        assert len(r1) == len(r2) == 3

    def test_wide_graph_forces_stale_heap_entries(self) -> None:
        """A wide fan-out forces many stale entries: one hub connects to N
        leaves, all of which then connect to a single sink.  The sink is
        pushed N times before it is expanded; only the first (lowest-g)
        entry should be processed (lines 121/123)."""
        n = 6
        systems: dict[str, tuple[tuple[int, int], list[str]]] = {}
        leaves = [f"LEAF{i}" for i in range(n)]
        systems["HUB"] = ((0, 0), leaves)
        systems["SINK"] = ((2, 0), leaves)
        for i, leaf in enumerate(leaves):
            systems[leaf] = ((1, i), ["HUB", "SINK"])

        graph = _build_graph_service(systems)
        svc = PathfindingService(graph)
        result = svc.make_route("HUB", "SINK")
        assert isinstance(result, list), f"Expected list, got {result!r}"
        assert result[0] == "HUB"
        assert result[-1] == "SINK"
        assert len(result) == 3, f"Expected 3 nodes (2 hops), got {len(result)}: {result}"
        _assert_route_valid(result, graph)


# ===========================================================================
# TestDanglingNeighbour — covers line 139 (neighbour_sys is None)
# ===========================================================================


class TestDanglingNeighbour:
    """Test that a neighbour name returned by get_neighbours() but absent
    from get_system() is safely skipped (line 139).

    Note on coverage: SystemGraphService.get_neighbours() pre-filters
    neighbours to only those present in _graph, so line 139
    (``if neighbour_sys is None: continue``) is a defensive guard
    that cannot be reached through normal graph construction.
    These tests verify the CORRECT BEHAVIOUR when the graph is consistent:
    pathfinding routes around absent neighbours and still finds valid paths.
    The mock-based tests below directly exercise line 139 via a controlled
    get_neighbours() override that returns a ghost name.
    """

    @staticmethod
    def _graph_with_mock_dangling_neighbour() -> SystemGraphService:
        """Return a graph whose get_neighbours() directly returns 'GHOST',
        which is absent from _graph.  This exercises line 139's None-guard
        by patching get_neighbours at the method level.
        """
        svc = SystemGraphService.__new__(SystemGraphService)
        svc._graph = {
            "A": SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1),
            "B": SystemNode(name="B", coordinates=(1, 0), neighbours=["A"], faction="", security=1),
        }
        svc._loaded = True

        # Override get_neighbours to inject a ghost name for node A
        original_graph = svc._graph.copy()

        def mock_get_neighbours(name: str) -> list[str]:
            if name == "A":
                return ["GHOST", "B"]  # GHOST is absent from _graph
            node = original_graph.get(name)
            if node is None:
                return []
            return [n for n in node.neighbours if n in original_graph]

        svc.get_neighbours = mock_get_neighbours  # type: ignore[method-assign]
        return svc

    def test_dangling_neighbour_skipped_reaches_target(self) -> None:
        """Pathfinding should silently skip 'GHOST' (line 139) and still find A → B."""
        graph = self._graph_with_mock_dangling_neighbour()
        svc = PathfindingService(graph)
        result = svc.make_route("A", "B")
        assert result == ["A", "B"], f"Expected ['A', 'B'], got {result!r}"

    def test_only_dangling_neighbour_no_route(self) -> None:
        """If only a ghost name is returned (no real neighbour), return NO_ROUTE_FOUND."""
        svc_graph = SystemGraphService.__new__(SystemGraphService)
        svc_graph._graph = {
            "A": SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1),
            "B": SystemNode(name="B", coordinates=(1, 0), neighbours=[], faction="", security=1),
        }
        svc_graph._loaded = True

        def mock_get_neighbours_ghost_only(name: str) -> list[str]:
            if name == "A":
                return ["GHOST"]  # GHOST is absent from _graph; B is not listed
            return []

        svc_graph.get_neighbours = mock_get_neighbours_ghost_only  # type: ignore[method-assign]
        svc = PathfindingService(svc_graph)
        result = svc.make_route("A", "B")
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_mixed_valid_and_ghost_neighbours(self) -> None:
        """Ghost entries interspersed with valid neighbours are all skipped safely."""
        svc_graph = SystemGraphService.__new__(SystemGraphService)
        svc_graph._graph = {
            "START": SystemNode(name="START", coordinates=(0, 0), neighbours=[], faction="", security=1),
            "MIDDLE": SystemNode(name="MIDDLE", coordinates=(1, 0), neighbours=[], faction="", security=1),
            "END": SystemNode(name="END", coordinates=(2, 0), neighbours=[], faction="", security=1),
        }
        svc_graph._loaded = True

        def mock_get_neighbours_mixed(name: str) -> list[str]:
            return {
                "START": ["GHOST1", "MIDDLE", "GHOST2"],
                "MIDDLE": ["START", "GHOST3", "END"],
                "END": ["MIDDLE"],
            }.get(name, [])

        svc_graph.get_neighbours = mock_get_neighbours_mixed  # type: ignore[method-assign]
        svc = PathfindingService(svc_graph)
        result = svc.make_route("START", "END")
        assert isinstance(result, list), f"Expected list, got {result!r}"
        assert result == ["START", "MIDDLE", "END"]


# ===========================================================================
# TestB77KnownBadCase — actual game graph: Union → Oom'Bak
# ===========================================================================


class TestB77KnownBadCase:
    """Regression test for B.77: the zero heuristic must prefer the shorter
    hop-count route, even when a longer route would be selected by an
    inadmissible Euclidean heuristic.

    Actual game graph topology (relevant excerpt):

        Union ──── Magnetar ──── Oom'Bak          (2 hops via Magnetar)
          │
          └──── Prospero ──── Vulpes ──── Oom'Bak  (3 hops via Prospero/Vulpes)

    With a Euclidean heuristic, Prospero (coords ≈965,406) is geometrically
    closer to Oom'Bak (coords ≈836,705) than Magnetar (coords ≈706,539),
    so the old heuristic could expand Prospero first and find the 3-hop path
    before exhausting Magnetar's 2-hop path — returning a suboptimal route.

    With the zero heuristic (B.77), A* degrades to Dijkstra/BFS and the
    2-hop Magnetar path is always found first.

    We reproduce the exact coordinates and adjacency from the seed files.
    """

    @staticmethod
    def _actual_game_subgraph() -> SystemGraphService:
        """Build the relevant portion of the real game graph using seed-file data."""
        return _build_graph_service(
            {
                # Terran systems
                "Union": ((683, 351), ["Prospero", "Magnetar"]),
                "Magnetar": ((706, 539), ["Union", "Oom'Bak"]),
                "Prospero": ((965, 406), ["Union", "Vulpes"]),
                "Vulpes": ((977, 602), ["Prospero", "Oom'Bak"]),
                # Vossk systems
                "Oom'Bak": ((836, 705), ["Magnetar", "Vulpes"]),
            }
        )

    def test_union_to_oombak_prefers_2_hop_magnetar_path(self) -> None:
        """B.77 regression: Union → Oom'Bak must be 2 hops (via Magnetar),
        NOT 3 hops (via Prospero → Vulpes).

        This was the known bad case that triggered the B.77 fix.
        """
        graph = self._actual_game_subgraph()
        svc = PathfindingService(graph)

        result = svc.make_route("Union", "Oom'Bak")

        assert isinstance(result, list), f"Expected list, got {result!r}"
        assert result[0] == "Union"
        assert result[-1] == "Oom'Bak"
        assert len(result) == 3, (
            f"B.77 regression: expected 3-node route (2 hops via Magnetar), got {len(result)} nodes: {result}"
        )
        assert result == ["Union", "Magnetar", "Oom'Bak"], (
            f"B.77 regression: expected ['Union', 'Magnetar', 'Oom'Bak'], got {result}"
        )
        _assert_route_valid(result, graph)

    def test_union_to_oombak_not_via_prospero(self) -> None:
        """Confirm Prospero does NOT appear in the optimal route (B.77)."""
        graph = self._actual_game_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Union", "Oom'Bak")
        assert isinstance(result, list)
        assert "Prospero" not in result, f"B.77 regression: Prospero should NOT be in the optimal route, got {result}"

    def test_union_to_oombak_not_via_vulpes(self) -> None:
        """Confirm Vulpes does NOT appear in the optimal route (B.77)."""
        graph = self._actual_game_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Union", "Oom'Bak")
        assert isinstance(result, list)
        assert "Vulpes" not in result, f"B.77 regression: Vulpes should NOT be in the optimal route, got {result}"

    def test_reverse_oombak_to_union_also_2_hops(self) -> None:
        """Reverse route should also be 2 hops (graph is undirected)."""
        graph = self._actual_game_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Oom'Bak", "Union")
        assert isinstance(result, list)
        assert len(result) == 3, f"Reverse route should also be 2 hops, got {len(result)}: {result}"
        assert result == ["Oom'Bak", "Magnetar", "Union"]


# ===========================================================================
# TestB78WahNorrIsolation — seed-data isolation verification
# ===========================================================================


class TestB78WahNorrIsolation:
    """B.78 regression: Wah'Norr must be completely isolated in the graph.

    We build the relevant Vossk subgraph using the exact seed-file adjacency
    data and verify that:
    1. Wah'Norr has no neighbours (empty adjacency list).
    2. No other system can reach Wah'Norr.
    3. Wah'Norr cannot reach any other system.
    4. K'Ontrr does NOT list Wah'Norr as a neighbour.
    5. Ni'Mrrod does NOT list Wah'Norr as a neighbour.
    """

    @staticmethod
    def _vossk_subgraph() -> SystemGraphService:
        """Build the relevant Vossk subgraph from seed-file data."""
        return _build_graph_service(
            {
                # Wah'Norr — isolated after B.78 fix
                "Wah'Norr": ((1155, 717), []),
                # K'Ontrr neighbours (post-B.78: no Wah'Norr)
                "K'Ontrr": ((965, 1010), ["S'Kolptorr", "Ni'Mrrod", "Me'Enkk"]),
                # Ni'Mrrod neighbours (post-B.78: no Wah'Norr)
                "Ni'Mrrod": ((1124, 1082), ["K'Ontrr", "Me'Enkk"]),
                # Supporting nodes to make the graph connected
                "S'Kolptorr": ((800, 900), ["K'Ontrr"]),
                "Me'Enkk": ((1000, 1150), ["K'Ontrr", "Ni'Mrrod"]),
            }
        )

    def test_wahnorr_has_no_neighbours(self) -> None:
        """B.78: Wah'Norr must have zero neighbours in the graph."""
        graph = self._vossk_subgraph()
        neighbours = graph.get_neighbours("Wah'Norr")
        assert neighbours == [], f"Wah'Norr should have no neighbours, got: {neighbours}"

    def test_wahnorr_unreachable_from_kontrr(self) -> None:
        """B.78: No path should exist from K'Ontrr to Wah'Norr."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("K'Ontrr", "Wah'Norr")
        assert result is PathfindingError.NO_ROUTE_FOUND, (
            f"B.78: K'Ontrr → Wah'Norr should be NO_ROUTE_FOUND, got {result!r}"
        )

    def test_wahnorr_unreachable_from_nimrrod(self) -> None:
        """B.78: No path should exist from Ni'Mrrod to Wah'Norr."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Ni'Mrrod", "Wah'Norr")
        assert result is PathfindingError.NO_ROUTE_FOUND, (
            f"B.78: Ni'Mrrod → Wah'Norr should be NO_ROUTE_FOUND, got {result!r}"
        )

    def test_wahnorr_cannot_reach_kontrr(self) -> None:
        """B.78: No path should exist from Wah'Norr to K'Ontrr."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Wah'Norr", "K'Ontrr")
        assert result is PathfindingError.NO_ROUTE_FOUND, (
            f"B.78: Wah'Norr → K'Ontrr should be NO_ROUTE_FOUND, got {result!r}"
        )

    def test_wahnorr_cannot_reach_nimrrod(self) -> None:
        """B.78: No path should exist from Wah'Norr to Ni'Mrrod."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Wah'Norr", "Ni'Mrrod")
        assert result is PathfindingError.NO_ROUTE_FOUND, (
            f"B.78: Wah'Norr → Ni'Mrrod should be NO_ROUTE_FOUND, got {result!r}"
        )

    def test_wahnorr_self_route_only(self) -> None:
        """Wah'Norr → Wah'Norr is trivially valid (same system)."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("Wah'Norr", "Wah'Norr")
        assert result == ["Wah'Norr"]

    def test_kontrr_neighbours_do_not_include_wahnorr(self) -> None:
        """B.78: K'Ontrr's neighbour list must NOT contain Wah'Norr."""
        graph = self._vossk_subgraph()
        neighbours = graph.get_neighbours("K'Ontrr")
        assert "Wah'Norr" not in neighbours, f"B.78: K'Ontrr neighbours should not include Wah'Norr; got {neighbours}"

    def test_nimrrod_neighbours_do_not_include_wahnorr(self) -> None:
        """B.78: Ni'Mrrod's neighbour list must NOT contain Wah'Norr."""
        graph = self._vossk_subgraph()
        neighbours = graph.get_neighbours("Ni'Mrrod")
        assert "Wah'Norr" not in neighbours, f"B.78: Ni'Mrrod neighbours should not include Wah'Norr; got {neighbours}"

    def test_kontrr_to_nimrrod_still_connected(self) -> None:
        """B.78: After isolating Wah'Norr, K'Ontrr → Ni'Mrrod must still be reachable."""
        graph = self._vossk_subgraph()
        svc = PathfindingService(graph)
        result = svc.make_route("K'Ontrr", "Ni'Mrrod")
        assert isinstance(result, list), f"K'Ontrr → Ni'Mrrod should be reachable, got {result!r}"
        assert result[0] == "K'Ontrr"
        assert result[-1] == "Ni'Mrrod"
        assert len(result) == 2, f"K'Ontrr → Ni'Mrrod should be 1 hop, got {len(result)}: {result}"
        _assert_route_valid(result, graph)


# ===========================================================================
# TestMakeRouteBlocked — the `blocked` set parameter (waypoint route support)
# ===========================================================================


class TestMakeRouteBlocked:
    """make_route(..., blocked=...) must never enter a blocked system, while
    always permitting the route's own endpoints.

    On the 6-cycle main graph (A-B-C-F-E-D-A) A→F has two equally short
    corridors: A-B-C-F and A-D-E-F. Blocking one corridor forces the other.
    """

    def test_blocked_forces_alternate_corridor(self, svc, graph_svc) -> None:
        # Block the B-C corridor: the only A→F route left is A-D-E-F.
        result = svc.make_route("A", "F", blocked=frozenset({"B"}))
        assert isinstance(result, list)
        assert "B" not in result
        assert result == ["A", "D", "E", "F"]
        _assert_route_valid(result, graph_svc)

    def test_blocked_node_never_appears_in_route(self, svc, graph_svc) -> None:
        # A→C is normally A-B-C; blocking B forces the long way round the cycle.
        result = svc.make_route("A", "C", blocked=frozenset({"B"}))
        assert isinstance(result, list)
        assert "B" not in result
        assert result[0] == "A" and result[-1] == "C"
        assert result == ["A", "D", "E", "F", "C"]
        _assert_route_valid(result, graph_svc)

    def test_blocking_all_corridors_yields_no_route(self, svc) -> None:
        # A's only neighbours are B and D; blocking both strands A.
        result = svc.make_route("A", "F", blocked=frozenset({"B", "D"}))
        assert result is PathfindingError.NO_ROUTE_FOUND

    def test_endpoints_are_stripped_from_blocked(self, svc, graph_svc) -> None:
        # Passing the endpoints themselves in `blocked` must not break routing —
        # they are always permitted (a caller may pass an accumulated visited set).
        result = svc.make_route("A", "C", blocked=frozenset({"A", "C"}))
        assert isinstance(result, list)
        assert result == ["A", "B", "C"]
        _assert_route_valid(result, graph_svc)

    def test_blocked_end_still_reachable(self, svc) -> None:
        # Even if `end` is in blocked, the endpoint exception lets the route finish.
        result = svc.make_route("A", "B", blocked=frozenset({"B"}))
        assert isinstance(result, list)
        assert result == ["A", "B"]

    def test_empty_blocked_matches_unblocked(self, svc) -> None:
        plain = svc.make_route("A", "F")
        blocked = svc.make_route("A", "F", blocked=frozenset())
        assert plain == blocked

    def test_blocked_is_not_mutated(self, svc) -> None:
        blk = frozenset({"B"})
        svc.make_route("A", "F", blocked=blk)
        assert blk == frozenset({"B"})
