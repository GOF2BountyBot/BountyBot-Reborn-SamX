"""
3D ship render router for the Blender service API.

Provides a POST endpoint that accepts a composited texture upload plus
render parameters, invokes the Blender render pipeline, and returns the
rendered PNG as a streaming response.

Also provides a POST /async endpoint that submits a render job to the
async job queue and returns immediately with a job_id for status polling.
"""

from __future__ import annotations

import shutil
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from services.render_service import RenderError, RenderService
from shared import bblogger

flogger = bblogger.get_logger("blender-render-api-router")

router = APIRouter(
    prefix="/render",
    tags=["render"],
    responses={
        400: {"description": "Bad request (invalid parameters or render failure)"},
        422: {"description": "Unprocessable entity (missing required field)"},
        500: {"description": "Internal server error (Blender render failed)"},
    },
)


@router.post(
    "/",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    summary="Render a 3D ship model",
    description=(
        "Accepts a composited texture image upload, a path to a .obj model file on "
        "disk, and render settings (resolution, CYCLES samples). Invokes Blender "
        "headlessly to render the ship and returns the trimmed PNG image."
    ),
)
async def render_ship(
    request: Request,
    texture: UploadFile = File(..., description="Composited texture image (PNG/JPG)"),
    model_path: str = Form(..., description="Absolute path to the .obj file on disk"),
    res_x: int = Form(default=1920, description="Render width in pixels (352-3840)"),
    res_y: int = Form(default=1080, description="Render height in pixels (240-2160)"),
    num_samples: int = Form(default=64, description="CYCLES samples per pixel (1-128)"),
) -> StreamingResponse:
    """Render a ship model with the supplied texture and return a PNG.

    Validation errors (out-of-range resolution / samples) return HTTP 400.
    Missing required fields return HTTP 422 (FastAPI default).
    Blender failures return HTTP 500.
    """
    flogger.info(f"render_ship request: model_path={model_path!r}, res={res_x}x{res_y}, num_samples={num_samples}")

    # Use live config from app state if available, else fall back to module-level service.
    render_config = getattr(getattr(request.app, "state", None), "render_config", None)
    service = RenderService(render_config.config if render_config is not None else None)

    # --- Validate render parameters before doing any file I/O ---
    try:
        service.validate_params(res_x, res_y, num_samples)
    except ValueError as exc:
        flogger.warning(f"render_ship validation error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # --- Save the uploaded texture to a temp location ---
    render_id = str(uuid.uuid4())
    temp_dir = Path(f"/tmp/blender_render_{render_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        texture_path = str(temp_dir / "texture.png")
        try:
            texture_data = await texture.read()
            Path(texture_path).write_bytes(texture_data)
            flogger.debug(f"Texture saved to {texture_path} ({len(texture_data)} bytes)")
        except Exception as exc:
            flogger.error(f"Failed to save uploaded texture: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to read uploaded texture: {exc}",
            ) from exc

        output_path = str(temp_dir / "render_output.png")

        # --- Invoke Blender render pipeline ---
        try:
            result_path = await service.render_ship(
                model_path=model_path,
                texture_path=texture_path,
                output_path=output_path,
                res_x=res_x,
                res_y=res_y,
                num_samples=num_samples,
            )
        except RenderError as exc:
            flogger.error(f"render_ship failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Render failed: {exc}",
            ) from exc
        except Exception as exc:
            flogger.error(f"render_ship unexpected error: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error during render: {exc}",
            ) from exc

        # --- Read the output before cleanup ---
        try:
            image_bytes = result_path.read_bytes()
        except Exception as exc:
            flogger.error(f"Failed to read render output: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read rendered output: {exc}",
            ) from exc

    finally:
        # Always clean up the temp directory (texture + render output).
        shutil.rmtree(temp_dir, ignore_errors=True)
        flogger.debug(f"Cleaned up temp dir {temp_dir}")

    output_buf = BytesIO(image_bytes)
    output_buf.seek(0)
    flogger.info(f"render_ship response: returning PNG ({len(image_bytes)} bytes), render_id={render_id}")
    return StreamingResponse(
        output_buf,
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=render.png"},
    )


@router.post(
    "/async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit async render job",
    description=(
        "Submits a render job and returns immediately with a job_id. "
        "Poll GET /api/v1/jobs/{job_id} for status. "
        "Download the result via GET /api/v1/jobs/{job_id}/result once complete."
    ),
)
async def submit_render_job(
    request: Request,
    texture: UploadFile = File(..., description="Composited texture image (PNG/JPG)"),
    model_path: str = Form(..., description="Absolute path to the .obj file on disk"),
    res_x: int = Form(default=1920, description="Render width in pixels (352-3840)"),
    res_y: int = Form(default=1080, description="Render height in pixels (240-2160)"),
    num_samples: int = Form(default=64, description="CYCLES samples per pixel (1-128)"),
) -> dict:
    """Submit an async render job. Returns job_id immediately."""
    flogger.info(
        f"submit_render_job request: model_path={model_path!r}, res={res_x}x{res_y}, num_samples={num_samples}"
    )

    # Use live config from app state if available.
    render_config = getattr(getattr(request.app, "state", None), "render_config", None)
    async_service = RenderService(render_config.config if render_config is not None else None)

    # --- Validate render parameters before doing any file I/O ---
    try:
        async_service.validate_params(res_x, res_y, num_samples)
    except ValueError as exc:
        flogger.warning(f"submit_render_job validation error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # --- Create the job first to get a job_id ---
    job_queue = request.app.state.job_queue
    job = job_queue.create_job(
        model_path=model_path,
        res_x=res_x,
        res_y=res_y,
        num_samples=num_samples,
    )
    job_id = job.job_id

    # --- Save the uploaded texture to a temp location named after the job ---
    # NOTE: temp_dir is intentionally NOT cleaned up here — the background render
    # job reads the texture and writes its output into this directory.  The
    # job-queue cleanup task removes the result file once the job expires; the
    # temp dir itself is cleaned up at that point by the OS or a future GC pass.
    # On upload failure we do clean up immediately.
    temp_dir = Path(f"/tmp/blender_render_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    texture_path = str(temp_dir / "texture.png")
    try:
        texture_data = await texture.read()
        Path(texture_path).write_bytes(texture_data)
        flogger.debug(f"[{job_id}] Texture saved to {texture_path} ({len(texture_data)} bytes)")
    except Exception as exc:
        flogger.error(f"[{job_id}] Failed to save uploaded texture: {exc}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded texture: {exc}",
        ) from exc

    output_path = str(temp_dir / "render_output.png")

    # --- Build the render coroutine and submit to job queue ---
    render_coro = async_service.render_ship(
        model_path=model_path,
        texture_path=texture_path,
        output_path=output_path,
        res_x=res_x,
        res_y=res_y,
        num_samples=num_samples,
    )
    await job_queue.submit_job(job, render_coro)

    flogger.info(f"submit_render_job: job {job_id!r} queued")
    return {
        "job_id": job_id,
        "status": "queued",
        "poll_url": f"/api/v1/jobs/{job_id}",
    }
