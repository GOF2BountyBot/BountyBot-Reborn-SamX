"""Bounty spawn executor — spawns new bounties per division when slots are available.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Queries all guild configs via ConfigRepository.list_all().
  2. For each guild and each division (Bronze, Silver, Gold), checks the count
     of currently active bounties.
  3. Uses TemperatureService.get_max_bounties() to determine the capacity limit
     (default 5 per division, overridable via BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION).
  4. When a slot is available, calls BountyService.spawn_bounty() to create a
     new bounty.
  5. After each successful spawn, schedules a one-time "bounty_expire" job via
     the bot-core scheduler REST API to fire at bounty.end_time.
  6. Posts an announcement to the discord-gateway REST API so the bot can relay
     the new bounty to the appropriate Discord channel.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import os
import traceback
import uuid
from datetime import UTC, datetime

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-spawn-executor")

# ---------------------------------------------------------------------------
# Supported bounty divisions (matches BountyService / GameConstants)
# ---------------------------------------------------------------------------
_BOUNTY_DIVISIONS = ["Bronze", "Silver", "Gold"]

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------
_SELF_HOST = os.getenv("EXECUTOR_HOST", "bot-core")
_SELF_PORT = os.getenv("EXECUTOR_PORT", "8000")
_SELF_BASE_URL = f"http://{_SELF_HOST}:{_SELF_PORT}/api/v1"

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"

# Default temperature value used when no guild-specific temperature is stored.
_DEFAULT_TEMPERATURE: float = 5.0


async def execute_bounty_spawn_job(job_id: str, payload: dict) -> dict:
    """Execute a bounty spawn job.

    Payload fields
    --------------
    guild_id : int, optional
        When provided only that guild is processed.  When omitted all
        configured guilds are processed (bulk mode).
    division : str, optional
        When provided only that division is processed for the given
        guild_id.  When omitted all three divisions are processed.
    temperature : float, optional
        Activity temperature override (default: ``_DEFAULT_TEMPERATURE`` = 5.0).
        Used to compute the maximum number of concurrent bounties via
        TemperatureService.get_max_bounties().

    Returns
    -------
    dict
        Summary of the spawn operation including spawned bounty counts.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.bounty_repository import BountyRepository
    from persist.repositories.config_repository import ConfigRepository
    from services.bounty_service import BountyService
    from services.temperature_service import TemperatureService

    start_ts = datetime.now(UTC)
    flogger.info(f"BountySpawnJob[{job_id}] START")
    flogger.trace(f"BountySpawnJob[{job_id}] payload: {payload}")

    guild_id: int | None = payload.get("guild_id")
    division: str | None = payload.get("division")
    temperature: float = float(payload.get("temperature", _DEFAULT_TEMPERATURE))

    max_bounties: int = TemperatureService.get_max_bounties(temperature)
    flogger.debug(f"BountySpawnJob[{job_id}] temperature={temperature}, max_bounties={max_bounties}")

    total_spawned = 0
    guild_results: dict = {}

    try:
        async with db_manager.get_session() as db:
            bounty_service = BountyService()
            bounty_repo = BountyRepository()
            config_repo = ConfigRepository()

            # ------------------------------------------------------------------
            # Determine which guilds to process
            # ------------------------------------------------------------------
            if guild_id:
                # Process a single guild (create a lightweight mock config object).
                class _SingleGuildConfig:
                    pass

                cfg = _SingleGuildConfig()
                cfg.guild_id = guild_id
                cfg.bounty_channel_id = None
                guild_configs = [cfg]
            else:
                # Bulk mode — enumerate all configured guilds.
                guild_configs = await config_repo.list_all(db)
                if not guild_configs:
                    flogger.info(f"BountySpawnJob[{job_id}] no guilds configured, nothing to do")
                    return {"status": "success", "guilds_processed": 0, "total_spawned": 0}

            # ------------------------------------------------------------------
            # Determine which divisions to process
            # ------------------------------------------------------------------
            divisions_to_check = [division] if division else _BOUNTY_DIVISIONS

            # ------------------------------------------------------------------
            # Process each guild x division
            # ------------------------------------------------------------------
            for config in guild_configs:
                gid: int = config.guild_id
                guild_spawned = 0
                division_results: dict = {}

                for div in divisions_to_check:
                    # Normalise: repository stores lowercase division names but
                    # the executor accepts either case.  BountyService.spawn_bounty
                    # expects lowercase.
                    div_lower = div.lower()

                    active_bounties = await bounty_repo.get_active_by_guild_and_division(db, gid, div_lower)
                    active_count = len(active_bounties)

                    if active_count >= max_bounties:
                        flogger.debug(
                            f"BountySpawnJob[{job_id}] guild={gid} div={div_lower}: "
                            f"{active_count}/{max_bounties} slots full, skipping"
                        )
                        division_results[div] = {"spawned": 0, "active": active_count}
                        continue

                    # There is at least one open slot — spawn a bounty.
                    flogger.debug(
                        f"BountySpawnJob[{job_id}] guild={gid} div={div_lower}: "
                        f"{active_count}/{max_bounties} active — spawning"
                    )
                    spawned_bounty = await bounty_service.spawn_bounty(db, gid, div_lower)

                    if spawned_bounty is None:
                        flogger.warning(
                            f"BountySpawnJob[{job_id}] guild={gid} div={div_lower}: "
                            "spawn_bounty returned None (no criminals / route failure)"
                        )
                        division_results[div] = {"spawned": 0, "active": active_count}
                        continue

                    guild_spawned += 1
                    flogger.info(
                        f"BountySpawnJob[{job_id}] spawned bounty id={spawned_bounty.id} "
                        f"guild={gid} div={div_lower} criminal={spawned_bounty.criminal_name}"
                    )

                    # ----------------------------------------------------------
                    # Schedule expiry job at bounty.end_time
                    # ----------------------------------------------------------
                    await _schedule_expiry_job(job_id, spawned_bounty)

                    # ----------------------------------------------------------
                    # Announce to discord-gateway
                    # ----------------------------------------------------------
                    bounty_channel_id = getattr(config, "bounty_channel_id", None)
                    await _announce_bounty(job_id, spawned_bounty, bounty_channel_id)

                    division_results[div] = {
                        "spawned": 1,
                        "bounty_id": spawned_bounty.id,
                        "criminal": spawned_bounty.criminal_name,
                        "reward": spawned_bounty.reward,
                        "end_time": (spawned_bounty.end_time.isoformat() if spawned_bounty.end_time else None),
                    }

                guild_results[gid] = {"spawned": guild_spawned, "divisions": division_results}
                total_spawned += guild_spawned

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(
            f"BountySpawnJob[{job_id}] completed: {total_spawned} bounties spawned "
            f"across {len(guild_results)} guild(s) in {duration:.2f}s"
        )
        return {
            "status": "success",
            "guilds_processed": len(guild_results),
            "total_spawned": total_spawned,
            "results": guild_results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountySpawnJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Helper: schedule bounty expiry
# ---------------------------------------------------------------------------


async def _schedule_expiry_job(parent_job_id: str, bounty) -> None:
    """POST a one-time job to the scheduler API to expire *bounty* at end_time.

    If end_time is not set or scheduling fails the error is logged but does NOT
    propagate — a failed expiry schedule is non-fatal for the spawn operation.
    """
    if bounty.end_time is None:
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] bounty id={bounty.id} has no end_time; skipping expiry scheduling"
        )
        return

    expiry_job_id = str(uuid.uuid4())
    expiry_payload = {
        "job_type": "bounty_expire",
        "bounty_id": bounty.id,
        "guild_id": bounty.guild_id,
        "division": bounty.division,
    }
    body = {
        "run_at": bounty.end_time.isoformat(),
        "payload": expiry_payload,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_SELF_BASE_URL}/jobs",
                json=body,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.info(
            f"BountySpawnJob[{parent_job_id}] scheduled expiry job {expiry_job_id} "
            f"for bounty id={bounty.id} at {bounty.end_time.isoformat()}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountySpawnJob[{parent_job_id}] failed to schedule expiry for bounty id={bounty.id}: {e}")
        flogger.trace(traceback.format_exc())


# ---------------------------------------------------------------------------
# Helper: announce bounty to discord-gateway
# ---------------------------------------------------------------------------


async def _announce_bounty(parent_job_id: str, bounty, bounty_channel_id: int | None) -> None:
    """POST a bounty announcement to the discord-gateway channel messages endpoint.

    POSTs to ``POST /api/v1/channels/{bounty_channel_id}/messages`` with an
    EmbedPayload as the request body (matching ``MessageCreateRequest`` schema).

    If ``bounty_channel_id`` is None, a warning is logged and the announcement
    is skipped — no channel has been configured for this guild yet.

    Failures are logged but do NOT propagate — a failed announcement is
    non-fatal for the spawn operation.
    """
    if bounty_channel_id is None:
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] guild={bounty.guild_id}: "
            "bounty_channel_id not configured, skipping announcement"
        )
        return

    route_length = len(bounty.route) if bounty.route else 0
    end_time_str = bounty.end_time.isoformat() if bounty.end_time else "Unknown"
    division_display = str(bounty.division).capitalize()

    announcement = {
        "content": {
            "title": "🎯 New Bounty!",
            "description": (
                f"A new **{division_display}** division bounty has been posted. "
                "Track down the criminal and claim your reward!"
            ),
            "color": 15158332,  # Red (#E74C3C)
            "fields": [
                {"name": "Criminal", "value": str(bounty.criminal_name), "inline": True},
                {"name": "Faction", "value": str(bounty.criminal_faction), "inline": True},
                {"name": "Division", "value": division_display, "inline": True},
                {"name": "Reward", "value": f"{bounty.reward:,} credits", "inline": True},
                {"name": "Route Length", "value": f"{route_length} systems", "inline": True},
                {"name": "Expires", "value": end_time_str, "inline": True},
            ],
            "footer_text": "Use /check to hunt this bounty!",
        },
        "message_type": "default",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{bounty_channel_id}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.info(f"BountySpawnJob[{parent_job_id}] announced bounty id={bounty.id} to channel {bounty_channel_id}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"BountySpawnJob[{parent_job_id}] failed to announce bounty id={bounty.id} "
            f"to channel {bounty_channel_id}: {e}"
        )
        flogger.trace(traceback.format_exc())
