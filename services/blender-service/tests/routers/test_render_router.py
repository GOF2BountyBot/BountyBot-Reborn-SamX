"""
Integration tests for the render router.

Uses FastAPI TestClient (via httpx) to test the POST /api/v1/render/ endpoint.
The RenderService.render_ship() method is mocked so Blender is never invoked.
Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure src/ is on sys.path (conftest.py handles this, but be explicit for isolation)
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    """Return a minimal valid PNG image as bytes."""
    buf = BytesIO()
    Image.new("RGB", (10, 10), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _make_texture_upload(filename: str = "texture.png") -> tuple[str, tuple]:
    """Return a (field_name, (filename, data, content_type)) tuple for multipart upload."""
    return ("texture", (filename, _png_bytes(), "image/png"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Validation error tests (400)
# ---------------------------------------------------------------------------


def test_render_validation_res_too_high(client: TestClient) -> None:
    """res_x above the 3840 limit should return HTTP 400."""
    with patch(
        "services.render_service.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 4001,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "above 2160p/4k" in response.json()["detail"]


def test_render_validation_samples_too_low(client: TestClient) -> None:
    """num_samples below the minimum (0) should return HTTP 400."""
    with patch(
        "services.render_service.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 0,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "numSamples" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Missing required field tests (422)
# ---------------------------------------------------------------------------


def test_render_missing_texture(client: TestClient) -> None:
    """A request with no texture upload should return HTTP 422."""
    response = client.post(
        "/api/v1/render/",
        data={
            "model_path": "/some/model.obj",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        # deliberately omit the texture file
    )
    assert response.status_code == 422


def test_render_missing_model_path(client: TestClient) -> None:
    """A request with no model_path form field should return HTTP 422."""
    response = client.post(
        "/api/v1/render/",
        data={
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
            # deliberately omit model_path
        },
        files=[_make_texture_upload()],
    )
    assert response.status_code == 422
