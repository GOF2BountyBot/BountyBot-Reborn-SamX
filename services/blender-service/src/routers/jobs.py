"""
Render job status router for the Blender service API.

Provides endpoints to query the status of async render jobs and to
download the resulting PNG once a job is complete.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from services.job_queue_service import JobStatus
from shared import bblogger

flogger = bblogger.get_logger("blender-jobs-api-router")

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={
        404: {"description": "Job not found"},
        409: {"description": "Job not yet complete"},
    },
)


@router.get(
    "/",
    summary="List all render jobs",
    description="Returns a list of all active (non-expired) render jobs.",
)
async def list_jobs(request: Request) -> list[dict]:
    """List all active render jobs."""
    flogger.info("list_jobs: endpoint called")
    job_queue = request.app.state.job_queue
    jobs = job_queue.list_jobs()
    flogger.debug(f"list_jobs: returning {len(jobs)} active jobs")
    return jobs


@router.get(
    "/{job_id}",
    summary="Get render job status",
    description="Get the current status and metadata for a render job.",
)
async def get_job_status(job_id: str, request: Request) -> dict:
    """Get the status of a render job."""
    flogger.info(f"get_job_status: endpoint called for job_id={job_id!r}")
    job_queue = request.app.state.job_queue
    job = job_queue.get_job(job_id)
    if job is None:
        flogger.warning(f"get_job_status: job {job_id!r} not found or expired")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found or has expired.",
        )
    flogger.debug(f"get_job_status: job {job_id!r} status={job.status.value}")
    return job.to_dict()


@router.get(
    "/{job_id}/result",
    summary="Download render result",
    description=(
        "Download the rendered PNG image for a completed job. "
        "Returns 404 if the job is not found, 409 if the job is not yet complete."
    ),
)
async def download_result(job_id: str, request: Request) -> StreamingResponse:
    """Download the rendered image for a completed job."""
    flogger.info(f"download_result: endpoint called for job_id={job_id!r}")
    job_queue = request.app.state.job_queue
    job = job_queue.get_job(job_id)

    if job is None:
        flogger.warning(f"download_result: job {job_id!r} not found or expired")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found or has expired.",
        )

    if job.status != JobStatus.COMPLETE:
        flogger.warning(f"download_result: job {job_id!r} not complete (status={job.status.value})")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is not complete yet (status: {job.status.value}).",
        )

    result_path = Path(job.result_path)
    if not result_path.exists():
        flogger.error(f"download_result: result file missing for job {job_id!r}: {result_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Result file for job '{job_id}' no longer exists.",
        )

    try:
        image_bytes = result_path.read_bytes()
    except Exception as exc:
        flogger.error(f"download_result: failed to read result for job {job_id!r}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read result file: {exc}",
        ) from exc

    flogger.info(f"download_result: streaming {len(image_bytes)} bytes for job {job_id!r}")
    return StreamingResponse(
        BytesIO(image_bytes),
        media_type="image/png",
        headers={"Content-Disposition": f"inline; filename=render_{job_id}.png"},
    )
