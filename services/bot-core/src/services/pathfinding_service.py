"""
Pathfinding Service for star system routes.

Finds the shortest path between two star systems using a zero-heuristic A*
(equivalent to Dijkstra / BFS), guaranteeing the shortest hop count.

A Euclidean coordinate heuristic is inadmissible for uniform-hop-cost graphs
because pixel distances do not reflect hop counts (B.77). With only ~34 nodes
the zero heuristic is perfectly adequate.
"""

from __future__ import annotations

import enum
import heapq
from dataclasses import dataclass, field

from shared import bblogger

from services.system_graph_service import SystemGraphService, SystemNode

flogger = bblogger.get_logger("service-pathfinding")

MAX_ROUTE_LENGTH = 50


class PathfindingError(enum.Enum):
    """Error types returned when pathfinding fails."""

    MAX_LENGTH_REACHED = "max_length_reached"
    NO_ROUTE_FOUND = "no_route_found"


@dataclass
class _AStarNode:
    """Internal node used during A* search."""

    system_name: str
    coordinates: tuple[int, int]
    parent: _AStarNode | None
    g: int  # cost from start (number of hops)
    h: float  # heuristic (Euclidean distance to goal)
    f: float = field(init=False)  # total estimated cost

    def __post_init__(self) -> None:
        self.f = self.g + self.h

    def __lt__(self, other: _AStarNode) -> bool:
        return self.f < other.f


class PathfindingService:
    """A* pathfinding between star systems."""

    def __init__(self, graph_service: SystemGraphService) -> None:
        self.graph = graph_service

    @staticmethod
    def _heuristic(a: SystemNode, b: SystemNode) -> float:
        """Zero heuristic — degrades A* to Dijkstra, guaranteeing shortest hop count.

        A Euclidean coordinate heuristic is inadmissible for uniform-hop-cost graphs
        because pixel distances do not reflect hop counts (B.77).
        """
        return 0.0

    def make_route(self, start: str, end: str) -> list[str] | PathfindingError:
        """Find shortest path from start to end system.

        Uses zero-heuristic A* (Dijkstra) with uniform hop costs.

        Args:
            start: Name of starting system.
            end: Name of destination system.

        Returns:
            Ordered list of system name strings (start → ... → end),
            or a PathfindingError enum value if no path could be found.
        """
        flogger.debug(f"Route request: {start} → {end}")

        # Trivial case
        if start == end:
            flogger.debug(f"Route trivial: start==end ({start}), returning single-system route")
            return [start]

        start_node = self.graph.get_system(start)
        end_node = self.graph.get_system(end)

        if start_node is None or end_node is None:
            flogger.error(f"Route not found: system not in graph (start={start!r}, end={end!r})")
            return PathfindingError.NO_ROUTE_FOUND

        # --- A* search ---
        # Open set: min-heap ordered by f value (heapq).
        # open_best tracks the best known f for each coordinate in the open
        # set, enabling O(1) duplicate/dominance checks instead of scanning.
        root = _AStarNode(
            system_name=start_node.name,
            coordinates=start_node.coordinates,
            parent=None,
            g=0,
            h=self._heuristic(start_node, end_node),
        )
        open_heap: list[_AStarNode] = [root]
        # Best f-value seen in open set for each coordinate.
        open_best: dict[tuple[int, int], float] = {root.coordinates: root.f}

        # Closed set: coordinates of expanded nodes.
        closed_coords: set[tuple[int, int]] = set()

        hop_counter = 0

        while open_heap:
            # Pop the node with the lowest f value — O(log N).
            q = heapq.heappop(open_heap)

            # Lazy deletion: skip if this node was superseded by a better
            # entry for the same coordinates that was pushed later.
            if q.coordinates in closed_coords:
                continue
            if q.coordinates in open_best and q.f > open_best[q.coordinates]:
                continue

            hop_counter += 1
            if hop_counter >= MAX_ROUTE_LENGTH:
                flogger.warning(
                    "A* exceeded MAX_ROUTE_LENGTH=%d between %s and %s",
                    MAX_ROUTE_LENGTH,
                    start,
                    end,
                )
                return PathfindingError.MAX_LENGTH_REACHED

            # Expand neighbours.
            for neighbour_name in self.graph.get_neighbours(q.system_name):
                neighbour_sys = self.graph.get_system(neighbour_name)
                if neighbour_sys is None:
                    continue

                # Goal check.
                if neighbour_name == end:
                    # Reconstruct route by tracing parent pointers.
                    route: list[str] = [end]
                    current: _AStarNode | None = q
                    while current is not None:
                        route.append(current.system_name)
                        current = current.parent
                    route.reverse()
                    flogger.info(
                        "Route found: %s → %s (%d hops)",
                        start,
                        end,
                        len(route) - 1,
                    )
                    return route

                new_g = q.g + 1
                new_h = self._heuristic(neighbour_sys, end_node)
                new_f = new_g + new_h
                n_coords = neighbour_sys.coordinates

                # Skip if already expanded (closed).
                if n_coords in closed_coords:
                    continue

                # Skip if a better (or equal) entry already exists in open.
                if n_coords in open_best and open_best[n_coords] <= new_f:
                    continue

                # Build the new candidate node and push to heap — O(log N).
                candidate = _AStarNode(
                    system_name=neighbour_name,
                    coordinates=n_coords,
                    parent=q,
                    g=new_g,
                    h=new_h,
                )
                heapq.heappush(open_heap, candidate)
                open_best[n_coords] = new_f

            # Mark q as expanded.
            closed_coords.add(q.coordinates)
            # Clean up open_best entry since it's now closed.
            open_best.pop(q.coordinates, None)

        flogger.warning("No route found between %s and %s", start, end)
        return PathfindingError.NO_ROUTE_FOUND
