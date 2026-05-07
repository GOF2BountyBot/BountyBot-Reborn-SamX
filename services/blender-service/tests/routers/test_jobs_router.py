"""
Integration tests for the jobs router.

Uses FastAPI TestClient to test the GET /api/v1/jobs/ endpoints.
A mock JobQueueService is injected into app.state to avoid real rendering.
Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

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


def _make_job(
    job_id: str = "abc12345",
    status: JobStatus = JobStatus.QUEUED,
    result_path: str | None = None,
) -> RenderJob:
    """Create a RenderJob with sensible defaults for testing."""
    return RenderJob(
        job_id=job_id,
        status=status,
        created_at=datetime.now(UTC),
        model_path="/models/ship.obj",
        res_x=1920,
        res_y=1080,
        num_samples=64,
        result_path=result_path,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_job_queue() -> MagicMock:
    """Return a MagicMock that mimics JobQueueService interface."""
    return MagicMock(spec=JobQueueService)


@pytest.fixture()
def client(mock_job_queue: MagicMock) -> TestClient:
    """Return a synchronous TestClient with a mock job queue in app.state."""
    # Inject mock queue before entering the test client context
    app.state.job_queue = mock_job_queue
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/ — list endpoint
# ---------------------------------------------------------------------------


def test_list_jobs_empty(client: TestClient, mock_job_queue: MagicMock) -> None:
    """When no jobs exist, list endpoint should return an empty list."""
    mock_job_queue.list_jobs.return_value = []

    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    assert response.json() == []
    mock_job_queue.list_jobs.assert_called_once()


def test_list_jobs_with_one_job(client: TestClient, mock_job_queue: MagicMock) -> None:
    """When one active job exists, list endpoint should return it."""
    job = _make_job("job001", JobStatus.QUEUED)
    mock_job_queue.list_jobs.return_value = [job.to_dict()]

    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == "job001"
    assert body[0]["status"] == "queued"


def test_list_jobs_with_multiple_jobs(client: TestClient, mock_job_queue: MagicMock) -> None:
    """List endpoint should return all jobs in the queue."""
    jobs = [
        _make_job("job001", JobStatus.QUEUED).to_dict(),
        _make_job("job002", JobStatus.PROCESSING).to_dict(),
        _make_job("job003", JobStatus.COMPLETE, result_path="/tmp/out.png").to_dict(),
    ]
    mock_job_queue.list_jobs.return_value = jobs

    response = client.get("/api/v1/jobs/")

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_list_jobs_contains_required_fields(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Each job dict in the list must have required fields."""
    job = _make_job("job001", JobStatus.QUEUED)
    mock_job_queue.list_jobs.return_value = [job.to_dict()]

    response = client.get("/api/v1/jobs/")
    body = response.json()

    assert len(body) == 1
    job_data = body[0]
    for field in ("job_id", "status", "created_at", "model_path", "res_x", "res_y", "num_samples"):
        assert field in job_data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id} — status endpoint
# ---------------------------------------------------------------------------


def test_get_job_not_found_404(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Unknown job_id should return HTTP 404."""
    mock_job_queue.get_job.return_value = None

    response = client.get("/api/v1/jobs/doesnotexist")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
    mock_job_queue.get_job.assert_called_once_with("doesnotexist")


def test_get_job_queued_returns_job_data(client: TestClient, mock_job_queue: MagicMock) -> None:
    """A queued job should return HTTP 200 with status='queued'."""
    job = _make_job("abc12345", JobStatus.QUEUED)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/abc12345")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "abc12345"
    assert body["status"] == "queued"
    mock_job_queue.get_job.assert_called_once_with("abc12345")


def test_get_job_processing_returns_status(client: TestClient, mock_job_queue: MagicMock) -> None:
    """A processing job should return HTTP 200 with status='processing'."""
    job = _make_job("proc001", JobStatus.PROCESSING)
    job.started_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/proc001")

    assert response.status_code == 200
    assert response.json()["status"] == "processing"


def test_get_job_complete_returns_status(client: TestClient, mock_job_queue: MagicMock) -> None:
    """A completed job should return HTTP 200 with status='complete'."""
    job = _make_job("done001", JobStatus.COMPLETE, result_path="/tmp/output.png")
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/done001")

    assert response.status_code == 200
    assert response.json()["status"] == "complete"


def test_get_job_failed_returns_status(client: TestClient, mock_job_queue: MagicMock) -> None:
    """A failed job should return HTTP 200 with status='failed'."""
    job = _make_job("fail001", JobStatus.FAILED)
    job.error_message = "Blender crashed"
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/fail001")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Blender crashed"


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id}/result — download endpoint
# ---------------------------------------------------------------------------


def test_download_result_not_found_404(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Unknown job_id for result download should return HTTP 404."""
    mock_job_queue.get_job.return_value = None

    response = client.get("/api/v1/jobs/abc12345/result")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
    mock_job_queue.get_job.assert_called_once_with("abc12345")


def test_download_result_queued_job_returns_409(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Attempting to download a queued (not complete) job should return HTTP 409."""
    job = _make_job("queued001", JobStatus.QUEUED)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/queued001/result")

    assert response.status_code == 409
    assert "not complete" in response.json()["detail"].lower()


def test_download_result_processing_job_returns_409(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Attempting to download a processing job should return HTTP 409."""
    job = _make_job("proc001", JobStatus.PROCESSING)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/proc001/result")

    assert response.status_code == 409


def test_download_result_failed_job_returns_409(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Attempting to download a failed job should return HTTP 409."""
    job = _make_job("fail001", JobStatus.FAILED)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/fail001/result")

    assert response.status_code == 409


def test_download_result_complete_result_file_missing_returns_404(
    client: TestClient, mock_job_queue: MagicMock
) -> None:
    """A complete job whose result file no longer exists should return HTTP 404."""
    job = _make_job("done001", JobStatus.COMPLETE, result_path="/tmp/nonexistent_output.png")
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/done001/result")

    assert response.status_code == 404
    assert "no longer exists" in response.json()["detail"].lower()


def test_download_result_success_returns_png(tmp_path: Path, client: TestClient, mock_job_queue: MagicMock) -> None:
    """A complete job with an existing result file should return a PNG stream."""
    from io import BytesIO

    from PIL import Image

    # Create a real PNG file in tmp_path
    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 128, 255)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _make_job("done001", JobStatus.COMPLETE, result_path=str(result_file))
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/done001/result")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


def test_download_result_content_disposition_includes_job_id(
    tmp_path: Path, client: TestClient, mock_job_queue: MagicMock
) -> None:
    """Result download response should include job_id in content-disposition header."""
    from io import BytesIO

    from PIL import Image

    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _make_job("myjob99", JobStatus.COMPLETE, result_path=str(result_file))
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    response = client.get("/api/v1/jobs/myjob99/result")

    assert response.status_code == 200
    assert "myjob99" in response.headers.get("content-disposition", "")


def test_download_result_file_read_error_returns_500(
    tmp_path: Path, client: TestClient, mock_job_queue: MagicMock
) -> None:
    """If reading the result file raises an IOError, the router should return HTTP 500."""
    from io import BytesIO
    from unittest.mock import patch

    from PIL import Image

    result_file = tmp_path / "render_output.png"
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(buf, format="PNG")
    result_file.write_bytes(buf.getvalue())

    job = _make_job("errjob1", JobStatus.COMPLETE, result_path=str(result_file))
    job.completed_at = datetime.now(UTC)
    mock_job_queue.get_job.return_value = job

    with patch("pathlib.Path.read_bytes", side_effect=OSError("disk error")):
        response = client.get("/api/v1/jobs/errjob1/result")

    assert response.status_code == 500
    assert "Failed to read result file" in response.json()["detail"]
