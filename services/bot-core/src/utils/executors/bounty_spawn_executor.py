"""Bounty spawn executor — orchestrates per-tier staggered bounty spawning.

Architecture overview
---------------------
The ``bounty_spawn_default`` cron job fires every ``bounty_spawn_interval_minutes``
(default 5 min, with jitter).  It now acts as an *orchestrator*:

1. ``execute_bounty_spawn_orchestrate_job`` (``bounty_spawn_orchestrate``)
   Iterates all guilds × all tiers and schedules individual one-time
   ``bounty_spawn_one`` jobs, each with a randomised fire-time offset so that
   bounties appear staggered across the interval window rather than all at once.

2. ``execute_bounty_spawn_one_job`` (``bounty_spawn_one``)
   A single one-time job for one guild × one tier.  At fire time it re-checks
   capacity, spawns a bounty, schedules expiry, and announces to Discord.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import os
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from random import uniform

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-spawn-executor")

# Gateway base URL for cache push endpoints (Phase 5b)
_GATEWAY_HOST_SPAWN = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT_SPAWN = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL_SPAWN = f"http://{_GATEWAY_HOST_SPAWN}:{_GATEWAY_PORT_SPAWN}/api/v1"

# ---------------------------------------------------------------------------
# Supported bounty divisions (matches BountyService / GameConstants)
# ---------------------------------------------------------------------------
_BOUNTY_DIVISIONS = ["Bronze", "Silver", "Gold", "Platinum"]

# Default max bounties per tier when config is missing the key.
DEFAULT_MAX = 3

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------
_SELF_HOST = os.getenv("EXECUTOR_HOST", "bot-core")
_SELF_PORT = os.getenv("EXECUTOR_PORT", "8000")
_SELF_BASE_URL = f"http://{_SELF_HOST}:{_SELF_PORT}/api/v1"

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


def _is_guild_fully_configured(config) -> bool:
    """Return True only if the guild config has all required channel and role IDs set.

    A guild is eligible for bounty spawning only when ALL of the following
    attributes on its GuildConfig record are non-null:

    1. bronze_bounty_channel_id
    2. silver_bounty_channel_id
    3. gold_bounty_channel_id
    4. platinum_bounty_channel_id
    5. bounty_hunter_role_id

    Args:
        config: GuildConfig object (or any object with the above attributes).

    Returns:
        True when all five fields are non-null, False otherwise.
    """
    return all(
        [
            getattr(config, "bronze_bounty_channel_id", None) is not None,
            getattr(config, "silver_bounty_channel_id", None) is not None,
            getattr(config, "gold_bounty_channel_id", None) is not None,
            getattr(config, "platinum_bounty_channel_id", None) is not None,
            getattr(config, "bounty_hunter_role_id", None) is not None,
        ]
    )


def _get_division_channel_id(config, division: str) -> int | None:
    """Get the bounty board channel ID for a given division.

    Args:
        config: GuildConfig object with per-division channel ID attributes.
        division: Division name (case-insensitive: 'bronze', 'silver', 'gold').

    Returns:
        The channel ID for the given division, or None if not configured.
    """
    mapping = {
        "bronze": getattr(config, "bronze_bounty_channel_id", None),
        "silver": getattr(config, "silver_bounty_channel_id", None),
        "gold": getattr(config, "gold_bounty_channel_id", None),
        "platinum": getattr(config, "platinum_bounty_channel_id", None),
    }
    return mapping.get(division.lower())


def _get_division_role_id(config, division: str) -> int | None:
    """Get the tier-specific role ID for a given division.

    Falls back to the general bounty_hunter_role_id if no tier role is configured.

    Args:
        config: GuildConfig object with per-division role ID attributes.
        division: Division name (case-insensitive: 'bronze', 'silver', 'gold').

    Returns:
        The tier-specific role ID for the given division, or the general
        bounty_hunter_role_id as a fallback, or None if neither is configured.
    """
    mapping = {
        "bronze": getattr(config, "bronze_role_id", None),
        "silver": getattr(config, "silver_role_id", None),
        "gold": getattr(config, "gold_role_id", None),
        "platinum": getattr(config, "platinum_role_id", None),
    }
    tier_role = mapping.get(division.lower())
    if tier_role is not None:
        return tier_role
    # Fall back to general Bounty Hunter role
    return getattr(config, "bounty_hunter_role_id", None)


# ---------------------------------------------------------------------------
# NEW: Orchestrator — schedules per-tier one-time jobs
# ---------------------------------------------------------------------------


async def execute_bounty_spawn_orchestrate_job(job_id: str, payload: dict) -> dict:
    """Orchestrate staggered per-tier bounty spawning.

    Called by the ``bounty_spawn_default`` cron job (``bounty_spawn_orchestrate``
    payload type).  For each guild × tier that still has open capacity, schedules
    a one-time ``bounty_spawn_one`` job with a randomised fire-time so that
    announcements are staggered instead of bursting synchronously.

    Design notes
    ------------
    - ``next_spawn_check_at`` gate removed per architect recommendation C1.
      The column remains in GuildConfig but is no longer read or written here.
      Rationale: per-tier one-time jobs already handle timing; the guild-level
      gate was redundant and caused unnecessary skips.

    - ``TemperatureService.get_max_bounties()`` cap removed per architect
      recommendation C3.  Only ``bounty_max_per_tier[tier_lower]`` is used as
      the cap.  TemperatureService and temperature_decay_executor are preserved
      for possible future re-enablement.

    Returns
    -------
    dict
        Summary: guilds processed, tiers queued, etc.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.bounty_repository import BountyRepository
    from persist.repositories.config_repository import ConfigRepository

    start_ts = datetime.now(UTC)
    flogger.info(f"BountySpawnOrchestrate[{job_id}] START")

    total_queued = 0
    guild_results: dict = {}

    try:
        async with db_manager.get_session() as db:
            config_repo = ConfigRepository()
            bounty_repo = BountyRepository()

            guild_configs = await config_repo.list_all(db)
            if not guild_configs:
                flogger.info(f"BountySpawnOrchestrate[{job_id}] no guilds configured, nothing to do")
                return {"status": "success", "guilds_processed": 0, "total_queued": 0}

            for config in guild_configs:
                gid: int = config.guild_id

                # ----------------------------------------------------------
                # Eligibility guard: skip guilds that aren't fully configured
                # ----------------------------------------------------------
                if not _is_guild_fully_configured(config):
                    flogger.info(
                        f"BountySpawnOrchestrate[{job_id}] skipping guild={gid}: "
                        "guild not fully configured (missing channel/role IDs)"
                    )
                    continue

                # NOTE: next_spawn_check_at gate removed per architect C1.
                # The column remains in GuildConfig for backward compatibility
                # but is no longer used to gate the orchestrator.

                bounty_max_per_tier: dict[str, int] = getattr(config, "bounty_max_per_tier", None) or {}
                interval_minutes: int = getattr(config, "bounty_spawn_interval_minutes", None) or 5

                guild_queued = 0
                tier_results: dict = {}

                for tier in _BOUNTY_DIVISIONS:
                    tier_lower = tier.lower()

                    # --------------------------------------------------
                    # Determine max for this tier — if None/0, skip tier
                    # --------------------------------------------------
                    max_for_tier: int | None = (bounty_max_per_tier or {}).get(tier_lower, DEFAULT_MAX)
                    if not max_for_tier:
                        flogger.trace(
                            f"BountySpawnOrchestrate[{job_id}] guild={gid} tier={tier_lower}: "
                            "max_for_tier=0 or missing, tier disabled — skipping"
                        )
                        tier_results[tier_lower] = {"queued": 0, "reason": "tier_disabled"}
                        continue

                    # --------------------------------------------------
                    # Count active bounties in DB
                    # --------------------------------------------------
                    active_count = await bounty_repo.count_active_by_guild_and_division(db, gid, tier_lower)

                    # --------------------------------------------------
                    # Count already-queued one-time spawn jobs in scheduler
                    # --------------------------------------------------
                    from sqlalchemy import text

                    pattern = f"bounty_spawn_{gid}_{tier_lower}_%"
                    queued_result = await db.execute(
                        text("SELECT COUNT(*) FROM apscheduler_jobs WHERE id LIKE :pattern"),
                        {"pattern": pattern},
                    )
                    queued_count = queued_result.scalar_one()

                    if active_count + queued_count >= max_for_tier:
                        flogger.trace(
                            f"BountySpawnOrchestrate[{job_id}] guild={gid} tier={tier_lower}: "
                            f"active={active_count} queued={queued_count} max={max_for_tier} — skipping"
                        )
                        tier_results[tier_lower] = {
                            "queued": 0,
                            "reason": "capacity_full",
                            "active": active_count,
                            "queued_jobs": queued_count,
                        }
                        continue

                    # --------------------------------------------------
                    # Compute randomised fire time within the jitter window
                    # --------------------------------------------------
                    window_minutes = min(15.0, 0.25 * interval_minutes)
                    fire_offset = uniform(
                        max(0.0, interval_minutes - window_minutes),
                        interval_minutes + window_minutes,
                    )
                    fire_time = datetime.now(UTC) + timedelta(minutes=fire_offset)

                    # --------------------------------------------------
                    # Schedule one-time bounty_spawn_one job
                    # --------------------------------------------------
                    spawn_job_id = f"bounty_spawn_{gid}_{tier_lower}_{uuid.uuid4()}"
                    one_time_payload = {
                        "job_type": "bounty_spawn_one",
                        "guild_id": gid,
                        "tier": tier_lower,
                    }
                    body = {
                        "run_at": fire_time.isoformat(),
                        "payload": one_time_payload,
                        "job_id": spawn_job_id,
                    }

                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await client.post(
                                f"{_SELF_BASE_URL}/jobs",
                                json=body,
                                timeout=10,
                            )
                        resp.raise_for_status()
                        guild_queued += 1
                        total_queued += 1
                        flogger.info(
                            f"BountySpawnOrchestrate[{job_id}] queued job={spawn_job_id} "
                            f"guild={gid} tier={tier_lower} fire_at={fire_time.isoformat()}"
                        )
                        tier_results[tier_lower] = {
                            "queued": 1,
                            "job_id": spawn_job_id,
                            "fire_at": fire_time.isoformat(),
                        }
                    except Exception as sched_err:  # pylint: disable=broad-exception-caught
                        flogger.error(
                            f"BountySpawnOrchestrate[{job_id}] failed to schedule spawn for "
                            f"guild={gid} tier={tier_lower}: {sched_err}"
                        )
                        tier_results[tier_lower] = {"queued": 0, "reason": "schedule_error"}

                guild_results[gid] = {"queued": guild_queued, "tiers": tier_results}

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(
            f"BountySpawnOrchestrate[{job_id}] completed: {total_queued} jobs queued "
            f"across {len(guild_results)} guild(s) in {duration:.2f}s"
        )
        return {
            "status": "success",
            "guilds_processed": len(guild_results),
            "total_queued": total_queued,
            "results": guild_results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountySpawnOrchestrate[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# NEW: One-time per-tier executor
# ---------------------------------------------------------------------------


async def execute_bounty_spawn_one_job(job_id: str, payload: dict) -> dict:
    """Execute a single per-tier bounty spawn at the scheduled fire time.

    Called by one-time APScheduler jobs queued by
    ``execute_bounty_spawn_orchestrate_job``.  At fire time:

    1. Validates payload fields (guild_id, tier).
    2. Re-loads guild config and re-checks eligibility.
    3. Re-checks active bounty count against max_for_tier (handles benign
       races where another job spawned in the meantime).
    4. Spawns one bounty, schedules expiry, and announces to Discord.

    Returns
    -------
    dict
        Result summary.

    Raises
    ------
    Exception
        Any unexpected DB / service exception propagates so APScheduler can
        mark the job as failed (correct behaviour — do not swallow).
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.bounty_repository import BountyRepository
    from persist.repositories.config_repository import ConfigRepository
    from services.bounty_service import BountyService

    flogger.info(f"BountySpawnOne[{job_id}] START")
    flogger.trace(f"BountySpawnOne[{job_id}] payload: {payload}")

    # ------------------------------------------------------------------
    # 1. Validate payload
    # ------------------------------------------------------------------
    guild_id: int | None = payload.get("guild_id")
    tier: str | None = payload.get("tier")

    if guild_id is None:
        flogger.warning(f"BountySpawnOne[{job_id}] missing guild_id in payload — aborting")
        return {"success": False, "reason": "missing_payload"}

    if tier is None:
        flogger.warning(f"BountySpawnOne[{job_id}] missing tier in payload — aborting")
        return {"success": False, "reason": "missing_payload"}

    tier_lower = tier.lower()

    async with db_manager.get_session() as db:
        config_repo = ConfigRepository()
        bounty_repo = BountyRepository()
        bounty_service = BountyService()

        # ------------------------------------------------------------------
        # 2. Load guild config
        # ------------------------------------------------------------------
        config = await config_repo.get_by_guild_id(db, guild_id)
        if config is None:
            flogger.warning(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: guild config not found — aborting"
            )
            return {"success": False, "reason": "guild_not_configured"}

        # ------------------------------------------------------------------
        # 3. Re-check full configuration
        # ------------------------------------------------------------------
        if not _is_guild_fully_configured(config):
            flogger.warning(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: "
                "guild not fully configured at fire time — aborting"
            )
            return {"success": False, "reason": "guild_not_configured"}

        # ------------------------------------------------------------------
        # 4. Verify tier channel and role are present
        # ------------------------------------------------------------------
        division_channel_id = _get_division_channel_id(config, tier_lower)
        if division_channel_id is None:
            flogger.warning(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: "
                "division channel not configured — aborting"
            )
            return {"success": False, "reason": "tier_not_configured"}

        division_role_id = _get_division_role_id(config, tier_lower)
        if division_role_id is None:
            flogger.warning(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: division role not configured — aborting"
            )
            return {"success": False, "reason": "tier_not_configured"}

        # ------------------------------------------------------------------
        # 5. Re-check active count (benign race handling)
        # ------------------------------------------------------------------
        active_count = await bounty_repo.count_active_by_guild_and_division(db, guild_id, tier_lower)
        bounty_max_per_tier: dict = getattr(config, "bounty_max_per_tier", None) or {}
        max_for_tier: int = (bounty_max_per_tier or {}).get(tier_lower, DEFAULT_MAX) or DEFAULT_MAX

        if active_count >= max_for_tier:
            # Benign race — another job already filled the slot.  Not a warning.
            flogger.info(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: "
                f"capacity reached at fire time ({active_count}/{max_for_tier}) — benign race, skipping"
            )
            return {"success": True, "reason": "capacity_reached"}

        # ------------------------------------------------------------------
        # 6. Spawn the bounty
        # ------------------------------------------------------------------
        expiry_minutes: int = getattr(config, "bounty_expiry_minutes", None) or 480
        spawned_bounty = await bounty_service.spawn_bounty(db, guild_id, tier_lower, expiry_minutes=expiry_minutes)

        if spawned_bounty is None:
            flogger.warning(
                f"BountySpawnOne[{job_id}] guild={guild_id} tier={tier_lower}: "
                "spawn_bounty returned None (no criminals / route failure)"
            )
            return {"success": False, "reason": "spawn_failed"}

        flogger.info(
            f"BountySpawnOne[{job_id}] spawned bounty id={spawned_bounty.id} "
            f"guild={guild_id} tier={tier_lower} criminal={spawned_bounty.criminal_name}"
        )

        # ------------------------------------------------------------------
        # 7. Schedule expiry job (non-fatal)
        # ------------------------------------------------------------------
        try:
            await _schedule_expiry_job(job_id, spawned_bounty)
        except Exception as expiry_err:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"BountySpawnOne[{job_id}] failed to schedule expiry for bounty id={spawned_bounty.id}: {expiry_err}"
            )

        # ------------------------------------------------------------------
        # 8. Push bounty cache to gateway autocomplete (Phase 5b, non-fatal)
        # Push BEFORE announce so cache is populated when users react to
        # the Discord notification (B-P1).
        # ------------------------------------------------------------------
        await _push_bounty_cache(job_id, guild_id, db)

        # ------------------------------------------------------------------
        # 9. Announce to Discord (non-fatal)
        # ------------------------------------------------------------------
        try:
            await _announce_bounty(job_id, spawned_bounty, config, db)
        except Exception as ann_err:  # pylint: disable=broad-exception-caught
            flogger.error(f"BountySpawnOne[{job_id}] failed to announce bounty id={spawned_bounty.id}: {ann_err}")

        return {"success": True, "bounty_id": spawned_bounty.id, "tier": tier_lower}


# ---------------------------------------------------------------------------
# Helper: schedule bounty expiry
# ---------------------------------------------------------------------------


async def _schedule_expiry_job(parent_job_id: str, bounty) -> None:
    """Schedule a one-time job to expire *bounty* at its end_time.

    B.23a fix: schedules via the direct APScheduler Python API (in-process) instead of
    an HTTP POST to the scheduler router.  The HTTP approach was unreliable — failures
    (timeouts, transient errors, startup races) were silently dropped, leaving bounties
    without an expire job and their Discord announcements as permanent zombies.

    The direct API call is synchronous and infallible within the same process.
    If the scheduler instance is not yet available (e.g. test environments), falls back
    to the original HTTP POST.

    If end_time is not set or scheduling fails, the error is logged but does NOT
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

    # B.23a: try direct in-process scheduler first (no HTTP round-trip, no silent failure).
    try:
        from utils.job_executor import run_job
        from utils.scheduler_holder import get_scheduler

        scheduler = get_scheduler()
        if scheduler is not None:
            scheduler.add_job(
                run_job,
                trigger="date",
                run_date=bounty.end_time,
                args=[expiry_job_id, expiry_payload],
                id=expiry_job_id,
            )
            flogger.info(
                f"BountySpawnJob[{parent_job_id}] scheduled expiry job {expiry_job_id} "
                f"(direct API) for bounty id={bounty.id} at {bounty.end_time.isoformat()}"
            )
            return
        flogger.debug(
            f"BountySpawnJob[{parent_job_id}] scheduler not available via holder; falling back to HTTP scheduling"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] direct scheduler call failed for bounty id={bounty.id}: {e}; "
            "falling back to HTTP scheduling"
        )
        flogger.trace(traceback.format_exc())

    # Fallback: original HTTP POST to the scheduler router.
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
            f"(HTTP fallback) for bounty id={bounty.id} at {bounty.end_time.isoformat()}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountySpawnJob[{parent_job_id}] failed to schedule expiry for bounty id={bounty.id}: {e}")
        flogger.trace(traceback.format_exc())


# ---------------------------------------------------------------------------
# Helper: announce bounty to discord-gateway (per-division routing, SEG-07)
# ---------------------------------------------------------------------------


async def _announce_bounty(
    parent_job_id: str,
    bounty,
    config,
    db,
    pre_resolved_route_map_url: str | None = None,
) -> None:
    """POST a bounty announcement to the discord-gateway per-division channel.

    Flow (post-A.48 unified-loadout-render):
    1. Determine the target channel from config based on bounty.division.
    2. Optionally upload a route map PNG to config.image_channel_id.
    3. Look up the criminal's icon URL from the Criminal model (non-fatal).
    4. Build the structured announcement payload via
       `build_bounty_announcement_request` (LoadoutResponse + metadata).
    5. POST to gateway `/announcements/bounty/channel/{id}` so the gateway
       renders the unified embed via the shared loadout builder.
    6. Persist the Discord message ID in the DiscordMessage table.

    All HTTP failures are non-fatal — errors are logged and the function
    returns without propagating, so a failed announcement never aborts the
    spawn operation.

    Args:
        parent_job_id: The job ID for log correlation.
        bounty: Bounty ORM object (or mock with same attributes).
        config: GuildConfig object with per-division channel IDs,
                image_channel_id, and bounty_hunter_role_id.
        db: AsyncSession for persisting the DiscordMessage record.
    """
    # Deferred imports to match the executor's deferred-import pattern.
    from persist.repositories.criminal_repository import CriminalRepository
    from persist.repositories.discord_message_repository import DiscordMessageRepository

    from utils.bounty_announcement_payload import build_bounty_announcement_request

    target_channel_id = _get_division_channel_id(config, bounty.division)

    if target_channel_id is None:
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] guild={bounty.guild_id} div={bounty.division}: "
            "division channel not configured, skipping announcement"
        )
        return

    image_channel_id: int | None = getattr(config, "image_channel_id", None)
    # Use tier-specific role for @-mention in announcements
    bounty_hunter_role_id: int | None = _get_division_role_id(config, bounty.division)

    # ------------------------------------------------------------------
    # Step 1: Upload route map (optional, non-fatal)
    # If pre_resolved_route_map_url is supplied, the caller has already
    # uploaded the map (e.g. via the batch-upload endpoint) and we skip
    # the per-bounty single upload entirely.
    # ------------------------------------------------------------------
    route_map_url: str | None = pre_resolved_route_map_url

    if route_map_url is None and image_channel_id is not None:
        try:
            async with httpx.AsyncClient() as client:
                map_resp = await client.get(
                    f"{_SELF_BASE_URL}/bounties/{bounty.id}/map",
                    timeout=15,
                )
                map_resp.raise_for_status()
                png_bytes = map_resp.content

                upload_resp = await client.post(
                    f"{_GATEWAY_BASE_URL}/channels/{image_channel_id}/upload",
                    content=png_bytes,
                    headers={"X-Filename": f"route_map_{bounty.id}.png", "Content-Type": "image/png"},
                    timeout=60,
                )
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
                route_map_url = upload_data.get("data", {}).get("attachment_url")
                flogger.debug(
                    f"BountySpawnJob[{parent_job_id}] uploaded route map for bounty id={bounty.id}: {route_map_url}"
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"BountySpawnJob[{parent_job_id}] route map upload failed for bounty id={bounty.id}: "
                f"{type(e).__name__}: {e} — continuing without image"
            )
            route_map_url = None

    # ------------------------------------------------------------------
    # Step 2: Look up criminal icon (non-fatal if not found)
    # ------------------------------------------------------------------
    criminal_icon: str | None = None
    try:
        criminal_repo = CriminalRepository()
        criminal = await criminal_repo.get_by_name(db, bounty.criminal_name)
        if criminal is not None:
            criminal_icon = getattr(criminal, "icon", None) or None
            flogger.debug(
                f"BountySpawnJob[{parent_job_id}] criminal icon for {bounty.criminal_name!r}: {criminal_icon!r}"
            )
    except Exception as _icon_exc:  # pylint: disable=broad-exception-caught
        flogger.debug(
            f"BountySpawnJob[{parent_job_id}] could not fetch criminal icon for {bounty.criminal_name!r}: {_icon_exc}"
        )

    # ------------------------------------------------------------------
    # Step 3: Build the unified announcement request body (A.48).
    # ------------------------------------------------------------------
    announcement = await build_bounty_announcement_request(
        db,
        bounty,
        criminal_icon=criminal_icon,
        route_map_url=route_map_url,
        bounty_hunter_role_id=bounty_hunter_role_id,
        captured=False,
    )

    # ------------------------------------------------------------------
    # Step 4: POST announcement to the gateway's bounty-announcement endpoint
    # ------------------------------------------------------------------
    discord_message_id: int | None = None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/announcements/bounty/channel/{target_channel_id}",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        resp_data = resp.json()
        discord_message_id = resp_data.get("data", {}).get("id")
        flogger.info(
            f"BountySpawnJob[{parent_job_id}] announced bounty id={bounty.id} "
            f"guild={bounty.guild_id} to channel {target_channel_id} (msg_id={discord_message_id})"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"BountySpawnJob[{parent_job_id}] failed to announce bounty id={bounty.id} "
            f"to channel {target_channel_id}: {e}"
        )
        flogger.trace(traceback.format_exc())
        return

    # ------------------------------------------------------------------
    # Step 5: Persist DiscordMessage record
    # ------------------------------------------------------------------
    if discord_message_id is not None:
        try:
            import json

            msg_repo = DiscordMessageRepository()
            await msg_repo.create_or_update(
                db,
                {
                    "guild_id": bounty.guild_id,
                    "channel_id": target_channel_id,
                    "message_id": discord_message_id,
                    "message_type": "bounty_announcement",
                    "reference_id": bounty.id,
                    # Persist the structured request body (loadout_response + metadata)
                    # rather than a rendered embed dict; the gateway is now the
                    # rendering authority.
                    "embed_payload": json.dumps(announcement),
                },
            )
            flogger.debug(
                f"BountySpawnJob[{parent_job_id}] persisted DiscordMessage for "
                f"bounty id={bounty.id} guild={bounty.guild_id}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Non-fatal: the bounty is already live and announced. Failing to write
            # the DiscordMessage record means the failsafe cleanup executor will not
            # be able to identify this post by DB lookup and will treat it as an
            # untracked bot message — the secondary orphan sweep (age-based heuristic)
            # will catch and clean it up on the next :30 run.
            flogger.error(
                f"BountySpawnJob[{parent_job_id}] failed to persist DiscordMessage for "
                f"bounty id={bounty.id} guild={bounty.guild_id} "
                f"channel={target_channel_id} msg_id={discord_message_id}: {e} "
                f"— post is live but untracked; failsafe cleanup will handle it"
            )
            flogger.trace(traceback.format_exc())


# ---------------------------------------------------------------------------
# Helper: push active bounty list to gateway autocomplete cache (Phase 5b)
# ---------------------------------------------------------------------------


async def _push_bounty_cache(parent_job_id: str, guild_id: int, db) -> None:
    """Non-fatal push of the current active bounty list to the gateway autocomplete cache.

    Fetches the full active bounty list for the guild and POSTs it to the
    gateway's internal autocomplete endpoint so that the next bounty_autocomplete
    keystroke returns fresh data without a GET round-trip to bot-core.

    Args:
        parent_job_id: Job ID for log correlation.
        guild_id: The Discord guild ID to push bounties for.
        db: An open AsyncSession (within the caller's db_manager.get_session() block).
    """
    try:
        from persist.repositories.bounty_repository import BountyRepository

        bounty_repo = BountyRepository()
        bounties_raw = await bounty_repo.get_active_by_guild(db, guild_id)

        # Serialise ORM objects to plain dicts (exclude SQLAlchemy internal keys)
        bounty_dicts: list[dict] = []
        for b in bounties_raw:
            if isinstance(b, dict):
                bounty_dicts.append(b)
            else:
                d = {k: v for k, v in b.__dict__.items() if not k.startswith("_")}
                # Convert ALL datetime fields to ISO strings for JSON serialisation.
                # Generic check: any value with .isoformat() is a datetime/date —
                # this future-proofs against new datetime fields on the Bounty model
                # (e.g. issue_time, respawn_time) without requiring a maintained list.
                for key, val in list(d.items()):
                    if hasattr(val, "isoformat"):
                        d[key] = val.isoformat()
                bounty_dicts.append(d)

        gateway_url = f"{_GATEWAY_BASE_URL_SPAWN}/internal/autocomplete/bounty-cache/{guild_id}"
        token = os.getenv("INTERNAL_AUTH_TOKEN", "")
        headers = {"X-Internal-Auth": token} if token else {}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                gateway_url,
                json={"bounties": bounty_dicts},
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
        flogger.debug(
            f"BountySpawnJob[{parent_job_id}] pushed bounty cache for guild={guild_id} count={len(bounty_dicts)}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] failed to push bounty cache to gateway for guild={guild_id}: {e}"
        )


# ---------------------------------------------------------------------------
# Helper: announce bounty payout summary embed (Sub-task B)
# ---------------------------------------------------------------------------


async def _announce_payout_embed(parent_job_id: str, guild_id: int, tier: str, channel_id: int, db) -> None:
    """Non-fatal: POST a second "Payouts" embed to the tier's bounty channel.

    Fetches all active bounties for the guild, builds the payout summary embed,
    and posts it as a plain message to the discord-gateway's messages endpoint.

    Args:
        parent_job_id: Job ID for log correlation.
        guild_id: The Discord guild ID.
        tier: The bounty tier that just spawned (used for color-coding).
        channel_id: The Discord channel ID to post to.
        db: An open AsyncSession.
    """
    try:
        from persist.repositories.bounty_repository import BountyRepository

        from utils.bounty_announcement_payload import build_bounty_cap_payout_embed

        bounty_repo = BountyRepository()
        active_bounties = await bounty_repo.get_active_by_guild(db, guild_id)

        embed_dict = build_bounty_cap_payout_embed(active_bounties, capped_tier=tier)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages",
                json={"embeds": [embed_dict]},
                timeout=10,
            )
        resp.raise_for_status()
        flogger.debug(
            f"BountySpawnJob[{parent_job_id}] posted payout embed for guild={guild_id} tier={tier} channel={channel_id}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] failed to post payout embed for guild={guild_id} tier={tier}: {e}"
        )
