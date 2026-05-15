"""
Router for render configuration management.

Provides GET/PUT/POST endpoints to inspect and update render settings at runtime.
"""

from dataclasses import fields as dataclass_fields

from fastapi import APIRouter, HTTPException, Request
from services.render_config_service import RenderConfig, RenderConfigError
from shared import bblogger

flogger = bblogger.get_logger("blender-config-api-router")

router = APIRouter(
    prefix="/config",
    tags=["config"],
)


@router.get("/render", summary="Get current render settings")
async def get_render_config(request: Request) -> dict:
    """Returns all current render configuration values."""
    config_service = request.app.state.render_config
    flogger.debug("GET /config/render called")
    return config_service.config.to_dict()


@router.put("/render", summary="Update render settings")
async def update_render_config(request: Request, updates: dict) -> dict:
    """Update one or more render settings.

    Only valid field names are accepted; unknown keys raise HTTP 422.

    B.91: the update is also rejected with HTTP 422 if it would violate a
    semantic config invariant (e.g. ``min_res_x > max_res_x`` or
    ``default_samples > max_samples``). The live config is left untouched.
    """
    config_service = request.app.state.render_config
    flogger.info(f"PUT /config/render called with updates: {updates}")
    # B.32 defense-in-depth: reject requests where no recognized field is provided.
    # dataclasses.fields() excludes ClassVar attrs (PARAM_GROUPS / INVARIANTS).
    valid_fields = {f.name for f in dataclass_fields(RenderConfig)}
    if not any(k in valid_fields for k in updates):
        raise HTTPException(
            status_code=422,
            detail=f"No valid fields in update. Valid fields: {sorted(valid_fields)}",
        )
    try:
        updated = config_service.update(updates)
    except RenderConfigError as exc:
        flogger.warning(f"PUT /config/render rejected — config invariant violation: {exc}")
        raise HTTPException(
            status_code=422,
            detail=f"Config invariant violation: {exc}",
        ) from exc
    return updated.to_dict()


@router.post("/render/reset", summary="Reset render settings to defaults")
async def reset_render_config(request: Request) -> dict:
    """Reset all render settings to env var defaults."""
    config_service = request.app.state.render_config
    flogger.info("POST /config/render/reset called")
    config_service.reset()
    return config_service.config.to_dict()
