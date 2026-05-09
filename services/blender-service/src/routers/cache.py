"""
Router for render cache management.

Provides endpoints to inspect and clear Blender render temp files under /tmp.
"""

import glob
import shutil
from pathlib import Path

from fastapi import APIRouter
from shared import bblogger

flogger = bblogger.get_logger("blender-cache-api-router")

router = APIRouter(
    prefix="/cache",
    tags=["cache"],
)

_BLENDER_TMP_PATTERN = "/tmp/blender_render_*"


@router.post("/clear", summary="Clear render temp files")
async def clear_cache() -> dict:
    """Delete all blender render temp files from /tmp.

    Removes all directories matching ``/tmp/blender_render_*``.
    Returns the number of directories removed and bytes freed.
    """
    cleared = 0
    total_bytes = 0
    errors = 0

    for path in glob.glob(_BLENDER_TMP_PATTERN):
        try:
            p = Path(path)
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            total_bytes += size
            shutil.rmtree(path)
            cleared += 1
            flogger.debug(f"Cleared cache dir: {path} ({size} bytes)")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Failed to remove {path}: {exc}")
            errors += 1

    flogger.info(f"Cache clear complete: {cleared} dirs removed, {total_bytes} bytes freed, {errors} errors")

    return {
        "cleared_directories": cleared,
        "freed_bytes": total_bytes,
        "freed_mb": round(total_bytes / (1024 * 1024), 2),
        "errors": errors,
    }


@router.get("/stats", summary="Get cache statistics")
async def cache_stats() -> dict:
    """Get current /tmp cache usage stats.

    Counts all ``/tmp/blender_render_*`` directories and their total size.
    """
    total_dirs = 0
    total_bytes = 0

    for path in glob.glob(_BLENDER_TMP_PATTERN):
        try:
            p = Path(path)
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            total_bytes += size
            total_dirs += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Could not stat {path}: {exc}")

    return {
        "cache_directories": total_dirs,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
    }
