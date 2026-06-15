"""
Unit tests for MapRenderer service.

All tests are pure computation — no database, no Docker, no async.
The real system-map.png is used (it's a project asset, always present).
"""

from __future__ import annotations

import io
import os
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Resolve the actual path to the base star-map so tests don't rely on
# the current working directory.
# ---------------------------------------------------------------------------

_MAP_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "import_data",
        "system-map.png",
    )
)

# ---------------------------------------------------------------------------
# Import the class under test.  Because src/ is on sys.path (added by the
# root conftest), we can import directly.
# ---------------------------------------------------------------------------

from src.services.map_renderer import MapRenderer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_COORDS: dict[str, tuple[int, int]] = {
    "Augmenta": (544, 592),
    "Pan": (261, 1039),
    "Nesla": (311, 274),
    "Wolf-Reiser": (945, 260),
    "Magnetar": (706, 539),
}


def _png_from_bytes(data: bytes) -> Image.Image:
    """Deserialise PNG bytes back into a Pillow Image."""
    return Image.open(io.BytesIO(data))


# ---------------------------------------------------------------------------
# Test: render_route returns valid PNG bytes
# ---------------------------------------------------------------------------


class TestRenderRouteReturnsPng:
    """render_route() must return well-formed PNG bytes."""

    def test_png_header_present(self):
        """Returned bytes start with the PNG magic number."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        route = ["Augmenta", "Magnetar", "Wolf-Reiser"]
        result = renderer.render_route(route, _SAMPLE_COORDS)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_result_is_bytes(self):
        """render_route returns bytes, not a file-like or string."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        result = renderer.render_route(["Augmenta"], _SAMPLE_COORDS)
        assert isinstance(result, bytes)

    def test_bytes_parseable_as_image(self):
        """The returned bytes can be opened by Pillow."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        route = ["Nesla", "Augmenta", "Pan"]
        result = renderer.render_route(route, _SAMPLE_COORDS)
        img = _png_from_bytes(result)
        assert img.format == "PNG"


# ---------------------------------------------------------------------------
# Test: empty route returns unmodified base map
# ---------------------------------------------------------------------------


class TestEmptyRoute:
    """An empty route must return a copy of the base map with no overlay."""

    def test_empty_route_returns_png(self):
        """render_route([]) still returns valid PNG bytes."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        result = renderer.render_route([], _SAMPLE_COORDS)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_empty_route_same_size_as_base(self):
        """The returned image has the same dimensions as the base map."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        result = renderer.render_route([], _SAMPLE_COORDS)
        img = _png_from_bytes(result)
        base = Image.open(_MAP_PATH)
        assert img.size == base.size


# ---------------------------------------------------------------------------
# Test: single-system route still works
# ---------------------------------------------------------------------------


class TestSingleSystemRoute:
    """A single-system route should draw one dot and no lines."""

    def test_single_system_returns_png(self):
        renderer = MapRenderer(map_path=_MAP_PATH)
        result = renderer.render_route(["Augmenta"], _SAMPLE_COORDS)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_single_system_correct_size(self):
        renderer = MapRenderer(map_path=_MAP_PATH)
        result = renderer.render_route(["Augmenta"], _SAMPLE_COORDS)
        img = _png_from_bytes(result)
        base = Image.open(_MAP_PATH)
        assert img.size == base.size


# ---------------------------------------------------------------------------
# Test: multi-system route draws the route (image differs from base map)
# ---------------------------------------------------------------------------


class TestMultiSystemRoute:
    """A route with 2+ known systems must produce pixels different from base."""

    def test_two_system_route_differs_from_base(self):
        """The rendered image has at least one pixel changed by the overlay."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        route = ["Augmenta", "Magnetar"]
        result = renderer.render_route(route, _SAMPLE_COORDS)

        rendered = _png_from_bytes(result).convert("RGB")
        base = Image.open(_MAP_PATH).convert("RGB")

        # At least one pixel should differ (the line or dots).
        different = any(
            rendered.getpixel((x, y)) != base.getpixel((x, y))
            for x in range(rendered.width)
            for y in range(rendered.height)
            if (x + y) % 50 == 0  # sample every 50th pixel for speed
        )
        assert different, "Rendered image should differ from base map"

    def test_full_route_returns_valid_png(self):
        renderer = MapRenderer(map_path=_MAP_PATH)
        route = ["Nesla", "Augmenta", "Magnetar", "Wolf-Reiser"]
        result = renderer.render_route(route, _SAMPLE_COORDS)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Test: base map is cached (loaded only once)
# ---------------------------------------------------------------------------


class TestBaseMapCaching:
    """The base image should be loaded from disk only on the first call."""

    def test_base_map_loaded_only_once(self):
        """Image.open is called exactly once across multiple render_route calls.

        We patch Image.open to return a real Image object (loaded first),
        so that ImageDraw.Draw still works normally.  The patch lets us
        count how many times open() is called.
        """
        # Pre-load the real image so our spy can return a genuine Image.
        real_image = Image.open(_MAP_PATH).convert("RGB")

        open_call_count = 0

        def counting_open(path):
            nonlocal open_call_count
            open_call_count += 1
            return real_image

        renderer = MapRenderer(map_path=_MAP_PATH)

        with patch("src.services.map_renderer.Image.open", side_effect=counting_open):
            renderer.render_route(["Augmenta"], _SAMPLE_COORDS)  # first: triggers open
            renderer.render_route(["Pan"], _SAMPLE_COORDS)  # second: should use cache
            renderer.render_route(["Nesla", "Augmenta"], _SAMPLE_COORDS)  # third: still cached

        # Image.open must have been called exactly once regardless of render count.
        assert open_call_count == 1, f"Expected Image.open to be called once, but it was called {open_call_count} times"


# ---------------------------------------------------------------------------
# Test: render_route_for_bounty delegates to render_route
# ---------------------------------------------------------------------------


class TestRenderRouteForBounty:
    """render_route_for_bounty extracts coords from a SystemGraphService."""

    def test_uses_coords_from_graph(self):
        """Coordinates are pulled from SystemGraphService nodes."""
        renderer = MapRenderer(map_path=_MAP_PATH)

        # Build a minimal mock graph.
        mock_graph = MagicMock()

        def _get_system(name):
            if name in _SAMPLE_COORDS:
                node = MagicMock()
                node.coordinates = _SAMPLE_COORDS[name]
                return node
            return None

        mock_graph.get_system.side_effect = _get_system

        route = ["Augmenta", "Magnetar", "Wolf-Reiser"]
        result = renderer.render_route_for_bounty(route, mock_graph)

        # Should be valid PNG.
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        # get_system should have been called for each system in the route.
        assert mock_graph.get_system.call_count == len(route)

    def test_missing_systems_skipped(self):
        """Systems not in the graph are silently skipped."""
        renderer = MapRenderer(map_path=_MAP_PATH)
        mock_graph = MagicMock()
        mock_graph.get_system.return_value = None  # nothing found

        result = renderer.render_route_for_bounty(["Unknown", "Also-Unknown"], mock_graph)
        # Should still return valid PNG (empty overlay = base map).
        assert result[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Test: API endpoint returns 200 with image/png content type
# ---------------------------------------------------------------------------


class TestBountyMapEndpoint:
    """GET /api/v1/bounties/{bounty_id}/map should return a PNG response."""

    @pytest.fixture(autouse=True)
    def _reset_map_cache(self):
        """Save and restore _map_cache so no plain-dict replacement leaks out."""
        import api.routers.bounties as bounty_module

        original = bounty_module._map_cache
        bounty_module._map_cache = OrderedDict()
        try:
            yield
        finally:
            bounty_module._map_cache = original

    def _make_app(self, mock_bounty_service, mock_renderer, mock_graph):
        """Build a test FastAPI app with dependency overrides."""
        import api.routers.bounties as bounty_module
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(bounty_module.router, prefix="/api/v1")
        app.dependency_overrides[bounty_module.get_bounty_service] = lambda: mock_bounty_service

        # Wire shared singletons on app.state (P3-T7: no module-level singletons).
        app.state.map_renderer = mock_renderer
        app.state.system_graph = mock_graph
        bounty_module._map_cache.clear()  # clear the OrderedDict (not replace it)

        return TestClient(app)

    @patch("api.routers.bounties.get_db_session")
    def test_map_endpoint_returns_200_png(self, mock_get_db):
        """Endpoint returns HTTP 200 with image/png content type."""
        from unittest.mock import AsyncMock

        # Configure the DB mock.
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock bounty.
        mock_bounty = MagicMock()
        mock_bounty.id = 1
        mock_bounty.route = ["Augmenta", "Magnetar"]

        # Mock service.
        mock_service = AsyncMock()
        mock_service.bounty_repo = AsyncMock()
        mock_service.bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        # Mock renderer — endpoint now calls render_route_offloaded (async), not render_route_for_bounty.
        _fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_renderer = MagicMock()
        mock_renderer.render_route_offloaded = AsyncMock(return_value=_fake_png)

        # Mock graph (already loaded).
        mock_graph = MagicMock()
        mock_graph.is_loaded.return_value = True

        client = self._make_app(mock_service, mock_renderer, mock_graph)
        response = client.get("/api/v1/bounties/1/map")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    @patch("api.routers.bounties.get_db_session")
    def test_map_endpoint_404_for_missing_bounty(self, mock_get_db):
        """Endpoint returns 404 when the bounty does not exist."""
        from unittest.mock import AsyncMock

        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_service = AsyncMock()
        mock_service.bounty_repo = AsyncMock()
        mock_service.bounty_repo.get_by_id = AsyncMock(return_value=None)

        mock_renderer = MagicMock()
        mock_graph = MagicMock()
        mock_graph.is_loaded.return_value = True

        client = self._make_app(mock_service, mock_renderer, mock_graph)
        response = client.get("/api/v1/bounties/999/map")

        assert response.status_code == 404
