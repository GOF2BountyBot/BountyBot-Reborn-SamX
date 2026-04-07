"""Bounty expire executor — marks a bounty as expired when its end_time is reached.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Extracts ``bounty_id`` from the job payload.
  2. Calls BountyService.expire_bounty() to set the bounty status to 'expired'.
  3. Posts an announcement to the discord-gateway REST API so the bot can relay
     the expiry notification to the appropriate Discord channel.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import os as _os
import traceback
from datetime import UTC, datetime

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-expire-executor")

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------

_GATEWAY_HOST = _os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = _os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def execute_bounty_expire_job(job_id: str, payload: dict) -> dict:
    """Execute a bounty expiry job.

    Payload fields
    --------------
    bounty_id : int
        The ID of the bounty to expire.

    Returns
    -------
    dict
        Summary with ``status`` and ``bounty_id``.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from services.bounty_service import BountyService

    start_ts = datetime.now(UTC)
    bounty_id: int | None = payload.get("bounty_id")
    flogger.info(f"BountyExpireJob[{job_id}] START (bounty_id={bounty_id})")
    flogger.trace(f"BountyExpireJob[{job_id}] payload: {payload}")
    if bounty_id is None:
        flogger.error(f"BountyExpireJob[{job_id}] missing bounty_id in payload")
        return {"status": "error", "reason": "missing bounty_id", "bounty_id": None}

    try:
        async with db_manager.get_session() as db:
            bounty_service = BountyService()
            bounty = await bounty_service.expire_bounty(db, bounty_id)

            if bounty is not None:
                # Delete the announcement message from Discord + DB (inside the session).
                await _delete_bounty_announcement(job_id, bounty, db)

        if bounty is None:
            flogger.warning(
                f"BountyExpireJob[{job_id}] expire_bounty returned None "
                f"for bounty_id={bounty_id} (not found or wrong status)"
            )
            return {"status": "skipped", "bounty_id": bounty_id}

        flogger.info(f"BountyExpireJob[{job_id}] bounty id={bounty.id} ({bounty.criminal_name}) expired")

        # Announce expiry to discord-gateway (non-fatal if it fails).
        await _announce_expiry(job_id, bounty)

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(f"BountyExpireJob[{job_id}] completed in {duration:.2f}s")
        return {"status": "success", "bounty_id": bounty.id}

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountyExpireJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Helper: announce bounty expiry to discord-gateway
# ---------------------------------------------------------------------------


async def _announce_expiry(parent_job_id: str, bounty) -> None:
    """POST a bounty expiry announcement to the discord-gateway messages endpoint.

    The gateway routes the message to the correct Discord channel.
    Failures are logged but do NOT propagate — a failed announcement is
    non-fatal for the expiry operation.
    """
    announcement = {
        "guild_id": bounty.guild_id,
        "message_type": "bounty_expire",
        "content": {
            "bounty_id": bounty.id,
            "division": bounty.division,
            "criminal_name": bounty.criminal_name,
            "criminal_faction": bounty.criminal_faction,
            "reward": bounty.reward,
            "tech_level": bounty.tech_level,
        },
    }

    try:
        flogger.debug(
            f"BountyExpireJob[{parent_job_id}] posting bounty expiry announcement "
            f"to discord-gateway (bounty_id={bounty.id})"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.debug(
            f"BountyExpireJob[{parent_job_id}] received HTTP {resp.status_code} "
            f"from discord-gateway for bounty id={bounty.id}"
        )
        flogger.info(f"BountyExpireJob[{parent_job_id}] announced expiry of bounty id={bounty.id} to discord-gateway")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"BountyExpireJob[{parent_job_id}] failed to announce expiry of "
            f"bounty id={bounty.id} to discord-gateway: {e}"
        )
        flogger.trace(traceback.format_exc())


# ---------------------------------------------------------------------------
# Helper: delete bounty announcement Discord message + DB record
# ---------------------------------------------------------------------------


async def _delete_bounty_announcement(parent_job_id: str, bounty, db) -> None:
    """Delete the bounty's Discord announcement message.

    1. Look up DiscordMessage by guild_id + "bounty_announcement" + bounty.id
    2. If found, DELETE the message from Discord via gateway
    3. Delete the DiscordMessage record from the database
    4. Non-fatal if any step fails
    """
    try:
        from persist.repositories.discord_message_repository import DiscordMessageRepository

        msg_repo = DiscordMessageRepository()
        discord_msg = await msg_repo.get_by_guild_type_and_reference(
            db, bounty.guild_id, "bounty_announcement", bounty.id
        )

        if discord_msg is None:
            flogger.debug(f"BountyExpireJob[{parent_job_id}] no announcement to delete for bounty {bounty.id}")
            return

        # Delete from Discord via gateway
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{_GATEWAY_BASE_URL}/messages/{discord_msg.message_id}",
                    timeout=10,
                )
            # 404 is OK — message may have been manually deleted
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
            flogger.info(f"BountyExpireJob[{parent_job_id}] deleted Discord message {discord_msg.message_id}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"BountyExpireJob[{parent_job_id}] failed to delete Discord message: {e}")

        # Delete the DiscordMessage record from DB
        await msg_repo.delete_by_guild_type_and_reference(db, bounty.guild_id, "bounty_announcement", bounty.id)
        flogger.info(f"BountyExpireJob[{parent_job_id}] cleaned up announcement record for bounty {bounty.id}")

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(f"BountyExpireJob[{parent_job_id}] failed to delete announcement for bounty {bounty.id}: {e}")
