"""
Integration tests for the textures router.

Covers:
  POST /api/v1/textures/composite
  POST /api/v1/textures/convert
  GET  /api/v1/textures/health

Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    Image.new(mode, size, (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _image_upload(filename: str = "test.png", mode: str = "RGBA") -> tuple[str, tuple]:
    """Return a (field_name, (filename, data, content_type)) tuple."""
    return ("image", (filename, _png_bytes(mode), "image/png"))


def _base_texture_upload(filename: str = "base.png", mode: str = "RGBA") -> tuple[str, tuple]:
    """Return a base_texture upload tuple."""
    return ("base_texture", (filename, _png_bytes(mode), "image/png"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def ship_dir(tmp_path: Path) -> Path:
    """Create a minimal ship asset directory with skinBase.png."""
    ship = tmp_path / "myship.bbship"
    ship.mkdir()
    # Write a valid RGBA PNG as skinBase.png
    buf = BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buf, format="PNG")
    (ship / "skinBase.png").write_bytes(buf.getvalue())
    return ship


# ===========================================================================
# GET /api/v1/textures/health
# ===========================================================================


def test_textures_health_returns_200(client: TestClient) -> None:
    """Textures health endpoint should return HTTP 200."""
    response = client.get("/api/v1/textures/health")
    assert response.status_code == 200


def test_textures_health_returns_ok(client: TestClient) -> None:
    """Textures health endpoint should return status='ok'."""
    response = client.get("/api/v1/textures/health")
    assert response.json() == {"status": "ok"}


# ===========================================================================
# POST /api/v1/textures/convert
# ===========================================================================


# --- validation errors (no conversion attempted) ---


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
    assert "quality" in response.json()["detail"].lower()


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


def test_convert_aepi_error_returns_400(client: TestClient) -> None:
    """Other AEIConversionError (not 'not available') should return HTTP 400."""
    from services.aei_conversion_service import AEIConversionError

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        side_effect=AEIConversionError("Codec failed: bad data"),
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[_image_upload()],
        )
    assert response.status_code == 400
    assert "Codec failed" in response.json()["detail"]


def test_convert_unexpected_error_returns_500(client: TestClient) -> None:
    """Unexpected exception during AEI conversion should return HTTP 500."""
    with patch(
        "routers.textures._aei_service.convert_to_aei",
        side_effect=RuntimeError("unexpected"),
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[_image_upload()],
        )
    assert response.status_code == 500
    assert "AEI conversion failed" in response.json()["detail"]


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


def test_convert_success_uses_input_filename_for_output(client: TestClient) -> None:
    """The output .aei filename should derive from the uploaded PNG filename."""
    fake_aei = BytesIO(b"\x00FAKE")
    fake_aei.seek(0)

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        return_value=fake_aei,
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[_image_upload("cool_ship.png")],
        )
    assert response.status_code == 200
    assert "cool_ship.aei" in response.headers["content-disposition"]


def test_convert_etc1_format_accepted(client: TestClient) -> None:
    """etc1 is a valid format and should not be rejected with 400."""
    fake_aei = BytesIO(b"\x00DATA")
    fake_aei.seek(0)

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        return_value=fake_aei,
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "etc1", "quality": "1"},
            files=[_image_upload()],
        )
    assert response.status_code == 200


def test_convert_dxt1_format_accepted(client: TestClient) -> None:
    """dxt1 is a valid format and should not be rejected with 400."""
    fake_aei = BytesIO(b"\x00DATA")
    fake_aei.seek(0)

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        return_value=fake_aei,
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt1", "quality": "2"},
            files=[_image_upload()],
        )
    assert response.status_code == 200


# ===========================================================================
# POST /api/v1/textures/composite
# ===========================================================================


def test_composite_missing_ship_path_returns_404(client: TestClient, tmp_path: Path) -> None:
    """composite with a non-existent ship_path must return HTTP 404."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(tmp_path / "nonexistent_ship")},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_composite_ship_path_is_file_not_dir_returns_400(client: TestClient, tmp_path: Path) -> None:
    """composite with ship_path pointing to a file (not dir) must return HTTP 400."""
    file_path = tmp_path / "somefile.txt"
    file_path.write_text("not a directory")

    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(file_path)},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"].lower()


def test_composite_missing_skin_base_returns_404(client: TestClient, tmp_path: Path) -> None:
    """composite with a ship dir that lacks skinBase.png returns HTTP 404."""
    ship = tmp_path / "ship_no_skin"
    ship.mkdir()
    # No skinBase.png here

    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship)},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 404
    assert "skinbase.png" in response.json()["detail"].lower()


def test_composite_invalid_region_indices_returns_422(client: TestClient, ship_dir: Path) -> None:
    """Non-integer region_indices value should return HTTP 422."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "region_indices": "abc"},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 422
    assert "region_indices" in response.json()["detail"].lower()


def test_composite_invalid_disabled_regions_returns_422(client: TestClient, ship_dir: Path) -> None:
    """Non-integer disabled_regions value should return HTTP 422."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "disabled_regions": "notanint"},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 422
    assert "disabled_regions" in response.json()["detail"].lower()


def test_composite_region_texture_index_mismatch_returns_422(client: TestClient, ship_dir: Path) -> None:
    """Uploading 1 region texture but providing 2 indices must return HTTP 422."""
    response = client.post(
        "/api/v1/textures/composite",
        data={
            "ship_path": str(ship_dir),
            "region_indices": "1,2",
        },
        files=[
            _base_texture_upload(),
            ("region_textures", ("region1.png", _png_bytes(), "image/png")),
            # Only one file uploaded but two indices
        ],
    )
    assert response.status_code == 422
    assert "mismatch" in response.json()["detail"].lower()


def test_composite_invalid_square_mode_returns_422(client: TestClient, ship_dir: Path) -> None:
    """An unrecognized square_mode value should return HTTP 422."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "square_mode": "invalid_mode"},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 422
    assert "square_mode" in response.json()["detail"].lower()


def test_composite_no_base_texture_or_path_returns_422(client: TestClient, ship_dir: Path) -> None:
    """composite with no base_texture upload AND no base_texture_path must return HTTP 422."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir)},
        # No base_texture file, no base_texture_path
    )
    assert response.status_code == 422
    assert "base_texture" in response.json()["detail"].lower()


def test_composite_base_texture_path_not_found_returns_404(client: TestClient, ship_dir: Path, tmp_path: Path) -> None:
    """composite with a base_texture_path that does not exist (but is within the allowed
    data dir) returns HTTP 404."""
    # Use a path under tmp_path so it passes path validation (BLENDER_DATA_ROOT=/tmp)
    # but the file itself does not exist.
    missing_texture = tmp_path / "nonexistent_texture.png"
    response = client.post(
        "/api/v1/textures/composite",
        data={
            "ship_path": str(ship_dir),
            "base_texture_path": str(missing_texture),
        },
    )
    assert response.status_code == 404
    assert "base texture file not found" in response.json()["detail"].lower()


def test_composite_success_with_upload_returns_png(client: TestClient, ship_dir: Path) -> None:
    """A valid composite request with base_texture upload should return HTTP 200 PNG."""
    fake_result = Image.new("RGB", (4, 4), (255, 0, 0))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir)},
            files=[_base_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


def test_composite_success_has_content_disposition(client: TestClient, ship_dir: Path) -> None:
    """A successful composite response should have a content-disposition header."""
    fake_result = Image.new("RGB", (4, 4), (0, 255, 0))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir)},
            files=[_base_texture_upload()],
        )
    assert response.status_code == 200
    assert "content-disposition" in response.headers
    assert "composite.png" in response.headers["content-disposition"]


def test_composite_success_from_disk_path(client: TestClient, ship_dir: Path, tmp_path: Path) -> None:
    """A valid composite request using base_texture_path (disk) should return HTTP 200."""
    # Write a real PNG to disk
    disk_texture = tmp_path / "base.png"
    buf = BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(buf, format="PNG")
    disk_texture.write_bytes(buf.getvalue())

    fake_result = Image.new("RGB", (4, 4), (0, 0, 255))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={
                "ship_path": str(ship_dir),
                "base_texture_path": str(disk_texture),
            },
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_composite_compositing_failure_returns_500(client: TestClient, ship_dir: Path) -> None:
    """When the compositing service raises an exception, the router returns HTTP 500."""
    with patch(
        "routers.textures._service.composite_textures",
        new=MagicMock(side_effect=RuntimeError("Compositing failed")),
    ):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir)},
            files=[_base_texture_upload()],
        )
    assert response.status_code == 500
    assert "compositing failed" in response.json()["detail"].lower()


def test_composite_with_crop_square_mode(client: TestClient, ship_dir: Path) -> None:
    """composite with square_mode='crop' should succeed and return a PNG."""
    fake_result = Image.new("RGB", (4, 4), (128, 128, 128))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "square_mode": "crop"},
            files=[_base_texture_upload(mode="RGB")],
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_composite_with_stretch_square_mode(client: TestClient, ship_dir: Path) -> None:
    """composite with square_mode='stretch' should succeed and return a PNG."""
    fake_result = Image.new("RGB", (4, 4), (64, 64, 64))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "square_mode": "stretch"},
            files=[_base_texture_upload(mode="RGB")],
        )
    assert response.status_code == 200


def test_composite_skin_base_corrupt_returns_400(client: TestClient, tmp_path: Path) -> None:
    """composite with a corrupt skinBase.png should return HTTP 400."""
    ship = tmp_path / "corrupt_ship"
    ship.mkdir()
    # Write invalid bytes to skinBase.png
    (ship / "skinBase.png").write_bytes(b"not a png file at all")

    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship)},
        files=[_base_texture_upload()],
    )
    assert response.status_code == 400
    assert "skinbase.png" in response.json()["detail"].lower()


def test_composite_base_texture_from_disk_corrupt_returns_400(
    client: TestClient, ship_dir: Path, tmp_path: Path
) -> None:
    """composite with a corrupt on-disk base_texture file should return HTTP 400."""
    bad_texture = tmp_path / "bad_base.png"
    bad_texture.write_bytes(b"not an image")

    response = client.post(
        "/api/v1/textures/composite",
        data={
            "ship_path": str(ship_dir),
            "base_texture_path": str(bad_texture),
        },
    )
    assert response.status_code == 400
    assert "failed to open base texture file" in response.json()["detail"].lower()


def test_composite_with_region_texture_and_mask(client: TestClient, ship_dir: Path) -> None:
    """composite with a region texture + mask index should succeed."""
    # Write a mask file to the ship dir
    buf = BytesIO()
    Image.new("L", (4, 4), 128).save(buf, format="PNG")
    (ship_dir / "mask1.png").write_bytes(buf.getvalue())

    fake_result = Image.new("RGB", (4, 4), (100, 100, 100))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "region_indices": "1"},
            files=[
                _base_texture_upload(),
                ("region_textures", ("region1.png", _png_bytes(), "image/png")),
            ],
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_composite_region_square_mode_stretch_applied(client: TestClient, ship_dir: Path) -> None:
    """A non-square region texture is squared (per region_square_modes) before compositing."""
    buf = BytesIO()
    Image.new("L", (4, 4), 128).save(buf, format="PNG")
    (ship_dir / "mask1.png").write_bytes(buf.getvalue())

    captured: dict = {}

    def fake_composite(**kwargs):
        captured.update(kwargs)
        return Image.new("RGB", (4, 4), (10, 10, 10))

    nonsquare_region = _png_bytes(size=(8, 4))  # 8x4 → stretch_to_square → 8x8
    with patch("routers.textures._service.composite_textures", side_effect=fake_composite):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "region_indices": "1", "region_square_modes": "stretch"},
            files=[
                _base_texture_upload(),
                ("region_textures", ("region1.png", nonsquare_region, "image/png")),
            ],
        )
    assert response.status_code == 200
    region_img = captured["region_textures"][1]
    assert region_img.size[0] == region_img.size[1], f"region texture must be squared; got {region_img.size}"


def test_composite_region_square_modes_length_mismatch_returns_422(client: TestClient, ship_dir: Path) -> None:
    """region_square_modes count must match region_indices count."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "region_indices": "1", "region_square_modes": "stretch,none"},
        files=[
            _base_texture_upload(),
            ("region_textures", ("region1.png", _png_bytes(), "image/png")),
        ],
    )
    assert response.status_code == 422


def test_composite_region_square_modes_invalid_value_returns_422(client: TestClient, ship_dir: Path) -> None:
    """An unknown per-region square mode is rejected."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "region_indices": "1", "region_square_modes": "warp"},
        files=[
            _base_texture_upload(),
            ("region_textures", ("region1.png", _png_bytes(), "image/png")),
        ],
    )
    assert response.status_code == 422


def test_composite_with_disabled_region(client: TestClient, ship_dir: Path) -> None:
    """composite with a disabled region should succeed."""
    # Write mask file
    buf = BytesIO()
    Image.new("L", (4, 4), 200).save(buf, format="PNG")
    (ship_dir / "mask2.png").write_bytes(buf.getvalue())

    fake_result = Image.new("RGB", (4, 4), (50, 50, 50))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "disabled_regions": "2"},
            files=[_base_texture_upload()],
        )
    assert response.status_code == 200


def test_convert_success_no_extension_filename(client: TestClient) -> None:
    """When uploaded image filename has no extension, output filename is still valid."""
    fake_aei = BytesIO(b"\x00DATA")
    fake_aei.seek(0)

    with patch(
        "routers.textures._aei_service.convert_to_aei",
        return_value=fake_aei,
    ):
        response = client.post(
            "/api/v1/textures/convert",
            data={"format": "dxt5", "quality": "3"},
            files=[("image", ("myfile", _png_bytes(), "image/png"))],
        )
    assert response.status_code == 200
    # "myfile" has no dot, so rsplit(".", 1)[0] gives "myfile"
    assert "myfile.aei" in response.headers["content-disposition"]


def test_convert_corrupt_image_returns_400(client: TestClient) -> None:
    """When the uploaded PNG image is corrupt/unreadable, return HTTP 400."""
    response = client.post(
        "/api/v1/textures/convert",
        data={"format": "dxt5", "quality": "3"},
        files=[("image", ("bad.png", b"not an image at all", "image/png"))],
    )
    assert response.status_code == 400
    assert "failed to read uploaded image" in response.json()["detail"].lower()


def test_composite_base_texture_upload_corrupt_returns_400(client: TestClient, ship_dir: Path) -> None:
    """composite with a corrupt base_texture upload should return HTTP 400."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir)},
        files=[("base_texture", ("bad.png", b"not valid image data", "image/png"))],
    )
    assert response.status_code == 400
    assert "failed to read base_texture upload" in response.json()["detail"].lower()


def test_composite_region_texture_corrupt_returns_400(client: TestClient, ship_dir: Path) -> None:
    """composite with a corrupt region_texture upload should return HTTP 400."""
    # Write a mask file so parsing continues to region_textures
    buf = BytesIO()
    Image.new("L", (4, 4), 128).save(buf, format="PNG")
    (ship_dir / "mask1.png").write_bytes(buf.getvalue())

    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": str(ship_dir), "region_indices": "1"},
        files=[
            _base_texture_upload(),
            ("region_textures", ("region1.png", b"bad image data", "image/png")),
        ],
    )
    assert response.status_code == 400
    assert "failed to read region_texture" in response.json()["detail"].lower()


def test_composite_mask_jpg_fallback(client: TestClient, ship_dir: Path) -> None:
    """composite falls back to mask{N}.jpg when mask{N}.png doesn't exist."""
    # Write mask as .jpg (not .png) so the .png fallback path is exercised
    buf = BytesIO()
    Image.new("L", (4, 4), 128).save(buf, format="JPEG")
    (ship_dir / "mask3.jpg").write_bytes(buf.getvalue())

    fake_result = Image.new("RGB", (4, 4), (90, 90, 90))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "disabled_regions": "3"},
            files=[_base_texture_upload()],
        )
    assert response.status_code == 200


def test_composite_missing_mask_file_skips_gracefully(client: TestClient, ship_dir: Path) -> None:
    """When requested mask doesn't exist, composite should succeed (warning + skip)."""
    # No mask file written — neither .png nor .jpg exists for mask 9
    fake_result = Image.new("RGB", (4, 4), (70, 70, 70))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "disabled_regions": "9"},
            files=[_base_texture_upload()],
        )
    # Should succeed with 200 — missing mask is just a warning
    assert response.status_code == 200


def test_composite_corrupt_mask_file_skips_gracefully(client: TestClient, ship_dir: Path) -> None:
    """When a mask file exists but is corrupt, composite should succeed (warning + skip)."""
    # Write a corrupt .jpg mask
    (ship_dir / "mask5.jpg").write_bytes(b"not a valid image")

    fake_result = Image.new("RGB", (4, 4), (30, 30, 30))

    with patch("routers.textures._service.composite_textures", return_value=fake_result):
        response = client.post(
            "/api/v1/textures/composite",
            data={"ship_path": str(ship_dir), "disabled_regions": "5"},
            files=[_base_texture_upload()],
        )
    # Corrupt mask is a warning — compositing still proceeds
    assert response.status_code == 200
