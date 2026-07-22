"""
Integration tests for the jobs router.

Uses FastAPI TestClient to test the GET /api/v1/jobs/ endpoints.
A real in-memory JobQueueService is injected into app.state (it has no
external dependencies, so there is nothing to mock at this boundary — see
services/test_job_queue_service.py for its own dedicated unit tests).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on sys.path
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app
from services.job_queue_service import JobQueueService, JobStatus, RenderJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_job(
    job_queue: JobQueueService,
    status: JobStatus = JobStatus.QUEUED,
    result_path: str | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    model_path: str = "/models/ship.obj",
    res_x: int = 1920,
    res_y: int = 1080,
    num_samples: int = 64,
) -> RenderJob:
    """Create a real job via the real queue, then mutate it into the desired state.

    Mirrors what the real render pipeline does over time (create -> processing ->
    complete/failed), just without waiting for a background task to do it.
    """
    job = job_queue.create_job(model_path=model_path, res_x=res_x, res_y=res_y, num_samples=num_samples)
    job.status = status
    job.result_path = result_path
    job.error_message = error_message
    job.started_at = started_at
    job.completed_at = completed_at
    return job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def job_queue() -> JobQueueService:
    """Return a real in-memory JobQueueService."""
    return JobQueueService()


@pytest.fixture()
def client(job_queue: JobQueueService) -> TestClient:
    """Return a synchronous TestClient with a real job queue in app.state."""
    # Inject the real queue before entering the test client context
    app.state.job_queue = job_queue
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/ — list endpoint
# ---------------------------------------------------------------------------


def test_list_jobs_empty(client: TestClient, job_queue: JobQueueService) -> None:
    """When no jobs exist, list endpoint should return an empty list."""
    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    assert response.json() == []


def test_list_jobs_with_one_job(client: TestClient, job_queue: JobQueueService) -> None:
    """When one active job exists, list endpoint should return it."""
    job = _seed_job(job_queue, JobStatus.QUEUED)

    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == job.job_id
    assert body[0]["status"] == "queued"


def test_list_jobs_with_multiple_jobs(client: TestClient, job_queue: JobQueueService) -> None:
    """List endpoint should return all jobs in the queue."""
    _seed_job(job_queue, JobStatus.QUEUED)
    _seed_job(job_queue, JobStatus.PROCESSING, started_at=datetime.now(UTC))
    _seed_job(job_queue, JobStatus.COMPLETE, result_path="/tmp/out.png", completed_at=datetime.now(UTC))

    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_list_jobs_contains_required_fields(client: TestClient, job_queue: JobQueueService) -> None:
    """Each job dict in the list must have required fields."""
    _seed_job(job_queue, JobStatus.QUEUED)

    response = client.get("/api/v1/jobs/")
    body = response.json()

    assert len(body) == 1
    job_data = body[0]
    for field in ("job_id", "status", "created_at", "model_path", "res_x", "res_y", "num_samples"):
        assert field in job_data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id} — status endpoint
# ---------------------------------------------------------------------------


def test_get_job_not_found_404(client: TestClient, job_queue: JobQueueService) -> None:
    """Unknown job_id should return HTTP 404."""
    response = client.get("/api/v1/jobs/doesnotexist")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_job_queued_returns_job_data(client: TestClient, job_queue: JobQueueService) -> None:
    """A queued job should return HTTP 200 with status='queued'."""
    job = _seed_job(job_queue, JobStatus.QUEUED)

    response = client.get(f"/api/v1/jobs/{job.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.job_id
    assert body["status"] == "queued"


def test_get_job_processing_returns_status(client: TestClient, job_queue: JobQueueService) -> None:
    """A processing job should return HTTP 200 with status='processing'."""
    job = _seed_job(job_queue, JobStatus.PROCESSING, started_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "processing"


def test_get_job_complete_returns_status(client: TestClient, job_queue: JobQueueService) -> None:
    """A completed job should return HTTP 200 with status='complete'."""
    job = _seed_job(job_queue, JobStatus.COMPLETE, result_path="/tmp/output.png", completed_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "complete"


def test_get_job_failed_returns_status(client: TestClient, job_queue: JobQueueService) -> None:
    """A failed job should return HTTP 200 with status='failed'."""
    job = _seed_job(job_queue, JobStatus.FAILED, error_message="Blender crashed", completed_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Blender crashed"


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id}/result — download endpoint
# ---------------------------------------------------------------------------


def test_download_result_not_found_404(client: TestClient, job_queue: JobQueueService) -> None:
    """Unknown job_id for result download should return HTTP 404."""
    response = client.get("/api/v1/jobs/abc12345/result")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_result_queued_job_returns_409(client: TestClient, job_queue: JobQueueService) -> None:
    """Attempting to download a queued (not complete) job should return HTTP 409."""
    job = _seed_job(job_queue, JobStatus.QUEUED)

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 409
    assert "not complete" in response.json()["detail"].lower()


def test_download_result_processing_job_returns_409(client: TestClient, job_queue: JobQueueService) -> None:
    """Attempting to download a processing job should return HTTP 409."""
    job = _seed_job(job_queue, JobStatus.PROCESSING, started_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 409
    assert "not complete" in response.json()["detail"].lower()


def test_download_result_failed_job_returns_409(client: TestClient, job_queue: JobQueueService) -> None:
    """Attempting to download a failed job should return HTTP 409."""
    job = _seed_job(job_queue, JobStatus.FAILED, completed_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 409
    assert "not complete" in response.json()["detail"].lower()


def test_download_result_complete_result_file_missing_returns_404(
    client: TestClient, job_queue: JobQueueService
) -> None:
    """A complete job whose result file no longer exists should return HTTP 404."""
    job = _seed_job(
        job_queue,
        JobStatus.COMPLETE,
        result_path="/tmp/nonexistent_output.png",
        completed_at=datetime.now(UTC),
    )

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 404
    assert "no longer exists" in response.json()["detail"].lower()


def test_download_result_success_returns_png(tmp_path: Path, client: TestClient, job_queue: JobQueueService) -> None:
    """A complete job with an existing result file should return a PNG stream."""
    from io import BytesIO

    from PIL import Image

    # Create a real PNG file in tmp_path
    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 128, 255)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _seed_job(job_queue, JobStatus.COMPLETE, result_path=str(result_file), completed_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


def test_download_result_content_disposition_includes_job_id(
    tmp_path: Path, client: TestClient, job_queue: JobQueueService
) -> None:
    """Result download response should include job_id in content-disposition header."""
    from io import BytesIO

    from PIL import Image

    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _seed_job(job_queue, JobStatus.COMPLETE, result_path=str(result_file), completed_at=datetime.now(UTC))

    response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 200
    assert job.job_id in response.headers.get("content-disposition", "")


def test_download_result_file_read_error_returns_500(
    tmp_path: Path, client: TestClient, job_queue: JobQueueService
) -> None:
    """If reading the result file raises an IOError, the router should return HTTP 500."""
    from io import BytesIO
    from unittest.mock import patch

    from PIL import Image

    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _seed_job(job_queue, JobStatus.COMPLETE, result_path=str(result_file), completed_at=datetime.now(UTC))

    with patch("pathlib.Path.read_bytes", side_effect=OSError("disk error")):
        response = client.get(f"/api/v1/jobs/{job.job_id}/result")

    assert response.status_code == 500
    assert "Failed to read result file" in response.json()["detail"]
