"""
A* Pathfinding Service for star system routes.

Finds the shortest path between two star systems using the A* algorithm
with Euclidean distance as the heuristic. Edge weights are uniform (1 hop).
"""

from __future__ import annotations

import bisect
import enum
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
        """Euclidean distance heuristic."""
        return SystemGraphService.euclidean_distance(a, b)

    def make_route(self, start: str, end: str) -> list[str] | PathfindingError:
        """Find shortest path from start to end system.

        Uses A* with Euclidean distance heuristic and uniform hop costs.

        Args:
            start: Name of starting system.
            end: Name of destination system.

        Returns:
            Ordered list of system name strings (start → ... → end),
            or a PathfindingError enum value if no path could be found.
        """
        # Trivial case
        if start == end:
            return [start]

        start_node = self.graph.get_system(start)
        end_node = self.graph.get_system(end)

        if start_node is None or end_node is None:
            return PathfindingError.NO_ROUTE_FOUND

        # --- A* search ---
        # Open list: sorted ascending by f (bisect.insort keeps it ordered).
        # Each element is an _AStarNode.
        root = _AStarNode(
            system_name=start_node.name,
            coordinates=start_node.coordinates,
            parent=None,
            g=0,
            h=self._heuristic(start_node, end_node),
        )
        open_list: list[_AStarNode] = [root]

        # Closed set: coordinates of expanded nodes.
        closed_coords: set[tuple[int, int]] = set()

        hop_counter = 0

        while open_list:
            # Pop the node with the lowest f value (front of sorted list).
            q = open_list.pop(0)

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

                # Skip if a better (or equal) node with the same coordinates
                # already exists in open or closed lists.
                if n_coords in closed_coords:
                    continue

                existing_in_open = next(
                    (node for node in open_list if node.coordinates == n_coords),
                    None,
                )
                if existing_in_open is not None and existing_in_open.f <= new_f:
                    continue

                # Build the new candidate node.
                candidate = _AStarNode(
                    system_name=neighbour_name,
                    coordinates=n_coords,
                    parent=q,
                    g=new_g,
                    h=new_h,
                )

                # Remove any stale entry for the same coordinates from open.
                if existing_in_open is not None:
                    open_list.remove(existing_in_open)

                # Insert in sorted order by f.
                bisect.insort(open_list, candidate)

            # Mark q as expanded.
            closed_coords.add(q.coordinates)

        flogger.warning("No route found between %s and %s", start, end)
        return PathfindingError.NO_ROUTE_FOUND
