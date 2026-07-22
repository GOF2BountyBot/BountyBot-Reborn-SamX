"""
Integration tests for the cache router.

Covers:
  POST /api/v1/cache/clear
  GET  /api/v1/cache/stats

These tests do NOT patch glob.glob. Patching it (as this file used to) meant the
router's real ``_BLENDER_TMP_PATTERN`` glob was never exercised — a regression to
that pattern (e.g. a typo'd wildcard) would have gone undetected. Instead, an
autouse fixture points the module's ``_BLENDER_TMP_PATTERN`` constant at a
per-test ``tmp_path`` (isolating tests from any real ``/tmp/blender_render_*``
dirs on the host) and every test exercises the real ``glob.glob`` call against
real directories on disk.

Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on sys.path
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app
from routers import cache as cache_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_tmp_pattern(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the router's glob pattern at tmp_path instead of the real /tmp.

    This keeps tests off the host's real /tmp (so we never list/delete a real
    running service's cache dirs) while still exercising the real glob.glob
    call and the real "blender_render_*" wildcard shape.
    """
    monkeypatch.setattr(cache_router, "_BLENDER_TMP_PATTERN", str(tmp_path / "blender_render_*"))


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/v1/cache/clear
# ---------------------------------------------------------------------------


def test_clear_cache_empty(client: TestClient) -> None:
    """POST /cache/clear with no matching dirs should return zero stats."""
    response = client.post("/api/v1/cache/clear")
    assert response.status_code == 200
    body = response.json()
    assert body["cleared_directories"] == 0
    assert body["freed_bytes"] == 0
    assert body["freed_mb"] == 0.0
    assert body["errors"] == 0


def test_clear_cache_removes_dirs(tmp_path: Path, client: TestClient) -> None:
    """POST /cache/clear should remove blender_render_* dirs and count them."""
    # Create two real blender render dirs matching the (tmp_path-scoped) pattern.
    dir1 = tmp_path / "blender_render_aaa"
    dir2 = tmp_path / "blender_render_bbb"
    dir1.mkdir()
    dir2.mkdir()
    (dir1 / "file.png").write_bytes(b"x" * 1024)

    response = client.post("/api/v1/cache/clear")

    assert response.status_code == 200
    body = response.json()
    assert body["cleared_directories"] == 2
    assert body["freed_bytes"] == 1024
    # The real glob really removed the dirs on disk.
    assert not dir1.exists()
    assert not dir2.exists()


def test_clear_cache_ignores_non_matching_dirs(tmp_path: Path, client: TestClient) -> None:
    """POST /cache/clear must only remove dirs matching the blender_render_* pattern."""
    matching = tmp_path / "blender_render_keep_me_gone"
    matching.mkdir()
    other = tmp_path / "some_other_dir"
    other.mkdir()

    response = client.post("/api/v1/cache/clear")

    assert response.status_code == 200
    body = response.json()
    assert body["cleared_directories"] == 1
    assert not matching.exists()
    assert other.exists()  # untouched — proves the real glob pattern is selective


def test_clear_cache_returns_freed_mb(tmp_path: Path, client: TestClient) -> None:
    """POST /cache/clear should report freed_mb as rounded float."""
    dir1 = tmp_path / "blender_render_xyz"
    dir1.mkdir()
    # Write 2MB of fake data
    (dir1 / "big.png").write_bytes(b"x" * (2 * 1024 * 1024))

    response = client.post("/api/v1/cache/clear")

    assert response.status_code == 200
    body = response.json()
    assert body["freed_mb"] == 2.0


def test_clear_cache_response_has_required_fields(client: TestClient) -> None:
    """POST /cache/clear response must include all required fields."""
    response = client.post("/api/v1/cache/clear")
    body = response.json()
    for field in ("cleared_directories", "freed_bytes", "freed_mb", "errors"):
        assert field in body, f"Missing field: {field}"


def test_clear_cache_counts_errors_on_rmtree_failure(tmp_path: Path, client: TestClient) -> None:
    """POST /cache/clear should count errors when shutil.rmtree fails."""
    # Create a dir that we'll simulate a failure for
    dir1 = tmp_path / "blender_render_fail"
    dir1.mkdir()

    with patch("shutil.rmtree", side_effect=OSError("Permission denied")):
        response = client.post("/api/v1/cache/clear")

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == 1
    assert body["cleared_directories"] == 0
    assert dir1.exists()  # rmtree "failed", so it's still there


# ---------------------------------------------------------------------------
# GET /api/v1/cache/stats
# ---------------------------------------------------------------------------


def test_cache_stats_empty(client: TestClient) -> None:
    """GET /cache/stats with no dirs should return zero counts."""
    response = client.get("/api/v1/cache/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["cache_directories"] == 0
    assert body["total_bytes"] == 0
    assert body["total_mb"] == 0.0


def test_cache_stats_counts_dirs(tmp_path: Path, client: TestClient) -> None:
    """GET /cache/stats should count all blender_render_* dirs."""
    dir1 = tmp_path / "blender_render_a"
    dir2 = tmp_path / "blender_render_b"
    dir1.mkdir()
    dir2.mkdir()
    (dir1 / "out.png").write_bytes(b"y" * 512)

    response = client.get("/api/v1/cache/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["cache_directories"] == 2
    assert body["total_bytes"] == 512


def test_cache_stats_response_has_required_fields(client: TestClient) -> None:
    """GET /cache/stats response must include all required fields."""
    response = client.get("/api/v1/cache/stats")
    body = response.json()
    for field in ("cache_directories", "total_bytes", "total_mb"):
        assert field in body, f"Missing field: {field}"


def test_cache_stats_returns_total_mb_rounded(tmp_path: Path, client: TestClient) -> None:
    """GET /cache/stats should report total_mb as a rounded float."""
    dir1 = tmp_path / "blender_render_mb"
    dir1.mkdir()
    # 1MB exactly
    (dir1 / "file.bin").write_bytes(b"z" * (1024 * 1024))

    response = client.get("/api/v1/cache/stats")

    body = response.json()
    assert body["total_mb"] == 1.0


def test_cache_stats_handles_stat_error(tmp_path: Path, client: TestClient) -> None:
    """GET /cache/stats should not crash if iterating a directory raises an exception."""
    # Create a real dir that the real glob will find, but patch Path.rglob to raise
    dir1 = tmp_path / "blender_render_gone"
    dir1.mkdir()

    with patch("pathlib.Path.rglob", side_effect=OSError("Permission denied")):
        response = client.get("/api/v1/cache/stats")

    # Should return 200 (errors are swallowed via except clause)
    assert response.status_code == 200
    body = response.json()
    # The directory is NOT counted because rglob raised before total_dirs += 1
    assert body["cache_directories"] == 0
