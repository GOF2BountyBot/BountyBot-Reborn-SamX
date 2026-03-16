"""
Integration tests for the /textures/convert endpoint.

Uses FastAPI TestClient to exercise the POST /api/v1/textures/convert route.
Each test uses at most 2 mocks.
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure src/ is on sys.path
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(mode: str = "RGBA", size: tuple[int, int] = (4, 4)) -> bytes:
    """Return minimal valid PNG bytes."""
    buf = BytesIO()
    Image.new(mode, size, (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0)).save(
        buf, format="PNG"
    )
    return buf.getvalue()


def _image_upload(
    filename: str = "test.png", mode: str = "RGBA"
) -> tuple[str, tuple]:
    """Return a (field_name, (filename, data, content_type)) tuple."""
    return ("image", (filename, _png_bytes(mode), "image/png"))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests — validation errors (no conversion attempted)
# ---------------------------------------------------------------------------


def test_convert_missing_image_returns_422(client: TestClient) -> None:
    """Request with no image upload must return HTTP 422 (FastAPI validation)."""
    response = client.post(
        "/api/v1/textures/convert",
        data={"format": "dxt5", "quality": "3"},
        # deliberately omit the image file
    )
    assert response.status_code == 422


def test_convert_invalid_format_returns_400(client: TestClient) -> None:
    """Unsupported format value must return HTTP 400."""
    response = client.post(
        "/api/v1/textures/convert",
        data={"format": "webp", "quality": "3"},
        files=[_image_upload()],
    )
    assert response.status_code == 400
    assert "webp" in response.json()["detail"].lower() or "format" in response.json()["detail"].lower()


def test_convert_invalid_quality_returns_400(client: TestClient) -> None:
    """quality=0 must return HTTP 400."""
    response = client.post(
        "/api/v1/textures/convert",
        data={"format": "dxt5", "quality": "0"},
        files=[_image_upload()],
    )
    assert response.status_code == 400
    assert "quality" in response.json()["detail"].lower()


def test_convert_quality_too_high_returns_400(client: TestClient) -> None:
    """quality=4 must return HTTP 400."""
    response = client.post(
        "/api/v1/textures/convert",
        data={"format": "dxt5", "quality": "4"},
        files=[_image_upload()],
    )
    assert response.status_code == 400


def test_convert_aepi_unavailable_returns_422(client: TestClient) -> None:
    """When AEPi is unavailable the service raises AEIConversionError with
    'not available'; the router must return HTTP 422."""
    from services.aei_conversion_service import AEIConversionError

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        side_effect=AEIConversionError("AEPi library is not available."),
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[_image_upload()],
        )
    assert response.status_code == 422


def test_convert_success_returns_octet_stream(client: TestClient) -> None:
    """A successful conversion must return 200 with application/octet-stream."""
    fake_aei = BytesIO(b"AEimage\x00FAKE_PAYLOAD")
    fake_aei.seek(0)

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        return_value=fake_aei,
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[_image_upload("myship.png")],
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert "content-disposition" in response.headers
    assert ".aei" in response.headers["content-disposition"]
    assert response.content == b"AEimage\x00FAKE_PAYLOAD"
