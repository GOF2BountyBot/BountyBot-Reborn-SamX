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
from utils.safe_path import validate_user_path_http

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
    res_x: int = Form(default=1280, description="Render width in pixels (352-1920)"),
    res_y: int = Form(default=720, description="Render height in pixels (240-1080)"),
    num_samples: int = Form(default=32, description="CYCLES samples per pixel (1-64)"),
) -> StreamingResponse:
    """Render a ship model with the supplied texture and return a PNG.

    B.93: out-of-range ``res_x`` / ``res_y`` / ``num_samples`` are **clamped**
    to the nearest valid config bound rather than rejected. When at least one
    parameter was adjusted the response includes an ``X-Render-Clamped`` header
    with the format ``field:requested->actual`` (comma-separated for multiple
    clamps). Fully in-bounds requests have no such header.

    Missing required fields return HTTP 422 (FastAPI default).
    Blender failures return HTTP 500.
    """
    flogger.info(f"render_ship request: model_path={model_path!r}, res={res_x}x{res_y}, num_samples={num_samples}")

    # --- Validate model_path against allowed data directory ---
    validated_model_path = validate_user_path_http(model_path, description="model_path")

    # Use live config from app state if available, else fall back to module-level service.
    render_config = getattr(getattr(request.app, "state", None), "render_config", None)
    service = RenderService(render_config.config if render_config is not None else None)

    # --- B.93: clamp out-of-bounds render parameters to the nearest valid bound ---
    # Rather than rejecting the request, a too-large / too-small resolution or
    # sample count is clamped and the render proceeds; clamps are logged and
    # reported back via the X-Render-Clamped response header.
    clamp = service.clamp_params(res_x, res_y, num_samples)
    res_x, res_y, num_samples = clamp.res_x, clamp.res_y, clamp.num_samples
    if clamp.was_clamped:
        flogger.info(f"render_ship: request parameters clamped: {clamp.clamped}")

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
                model_path=str(validated_model_path),
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
    headers = {"Content-Disposition": "inline; filename=render.png"}
    # B.93: surface any parameter clamping in a response header so callers can
    # tell the render did not use exactly the resolution / samples they asked for.
    if clamp.was_clamped:
        headers["X-Render-Clamped"] = ",".join(
            f"{name}:{info['requested']}->{info['actual']}" for name, info in clamp.clamped.items()
        )
    return StreamingResponse(
        output_buf,
        media_type="image/png",
        headers=headers,
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
    res_x: int = Form(default=1280, description="Render width in pixels (352-1920)"),
    res_y: int = Form(default=720, description="Render height in pixels (240-1080)"),
    num_samples: int = Form(default=32, description="CYCLES samples per pixel (1-64)"),
) -> dict:
    """Submit an async render job. Returns job_id immediately."""
    flogger.info(
        f"submit_render_job request: model_path={model_path!r}, res={res_x}x{res_y}, num_samples={num_samples}"
    )

    # --- Validate model_path against allowed data directory ---
    validated_model_path = validate_user_path_http(model_path, description="model_path")

    # Use live config from app state if available.
    render_config = getattr(getattr(request.app, "state", None), "render_config", None)
    async_service = RenderService(render_config.config if render_config is not None else None)

    # --- B.93: clamp out-of-bounds render parameters to the nearest valid bound ---
    # The job is queued (and tracked) with the clamped values; the clamp record
    # is returned in the response body so the caller knows what was adjusted.
    clamp = async_service.clamp_params(res_x, res_y, num_samples)
    res_x, res_y, num_samples = clamp.res_x, clamp.res_y, clamp.num_samples
    if clamp.was_clamped:
        flogger.info(f"submit_render_job: request parameters clamped: {clamp.clamped}")

    # --- Create the job first to get a job_id ---
    job_queue = request.app.state.job_queue
    job = job_queue.create_job(
        model_path=str(validated_model_path),
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
        model_path=str(validated_model_path),
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
        # B.93: empty dict when nothing was clamped; otherwise
        # {field: {"requested": x, "actual": y}}.
        "clamped": clamp.clamped,
    }
