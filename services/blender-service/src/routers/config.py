"""
Router for render configuration management.

Provides GET/PUT/POST endpoints to inspect and update render settings at runtime.
"""

from fastapi import APIRouter, Request
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

    Only valid field names are accepted; unknown keys are silently ignored.
    """
    config_service = request.app.state.render_config
    flogger.info(f"PUT /config/render called with updates: {updates}")
    updated = config_service.update(updates)
    return updated.to_dict()


@router.post("/render/reset", summary="Reset render settings to defaults")
async def reset_render_config(request: Request) -> dict:
    """Reset all render settings to env var defaults."""
    config_service = request.app.state.render_config
    flogger.info("POST /config/render/reset called")
    config_service.reset()
    return config_service.config.to_dict()
