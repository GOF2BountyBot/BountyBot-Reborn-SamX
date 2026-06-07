"""
Map Renderer Service.

Renders bounty route overlays on the star map image using Pillow.
The base map is lazy-loaded and cached; each render creates a copy
and draws coloured lines and dots for the supplied route.

CONCURRENCY MODEL (P3-T3)
--------------------------
``render_route(route, system_coords)`` is a PURE callable: it performs only
PIL pixel work, accepts plain Python data (list / dict / tuples), and can be
safely offloaded to a worker thread via ``offload_io``.

For production use the caller MUST pre-warm the renderer once on the loop
thread (via ``prewarm()`` or ``render_route_for_bounty``).  When the base image
is already in ``_base_image``, ``_load_base()`` short-circuits with zero I/O or
lock contention — making the worker execution genuinely CPU/IO-bound Pillow
work only.  If the base image is NOT pre-warmed before the first offloaded
call, ``_load_base()`` will execute on the worker thread — which is not the
intended usage (use ``prewarm()`` at startup to prevent this).

The recommended offload pattern is ``render_route_offloaded``::

    png_bytes = await renderer.render_route_offloaded(route, system_graph)

This method:
  1. Resolves ``system_coords`` on the event-loop thread (safe: read-only
     graph access, no DB).
  2. Offloads the pure PIL render to the thread pool via ``offload_io``.
"""

from __future__ import annotations

import io
import os
import threading
import time
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
from shared import bblogger

if TYPE_CHECKING:
    from services.system_graph_service import SystemGraphService

flogger = bblogger.get_logger("service-map-renderer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Absolute path to the base star-map image shipped with the service.
_DEFAULT_MAP_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "import_data",
    "system-map.png",
)

# Drawing colours (RGB tuples)
_COLOUR_LINE = (255, 0, 0)  # bright red
_COLOUR_START = (0, 255, 0)  # green
_COLOUR_END = (255, 0, 0)  # red
_COLOUR_WAYPOINT = (255, 255, 0)  # yellow

# Drawing dimensions
_LINE_WIDTH = 4
_RADIUS_START = 8
_RADIUS_END = 8
_RADIUS_WAYPOINT = 6


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _draw_circle(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    colour: tuple[int, int, int],
) -> None:
    """Draw a filled circle at *center* with the given *radius* and *colour*."""
    x, y = center
    draw.ellipse(
        [x - radius, y - radius, x + radius, y + radius],
        fill=colour,
        outline=colour,
    )


# ---------------------------------------------------------------------------
# MapRenderer
# ---------------------------------------------------------------------------


class MapRenderer:
    """Renders route overlays on the star map image.

    The base map is loaded once on first use and cached as an instance
    attribute.  Each call to :meth:`render_route` copies the cached image
    before drawing so the original is never mutated.

    Parameters
    ----------
    map_path:
        Filesystem path to the base PNG star-map image.  Defaults to the
        ``import_data/system-map.png`` file included with the service.
    """

    def __init__(self, map_path: str | None = None) -> None:
        self._map_path: str = map_path or _DEFAULT_MAP_PATH
        self._base_image: Image.Image | None = None
        self._base_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_base(self) -> Image.Image:
        """Return the cached base map, loading it from disk if necessary.

        Thread-safe: the double-checked lock ensures Image.open() is called
        at most once even if multiple threads enter concurrently on a cold
        cache.  After the first load the fast-path (outer ``if``) short-
        circuits with zero lock contention.
        """
        if self._base_image is None:
            with self._base_lock:
                # Second check inside the lock: another thread may have
                # loaded the image while we were waiting to acquire it.
                if self._base_image is None:
                    try:
                        self._base_image = Image.open(self._map_path).convert("RGB")
                        flogger.info(f"Base map loaded from {self._map_path}")
                    except Exception as e:
                        flogger.error(f"Failed to load base map from {self._map_path}: {e}")
                        raise
        return self._base_image

    # ------------------------------------------------------------------
    # Public warm API
    # ------------------------------------------------------------------

    def prewarm(self) -> None:
        """Load and cache the base map image eagerly.

        Intended to be called once on the event-loop thread during application
        startup (inside the FastAPI lifespan block) so that the image is
        already in memory before any worker threads touch the renderer.
        Calling this more than once is a no-op (the cache is already warm).
        """
        self._load_base()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_route(
        self,
        route: list[str],
        system_coords: dict[str, tuple[int, int]],
    ) -> bytes:
        """Render *route* on a copy of the base map and return PNG bytes.

        Parameters
        ----------
        route:
            Ordered list of system names forming the route.  Systems not
            present in *system_coords* are silently skipped.
        system_coords:
            Mapping of system name → ``(x, y)`` pixel coordinates on the
            base map (direct pixel positions — no transformation needed).

        Returns
        -------
        bytes
            PNG-encoded image bytes.
        """
        flogger.debug(f"render_route: {len(route)} systems, {len(system_coords)} coords provided")
        _start_time = time.monotonic()
        base = self._load_base()
        img = base.copy()

        # Fast path: nothing to draw for an empty route.
        if not route:
            flogger.debug("render_route: empty route, returning base map copy")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        draw = ImageDraw.Draw(img)

        # Build the list of systems for which we have coordinates.
        known = [s for s in route if s in system_coords]

        # Draw route lines first (so dots appear on top).
        for i in range(len(known) - 1):
            start_coord = system_coords[known[i]]
            end_coord = system_coords[known[i + 1]]
            draw.line([start_coord, end_coord], fill=_COLOUR_LINE, width=_LINE_WIDTH)

        # Draw system dots.
        for i, system in enumerate(known):
            coord = system_coords[system]
            if i == 0:
                colour = _COLOUR_START
                radius = _RADIUS_START
            elif i == len(known) - 1:
                colour = _COLOUR_END
                radius = _RADIUS_END
            else:
                colour = _COLOUR_WAYPOINT
                radius = _RADIUS_WAYPOINT
            _draw_circle(draw, coord, radius, colour)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        elapsed_ms = (time.monotonic() - _start_time) * 1000
        flogger.debug(f"render_route: completed in {elapsed_ms:.1f}ms, {len(png_bytes)} bytes")
        return png_bytes

    def render_route_for_bounty(
        self,
        route: list[str],
        system_graph: SystemGraphService,
    ) -> bytes:
        """Render *route* using coordinate data from *system_graph*.

        Looks up each system in *route* via :meth:`SystemGraphService.get_system`
        and extracts its ``coordinates`` field (pixel positions).  Systems not
        found in the graph are skipped.

        Parameters
        ----------
        route:
            Ordered list of system names.
        system_graph:
            A loaded :class:`~services.system_graph_service.SystemGraphService`
            instance to source coordinates from.

        Returns
        -------
        bytes
            PNG-encoded image bytes.
        """
        system_coords: dict[str, tuple[int, int]] = {}
        for system_name in route:
            node = system_graph.get_system(system_name)
            if node is not None:
                system_coords[system_name] = node.coordinates
        flogger.info(
            f"render_route_for_bounty: route={len(route)} systems, {len(system_coords)} coords resolved from graph"
        )
        return self.render_route(route, system_coords)

    async def render_route_offloaded(
        self,
        route: list[str],
        system_graph: SystemGraphService,
    ) -> bytes:
        """Resolve coords on the loop, then offload the pure PIL render to a worker thread.

        This is the P3-T3 offload seam.  The method has two distinct phases:

        **Phase 1 (loop thread)** — coordinate resolution:
            Iterates the pre-warmed *system_graph* in-process to build a plain
            ``dict[str, tuple[int, int]]``.  No DB, no async I/O.

        **Phase 2 (worker thread, via offload_io)** — pure PIL render:
            Calls :meth:`render_route` with the resolved coords.  Because the
            renderer is always pre-warmed before requests are served (see
            :meth:`prewarm` and the lifespan block in ``main.py``), ``_load_base``
            short-circuits on the fast-path and the worker does only PIL work.

        Parameters
        ----------
        route:
            Ordered list of system names forming the route.
        system_graph:
            A **pre-loaded** :class:`~services.system_graph_service.SystemGraphService`
            instance (graph must be warm; ``load_graph`` must NOT be called here).

        Returns
        -------
        bytes
            PNG-encoded image bytes, identical to those produced by
            :meth:`render_route_for_bounty` for the same inputs.
        """
        from utils.offload import offload_io

        # Phase 1: resolve coords on the event-loop thread.
        # Read-only access to the pre-warmed graph — no DB, no async I/O.
        system_coords: dict[str, tuple[int, int]] = {}
        for system_name in route:
            node = system_graph.get_system(system_name)
            if node is not None:
                system_coords[system_name] = node.coordinates
        flogger.info(
            f"render_route_offloaded: route={len(route)} systems, {len(system_coords)} coords resolved from graph"
        )

        # Phase 2: offload the pure PIL render to the thread pool.
        # render_route accepts only plain Python data — safe to call from any thread.
        return await offload_io(self.render_route, route, system_coords)
