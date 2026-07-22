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
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure src/ is on sys.path (conftest.py handles this, but be explicit for isolation)
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app
from services.job_queue_service import JobQueueService

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


@pytest.fixture(autouse=True)
def _seed_render_config() -> None:
    """Seed a fresh RenderConfigService into app.state before each test.

    The FastAPI ``app`` object is shared across test modules; other modules
    (e.g. test_config_router) mutate ``app.state.render_config``. Re-seeding
    here keeps the clamp bounds deterministic (RenderConfig defaults) per test.
    """
    from services.render_config_service import RenderConfigService

    app.state.render_config = RenderConfigService()


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def job_queue() -> JobQueueService:
    """Return a real in-memory JobQueueService injected into app.state.

    JobQueueService is a pure in-memory object with no external dependencies
    (see services/test_job_queue_service.py for its own dedicated unit tests),
    so there's nothing at this boundary that needs mocking. ``create_job`` /
    ``submit_job`` run for real; only the actual Blender subprocess boundary
    (``RenderService.render_ship``, patched per-test below) is mocked.
    """
    return JobQueueService()


# ---------------------------------------------------------------------------
# Sync render — B.93 out-of-bounds parameters are clamped, not rejected
# ---------------------------------------------------------------------------


def test_render_res_x_too_high_is_clamped(tmp_path: Path, client: TestClient) -> None:
    """B.93: res_x above the configured max is clamped down and the render succeeds."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 4001, "res_y": 720, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["x-render-clamped"] == "res_x:4001->1920"


def test_render_res_x_too_low_is_clamped(tmp_path: Path, client: TestClient) -> None:
    """B.93: res_x below the configured min is clamped up and the render succeeds."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 100, "res_y": 720, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["x-render-clamped"] == "res_x:100->352"


def test_render_res_y_out_of_bounds_is_clamped(tmp_path: Path, client: TestClient) -> None:
    """B.93: res_y above the configured max is clamped down and the render succeeds."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 1280, "res_y": 9999, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["x-render-clamped"] == "res_y:9999->1080"


def test_render_samples_out_of_bounds_is_clamped(tmp_path: Path, client: TestClient) -> None:
    """B.93: num_samples below the minimum is clamped up and the render succeeds."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 1280, "res_y": 720, "num_samples": 0},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert response.headers["x-render-clamped"] == "num_samples:0->1"


def test_render_clamped_values_passed_to_render_ship(tmp_path: Path, client: TestClient) -> None:
    """B.93: the clamped (not the requested) params are what render_ship is invoked with."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ) as mock_render:
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 4001, "res_y": 9999, "num_samples": 999},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    call_kwargs = mock_render.call_args.kwargs
    assert (call_kwargs["res_x"], call_kwargs["res_y"], call_kwargs["num_samples"]) == (1920, 1080, 64)


def test_render_in_bounds_has_no_clamp_header(tmp_path: Path, client: TestClient) -> None:
    """B.93: an in-bounds request renders normally with no X-Render-Clamped header."""
    fake_output = tmp_path / "render_output.png"
    fake_output.write_bytes(_png_bytes())
    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
        return_value=fake_output,
    ):
        response = client.post(
            "/api/v1/render/",
            data={"model_path": "/tmp/model.obj", "res_x": 1280, "res_y": 720, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 200
    assert "x-render-clamped" not in response.headers


# ---------------------------------------------------------------------------
# Sync render — missing required field tests (422)
# ---------------------------------------------------------------------------


def test_render_missing_texture(client: TestClient) -> None:
    """A request with no texture upload should return HTTP 422."""
    response = client.post(
        "/api/v1/render/",
        data={
            "model_path": "/tmp/model.obj",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
        # deliberately omit the texture file
    )
    assert response.status_code == 422
    assert len(response.json()["detail"]) > 0


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
    detail = response.json()["detail"]
    assert len(detail) > 0
    # FastAPI places the missing field name in the loc array
    locs = [str(err.get("loc", "")) for err in detail]
    assert any("model_path" in loc for loc in locs)


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
                "model_path": "/tmp/model.obj",
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
                "model_path": "/tmp/model.obj",
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
                "model_path": "/tmp/model.obj",
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
                "model_path": "/tmp/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 500
    assert "Unexpected error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Async render endpoint — B.93 out-of-bounds parameters are clamped, not rejected
# ---------------------------------------------------------------------------


def test_async_render_res_too_high_is_clamped(client: TestClient, job_queue: JobQueueService) -> None:
    """B.93: async endpoint clamps res_x above the configured max and still queues the job."""
    app.state.job_queue = job_queue

    with patch("routers.render.RenderService.render_ship", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/render/async",
            data={"model_path": "/tmp/model.obj", "res_x": 9999, "res_y": 720, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["clamped"]["res_x"] == {"requested": 9999, "actual": 1920}


def test_async_render_samples_too_low_is_clamped(client: TestClient, job_queue: JobQueueService) -> None:
    """B.93: async endpoint clamps num_samples below the minimum and still queues the job."""
    app.state.job_queue = job_queue

    with patch("routers.render.RenderService.render_ship", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/render/async",
            data={"model_path": "/tmp/model.obj", "res_x": 1280, "res_y": 720, "num_samples": 0},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 202
    body = response.json()
    assert body["clamped"]["num_samples"] == {"requested": 0, "actual": 1}


def test_async_render_in_bounds_reports_empty_clamped(client: TestClient, job_queue: JobQueueService) -> None:
    """B.93: an in-bounds async request reports an empty 'clamped' object."""
    app.state.job_queue = job_queue

    with patch("routers.render.RenderService.render_ship", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/render/async",
            data={"model_path": "/tmp/model.obj", "res_x": 1280, "res_y": 720, "num_samples": 32},
            files=[_make_texture_upload()],
        )
    assert response.status_code == 202
    assert response.json()["clamped"] == {}


def test_async_render_missing_texture(client: TestClient) -> None:
    """Async endpoint: no texture upload should return HTTP 422."""
    response = client.post(
        "/api/v1/render/async",
        data={
            "model_path": "/tmp/model.obj",
            "res_x": 1920,
            "res_y": 1080,
            "num_samples": 64,
        },
    )
    assert response.status_code == 422
    assert len(response.json()["detail"]) > 0


# ---------------------------------------------------------------------------
# Async render endpoint — success path (202)
# ---------------------------------------------------------------------------


def test_async_render_success_returns_202(client: TestClient, job_queue: JobQueueService) -> None:
    """A valid async render request should return HTTP 202 with job_id."""
    app.state.job_queue = job_queue

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/tmp/model.obj",
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


def test_async_render_success_poll_url_format(client: TestClient, job_queue: JobQueueService) -> None:
    """The poll_url in async render response should reference /api/v1/jobs/{job_id}."""
    app.state.job_queue = job_queue

    with patch(
        "routers.render.RenderService.render_ship",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/tmp/model.obj",
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
                "model_path": "/tmp/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    # Either 500 (read failure) or 200 (if read succeeds with empty bytes)
    # The test verifies the router handles the case; nonexistent file → 500
    assert response.status_code == 500


def test_async_render_texture_upload_failure_returns_400(client: TestClient, job_queue: JobQueueService) -> None:
    """When saving the uploaded texture to disk fails in async mode, return HTTP 400."""
    app.state.job_queue = job_queue

    with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
        response = client.post(
            "/api/v1/render/async",
            data={
                "model_path": "/tmp/model.obj",
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
                "model_path": "/tmp/model.obj",
                "res_x": 1920,
                "res_y": 1080,
                "num_samples": 64,
            },
            files=[_make_texture_upload()],
        )
    assert response.status_code == 400
    assert "Failed to read uploaded texture" in response.json()["detail"]
