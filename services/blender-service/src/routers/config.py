"""
Router for render configuration management.

Provides GET/PUT/POST endpoints to inspect and update render settings at runtime.
"""

from fastapi import APIRouter, HTTPException, Request
from services.render_config_service import RenderConfig
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
    """
    config_service = request.app.state.render_config
    flogger.info(f"PUT /config/render called with updates: {updates}")
    # B.32 defense-in-depth: reject requests where no recognized field is provided
    valid_fields = set(RenderConfig.__dataclass_fields__)
    if not any(k in valid_fields for k in updates):
        raise HTTPException(
            status_code=422,
            detail=f"No valid fields in update. Valid fields: {sorted(valid_fields)}",
        )
    updated = config_service.update(updates)
    return updated.to_dict()


@router.post("/render/reset", summary="Reset render settings to defaults")
async def reset_render_config(request: Request) -> dict:
    """Reset all render settings to env var defaults."""
    config_service = request.app.state.render_config
    flogger.info("POST /config/render/reset called")
    config_service.reset()
    return config_service.config.to_dict()
