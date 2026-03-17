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
        # Pre-computed caches — built once in load_graph, invalidated by reset.
        self._validated_neighbours: dict[str, list[str]] = {}
        self._jump_gate_systems: list[str] = []
        flogger.debug("SystemGraphService initialized")

    async def load_graph(self, db: AsyncSession) -> None:
        """Load all systems from DB and build adjacency graph.

        Cached after first load; subsequent calls are no-ops.
        """
        if self._loaded:
            flogger.debug("System graph cache hit — already loaded, skipping DB load")
            return

        flogger.debug("System graph cache miss — loading from database")
        try:
            systems = await self.system_repo.list_all(db)
            flogger.debug(f"Retrieved {len(systems)} systems from database")
        except Exception as e:
            flogger.error(f"Failed to load system graph from database: {e}")
            raise

        self._graph = {}
        edge_count = 0
        for sys in systems:
            neighbours = list(sys.neighbours) if sys.neighbours else []
            node = SystemNode(
                name=sys.name,
                coordinates=tuple(sys.coordinates) if sys.coordinates else (0, 0),
                neighbours=neighbours,
                faction=sys.faction or "",
                security=sys.security or 1,
            )
            self._graph[sys.name] = node
            edge_count += len(neighbours)
            flogger.trace(f"Loaded system '{sys.name}' with {len(neighbours)} connections")

        # Pre-compute validated neighbours (only those present in graph)
        # and jump-gate system list so hot-path lookups avoid repeated work.
        self._validated_neighbours = {}
        jump_gates: list[str] = []
        for name, node in self._graph.items():
            valid = [n for n in node.neighbours if n in self._graph]
            self._validated_neighbours[name] = valid
            if node.neighbours:
                jump_gates.append(name)
            flogger.trace(f"Validated neighbours for '{name}': {len(valid)} valid out of {len(node.neighbours)}")
        self._jump_gate_systems = jump_gates

        self._loaded = True
        flogger.info(f"System graph loaded: {len(self._graph)} systems, {edge_count} edges, "
                     f"{len(self._jump_gate_systems)} jump-gate systems")

    def get_system(self, name: str) -> SystemNode | None:
        """Get a system by name. Returns None if not found."""
        result = self._graph.get(name)
        if result:
            flogger.debug(f"Retrieved system: {name}")
        else:
            flogger.debug(f"System not found: {name}")
        return result

    def get_neighbours(self, name: str) -> list[str]:
        """Get adjacent system names present in the graph.

        Returns empty list if the system is not found or has no
        in-graph neighbours.  Uses a pre-computed cache built at load
        time for O(1) lookup instead of per-call filtering.
        """
        cache = getattr(self, "_validated_neighbours", None)
        if cache:
            neighbours = cache.get(name, [])
            flogger.debug(f"Retrieved {len(neighbours)} neighbours for system '{name}' from cache")
            return neighbours
        # Fallback for instances created without load_graph (e.g. tests).
        node = self._graph.get(name)
        if node is None:
            flogger.debug(f"System '{name}' not found in graph; returning empty neighbour list")
            return []
        neighbours = [n for n in node.neighbours if n in self._graph]
        flogger.debug(f"Retrieved {len(neighbours)} neighbours for system '{name}' (fallback mode)")
        return neighbours

    def get_systems_with_jump_gates(self) -> list[str]:
        """Return names of systems that have at least one neighbour entry.

        Uses a pre-computed list built at load time.
        """
        cache = getattr(self, "_jump_gate_systems", None)
        if cache:
            result = list(cache)
            flogger.debug(f"Retrieved {len(result)} jump-gate systems from cache")
            return result
        # Fallback for instances created without load_graph (e.g. tests).
        result = [name for name, node in self._graph.items() if node.neighbours]
        flogger.debug(f"Retrieved {len(result)} jump-gate systems (fallback mode)")
        return result

    def get_all_systems(self) -> list[SystemNode]:
        """Return all loaded system nodes."""
        systems = list(self._graph.values())
        flogger.debug(f"Retrieved all {len(systems)} system nodes")
        return systems

    @staticmethod
    def euclidean_distance(sys_a: SystemNode, sys_b: SystemNode) -> float:
        """Calculate Euclidean distance between two system nodes."""
        dx = sys_b.coordinates[0] - sys_a.coordinates[0]
        dy = sys_b.coordinates[1] - sys_a.coordinates[1]
        return math.sqrt(dx * dx + dy * dy)

    def is_loaded(self) -> bool:
        """Return True if the graph has been loaded from the database."""
        flogger.trace(f"Graph loaded status: {self._loaded}")
        return self._loaded

    def reset(self) -> None:
        """Reset the graph state (useful for testing or forced reload)."""
        flogger.info("Resetting system graph state")
        self._graph = {}
        self._validated_neighbours = {}
        self._jump_gate_systems = []
        self._loaded = False
        flogger.debug("System graph reset complete")
