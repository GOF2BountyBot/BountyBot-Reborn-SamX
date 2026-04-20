"""Bounty expire executor — marks a bounty as expired when its end_time is reached.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Extracts ``bounty_id`` from the job payload.
  2. Fetches the bounty object (needed for announcement lookup regardless of status).
  3. Calls BountyService.expire_bounty() to set the bounty status to 'expired'
     (only succeeds if bounty is still active; returns None for captured/completed etc.).
  4. Always attempts to delete the Discord announcement message, regardless of
     whether the bounty was already captured.

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
            from persist.repositories.bounty_repository import BountyRepository

            bounty_service = BountyService()
            bounty_repo = BountyRepository()

            # Fetch the bounty first (regardless of status) so we can delete the announcement.
            bounty_obj = await bounty_repo.get_by_id(db, bounty_id)

            # Try to expire it (only succeeds if still active; returns None otherwise).
            bounty = await bounty_service.expire_bounty(db, bounty_id)

            # Always attempt to delete the announcement, even if bounty was already captured.
            if bounty_obj is not None:
                await _delete_bounty_announcement(job_id, bounty_obj, db)

        if bounty_obj is None:
            flogger.warning(f"BountyExpireJob[{job_id}] bounty {bounty_id} not found in database")
            return {"status": "skipped", "bounty_id": bounty_id}

        if bounty is None:
            flogger.info(
                f"BountyExpireJob[{job_id}] expire_bounty returned None for bounty_id={bounty_id} "
                f"(already captured/completed); announcement deleted"
            )
        else:
            flogger.info(f"BountyExpireJob[{job_id}] bounty id={bounty.id} ({bounty.criminal_name}) expired")

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(f"BountyExpireJob[{job_id}] completed in {duration:.2f}s")
        return {"status": "success", "bounty_id": bounty_id}

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountyExpireJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


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

        # Delete from Discord via gateway using channel-specific endpoint (more reliable —
        # does not require a guild-wide channel scan to find the message).
        try:
            channel_id = discord_msg.channel_id
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages/{discord_msg.message_id}",
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
