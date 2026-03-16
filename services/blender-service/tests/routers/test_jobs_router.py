"""
Integration tests for the jobs router.

Uses FastAPI TestClient to test the GET /api/v1/jobs/ endpoints.
A mock JobQueueService is injected into app.state to avoid real rendering.
Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on sys.path
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app
from services.job_queue_service import JobQueueService

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
# GET /api/v1/jobs/{job_id} — status endpoint
# ---------------------------------------------------------------------------


def test_get_job_not_found_404(client: TestClient, mock_job_queue: MagicMock) -> None:
    """Unknown job_id should return HTTP 404."""
    mock_job_queue.get_job.return_value = None

    response = client.get("/api/v1/jobs/doesnotexist")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
    mock_job_queue.get_job.assert_called_once_with("doesnotexist")


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
