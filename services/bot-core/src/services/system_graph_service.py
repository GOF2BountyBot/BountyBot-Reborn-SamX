"""
Star System Graph Service.

Loads star systems from the database and constructs an in-memory
adjacency graph. The graph is loaded once and cached.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from persist.repositories.system_repository import SystemRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("service-system-graph")


@dataclass
class SystemNode:
    """Represents a star system in the graph."""

    name: str
    coordinates: tuple[int, int]
    neighbours: list[str]
    faction: str
    security: int


class SystemGraphService:
    """Star system graph with adjacency lookups and caching."""

    def __init__(self) -> None:
        self.system_repo = SystemRepository()
        self._graph: dict[str, SystemNode] = {}
        self._loaded = False

    async def load_graph(self, db: AsyncSession) -> None:
        """Load all systems from DB and build adjacency graph.

        Cached after first load; subsequent calls are no-ops.
        """
        if self._loaded:
            return

        systems = await self.system_repo.list_all(db)
        self._graph = {}
        for sys in systems:
            node = SystemNode(
                name=sys.name,
                coordinates=tuple(sys.coordinates) if sys.coordinates else (0, 0),
                neighbours=list(sys.neighbours) if sys.neighbours else [],
                faction=sys.faction or "",
                security=sys.security or 1,
            )
            self._graph[sys.name] = node

        self._loaded = True
        flogger.info(f"System graph loaded: {len(self._graph)} systems")

    def get_system(self, name: str) -> SystemNode | None:
        """Get a system by name. Returns None if not found."""
        return self._graph.get(name)

    def get_neighbours(self, name: str) -> list[str]:
        """Get adjacent system names present in the graph.

        Returns empty list if the system is not found or has no
        in-graph neighbours.
        """
        node = self._graph.get(name)
        if node is None:
            return []
        # Only return neighbours that actually exist in the graph
        return [n for n in node.neighbours if n in self._graph]

    def get_systems_with_jump_gates(self) -> list[str]:
        """Return names of systems that have at least one neighbour entry."""
        return [name for name, node in self._graph.items() if node.neighbours]

    def get_all_systems(self) -> list[SystemNode]:
        """Return all loaded system nodes."""
        return list(self._graph.values())

    @staticmethod
    def euclidean_distance(sys_a: SystemNode, sys_b: SystemNode) -> float:
        """Calculate Euclidean distance between two system nodes."""
        dx = sys_b.coordinates[0] - sys_a.coordinates[0]
        dy = sys_b.coordinates[1] - sys_a.coordinates[1]
        return math.sqrt(dx * dx + dy * dy)

    def is_loaded(self) -> bool:
        """Return True if the graph has been loaded from the database."""
        return self._loaded

    def reset(self) -> None:
        """Reset the graph state (useful for testing or forced reload)."""
        self._graph = {}
        self._loaded = False
