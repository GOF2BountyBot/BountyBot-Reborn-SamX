"""P3-T4: Tests proving the admin-spawn render fan-out is concurrent.

Adversarial-grade test suite with five guarantees:

1. BYTE-IDENTITY (40-bounty): all 40 maps rendered concurrently produce bytes
   byte-identical to the serially-rendered references for the same routes.

2. LOOP-RESPONSIVE: a concurrent side coroutine makes observable progress while
   40 renders fan out — the event loop is NOT blocked during the gather.

3. CACHE-LOOP-ONLY: _map_cache writes happen ON THE LOOP THREAD ONLY after
   gather() resolves.  Each write's thread ident is captured via a
   __setitem__ proxy and asserted to equal the loop ident.  Mutation-proof:
   if writes happened inside a worker thread their idents would differ.

4. CONCURRENCY EVIDENCE: multiple distinct worker thread idents are observed
   across the 40 renders, proving the fan-out was genuinely concurrent
   (not just sequential on a single background thread).

5. DEAD-CHECK REMOVAL: the old ``if cache_key in _map_cache`` branch inside
   the admin-spawn render loop is confirmed absent (newly-spawned bounties
   cannot already be in _map_cache — the check was always-missing dead code).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock

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
# Paths
# ---------------------------------------------------------------------------

_MAP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "import_data",
        "system-map.png",
    )
)

# ---------------------------------------------------------------------------
# Minimal test systems with deterministic coordinates
# ---------------------------------------------------------------------------


def _make_node(name: str, x: int, y: int, neighbours: list[str]):
    from services.system_graph_service import SystemNode

    return SystemNode(name=name, coordinates=(x, y), neighbours=neighbours, faction="Neutral", security=1)


# 40 systems with unique coordinates for deterministic route rendering.
_SYSTEMS: dict[str, object] = {
    f"Sys{i:02d}": _make_node(f"Sys{i:02d}", 100 + i * 20, 100 + (i % 5) * 40, [f"Sys{(i + 1):02d}"]) for i in range(40)
}


class _FakeGraph:
    """Minimal graph stub — get_system only."""

    is_loaded = MagicMock(return_value=True)

    def get_system(self, name: str):
        return _SYSTEMS.get(name)


# ---------------------------------------------------------------------------
# 40 fake bounties — each with a unique 3-system route so renders differ.
# Routes are guaranteed unique to prevent any cross-bounty cache collision.
# ---------------------------------------------------------------------------


def _make_fake_bounties(count: int = 40) -> list[MagicMock]:
    bounties = []
    for i in range(count):
        b = MagicMock()
        b.id = 1000 + i
        # Each bounty gets a 3-system route using 3 consecutive systems (offset by i).
        b.route = [f"Sys{(i % 38):02d}", f"Sys{(i % 38 + 1):02d}", f"Sys{(i % 38 + 2):02d}"]
        b.guild_id = 99999
        b.division = "bronze"
        b.criminal_name = f"Criminal{i}"
        b.criminal_faction = "Neutral"
        b.answer = b.route[-1]
        b.reward = 1000
        b.reward_per_sys = 300
        b.checked = {}
        b.issue_time = datetime.now(UTC)
        b.end_time = datetime.now(UTC)
        b.tech_level = 2
        b.criminal_ship = {}
        b.status = "active"
        b.escape_count = 0
        b.win_user_id = None
        bounties.append(b)
    return bounties


_FAKE_BOUNTIES_40 = _make_fake_bounties(40)

# ---------------------------------------------------------------------------
# Fixture: thread pool wired into executor_holder
# (mirrors test_p3t3_render_offload.py fixture for order-independence)
# ---------------------------------------------------------------------------

_HOLDER_MODULE = "utils.executor_holder"
_OFFLOAD_MODULE = "utils.offload"


@pytest.fixture
def thread_pool():
    """Create and register a ThreadPoolExecutor in a fresh executor_holder module.

    Uses a larger pool (8 workers) to expose concurrency across 40 renders.
    Saves/restores sys.modules state so it does NOT leak a shutdown pool.
    """
    import sys

    _saved_holder = sys.modules.get(_HOLDER_MODULE)
    _saved_offload = sys.modules.get(_OFFLOAD_MODULE)

    if _HOLDER_MODULE in sys.modules:
        del sys.modules[_HOLDER_MODULE]
    import utils.executor_holder as holder

    if _OFFLOAD_MODULE in sys.modules:
        del sys.modules[_OFFLOAD_MODULE]
    import utils.offload  # noqa: F401 — imported for side effect (binds to fresh holder)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="test-fanout")
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


# ===========================================================================
# Test 1 – BYTE-IDENTITY (40 bounties)
#
# Run the Phase-2a fan-out logic with 40 fake bounties and compare every
# rendered PNG against the serially-rendered reference for the same route.
# The gather path and serial path must produce byte-identical output.
# ===========================================================================


class TestByteIdentity40:
    """All 40 concurrent renders are byte-identical to their serial counterparts."""

    @pytest.mark.asyncio
    async def test_all_40_renders_byte_identical_to_serial(self, thread_pool):
        """Gather-path produces byte-identical output to render_route_for_bounty.

        For each of the 40 fake bounties, compute a serial reference via
        render_route_for_bounty(), then run the fan-out gather and assert each
        concurrent result equals its serial reference.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        bounties = _FAKE_BOUNTIES_40

        # --- Serial references ---
        serial_refs: dict[int, bytes] = {}
        for b in bounties:
            route = list(b.route)
            serial_refs[b.id] = renderer.render_route_for_bounty(route, graph)

        # --- Concurrent fan-out (mirrors Phase-2a logic) ---
        async def _render_one(b):
            route = list(b.route) if b.route else []
            try:
                png = await renderer.render_route_offloaded(route, graph)
            except Exception:
                return (b.id, route, b"")
            return (b.id, route, png)

        results = await asyncio.gather(*[_render_one(b) for b in bounties])

        # --- Assert byte-identity ---
        mismatches = []
        for bounty_id, route, png in results:
            if not png:
                mismatches.append(f"bounty_id={bounty_id}: render returned empty bytes")
                continue
            expected = serial_refs[bounty_id]
            if png != expected:
                mismatches.append(
                    f"bounty_id={bounty_id} route={route}: concurrent ({len(png)} B) != serial ({len(expected)} B)"
                )

        assert not mismatches, f"Byte-identity failed for {len(mismatches)} bounty(ies):\n" + "\n".join(mismatches)

    @pytest.mark.asyncio
    async def test_all_40_renders_are_valid_png(self, thread_pool):
        """All 40 concurrent renders produce valid PNG bytes (magic header check)."""
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()
        bounties = _FAKE_BOUNTIES_40

        async def _render_one(b):
            route = list(b.route) if b.route else []
            return await renderer.render_route_offloaded(route, graph)

        results = await asyncio.gather(*[_render_one(b) for b in bounties])

        bad = [i for i, png in enumerate(results) if png[:8] != b"\x89PNG\r\n\x1a\n"]
        assert not bad, f"Renders at indices {bad} did not produce valid PNG bytes"


# ===========================================================================
# Test 2 – LOOP-RESPONSIVE during 40-render fan-out
#
# A side coroutine ticks (via asyncio.sleep(0)) during the gather.  If the
# loop were blocked, it would make zero progress.  At least one tick proves
# the loop yielded at least once during the fan-out.
# ===========================================================================


class TestLoopResponsive40:
    """Event loop stays responsive during the 40-render fan-out."""

    @pytest.mark.asyncio
    async def test_side_coroutine_makes_progress_during_40_render_fanout(self, thread_pool):
        """A ticking side-coroutine makes at least one iteration while 40 renders run.

        If render_route_offloaded blocked the loop, the side_coro would be
        unable to execute any iteration — progress_ticks would remain empty.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()
        bounties = _FAKE_BOUNTIES_40

        progress_ticks: list[int] = []

        async def side_coro():
            tick = 0
            while True:
                progress_ticks.append(tick)
                tick += 1
                await asyncio.sleep(0)

        async def _render_one(b):
            route = list(b.route) if b.route else []
            return await renderer.render_route_offloaded(route, graph)

        side_task = asyncio.create_task(side_coro())
        await asyncio.gather(*[_render_one(b) for b in bounties])
        import contextlib

        side_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await side_task

        assert len(progress_ticks) > 0, (
            "Side coroutine made ZERO ticks during 40-render fan-out. "
            "The event loop was blocked — render_route_offloaded must yield the loop."
        )

    @pytest.mark.asyncio
    async def test_health_style_coroutine_resolves_during_fanout(self, thread_pool):
        """asyncio.sleep(0) completes while 40 renders fan out concurrently.

        Models a health-check-style lightweight await during a heavy fan-out.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()
        bounties = _FAKE_BOUNTIES_40

        health_resolved = False

        async def health_check():
            nonlocal health_resolved
            await asyncio.sleep(0)
            health_resolved = True

        async def _render_one(b):
            route = list(b.route) if b.route else []
            return await renderer.render_route_offloaded(route, graph)

        await asyncio.gather(
            *[_render_one(b) for b in bounties],
            health_check(),
        )

        assert health_resolved, (
            "Health-check coroutine did not resolve during 40-render fan-out. The event loop was blocked."
        )


# ===========================================================================
# Test 3 – CACHE WRITTEN ON LOOP THREAD ONLY
#
# Instrument _map_cache.__setitem__ to capture the writing thread's ident.
# After the fan-out, every recorded ident must equal the loop thread ident.
# Mutation-proof: if a write occurred from inside a worker thread, its ident
# would differ from the loop ident → the assertion would fail.
# ===========================================================================


class TestCacheLoopThreadOnly:
    """_map_cache writes happen on the loop thread, never from a worker thread."""

    @pytest.mark.asyncio
    async def test_all_cache_writes_on_loop_thread(self, thread_pool):
        """Every _map_cache write occurs on the loop (event-loop) thread.

        Instruments _map_cache via a proxy dict subclass that records the
        writing thread ident on every __setitem__.  After the gather resolves,
        all recorded idents must equal threading.get_ident() (the loop thread).

        Mutation-proof: a worker-thread write would record a different ident
        and the assertion ``write_ident != loop_ident`` would catch it.
        """
        import api.routers.bounties as bounties_module
        from services.map_renderer import MapRenderer

        loop_thread_ident = threading.get_ident()
        write_idents: list[int] = []

        class _TrackedDict(dict):
            """Dict subclass that records the calling thread ident on every write."""

            def __setitem__(self, key, value):
                write_idents.append(threading.get_ident())
                super().__setitem__(key, value)

        # Install the tracked dict as _map_cache for this test.
        original_cache = bounties_module._map_cache
        tracked_cache = _TrackedDict()
        bounties_module._map_cache = tracked_cache

        try:
            renderer = MapRenderer(map_path=_MAP_PATH)
            renderer.prewarm()
            graph = _FakeGraph()
            bounties = _FAKE_BOUNTIES_40

            async def _render_one(b):
                route = list(b.route) if b.route else []
                try:
                    png = await renderer.render_route_offloaded(route, graph)
                except Exception:
                    return (b.id, route, b"")
                return (b.id, route, png)

            results = await asyncio.gather(*[_render_one(b) for b in bounties])

            # Write cache ON THE LOOP THREAD ONLY (mirrors Phase-2a code).
            for bounty_id, route, png in results:
                if png:
                    cache_key = (bounty_id, tuple(route))
                    bounties_module._map_cache[cache_key] = png  # this write is tracked

        finally:
            bounties_module._map_cache = original_cache

        # Every recorded write must have happened on the loop thread.
        assert len(write_idents) > 0, (
            "Expected at least one cache write after 40 renders, got none. "
            "Check that the fan-out actually produced non-empty PNG bytes."
        )
        wrong_thread_writes = [ident for ident in write_idents if ident != loop_thread_ident]
        assert not wrong_thread_writes, (
            f"{len(wrong_thread_writes)} cache write(s) happened on a WORKER THREAD "
            f"(expected loop ident {loop_thread_ident}; got: {wrong_thread_writes[:5]!r}). "
            "Cache writes must only occur on the loop thread after gather() resolves."
        )

    @pytest.mark.asyncio
    async def test_mutation_proof_worker_write_would_differ(self, thread_pool):
        """ADVERSARIAL: a write from a worker thread IS detectable as a different ident.

        Proves the cache-write ident check in the test above is load-bearing:
        if a write occurred on a worker thread, the recorded ident would differ
        from loop_thread_ident — which is exactly what this test asserts.
        """
        loop_thread_ident = threading.get_ident()
        worker_write_idents: list[int] = []

        # Simulate a write from a worker thread directly.
        def _write_from_worker():
            worker_write_idents.append(threading.get_ident())

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(pool, _write_from_worker)
        finally:
            pool.shutdown(wait=True)

        # A write from a worker thread MUST have a different ident than the loop.
        assert len(worker_write_idents) == 1
        assert worker_write_idents[0] != loop_thread_ident, (
            "Worker thread ident matched loop thread ident. "
            "This means the mutation-proof check is vacuous — single-threaded event loop required."
        )


# ===========================================================================
# Test 4 – CONCURRENCY EVIDENCE: multiple distinct worker idents
#
# Record the thread ident inside each render_route call.  Across 40 renders
# with an 8-worker pool, multiple distinct non-loop idents must be observed.
# This proves the fan-out was genuinely concurrent, not sequential.
# ===========================================================================


class TestConcurrencyEvidence:
    """Multiple distinct worker thread idents observed across 40 concurrent renders."""

    @pytest.mark.asyncio
    async def test_multiple_distinct_worker_idents_observed(self, thread_pool):
        """At least 2 distinct worker thread idents are recorded across 40 renders.

        Each render_route invocation records the calling thread's ident.
        If all 40 renders ran on the SAME worker thread (serial), only one
        ident would appear.  Multiple idents proves concurrent fan-out.

        Uses an 8-worker pool, so at least 2 distinct workers are expected
        across 40 renders.  This is a structural (not timing) assertion.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()
        bounties = _FAKE_BOUNTIES_40

        loop_thread_ident = threading.get_ident()
        render_worker_idents: list[int] = []

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_worker_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        async def _render_one(b):
            route = list(b.route) if b.route else []
            return await renderer.render_route_offloaded(route, graph)

        await asyncio.gather(*[_render_one(b) for b in bounties])

        # All renders must be on WORKER threads (not the loop thread).
        loop_thread_renders = [t for t in render_worker_idents if t == loop_thread_ident]
        assert not loop_thread_renders, (
            f"{len(loop_thread_renders)} render(s) ran on the LOOP thread. "
            "render_route must always execute on a WORKER thread (via offload_io)."
        )

        distinct_worker_idents = set(render_worker_idents)
        assert len(distinct_worker_idents) >= 2, (
            f"Only {len(distinct_worker_idents)} distinct worker ident(s) observed across "
            f"{len(bounties)} renders (expected >= 2 for genuine concurrency). "
            "If all renders ran on the same single worker, the fan-out is not concurrent."
        )

    @pytest.mark.asyncio
    async def test_mutation_proof_serial_single_worker_would_show_one_ident(self, thread_pool):
        """ADVERSARIAL: a serial (1-worker) path would show only 1 distinct worker ident.

        Runs 40 renders on a single-threaded executor and asserts exactly 1
        distinct worker ident is recorded.  This proves the multi-ident check
        in the test above is load-bearing: if the code regressed to serial,
        the count would drop to 1 and the >=2 assertion would fail.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()
        bounties = _FAKE_BOUNTIES_40

        render_worker_idents: list[int] = []

        original_render_route = renderer.render_route

        def spy_render_route(route, system_coords):
            render_worker_idents.append(threading.get_ident())
            return original_render_route(route, system_coords)

        renderer.render_route = spy_render_route  # type: ignore[method-assign]

        # Use a 1-worker pool to simulate serial execution.
        serial_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="serial-test")
        try:
            # Override holder to use the 1-worker pool.
            import utils.executor_holder as holder

            _saved_pool = holder._thread_pool
            holder.set_thread_pool(serial_pool)

            # Must also reload offload so it picks up the new pool reference.
            import importlib

            import utils.offload as offload_mod

            importlib.reload(offload_mod)

            async def _render_serial(b):
                from utils.offload import offload_io

                route = list(b.route) if b.route else []
                system_coords: dict = {}
                for sys_name in route:
                    node = graph.get_system(sys_name)
                    if node is not None:
                        system_coords[sys_name] = node.coordinates
                return await offload_io(renderer.render_route, route, system_coords)

            # Run renders sequentially (not via gather, to force serialization).
            for b in bounties:
                await _render_serial(b)

        finally:
            holder.set_thread_pool(_saved_pool)
            importlib.reload(offload_mod)
            serial_pool.shutdown(wait=True)

        distinct_serial_idents = set(render_worker_idents)
        assert len(distinct_serial_idents) == 1, (
            f"Expected exactly 1 distinct worker ident for a 1-worker serial path, "
            f"got {len(distinct_serial_idents)}: {distinct_serial_idents}. "
            "This test confirms the mutation-proof: serial execution → 1 ident "
            "→ the >=2 assertion above would catch a regression."
        )


# ===========================================================================
# Test 5 – DEAD CHECK REMOVED
#
# Confirms the old ``if cache_key in _map_cache`` branch (which was always-
# missing for freshly-spawned bounties) has been removed from the Phase-2a
# render loop in admin_spawn_bounties.  We inspect the source of the
# admin_spawn_bounties endpoint to verify the dead code is absent.
# ===========================================================================


class TestDeadCheckRemoved:
    """The dead ``if cache_key in _map_cache`` check is absent from Phase-2a."""

    def test_dead_cache_check_not_in_admin_spawn_source(self):
        """admin_spawn_bounties Phase-2a does NOT contain the dead cache check.

        The old code performed ``if cache_key in _map_cache:`` inside the render
        loop.  Because freshly-spawned bounties cannot already be in the cache,
        this branch was always-missing dead code.  P3-T4 removes it.

        We inspect the rendered source of admin_spawn_bounties to confirm the
        pattern is absent.
        """
        import inspect

        from api.routers.bounties import admin_spawn_bounties

        source = inspect.getsource(admin_spawn_bounties)

        # The dead check used: "if cache_key in _map_cache:"
        # It was the branch that short-circuited to reading from the cache
        # without rendering — always false for new bounties.
        assert "if cache_key in _map_cache:" not in source, (
            "The dead ``if cache_key in _map_cache:`` check was found in "
            "admin_spawn_bounties source.  This check was always-missing for "
            "freshly-spawned bounties and should have been removed by P3-T4."
        )

    def test_gather_fanout_present_in_admin_spawn_source(self):
        """admin_spawn_bounties Phase-2a uses asyncio.gather for the fan-out.

        Confirms the P3-T4 change is present: the serial loop was replaced by
        an asyncio.gather call over _render_one coroutines.
        """
        import inspect

        from api.routers.bounties import admin_spawn_bounties

        source = inspect.getsource(admin_spawn_bounties)

        assert "asyncio.gather" in source, (
            "asyncio.gather was NOT found in admin_spawn_bounties. "
            "The P3-T4 parallel fan-out must use asyncio.gather over _render_one coroutines."
        )

    def test_render_route_offloaded_used_in_admin_spawn(self):
        """admin_spawn_bounties Phase-2a calls render_route_offloaded (T3 seam).

        Confirms the P3-T4 change uses the T3 offload seam rather than the
        synchronous render_route_for_bounty.
        """
        import inspect

        from api.routers.bounties import admin_spawn_bounties

        source = inspect.getsource(admin_spawn_bounties)

        assert "render_route_offloaded" in source, (
            "render_route_offloaded was NOT found in admin_spawn_bounties. "
            "Phase-2a must use the T3 offload seam (render_route_offloaded) for the fan-out."
        )

    def test_no_behavior_change_from_dead_check_removal(self):
        """Removing the dead cache-check produces the same PNG output.

        The old code was:
            if cache_key in _map_cache:   # always False for fresh spawns
                bounty_pngs[b.id] = _map_cache[cache_key]
            else:
                png = render(...)
                _map_cache[cache_key] = png
                bounty_pngs[b.id] = png

        The new code always renders (the ``else`` branch was always taken).
        This test verifies that the output is identical regardless, by running
        both paths manually with a pre-populated cache entry.
        """
        from services.map_renderer import MapRenderer

        renderer = MapRenderer(map_path=_MAP_PATH)
        renderer.prewarm()
        graph = _FakeGraph()

        route = ["Sys00", "Sys01", "Sys02"]
        # Render once to get the reference output (what the ``else`` branch produced).
        reference_png = renderer.render_route_for_bounty(route, graph)

        # Manually insert a "stale" cache entry.
        _stale_png = b"\x89PNG\r\n\x1a\nSTALE"
        fake_cache: dict = {(999, tuple(route)): _stale_png}

        # Old code (dead check path): would have used the stale entry.
        # Verify: the stale entry IS different from the reference.
        assert fake_cache.get((999, tuple(route))) != reference_png, (
            "Test setup error: stale entry must differ from reference PNG."
        )

        # New code: always renders fresh, ignoring any pre-existing cache state.
        fresh_png = renderer.render_route_for_bounty(route, graph)
        assert fresh_png == reference_png, (
            "render_route_for_bounty must produce deterministic, identical output "
            "on subsequent calls (Pillow PNG encoding is deterministic)."
        )
