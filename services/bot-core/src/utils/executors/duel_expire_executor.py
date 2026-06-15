"""Duel expire executor — marks a pending duel request as expired when its
timeout is reached.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Extracts ``duel_id`` from the job payload.
  2. Calls DuelService.expire_duel() to set the duel status to 'expired'.
  3. Posts a notification to the discord-gateway REST API so the bot can relay
     the expiry message to both the challenger and the target.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import os as _os
import traceback
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from shared.bblogger import get_logger

flogger = get_logger("duel-expire-executor")

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------

_GATEWAY_HOST = _os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = _os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def execute_duel_expire_job(job_id: str, payload: dict) -> dict:
    """Execute a duel expiry job.

    Payload fields
    --------------
    duel_id : int
        The ID of the DuelRequest to expire.

    Returns
    -------
    dict
        Summary with ``status`` and ``duel_id``.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from services.duel_service import DuelService

    start_ts = datetime.now(UTC)
    flogger.info(f"DuelExpireJob[{job_id}] START")
    flogger.trace(f"DuelExpireJob[{job_id}] payload: {payload}")

    duel_id: int | None = payload.get("duel_id")
    if duel_id is None:
        flogger.error(f"DuelExpireJob[{job_id}] missing duel_id in payload")
        return {"status": "error", "reason": "missing duel_id", "duel_id": None}

    try:
        async with db_manager.get_session() as db:
            duel_service = DuelService()
            try:
                duel = await duel_service.expire_duel(db, duel_id)
            except ValueError as exc:
                flogger.warning(
                    f"DuelExpireJob[{job_id}] expire_duel raised ValueError "
                    f"for duel_id={duel_id}: {exc} (not found or wrong status)"
                )
                return {"status": "skipped", "duel_id": duel_id}

        flogger.info(
            f"DuelExpireJob[{job_id}] duel id={duel.id} "
            f"(challenger={duel.challenger_id}, target={duel.target_id}) expired"
        )

        # Notify both players via discord-gateway (non-fatal if it fails).
        await _notify_expiry(job_id, duel)

        # Push updated (empty) duel cache for both affected players (non-fatal).
        await _push_duel_cache(job_id, duel.guild_id, duel.challenger_id)
        await _push_duel_cache(job_id, duel.guild_id, duel.target_id)

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(f"DuelExpireJob[{job_id}] completed in {duration:.2f}s")
        return {"status": "success", "duel_id": duel.id}

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"DuelExpireJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Helper: notify both players of duel expiry via discord-gateway
# ---------------------------------------------------------------------------


async def _push_duel_cache(parent_job_id: str, guild_id: int, player_id: int) -> None:
    """Push empty duel lists to the gateway cache for a player, invalidating stale data.

    Pushes empty lists so the gateway knows to refresh on next autocomplete access.
    Non-fatal: exceptions are logged as WARNING and silently swallowed.
    """
    token = _os.getenv("INTERNAL_AUTH_TOKEN", "")
    headers = {"x-internal-auth": token} if token else {}
    try:
        flogger.debug(
            f"DuelExpireJob[{parent_job_id}] pushing duel cache invalidation for guild={guild_id} player={player_id}"
        )
        # SSRF guard: coerce IDs to int so any non-numeric value (path-traversal
        # payload, injection attempt) raises ValueError before the URL is built.
        safe_guild = int(guild_id)
        safe_player = int(player_id)
        cache_url = (
            f"{_GATEWAY_BASE_URL}/internal/autocomplete/duel-cache"
            f"/{quote(str(safe_guild), safe='')}/{quote(str(safe_player), safe='')}"
        )
        async with httpx.AsyncClient() as client:
            from shared.http_retry import with_transient_retry  # deferred — avoids forkserver mock-shared collision

            await with_transient_retry(
                client.post,
                cache_url,
                json={"pending_duels": [], "outgoing_duels": []},
                headers=headers,
                timeout=5,
            )
        flogger.debug(f"DuelExpireJob[{parent_job_id}] duel cache push OK for guild={guild_id} player={player_id}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"DuelExpireJob[{parent_job_id}] duel cache push failed for "
            f"guild={guild_id} player={player_id}: {type(exc).__name__}: {exc}"
        )


async def _notify_expiry(parent_job_id: str, duel) -> None:
    """POST a duel expiry notification to the discord-gateway messages endpoint.

    The gateway routes the message to the correct Discord channel and DMs
    both the challenger and the target.
    Failures are logged but do NOT propagate — a failed notification is
    non-fatal for the expiry operation.
    """
    notification = {
        "guild_id": duel.guild_id,
        "message_type": "duel_expire",
        "content": {
            "duel_id": duel.id,
            "challenger_id": duel.challenger_id,
            "target_id": duel.target_id,
            "stakes": duel.stakes,
        },
    }

    try:
        flogger.debug(
            f"DuelExpireJob[{parent_job_id}] posting duel expiry notification to discord-gateway for duel id={duel.id}"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/messages",
                json=notification,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.debug(f"DuelExpireJob[{parent_job_id}] received HTTP {resp.status_code} from discord-gateway")
        flogger.info(
            f"DuelExpireJob[{parent_job_id}] notified discord-gateway of "
            f"duel id={duel.id} expiry (challenger={duel.challenger_id}, "
            f"target={duel.target_id})"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"DuelExpireJob[{parent_job_id}] failed to notify discord-gateway of duel id={duel.id} expiry: {e}"
        )
        flogger.trace(traceback.format_exc())
