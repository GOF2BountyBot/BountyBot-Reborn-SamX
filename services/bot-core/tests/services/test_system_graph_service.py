"""
Unit tests for SystemGraphService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root).

Test data uses SimpleNamespace objects that mirror the System SQLAlchemy
model columns, seeded with the five systems from tests/fixtures/game_data.py
(Aquila, V'Ikka, Mido, Alda, Nesla).

Fixture system data (from game_data.get_seed_systems):
  Aquila:  coordinates=[549,131], neighbours=["Wolf-Reiser","Loma","Union"]
  V'Ikka:  coordinates=[430,522], neighbours=["Augmenta","Buntta","Magnetar","Oom'Bak","S'Kolptorr"]
  Mido:    coordinates=[226, 82], neighbours=[]
  Alda:    coordinates=[461,790], neighbours=[]
  Nesla:   coordinates=[310,205], neighbours=["Pareah","Weymire"]

Notes:
  - Aquila, V'Ikka, and Nesla have neighbours, but none of those neighbour
    names appear among the five fixture systems. So get_neighbours() returns
    [] for all of them (graph-filtered).
  - Mido and Alda are isolated (empty neighbours list).
"""

from __future__ import annotations

import asyncio
import math
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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

from services.system_graph_service import SystemGraphService, SystemNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_system(
    *,
    id: int,
    name: str,
    coordinates: list[int],
    neighbours: list[str],
    faction: str = "terran",
    security: int = 2,
    aliases: list[str] | None = None,
    wiki: str = "",
) -> SimpleNamespace:
    """Return a SimpleNamespace that mirrors System model columns."""
    return SimpleNamespace(
        id=id,
        name=name,
        aliases=aliases or [],
        coordinates=coordinates,
        neighbours=neighbours,
        faction=faction,
        security=security,
        wiki=wiki,
    )


def _seed_systems() -> list[SimpleNamespace]:
    """Five systems from game_data.get_seed_systems() as mock DB objects."""
    return [
        _make_db_system(
            id=601,
            name="Aquila",
            coordinates=[549, 131],
            neighbours=["Wolf-Reiser", "Loma", "Union"],
            faction="terran",
            security=2,
        ),
        _make_db_system(
            id=602,
            name="V'Ikka",
            coordinates=[430, 522],
            neighbours=["Augmenta", "Buntta", "Magnetar", "Oom'Bak", "S'Kolptorr"],
            faction="vossk",
            security=1,
        ),
        _make_db_system(
            id=603,
            name="Mido",
            coordinates=[226, 82],
            neighbours=[],
            faction="midorian",
            security=3,
        ),
        _make_db_system(
            id=604,
            name="Alda",
            coordinates=[461, 790],
            neighbours=[],
            faction="neutral",
            security=3,
        ),
        _make_db_system(
            id=605,
            name="Nesla",
            coordinates=[310, 205],
            neighbours=["Pareah", "Weymire"],
            faction="nivelian",
            security=2,
        ),
    ]


async def _build_loaded_service(systems: list[SimpleNamespace] | None = None) -> SystemGraphService:
    """Create a SystemGraphService with list_all mocked to return *systems*."""
    if systems is None:
        systems = _seed_systems()
    service = SystemGraphService()
    mock_db = MagicMock()
    with patch.object(service.system_repo, "list_all", new=AsyncMock(return_value=systems)):
        await service.load_graph(mock_db)
    return service


# ---------------------------------------------------------------------------
# TestLoadGraph
# ---------------------------------------------------------------------------


class TestLoadGraph:
    """Tests for SystemGraphService.load_graph()."""

    @pytest.mark.asyncio
    async def test_load_populates_graph(self):
        """Loading 5 systems produces a graph with 5 entries."""
        service = await _build_loaded_service()
        assert len(service.get_all_systems()) == 5

    @pytest.mark.asyncio
    async def test_load_sets_loaded_flag(self):
        """is_loaded() returns True after load_graph() completes."""
        service = await _build_loaded_service()
        assert service.is_loaded() is True

    @pytest.mark.asyncio
    async def test_load_is_idempotent(self):
        """A second call to load_graph() is a no-op (repo called only once)."""
        service = SystemGraphService()
        mock_db = MagicMock()
        mock_list_all = AsyncMock(return_value=_seed_systems())
        with patch.object(service.system_repo, "list_all", new=mock_list_all):
            await service.load_graph(mock_db)
            await service.load_graph(mock_db)  # second call
        assert mock_list_all.call_count == 1

    @pytest.mark.asyncio
    async def test_load_empty_db_produces_empty_graph(self):
        """Loading with no systems results in an empty graph."""
        service = await _build_loaded_service(systems=[])
        assert service.get_all_systems() == []
        assert service.is_loaded() is True

    @pytest.mark.asyncio
    async def test_reset_clears_graph(self):
        """reset() clears the graph and resets is_loaded to False."""
        service = await _build_loaded_service()
        assert service.is_loaded() is True

        service.reset()

        assert service.is_loaded() is False
        assert service.get_all_systems() == []

    @pytest.mark.asyncio
    async def test_reset_then_reload(self):
        """After reset(), load_graph() can load again."""
        service = await _build_loaded_service()
        service.reset()

        mock_db = MagicMock()
        mock_list_all = AsyncMock(return_value=_seed_systems())
        with patch.object(service.system_repo, "list_all", new=mock_list_all):
            await service.load_graph(mock_db)

        assert service.is_loaded() is True
        assert len(service.get_all_systems()) == 5

    @pytest.mark.asyncio
    async def test_load_handles_none_coordinates(self):
        """Systems with None coordinates default to (0, 0)."""
        null_coord_system = _make_db_system(id=700, name="Void", coordinates=None, neighbours=[])
        # Override coordinates attribute to None directly
        null_coord_system.coordinates = None
        service = await _build_loaded_service(systems=[null_coord_system])
        node = service.get_system("Void")
        assert node is not None
        assert node.coordinates == (0, 0)

    @pytest.mark.asyncio
    async def test_load_handles_none_neighbours(self):
        """Systems with None neighbours default to empty list."""
        null_nb_system = _make_db_system(id=701, name="Orphan", coordinates=[100, 200], neighbours=[])
        null_nb_system.neighbours = None
        service = await _build_loaded_service(systems=[null_nb_system])
        node = service.get_system("Orphan")
        assert node is not None
        assert node.neighbours == []

    @pytest.mark.asyncio
    async def test_load_handles_none_faction_and_security(self):
        """None faction defaults to '' and None security defaults to 1."""
        raw = _make_db_system(id=702, name="Unknown", coordinates=[0, 0], neighbours=[], faction="terran", security=2)
        raw.faction = None
        raw.security = None
        service = await _build_loaded_service(systems=[raw])
        node = service.get_system("Unknown")
        assert node is not None
        assert node.faction == ""
        assert node.security == 1


# ---------------------------------------------------------------------------
# TestGetSystem
# ---------------------------------------------------------------------------


class TestGetSystem:
    """Tests for SystemGraphService.get_system()."""

    @pytest.mark.asyncio
    async def test_get_existing_system_returns_node(self):
        """get_system() for a loaded system returns a SystemNode."""
        service = await _build_loaded_service()
        node = service.get_system("Aquila")
        assert node is not None
        assert isinstance(node, SystemNode)

    @pytest.mark.asyncio
    async def test_get_system_correct_name(self):
        """Returned SystemNode has the correct name."""
        service = await _build_loaded_service()
        node = service.get_system("V'Ikka")
        assert node is not None
        assert node.name == "V'Ikka"

    @pytest.mark.asyncio
    async def test_get_system_correct_coordinates(self):
        """Returned SystemNode has correct coordinates as a tuple."""
        service = await _build_loaded_service()
        node = service.get_system("Aquila")
        assert node is not None
        assert node.coordinates == (549, 131)

    @pytest.mark.asyncio
    async def test_get_system_correct_faction(self):
        """Returned SystemNode has the correct faction."""
        service = await _build_loaded_service()
        node = service.get_system("V'Ikka")
        assert node is not None
        assert node.faction == "vossk"

    @pytest.mark.asyncio
    async def test_get_system_correct_security(self):
        """Returned SystemNode has the correct security level."""
        service = await _build_loaded_service()
        node = service.get_system("Mido")
        assert node is not None
        assert node.security == 3

    @pytest.mark.asyncio
    async def test_get_nonexistent_system_returns_none(self):
        """get_system() for an unknown name returns None."""
        service = await _build_loaded_service()
        assert service.get_system("DoesNotExist") is None

    @pytest.mark.asyncio
    async def test_get_system_before_load_returns_none(self):
        """get_system() before loading always returns None."""
        service = SystemGraphService()
        assert service.get_system("Aquila") is None

    @pytest.mark.asyncio
    async def test_get_all_fixture_systems(self):
        """All five fixture systems are accessible by name."""
        service = await _build_loaded_service()
        names = ["Aquila", "V'Ikka", "Mido", "Alda", "Nesla"]
        for name in names:
            assert service.get_system(name) is not None, f"Expected {name} in graph"


# ---------------------------------------------------------------------------
# TestGetNeighbours
# ---------------------------------------------------------------------------


class TestGetNeighbours:
    """Tests for SystemGraphService.get_neighbours()."""

    @pytest.mark.asyncio
    async def test_get_neighbours_nonexistent_system_returns_empty(self):
        """get_neighbours() for an unknown system returns []."""
        service = await _build_loaded_service()
        assert service.get_neighbours("Ghost") == []

    @pytest.mark.asyncio
    async def test_get_neighbours_isolated_system_returns_empty(self):
        """Mido has no neighbours; get_neighbours returns []."""
        service = await _build_loaded_service()
        assert service.get_neighbours("Mido") == []

    @pytest.mark.asyncio
    async def test_get_neighbours_filters_out_of_graph_systems(self):
        """Aquila's neighbours (Wolf-Reiser, Loma, Union) are not in the graph.

        get_neighbours() should return [] because none are loaded.
        """
        service = await _build_loaded_service()
        result = service.get_neighbours("Aquila")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_neighbours_returns_in_graph_neighbours(self):
        """When neighbours ARE in the graph, they are returned."""
        # Build a small graph where A -> B and B is loaded
        sys_a = _make_db_system(id=1, name="Alpha", coordinates=[0, 0], neighbours=["Beta"])
        sys_b = _make_db_system(id=2, name="Beta", coordinates=[10, 0], neighbours=["Alpha"])
        service = await _build_loaded_service(systems=[sys_a, sys_b])
        assert service.get_neighbours("Alpha") == ["Beta"]
        assert service.get_neighbours("Beta") == ["Alpha"]

    @pytest.mark.asyncio
    async def test_get_neighbours_partial_filtering(self):
        """Only in-graph neighbours are returned; out-of-graph ones are dropped."""
        sys_a = _make_db_system(id=1, name="Alpha", coordinates=[0, 0], neighbours=["Beta", "Gamma", "Delta"])
        sys_b = _make_db_system(id=2, name="Beta", coordinates=[10, 0], neighbours=["Alpha"])
        # Gamma and Delta are NOT loaded
        service = await _build_loaded_service(systems=[sys_a, sys_b])
        result = service.get_neighbours("Alpha")
        assert result == ["Beta"]
        assert "Gamma" not in result
        assert "Delta" not in result

    @pytest.mark.asyncio
    async def test_get_neighbours_before_load_returns_empty(self):
        """get_neighbours() before loading always returns []."""
        service = SystemGraphService()
        assert service.get_neighbours("Aquila") == []

    @pytest.mark.asyncio
    async def test_nesla_neighbours_not_in_graph(self):
        """Nesla's neighbours (Pareah, Weymire) are not among the 5 fixture systems."""
        service = await _build_loaded_service()
        assert service.get_neighbours("Nesla") == []

    @pytest.mark.asyncio
    async def test_vikka_neighbours_not_in_graph(self):
        """V'Ikka's neighbours are none of the fixture systems."""
        service = await _build_loaded_service()
        assert service.get_neighbours("V'Ikka") == []


# ---------------------------------------------------------------------------
# TestGetSystemsWithJumpGates
# ---------------------------------------------------------------------------


class TestGetSystemsWithJumpGates:
    """Tests for SystemGraphService.get_systems_with_jump_gates()."""

    @pytest.mark.asyncio
    async def test_returns_systems_with_neighbours(self):
        """Systems with at least one neighbour entry are included."""
        service = await _build_loaded_service()
        result = service.get_systems_with_jump_gates()
        # Aquila, V'Ikka, Nesla have non-empty neighbours lists
        assert "Aquila" in result
        assert "V'Ikka" in result
        assert "Nesla" in result

    @pytest.mark.asyncio
    async def test_excludes_isolated_systems(self):
        """Systems with no neighbours are excluded."""
        service = await _build_loaded_service()
        result = service.get_systems_with_jump_gates()
        # Mido and Alda have empty neighbours
        assert "Mido" not in result
        assert "Alda" not in result

    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty_list(self):
        """Returns [] when graph is empty."""
        service = await _build_loaded_service(systems=[])
        assert service.get_systems_with_jump_gates() == []

    @pytest.mark.asyncio
    async def test_all_isolated_returns_empty(self):
        """Returns [] when all systems have no neighbours."""
        systems = [
            _make_db_system(id=1, name="Alpha", coordinates=[0, 0], neighbours=[]),
            _make_db_system(id=2, name="Beta", coordinates=[10, 0], neighbours=[]),
        ]
        service = await _build_loaded_service(systems=systems)
        assert service.get_systems_with_jump_gates() == []

    @pytest.mark.asyncio
    async def test_all_connected_returns_all(self):
        """Returns all systems when all have at least one neighbour."""
        systems = [
            _make_db_system(id=1, name="Alpha", coordinates=[0, 0], neighbours=["Beta"]),
            _make_db_system(id=2, name="Beta", coordinates=[10, 0], neighbours=["Alpha"]),
        ]
        service = await _build_loaded_service(systems=systems)
        result = service.get_systems_with_jump_gates()
        assert set(result) == {"Alpha", "Beta"}

    @pytest.mark.asyncio
    async def test_count_matches_fixture_expectation(self):
        """Exactly 3 of the 5 fixture systems have jump gates (Aquila, V'Ikka, Nesla)."""
        service = await _build_loaded_service()
        result = service.get_systems_with_jump_gates()
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestGetAllSystems
# ---------------------------------------------------------------------------


class TestGetAllSystems:
    """Tests for SystemGraphService.get_all_systems()."""

    @pytest.mark.asyncio
    async def test_returns_all_loaded_systems(self):
        """get_all_systems() returns a list of all SystemNode objects."""
        service = await _build_loaded_service()
        all_systems = service.get_all_systems()
        assert len(all_systems) == 5

    @pytest.mark.asyncio
    async def test_returns_system_nodes(self):
        """Every item in the returned list is a SystemNode."""
        service = await _build_loaded_service()
        for node in service.get_all_systems():
            assert isinstance(node, SystemNode)

    @pytest.mark.asyncio
    async def test_all_fixture_names_present(self):
        """Names of all five fixture systems appear in the result."""
        service = await _build_loaded_service()
        names = {node.name for node in service.get_all_systems()}
        assert names == {"Aquila", "V'Ikka", "Mido", "Alda", "Nesla"}

    @pytest.mark.asyncio
    async def test_returns_empty_list_before_load(self):
        """Before load_graph() is called, get_all_systems() returns []."""
        service = SystemGraphService()
        assert service.get_all_systems() == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_after_empty_load(self):
        """After loading with no systems, get_all_systems() returns []."""
        service = await _build_loaded_service(systems=[])
        assert service.get_all_systems() == []

    @pytest.mark.asyncio
    async def test_returns_copy_not_internal_state(self):
        """Mutating the returned list does not affect the internal graph."""
        service = await _build_loaded_service()
        all_systems = service.get_all_systems()
        all_systems.clear()
        # Internal graph should be unchanged
        assert len(service.get_all_systems()) == 5


# ---------------------------------------------------------------------------
# TestEuclideanDistance
# ---------------------------------------------------------------------------


class TestEuclideanDistance:
    """Tests for SystemGraphService.euclidean_distance() static method."""

    def test_pythagorean_triple_3_4_5(self):
        """Distance between (0,0) and (3,4) should be exactly 5.0."""
        node_a = SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(3, 4), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node_a, node_b) == pytest.approx(5.0)

    def test_same_system_distance_is_zero(self):
        """Distance from a system to itself is 0.0."""
        node = SystemNode(name="A", coordinates=(100, 200), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node, node) == pytest.approx(0.0)

    def test_horizontal_distance(self):
        """Horizontal distance: (0,0) to (10,0) = 10.0."""
        node_a = SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(10, 0), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node_a, node_b) == pytest.approx(10.0)

    def test_vertical_distance(self):
        """Vertical distance: (0,0) to (0,7) = 7.0."""
        node_a = SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(0, 7), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node_a, node_b) == pytest.approx(7.0)

    def test_distance_is_symmetric(self):
        """Distance from A to B equals distance from B to A."""
        node_a = SystemNode(name="A", coordinates=(549, 131), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(430, 522), neighbours=[], faction="", security=1)
        dist_ab = SystemGraphService.euclidean_distance(node_a, node_b)
        dist_ba = SystemGraphService.euclidean_distance(node_b, node_a)
        assert dist_ab == pytest.approx(dist_ba)

    def test_distance_between_fixture_systems(self):
        """Distance between Aquila (549,131) and V'Ikka (430,522) is calculable."""
        aquila = SystemNode(name="Aquila", coordinates=(549, 131), neighbours=[], faction="terran", security=2)
        vikka = SystemNode(name="V'Ikka", coordinates=(430, 522), neighbours=[], faction="vossk", security=1)
        expected = math.hypot(430 - 549, 522 - 131)
        assert SystemGraphService.euclidean_distance(aquila, vikka) == pytest.approx(expected)

    def test_distance_is_always_non_negative(self):
        """Euclidean distance is always >= 0."""
        node_a = SystemNode(name="A", coordinates=(10, 20), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(5, 5), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node_a, node_b) >= 0.0

    def test_pythagorean_triple_5_12_13(self):
        """Distance between (0,0) and (5,12) should be exactly 13.0."""
        node_a = SystemNode(name="A", coordinates=(0, 0), neighbours=[], faction="", security=1)
        node_b = SystemNode(name="B", coordinates=(5, 12), neighbours=[], faction="", security=1)
        assert SystemGraphService.euclidean_distance(node_a, node_b) == pytest.approx(13.0)

    def test_distance_with_loaded_nodes(self):
        """Distance calculation works with nodes retrieved from the graph."""
        service = SystemGraphService()
        service._graph = {
            "Aquila": SystemNode("Aquila", (549, 131), [], "terran", 2),
            "Nesla": SystemNode("Nesla", (310, 205), [], "nivelian", 2),
        }
        service._loaded = True
        aquila = service.get_system("Aquila")
        nesla = service.get_system("Nesla")
        expected = math.hypot(310 - 549, 205 - 131)
        assert SystemGraphService.euclidean_distance(aquila, nesla) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TestLoadGraphConcurrency
# ---------------------------------------------------------------------------


class TestLoadGraphConcurrency:
    """Adversarial concurrency tests for the asyncio.Lock double-checked pattern.

    Race determinism:
        The mocked list_all uses an asyncio.Event rendezvous.  The first call
        suspends at ``await started_event.wait()`` (an await point that yields
        control back to the event loop).  The test driver then sets the event
        so both callers can proceed.  Because ``list_all`` is async and awaited
        inside the lock, the second coroutine blocks at ``async with
        self._load_lock:`` until the first finishes — so it never reaches
        ``list_all`` at all.

        Without the lock (the regression path tested in
        ``test_no_lock_regression_both_enter``), both coroutines enter the build
        body and both call ``list_all``.
    """

    @pytest.mark.asyncio
    async def test_concurrent_cold_load_builds_exactly_once(self):
        """Two concurrent first-load calls result in list_all called exactly once.

        The mock uses an asyncio.Event so the first coroutine suspends inside
        the lock at a genuine await point, forcing a deterministic interleave.
        """
        service = SystemGraphService()
        mock_db = MagicMock()
        systems = _seed_systems()

        # Rendezvous: first call suspends here; second is waiting on the lock.
        proceed_event = asyncio.Event()
        call_count = 0

        async def slow_list_all(_db):
            nonlocal call_count
            call_count += 1
            # Yield control so the second coroutine can attempt to acquire the lock.
            await proceed_event.wait()
            return systems

        with patch.object(service.system_repo, "list_all", side_effect=slow_list_all):
            # Schedule both calls concurrently before awaiting either.
            task1 = asyncio.create_task(service.load_graph(mock_db))
            task2 = asyncio.create_task(service.load_graph(mock_db))

            # Let both tasks start; the first will suspend at proceed_event.wait(),
            # the second will block on the asyncio.Lock.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # Unblock the first task so it finishes the build and releases the lock.
            proceed_event.set()

            await asyncio.gather(task1, task2)

        assert call_count == 1, f"list_all called {call_count} times; expected exactly 1"
        assert service.is_loaded() is True
        assert len(service.get_all_systems()) == 5

    @pytest.mark.asyncio
    async def test_loaded_flag_set_last_graph_complete_when_visible(self):
        """When _loaded becomes True the graph is already fully populated.

        We check this by reading the graph immediately after _loaded flips to
        True inside a concurrent task — it must contain all systems.
        """
        service = SystemGraphService()
        mock_db = MagicMock()
        systems = _seed_systems()

        proceed_event = asyncio.Event()

        async def slow_list_all(_db):
            await proceed_event.wait()
            return systems

        observations: list[tuple[bool, int]] = []

        async def observing_load():
            """Load and then record (_loaded, graph_size) immediately after."""
            await service.load_graph(mock_db)
            observations.append((service._loaded, len(service._graph)))

        with patch.object(service.system_repo, "list_all", side_effect=slow_list_all):
            task1 = asyncio.create_task(observing_load())
            task2 = asyncio.create_task(observing_load())

            await asyncio.sleep(0)
            await asyncio.sleep(0)
            proceed_event.set()
            await asyncio.gather(task1, task2)

        # Both tasks finished; every observation must see a complete graph.
        assert len(observations) == 2
        for loaded_flag, graph_size in observations:
            assert loaded_flag is True
            assert graph_size == 5, f"Observed incomplete graph (size={graph_size}) when _loaded was True"

    @pytest.mark.asyncio
    async def test_subsequent_reads_are_cache_hits(self):
        """After the graph is warm, additional load_graph calls are no-ops."""
        service = SystemGraphService()
        mock_db = MagicMock()
        systems = _seed_systems()
        call_count = 0

        async def counting_list_all(_db):
            nonlocal call_count
            call_count += 1
            return systems

        with patch.object(service.system_repo, "list_all", side_effect=counting_list_all):
            # First load (cold).
            await service.load_graph(mock_db)
            # Three more calls — all should be fast-path cache hits.
            await service.load_graph(mock_db)
            await service.load_graph(mock_db)
            await service.load_graph(mock_db)

        assert call_count == 1, f"list_all called {call_count} times; expected exactly 1"

    @pytest.mark.asyncio
    async def test_no_lock_regression_both_enter(self):
        """Regression proof: without the lock, concurrent callers BOTH call list_all.

        This test temporarily monkey-patches load_graph to remove the double-
        checked lock (bare check-then-build), runs the same concurrent gather,
        and asserts that list_all IS called twice — confirming the test catches
        the regression.  The lock-guarded implementation (the real code) passes
        only because it prevents the second entry.

        Note: this test asserts the BROKEN behaviour so we can prove the test is
        a genuine regression catcher.  It is labelled ``_regression_`` so it is
        easy to identify in the report.
        """
        service = SystemGraphService()
        mock_db = MagicMock()
        systems = _seed_systems()

        proceed_event = asyncio.Event()
        call_count = 0

        async def slow_list_all(_db):
            nonlocal call_count
            call_count += 1
            await proceed_event.wait()
            return systems

        # Unguarded (bare check-then-build) version of load_graph.
        async def unguarded_load_graph(db):
            if service._loaded:
                return
            # No lock — both coroutines race past this check.
            result_systems = await service.system_repo.list_all(db)
            graph: dict = {}
            edge_count = 0
            for s in result_systems:
                neighbours = list(s.neighbours) if s.neighbours else []
                node = SystemNode(
                    name=s.name,
                    coordinates=tuple(s.coordinates) if s.coordinates else (0, 0),
                    neighbours=neighbours,
                    faction=s.faction or "",
                    security=s.security or 1,
                )
                graph[s.name] = node
                edge_count += len(neighbours)
            validated: dict = {}
            jump_gates: list = []
            for name, node in graph.items():
                valid = [n for n in node.neighbours if n in graph]
                validated[name] = valid
                if node.neighbours:
                    jump_gates.append(name)
            service._graph = graph
            service._validated_neighbours = validated
            service._jump_gate_systems = jump_gates
            service._loaded = True

        with patch.object(service.system_repo, "list_all", side_effect=slow_list_all):
            # Bind the unguarded version directly onto the instance.
            service.load_graph = unguarded_load_graph  # type: ignore[method-assign]

            task1 = asyncio.create_task(service.load_graph(mock_db))
            task2 = asyncio.create_task(service.load_graph(mock_db))

            await asyncio.sleep(0)
            await asyncio.sleep(0)
            proceed_event.set()
            await asyncio.gather(task1, task2)

        # Without the lock BOTH coroutines enter the build — call_count must be 2.
        assert call_count == 2, (
            f"Expected 2 list_all calls without the lock (got {call_count}); "
            "the race-detection test is not catching the regression"
        )

    @pytest.mark.asyncio
    async def test_many_concurrent_calls_build_exactly_once(self):
        """10 concurrent cold loads → list_all called exactly once."""
        service = SystemGraphService()
        mock_db = MagicMock()
        systems = _seed_systems()

        proceed_event = asyncio.Event()
        call_count = 0

        async def slow_list_all(_db):
            nonlocal call_count
            call_count += 1
            await proceed_event.wait()
            return systems

        with patch.object(service.system_repo, "list_all", side_effect=slow_list_all):
            tasks = [asyncio.create_task(service.load_graph(mock_db)) for _ in range(10)]
            # Give all tasks time to start and queue up on the lock.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            proceed_event.set()
            await asyncio.gather(*tasks)

        assert call_count == 1, f"list_all called {call_count} times; expected exactly 1"
        assert service.is_loaded() is True
        assert len(service.get_all_systems()) == 5
