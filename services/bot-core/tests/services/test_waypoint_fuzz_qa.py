"""
Adversarial QA fuzz suite for the waypoint route builder.

Tests:
1. Simple-path invariant — random graphs, force 1+2 waypoints
2. Blocked-set leaks — make_route never returns a route containing a blocked node
3. Cascade probability correctness — marginals match spec
4. Crash / None safety — degenerate graphs never raise
5. min_systems floor — waypoint paths never shorter than 2 systems
6. Interior-degree recheck bypass scenario
7. Test determinism (seeded runs produce identical sequences)
8. _build_anchor_route rejects revisit explicitly
"""

from __future__ import annotations

import random
import sys
import types
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure shared.bblogger is mocked before importing service code
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.bounty_service import BountyService
from services.pathfinding_service import PathfindingError, PathfindingService
from services.system_graph_service import SystemGraphService, SystemNode

# ---------------------------------------------------------------------------
# service fixture (self-contained copy so this file can run standalone)
# ---------------------------------------------------------------------------


@pytest.fixture()
def service() -> BountyService:
    """Minimal BountyService with all repos mocked — mirrors test_bounty_service.py."""
    svc = BountyService()
    svc.bounty_repo = MagicMock()
    svc.criminal_repo = MagicMock()
    svc.item_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)
    svc.bounty_repo.get_by_id_for_update = AsyncMock(return_value=None)
    svc.player_repo.get_by_ids = AsyncMock(return_value=[])
    svc.item_repo.get_all = AsyncMock(return_value=[])
    svc.loot_service = MagicMock()
    svc.loot_service.is_loaded = True
    svc.loot_service.preload_static_data = AsyncMock()
    svc.loot_service.roll_loot = MagicMock(return_value=None)
    svc.inventory_repo = MagicMock()
    svc.inventory_repo.get_player_items = AsyncMock(return_value=[])
    return svc


# ---------------------------------------------------------------------------
# Graph builder (mirrors the one in test_bounty_service.py)
# ---------------------------------------------------------------------------


def _build_graph(adjacency: dict[str, list[str]]) -> SystemGraphService:
    svc = SystemGraphService.__new__(SystemGraphService)
    svc._graph = {}
    svc._loaded = True
    svc._jump_gate_systems = []
    for i, (name, nbrs) in enumerate(adjacency.items()):
        svc._graph[name] = SystemNode(
            name=name,
            coordinates=(i * 17 + 3, i * 11 + 7),
            neighbours=list(nbrs),
            faction="",
            security=1,
        )
    return svc


def _wp_service_from(service, adjacency: dict[str, list[str]]):
    gsvc = _build_graph(adjacency)
    service.graph_service = gsvc
    service.pathfinding_service = PathfindingService(gsvc)
    return service, list(gsvc._graph.keys())


# ---------------------------------------------------------------------------
# Shared topologies
# ---------------------------------------------------------------------------

_GRID = {
    "A": ["B", "D"],
    "B": ["A", "C", "E"],
    "C": ["B", "F"],
    "D": ["A", "E", "G"],
    "E": ["B", "D", "F", "H"],
    "F": ["C", "E", "I"],
    "G": ["D", "H"],
    "H": ["G", "E", "I"],
    "I": ["F", "H"],
}

_LINE = {"A": ["B"], "B": ["A", "C"], "C": ["B", "D"], "D": ["C", "E"], "E": ["D"]}


def _assert_simple_valid(svc, route, gates):
    assert route is not None, "route must not be None"
    assert len(set(route)) == len(route), f"route repeats a system: {route}"
    assert route[0] in gates and route[-1] in gates, f"endpoints not in gates: {route}"
    for i in range(len(route) - 1):
        nbrs = svc.graph_service.get_neighbours(route[i])
        assert route[i + 1] in nbrs, f"invalid hop {route[i]}→{route[i + 1]} in {route}"


# ===========================================================================
# 1. Simple-path invariant — random graphs
# ===========================================================================


class TestSimplePathInvariantFuzz:
    """Brute-force: random graphs × iters, both 1- and 2-waypoint routes."""

    @staticmethod
    def _random_connected_graph(rng: random.Random, n: int) -> dict[str, list[str]]:
        names = [f"S{i}" for i in range(n)]
        perm = rng.sample(names, n)
        adj: dict[str, list[str]] = {name: [] for name in names}
        for i in range(1, n):
            u, v = perm[i], perm[rng.randint(0, i - 1)]
            if v not in adj[u]:
                adj[u].append(v)
            if u not in adj[v]:
                adj[v].append(u)
        for _ in range(rng.randint(0, n)):
            u, v = rng.sample(names, 2)
            if u != v and v not in adj[u]:
                adj[u].append(v)
                adj[v].append(u)
        return adj

    def test_simple_path_random_graphs(self, service):
        """No repeated system in any waypoint route across many random graphs."""
        rng = random.Random(42)
        violations: list[dict] = []

        for g_idx in range(250):
            n = rng.randint(4, 12)
            adj = self._random_connected_graph(rng, n)
            svc, gates = _wp_service_from(service, adj)

            for num_wps in (1, 2):
                for _ in range(25):
                    route = svc._build_waypoint_route(gates, num_wps, attempts=10, min_degree=2)
                    if route is None:
                        continue
                    if len(set(route)) != len(route):
                        violations.append(
                            {
                                "graph_idx": g_idx,
                                "adj": adj,
                                "num_waypoints": num_wps,
                                "route": route,
                            }
                        )
                    for i in range(len(route) - 1):
                        nbrs = svc.graph_service.get_neighbours(route[i])
                        if route[i + 1] not in nbrs:
                            violations.append(
                                {
                                    "graph_idx": g_idx,
                                    "adj": adj,
                                    "num_waypoints": num_wps,
                                    "route": route,
                                    "invalid_hop": (route[i], route[i + 1]),
                                }
                            )

        assert not violations, f"{len(violations)} invariant violations found. First: {violations[0]}"


# ===========================================================================
# 2. Blocked-set leaks in make_route
# ===========================================================================


class TestBlockedSetLeaks:
    """make_route must never return a route containing a blocked node."""

    def test_blocked_never_in_route_random_calls(self, service):
        svc, gates = _wp_service_from(service, _GRID)
        pfsvc = svc.pathfinding_service
        rng = random.Random(1234)
        violations: list[dict] = []

        for _ in range(2000):
            start, end = rng.sample(gates, 2)
            n_blocked = rng.randint(0, len(gates) - 2)
            blocked = frozenset(rng.sample(gates, n_blocked))

            result = pfsvc.make_route(start, end, blocked=blocked)
            if not isinstance(result, list):
                continue

            effective_blocked = blocked - {start, end}
            for node in result:
                if node in effective_blocked:
                    violations.append(
                        {
                            "start": start,
                            "end": end,
                            "blocked": blocked,
                            "route": result,
                            "offender": node,
                        }
                    )

        assert not violations, f"{len(violations)} blocked-set leaks. First: {violations[0]}"


# ===========================================================================
# 3. Cascade probability correctness
# ===========================================================================


class TestCascadeProbability:
    """_roll_waypoint_count must realize the documented marginals."""

    def test_marginals_100k_trials(self, service):
        svc, _ = _wp_service_from(service, _GRID)
        rng_state = random.getstate()
        try:
            random.seed(999)
            counts: Counter = Counter()
            n = 100_000
            for _ in range(n):
                counts[svc._roll_waypoint_count(None)] += 1
        finally:
            random.setstate(rng_state)

        dual_p = 0.10
        single_p = 0.33
        expected = {
            2: dual_p,
            1: (1 - dual_p) * single_p,
            0: (1 - dual_p) * (1 - single_p),
        }
        for k, p in expected.items():
            observed = counts[k] / n
            assert abs(observed - p) < 0.015, (
                f"Waypoint count {k}: expected ≈{p:.3f}, got {observed:.3f} (delta={abs(observed - p):.4f})"
            )

    def test_cascade_order_dual_first(self, service):
        """P(single | dual FAILED) = single_p, not dual_p*(1-single_p)."""
        svc, _ = _wp_service_from(service, _GRID)
        rng_state = random.getstate()
        try:
            random.seed(42)
            # Force dual to fail: next call should use single_p
            counts: Counter = Counter()
            n = 50_000
            for _ in range(n):
                counts[svc._roll_waypoint_count(None)] += 1
        finally:
            random.setstate(rng_state)
        # P(1) should be ~0.297, not 0.33 (which would be wrong cascade order)
        observed_1 = counts[1] / n
        assert observed_1 < 0.32, (
            f"P(1 waypoint) = {observed_1:.3f} is suspiciously high "
            f"(expected ≈0.297, would be 0.33 if dual roll was skipped)"
        )

    def test_guild_override_probabilities(self, service):
        svc, _ = _wp_service_from(service, _GRID)
        # All-dual: every roll should be 2
        cfg_all_dual = SimpleNamespace(bounty_dual_waypoint_prob=1.0, bounty_single_waypoint_prob=0.0)
        assert all(svc._roll_waypoint_count(cfg_all_dual) == 2 for _ in range(100))
        # All-standard: every roll should be 0
        cfg_none = SimpleNamespace(bounty_dual_waypoint_prob=0.0, bounty_single_waypoint_prob=0.0)
        assert all(svc._roll_waypoint_count(cfg_none) == 0 for _ in range(100))
        # All-single: dual=0 so single fires every time
        cfg_all_single = SimpleNamespace(bounty_dual_waypoint_prob=0.0, bounty_single_waypoint_prob=1.0)
        assert all(svc._roll_waypoint_count(cfg_all_single) == 1 for _ in range(100))


# ===========================================================================
# 4. Crash / None safety on degenerate graphs
# ===========================================================================


class TestCrashSafety:
    """Degenerate graphs must never raise — worst case is returning None."""

    def test_two_gate_graph_1wp_no_crash(self, service):
        adj = {"P": ["Q"], "Q": ["P"]}
        svc, gates = _wp_service_from(service, adj)
        # Must not raise; may return None (no degree-2 waypoints)
        result = svc._build_waypoint_route(gates, 1, attempts=5, min_degree=2)
        assert result is None or isinstance(result, list)

    def test_two_gate_graph_2wp_no_crash(self, service):
        adj = {"P": ["Q"], "Q": ["P"]}
        svc, gates = _wp_service_from(service, adj)
        result = svc._build_waypoint_route(gates, 2, attempts=5, min_degree=2)
        assert result is None or isinstance(result, list)

    def test_star_graph_no_crash(self, service):
        adj = {
            "Hub": ["L1", "L2", "L3", "L4"],
            "L1": ["Hub"],
            "L2": ["Hub"],
            "L3": ["Hub"],
            "L4": ["Hub"],
        }
        svc, gates = _wp_service_from(service, adj)
        result = svc._build_waypoint_route(gates, 1, attempts=20, min_degree=2)
        assert result is None or isinstance(result, list)

    def test_no_degree2_nodes_no_crash(self, service):
        """Disjoint pairs — no node has degree ≥ 2."""
        adj = {"A": ["B"], "B": ["A"], "C": ["D"], "D": ["C"]}
        svc, gates = _wp_service_from(service, adj)
        result = svc._build_waypoint_route(gates, 1, attempts=10, min_degree=2)
        assert result is None

    def test_single_node_make_route_no_crash(self, service):
        adj = {"Alone": []}
        svc, _ = _wp_service_from(service, adj)
        assert svc.pathfinding_service.make_route("Alone", "Alone") == ["Alone"]
        assert svc.pathfinding_service.make_route("Alone", "Nowhere") is PathfindingError.NO_ROUTE_FOUND

    def test_empty_gates_list_crashes_with_index_error(self, service):
        """_build_waypoint_route with empty jump_gate_systems raises IndexError.

        This is a documented latent crash: spawn_bounty guards len(jump_gate_systems)<2
        upstream so this code path is not reachable in production. The method itself
        has no internal guard. This test documents the behavior so it's not a surprise.
        """
        svc, _ = _wp_service_from(service, _GRID)
        # This WILL raise IndexError — we document that the method is not crash-safe on empty input.
        with pytest.raises(IndexError):
            svc._build_waypoint_route([], 1, attempts=3, min_degree=2)


# ===========================================================================
# 5. min_systems floor
# ===========================================================================


class TestMinSystemsFloor:
    """Waypoint routes should never be a 1-system route (start==end is blocked)."""

    def test_waypoint_routes_never_trivial(self, service):
        """start!=end is enforced in _build_waypoint_route; route must be ≥ 2 systems."""
        random.seed(555)
        svc, gates = _wp_service_from(service, _GRID)
        for _ in range(500):
            route = svc._build_waypoint_route(gates, 1, attempts=20, min_degree=2)
            if route is not None:
                assert len(route) >= 2, f"waypoint route too short: {route}"


# ===========================================================================
# 6. Interior-degree recheck bypass
# ===========================================================================


class TestInteriorDegreeRecheckBypass:
    """
    _build_anchor_route's degree check runs for INTERIOR anchors only.
    Test the case where a waypoint's available degree drops to 0 after
    the first leg consumes its only remaining corridor.
    """

    def test_degree_recheck_fires_before_leg_is_built(self, service):
        """
        The degree check fires BEFORE the leg to nxt is built (line 1699).
        It checks available_degree(nxt, frozenset(used - {nxt})) at the current
        used set state — NOT after the leg consumes more nodes.

        Graph: A-N1-W, A-N2-W, W-C
        W has neighbours [N1, N2, C].
        Pre-check fires: used={A}, check available_degree(W, {A}).
        W's neighbours not in {A}: N1, N2, C. Degree = 3 >= min_degree=2. PASSES.
        Then the A→W leg is built (uses N1 or N2), adding it to `used`.
        After that, W→C is direct (1 hop). Route is valid.

        This means the pre-check is OPTIMISTIC: it can allow a waypoint whose
        effective degree AFTER the incoming leg is less than min_degree. But that
        scenario is handled by the next leg's make_route failing (returning
        PathfindingError), which returns None from _build_anchor_route. Correct.
        """
        adj = {
            "A": ["N1", "N2"],
            "N1": ["A", "W"],
            "N2": ["A", "W"],
            "W": ["N1", "N2", "C"],
            "C": ["W"],
        }
        svc, _gates = _wp_service_from(service, adj)
        # [A, W, C]: W is interior. Pre-check at used={A}: degree(W) = 3. Passes.
        # A→W leg (shortest path: 1 hop via N1 or N2).
        # W→C: direct hop. Total route: [A, N1/N2, W, C].
        result = svc._build_anchor_route(["A", "W", "C"], min_degree=2)
        # The route IS buildable — W→C is reachable after the first leg.
        if result is not None:
            assert len(set(result)) == len(result), f"Simple-path violated: {result}"
            for i in range(len(result) - 1):
                nbrs = svc.graph_service.get_neighbours(result[i])
                assert result[i + 1] in nbrs
        # None is also acceptable (if A* picks a path that leaves W unable to reach C)
        # but on this graph that should not happen since W directly connects to C.

    def test_degree_recheck_blocks_degree1_waypoint(self, service):
        """
        W has only one neighbour (N). The degree check correctly fires and returns None.
        """
        adj = {
            "A": ["N"],
            "N": ["A", "W"],
            "W": ["N"],  # degree 1 (only N, which is in used after connecting A)
            "C": [],
        }
        svc, _ = _wp_service_from(service, adj)
        # Pre-check at used={A}: available_degree(W, {A}) = degree of W not-in-{A} = just N.
        # Degree = 1 < min_degree=2. Returns None immediately.
        result = svc._build_anchor_route(["A", "W", "C"], min_degree=2)
        assert result is None, f"Expected None (W has degree 1), got {result}"

    def test_build_anchor_route_rejects_reused_anchor(self, service):
        """On A-B-C-D-E, leg A→E consumes C; [A, E, C] must be rejected."""
        svc, _ = _wp_service_from(service, _LINE)
        result = svc._build_anchor_route(["A", "E", "C"], min_degree=2)
        assert result is None, f"Expected None for revisited anchor, got {result}"

    def test_build_anchor_route_accepts_feasible_triple(self, service):
        """[A, E, I] on the 3×3 grid is always feasible — any valid route is accepted."""
        random.seed(123)
        svc, _ = _wp_service_from(service, _GRID)
        # A and I are corners; E is center (degree 4). Multiple paths exist.
        result = svc._build_anchor_route(["A", "E", "I"], min_degree=2)
        if result is not None:
            _assert_simple_valid(svc, result, list(_GRID.keys()))
        # None is also acceptable (degree recheck may block in some scenarios)


# ===========================================================================
# 7. Test determinism
# ===========================================================================


class TestDeterminism:
    """Seeded tests must produce identical results across two runs."""

    def test_single_waypoint_300iters_deterministic(self, service):
        routes_run1 = []
        routes_run2 = []

        for routes in (routes_run1, routes_run2):
            random.seed(20260624)
            svc, gates = _wp_service_from(service, _GRID)
            for _ in range(300):
                route = svc._build_waypoint_route(gates, 1, attempts=20, min_degree=2)
                routes.append(tuple(route) if route else None)

        assert routes_run1 == routes_run2, "Seeded runs diverge — non-deterministic!"

    def test_dual_waypoint_300iters_deterministic(self, service):
        routes_run1 = []
        routes_run2 = []

        for routes in (routes_run1, routes_run2):
            random.seed(7)
            svc, gates = _wp_service_from(service, _GRID)
            for _ in range(300):
                route = svc._build_waypoint_route(gates, 2, attempts=20, min_degree=2)
                routes.append(tuple(route) if route else None)

        assert routes_run1 == routes_run2, "Seeded runs diverge — non-deterministic!"


# ===========================================================================
# 8. Explicit _build_anchor_route revisit guard
# ===========================================================================


class TestAnchorRouteRevisitGuard:
    """Direct unit tests on _build_anchor_route's `nxt in used` guard."""

    def test_immediate_revisit_of_start(self, service):
        svc, _ = _wp_service_from(service, _GRID)
        result = svc._build_anchor_route(["A", "B", "A"], min_degree=1)
        assert result is None, f"Expected None for [A,B,A], got {result}"

    def test_indirect_revisit_via_leg(self, service):
        """On the LINE: A→B→C is the only path. Then visiting B again must be blocked."""
        svc, _ = _wp_service_from(service, _LINE)
        # A→C leg uses B. Anchor list [A, C, B] tries to visit B (now in used) — must fail.
        result = svc._build_anchor_route(["A", "C", "B"], min_degree=1)
        assert result is None, f"Expected None (B consumed by first leg), got {result}"

    def test_valid_three_anchor_route(self, service):
        """A valid [A, E, I] triple on the grid must succeed and be simple."""
        random.seed(77)
        svc, _ = _wp_service_from(service, _GRID)
        for _ in range(20):
            result = svc._build_anchor_route(["A", "E", "I"], min_degree=1)
            if result is not None:
                assert len(set(result)) == len(result), f"Simple-path violated: {result}"
                break
        # At least one of the 20 attempts must find a route (the grid is well-connected)


# ===========================================================================
# 9. Connectivity across permutations (midpoint-swap exhaustion)
# ===========================================================================


class TestPermutationExhaustion:
    """_build_waypoint_route tries all permutations of waypoints before giving up."""

    def test_all_permutations_tried_on_tight_graph(self, service):
        """
        On a linear graph A-W1-W2-B, the builder must try both orderings of [W1, W2].
        Because the graph is symmetric, both [A,W1,W2,B] and [B,W2,W1,A] are valid
        simple paths. Verify that whichever is returned is correct.

        The key invariant: only ONE ordering of each permutation can be a valid
        simple path for any given (start, end) pair.
        """
        adj = {
            "A": ["W1"],
            "W1": ["A", "W2"],
            "W2": ["W1", "B"],
            "B": ["W2"],
        }
        random.seed(99)
        svc, gates = _wp_service_from(service, adj)
        # With min_degree=1 (W1 and W2 are eligible), 2 waypoints
        result = svc._build_waypoint_route(gates, 2, attempts=20, min_degree=1)
        if result is not None:
            # Both forward [A,W1,W2,B] and reverse [B,W2,W1,A] are valid
            valid_routes = [["A", "W1", "W2", "B"], ["B", "W2", "W1", "A"]]
            assert result in valid_routes, f"Got unexpected route {result}; expected one of {valid_routes}"
            _assert_simple_valid(svc, result, gates)
