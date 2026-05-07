"""
Integration tests for the render router.

Uses FastAPI TestClient (via httpx) to test the POST /api/v1/render/ endpoint
and the POST /api/v1/render/async endpoint.
The RenderService.render_ship() method is mocked so Blender is never invoked.
Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture()
def mock_job_queue() -> MagicMock:
    """Return a MagicMock for JobQueueService injected into app.state."""
    from datetime import UTC, datetime

    from services.job_queue_service import JobQueueService, JobStatus, RenderJob

    mock_queue = MagicMock(spec=JobQueueService)
    # Default job returned by create_job
    fake_job = RenderJob(
        job_id="abc12345",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC),
        model_path="/model/ship.obj",
        res_x=1920,
        res_y=1080,
        num_samples=64,
    )
    mock_queue.create_job.return_value = fake_job
    mock_queue.submit_job = AsyncMock()
    return mock_queue


# ---------------------------------------------------------------------------
# Sync render — validation error tests (400)
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


def test_render_validation_res_too_low(client: TestClient) -> None:
    """res_x below 352 should return HTTP 400."""
    with patch(
        "services.render_service.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 100,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "below 240p" in response.json()["detail"]


def test_render_validation_res_y_too_high(client: TestClient) -> None:
    """res_y above 2160 should return HTTP 400."""
    with patch(
        "services.render_service.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 9999,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "above 2160p/4k" in response.json()["detail"]


def test_render_validation_res_y_too_low(client: TestClient) -> None:
    """res_y below 240 should return HTTP 400."""
    with patch(
        "services.render_service.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 10,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "below 240p" in response.json()["detail"]


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


def test_render_validation_samples_too_high(client: TestClient) -> None:
    """num_samples above 128 should return HTTP 400."""
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
                "num_samples": 200,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "numSamples" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Sync render — missing required field tests (422)
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


# ---------------------------------------------------------------------------
# Sync render — success path (200)
# ---------------------------------------------------------------------------


def test_render_success_returns_png(tmp_path: Path, client: TestClient) -> None:
    """A successful render should return HTTP 200 with image/png content-type."""
    # Write a fake PNG to the output path
    fake_output = tmp_path / "render_output.png"
    fake_png_data = _png_bytes()
    fake_output.write_bytes(fake_png_data)

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


def test_render_success_has_content_disposition(tmp_path: Path, client: TestClient) -> None:
    """A successful render response should include content-disposition header."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert "content-disposition" in response.headers
    assert "render.png" in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Sync render — error paths (500)
# ---------------------------------------------------------------------------


def test_render_blender_failure_returns_500(client: TestClient) -> None:
    """When Blender fails (RenderError), the router should return HTTP 500."""
    from services.render_service import RenderError

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        side_effect=RenderError("Blender exited with code 1"),
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 500
    assert "Render failed" in response.json()["detail"]


def test_render_unexpected_exception_returns_500(client: TestClient) -> None:
    """When an unexpected exception occurs in render, the router returns HTTP 500."""
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Unexpected crash"),
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 500
    assert "Unexpected error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Async render endpoint — validation error tests (400)
# ---------------------------------------------------------------------------


def test_async_render_validation_res_too_high(client: TestClient) -> None:
    """Async endpoint: res_x above limit should return HTTP 400."""
    response = client.post(
        "/api/v1/render/async",
        data={
            "model_path": "/some/model.obj",
            "res_x": 9999,
            "res_y": 1080,
            "num_samples": 64,
        },
        files=[_make_texture_upload()],
    )
    assert response.status_code == 400
    assert "above 2160p/4k" in response.json()["detail"]


def test_async_render_validation_samples_too_low(client: TestClient) -> None:
    """Async endpoint: num_samples=0 should return HTTP 400."""
    response = client.post(
        "/api/v1/render/async",
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


def test_async_render_missing_texture(client: TestClient) -> None:
    """Async endpoint: no texture upload should return HTTP 422."""
    response = client.post(
        "/api/v1/render/async",
        data={
            "model_path": "/some/model.obj",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Async render endpoint — success path (202)
# ---------------------------------------------------------------------------


def test_async_render_success_returns_202(client: TestClient, mock_job_queue: MagicMock) -> None:
    """A valid async render request should return HTTP 202 with job_id."""
    app.state.job_queue = mock_job_queue

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert "poll_url" in body
    assert body["job_id"] in body["poll_url"]


def test_async_render_success_poll_url_format(client: TestClient, mock_job_queue: MagicMock) -> None:
    """The poll_url in async render response should reference /api/v1/jobs/{job_id}."""
    app.state.job_queue = mock_job_queue

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 202
    body = response.json()
    assert "/api/v1/jobs/" in body["poll_url"]


# ---------------------------------------------------------------------------
# Sync render — output file read failure (500)
# ---------------------------------------------------------------------------


def test_render_output_read_failure_returns_500(tmp_path: Path, client: TestClient) -> None:
    """When reading the render output bytes fails, the router should return HTTP 500."""
    # Return a path to a file that won't be readable (nonexistent after render)
    fake_output = tmp_path / "render_output.png"
    # Do NOT write the file — render_ship returns path but it doesn't exist

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    # Either 500 (read failure) or 200 (if read succeeds with empty bytes)
    # The test verifies the router handles the case; nonexistent file → 500
    assert response.status_code == 500


def test_async_render_texture_upload_failure_returns_400(client: TestClient, mock_job_queue: MagicMock) -> None:
    """When saving the uploaded texture to disk fails in async mode, return HTTP 400."""
    app.state.job_queue = mock_job_queue

    with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "Failed to read uploaded texture" in response.json()["detail"]


def test_render_texture_save_failure_returns_400(client: TestClient) -> None:
    """When saving the uploaded texture to disk fails in sync mode, return HTTP 400."""
    with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
        response = client.post(
            "/api/v1/render/",
            data={
                "model_path": "/some/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "Failed to read uploaded texture" in response.json()["detail"]
