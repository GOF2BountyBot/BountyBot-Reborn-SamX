"""Bounty respawn executor — respawns an escaped bounty with a new route and answer.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Extracts ``bounty_id`` from the job payload.
  2. Calls BountyService.respawn_bounty() which regenerates the A* route and
     answer while keeping the same criminal.  Status is reset to 'active'.
  3. Posts an announcement to the discord-gateway REST API so the bot can relay
     the respawn notification to the appropriate Discord channel.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import os as _os
import traceback
from datetime import UTC, datetime

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-respawn-executor")

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------
_GATEWAY_HOST = _os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = _os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def execute_bounty_respawn_job(job_id: str, payload: dict) -> dict:
    """Execute a bounty respawn job.

    Payload fields
    --------------
    bounty_id : int
        The ID of the escaped bounty to respawn.

    Returns
    -------
    dict
        Summary with ``status`` and ``bounty_id``.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from services.bounty_service import BountyService

    start_ts = datetime.now(UTC)
    flogger.info(f"BountyRespawnJob[{job_id}] START")
    flogger.trace(f"BountyRespawnJob[{job_id}] payload: {payload}")

    bounty_id: int | None = payload.get("bounty_id")
    if bounty_id is None:
        flogger.error(f"BountyRespawnJob[{job_id}] missing bounty_id in payload")
        return {"status": "error", "reason": "missing bounty_id", "bounty_id": None}

    try:
        async with db_manager.get_session() as db:
            bounty_service = BountyService()
            bounty = await bounty_service.respawn_bounty(db, bounty_id)

        if bounty is None:
            flogger.warning(
                f"BountyRespawnJob[{job_id}] respawn_bounty returned None "
                f"for bounty_id={bounty_id} (not found, wrong status, or route failure)"
            )
            return {"status": "skipped", "bounty_id": bounty_id}

        flogger.info(
            f"BountyRespawnJob[{job_id}] bounty id={bounty.id} "
            f"({bounty.criminal_name}) respawned with "
            f"{len(bounty.route) if bounty.route else 0} system route"
        )

        # Announce respawn to discord-gateway (non-fatal if it fails).
        await _announce_respawn(job_id, bounty)

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(f"BountyRespawnJob[{job_id}] completed in {duration:.2f}s")
        return {"status": "success", "bounty_id": bounty.id}

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountyRespawnJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Helper: announce bounty respawn to discord-gateway
# ---------------------------------------------------------------------------


async def _announce_respawn(parent_job_id: str, bounty) -> None:
    """POST a bounty respawn announcement to the discord-gateway messages endpoint.

    The gateway routes the message to the correct Discord channel.
    Failures are logged but do NOT propagate — a failed announcement is
    non-fatal for the respawn operation.
    """
    announcement = {
        "guild_id": bounty.guild_id,
        "message_type": "bounty_respawn",
        "content": {
            "bounty_id": bounty.id,
            "division": bounty.division,
            "criminal_name": bounty.criminal_name,
            "criminal_faction": bounty.criminal_faction,
            "reward": bounty.reward,
            "route_length": len(bounty.route) if bounty.route else 0,
            "tech_level": bounty.tech_level,
            "end_time": (bounty.end_time.isoformat() if bounty.end_time else None),
        },
    }

    try:
        flogger.debug(
            f"BountyRespawnJob[{parent_job_id}] posting bounty respawn announcement "
            f"to {_GATEWAY_BASE_URL}/messages (bounty_id={bounty.id})"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.debug(f"BountyRespawnJob[{parent_job_id}] announcement response status={resp.status_code}")
        flogger.info(f"BountyRespawnJob[{parent_job_id}] announced respawn of bounty id={bounty.id} to discord-gateway")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"BountyRespawnJob[{parent_job_id}] failed to announce respawn of "
            f"bounty id={bounty.id} to discord-gateway: {e}"
        )
        flogger.trace(traceback.format_exc())
