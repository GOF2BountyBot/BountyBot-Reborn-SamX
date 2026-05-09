"""
Tests for the safe_path utility module.

Covers:
  - safe_join_http: traversal rejection, absolute path rejection, valid paths,
    empty input, null bytes
  - safe_join: same as above but raises ValueError instead of HTTPException
  - validate_user_path_http: rejects paths outside BLENDER_DATA_ROOT
  - validate_user_path: same, raises ValueError
  - Integration: POST /api/v1/textures/composite with traversal paths → 400
  - Integration: POST /api/v1/render/ with traversal model_path → 400

Each test uses at most 2 mocks (project standard).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import app
from PIL import Image
from utils.safe_path import safe_join, safe_join_http, validate_user_path, validate_user_path_http

# ===========================================================================
# safe_join_http — HTTP variant
# ===========================================================================


def test_safe_join_http_valid_relative_path(tmp_path: Path) -> None:
    """A simple relative path inside base should be accepted."""
    result = safe_join_http(tmp_path, "subdir/file.txt")
    assert result == (tmp_path / "subdir/file.txt").resolve()


def test_safe_join_http_dotdot_traversal_rejected(tmp_path: Path) -> None:
    """A path containing '../' that escapes base must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        safe_join_http(tmp_path, "../escape.txt")
    assert exc_info.value.status_code == 400
    assert "escapes" in exc_info.value.detail


def test_safe_join_http_deep_traversal_rejected(tmp_path: Path) -> None:
    """Multiple '../' sequences that escape base must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        safe_join_http(tmp_path, "a/b/../../../../../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_safe_join_http_absolute_path_outside_base_rejected(tmp_path: Path) -> None:
    """An absolute path that resolves outside base must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        safe_join_http(tmp_path, "/etc/passwd")
    assert exc_info.value.status_code == 400
    assert "escapes" in exc_info.value.detail


def test_safe_join_http_absolute_path_inside_base_accepted(tmp_path: Path) -> None:
    """An absolute path that resolves inside base must be accepted."""
    # Create a subdirectory so the absolute path is within base
    sub = tmp_path / "allowed"
    sub.mkdir()
    result = safe_join_http(tmp_path, str(sub / "file.txt"))
    assert result.is_relative_to(tmp_path.resolve())


def test_safe_join_http_empty_input_rejected(tmp_path: Path) -> None:
    """An empty user_input must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        safe_join_http(tmp_path, "")
    assert exc_info.value.status_code == 400
    assert "empty" in exc_info.value.detail


def test_safe_join_http_null_byte_rejected(tmp_path: Path) -> None:
    """A path containing a null byte must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        safe_join_http(tmp_path, "valid\x00injected")
    assert exc_info.value.status_code == 400
    assert "null" in exc_info.value.detail


def test_safe_join_http_valid_path_stays_within_base(tmp_path: Path) -> None:
    """The resolved result must be a descendant of base."""
    result = safe_join_http(tmp_path, "a/b/c/file.obj")
    assert result.is_relative_to(tmp_path.resolve())


# ===========================================================================
# safe_join — ValueError variant
# ===========================================================================


def test_safe_join_valid_relative_path(tmp_path: Path) -> None:
    """A valid relative path inside base should return the resolved Path."""
    result = safe_join(tmp_path, "ships/foo.obj")
    assert result == (tmp_path / "ships/foo.obj").resolve()


def test_safe_join_dotdot_traversal_rejected(tmp_path: Path) -> None:
    """'../'-based traversal outside base must raise ValueError."""
    with pytest.raises(ValueError, match="escapes"):
        safe_join(tmp_path, "../outside.txt")


def test_safe_join_absolute_outside_base_rejected(tmp_path: Path) -> None:
    """Absolute path outside base must raise ValueError."""
    with pytest.raises(ValueError, match="escapes"):
        safe_join(tmp_path, "/etc/shadow")


def test_safe_join_empty_input_raises(tmp_path: Path) -> None:
    """Empty user_input must raise ValueError."""
    with pytest.raises(ValueError, match="empty"):
        safe_join(tmp_path, "")


def test_safe_join_null_byte_raises(tmp_path: Path) -> None:
    """Null byte in user_input must raise ValueError."""
    with pytest.raises(ValueError, match="null"):
        safe_join(tmp_path, "foo\x00bar")


def test_safe_join_result_within_base(tmp_path: Path) -> None:
    """Resolved result must be a descendant of the given base."""
    result = safe_join(tmp_path, "nested/path/file.png")
    assert result.is_relative_to(tmp_path.resolve())


# ===========================================================================
# validate_user_path_http — absolute path variant (uses BLENDER_DATA_ROOT)
# ===========================================================================


def test_validate_user_path_http_valid_path(tmp_path: Path) -> None:
    """A path within BLENDER_DATA_ROOT (/tmp in tests) should be accepted."""
    result = validate_user_path_http(str(tmp_path / "game-objects/ship.obj"))
    assert result.is_relative_to(Path("/tmp").resolve())


def test_validate_user_path_http_outside_root_rejected() -> None:
    """A path outside BLENDER_DATA_ROOT must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        validate_user_path_http("/etc/passwd")
    assert exc_info.value.status_code == 400
    assert "allowed" in exc_info.value.detail


def test_validate_user_path_http_empty_rejected() -> None:
    """Empty input must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        validate_user_path_http("")
    assert exc_info.value.status_code == 400
    assert "empty" in exc_info.value.detail


def test_validate_user_path_http_null_byte_rejected() -> None:
    """Null byte in path must raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        validate_user_path_http("/tmp/valid\x00evil")
    assert exc_info.value.status_code == 400
    assert "null" in exc_info.value.detail


def test_validate_user_path_http_traversal_rejected() -> None:
    """A path that traverses out of BLENDER_DATA_ROOT must be rejected."""
    # /tmp/../etc/passwd resolves to /etc/passwd which is outside /tmp
    with pytest.raises(HTTPException) as exc_info:
        validate_user_path_http("/tmp/../etc/passwd")
    assert exc_info.value.status_code == 400


# ===========================================================================
# validate_user_path — ValueError variant
# ===========================================================================


def test_validate_user_path_valid(tmp_path: Path) -> None:
    """A path within BLENDER_DATA_ROOT should return the resolved Path."""
    result = validate_user_path(str(tmp_path / "foo.obj"))
    assert result.is_relative_to(Path("/tmp").resolve())


def test_validate_user_path_outside_root_raises() -> None:
    """A path outside BLENDER_DATA_ROOT must raise ValueError."""
    with pytest.raises(ValueError, match="allowed"):
        validate_user_path("/etc/passwd")


def test_validate_user_path_empty_raises() -> None:
    """Empty input must raise ValueError."""
    with pytest.raises(ValueError, match="empty"):
        validate_user_path("")


def test_validate_user_path_null_byte_raises() -> None:
    """Null byte in path must raise ValueError."""
    with pytest.raises(ValueError, match="null"):
        validate_user_path("/tmp/foo\x00bar")


# ===========================================================================
# Integration: POST /api/v1/textures/composite — path traversal → 400
# ===========================================================================


def _png_bytes(mode: str = "RGBA", size: tuple[int, int] = (4, 4)) -> bytes:
    """Return minimal valid PNG bytes."""
    buf = BytesIO()
    Image.new(mode, size, (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


def test_composite_traversal_ship_path_rejected(client) -> None:
    """POST /textures/composite with '../'-bearing ship_path must return 400."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": "/tmp/../etc/passwd"},
        files=[("base_texture", ("base.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_composite_absolute_outside_root_ship_path_rejected(client) -> None:
    """POST /textures/composite with ship_path outside BLENDER_DATA_ROOT returns 400."""
    response = client.post(
        "/api/v1/textures/composite",
        data={"ship_path": "/etc/passwd"},
        files=[("base_texture", ("base.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_composite_traversal_base_texture_path_rejected(client, tmp_path: Path) -> None:
    """POST /textures/composite with '../'-bearing base_texture_path must return 400."""
    # Make ship_path valid (inside /tmp)
    ship = tmp_path / "myship.bbship"
    ship.mkdir()
    buf = BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buf, format="PNG")
    (ship / "skinBase.png").write_bytes(buf.getvalue())

    response = client.post(
        "/api/v1/textures/composite",
        data={
            "ship_path": str(ship),
            "base_texture_path": "/tmp/../etc/passwd",
        },
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_composite_absolute_outside_root_base_texture_rejected(client, tmp_path: Path) -> None:
    """POST /textures/composite with base_texture_path outside BLENDER_DATA_ROOT → 400."""
    ship = tmp_path / "myship2.bbship"
    ship.mkdir()
    buf = BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buf, format="PNG")
    (ship / "skinBase.png").write_bytes(buf.getvalue())

    response = client.post(
        "/api/v1/textures/composite",
        data={
            "ship_path": str(ship),
            "base_texture_path": "/etc/passwd",
        },
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


# ===========================================================================
# Integration: POST /api/v1/render/ — traversal model_path → 400
# ===========================================================================


def test_render_traversal_model_path_rejected(client) -> None:
    """POST /render/ with '../'-bearing model_path must return 400."""
    response = client.post(
        "/api/v1/render/",
        data={
            "model_path": "/tmp/../etc/passwd",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        files=[("texture", ("texture.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_render_absolute_outside_root_model_path_rejected(client) -> None:
    """POST /render/ with model_path outside BLENDER_DATA_ROOT returns 400."""
    response = client.post(
        "/api/v1/render/",
        data={
            "model_path": "/etc/passwd",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        files=[("texture", ("texture.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_async_render_traversal_model_path_rejected(client) -> None:
    """POST /render/async with '../'-bearing model_path must return 400."""
    response = client.post(
        "/api/v1/render/async",
        data={
            "model_path": "/tmp/../etc/passwd",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        files=[("texture", ("texture.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()


def test_async_render_outside_root_model_path_rejected(client) -> None:
    """POST /render/async with model_path outside BLENDER_DATA_ROOT returns 400."""
    response = client.post(
        "/api/v1/render/async",
        data={
            "model_path": "/etc/passwd",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        files=[("texture", ("texture.png", _png_bytes(), "image/png"))],
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower()
