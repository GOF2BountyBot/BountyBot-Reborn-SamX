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

The original ``execute_bounty_spawn_job`` is kept intact for backward
compatibility with the admin spawn endpoint import.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import contextlib
import os
import random
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from random import uniform

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-spawn-executor")

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

# Default temperature value used when no guild-specific temperature is stored.
_DEFAULT_TEMPERATURE: float = 5.0


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
        # 8. Announce to Discord (non-fatal)
        # ------------------------------------------------------------------
        try:
            await _announce_bounty(job_id, spawned_bounty, config, db)
        except Exception as ann_err:  # pylint: disable=broad-exception-caught
            flogger.error(f"BountySpawnOne[{job_id}] failed to announce bounty id={spawned_bounty.id}: {ann_err}")

        return {"success": True, "bounty_id": spawned_bounty.id, "tier": tier_lower}


# ---------------------------------------------------------------------------
# ORIGINAL: execute_bounty_spawn_job — kept for backward compat + admin spawn
# ---------------------------------------------------------------------------


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
    # payload temperature is kept for backward-compat but per-guild config takes precedence
    _payload_temperature: float = float(payload.get("temperature", _DEFAULT_TEMPERATURE))

    total_spawned = 0
    guild_results: dict = {}

    try:
        async with db_manager.get_session() as db:
            bounty_service = BountyService()
            bounty_repo = BountyRepository()
            config_repo = ConfigRepository()

            # ------------------------------------------------------------------
            # Determine which guilds to process — always use ConfigRepository
            # ------------------------------------------------------------------
            if guild_id:
                # Single-guild mode: look up the real config for this guild.
                guild_configs = await config_repo.list_all(db)
                guild_configs = [c for c in guild_configs if c.guild_id == guild_id]
                if not guild_configs:
                    # Guild not yet configured — process with a minimal stand-in.
                    class _MinimalConfig:
                        pass

                    cfg = _MinimalConfig()
                    cfg.guild_id = guild_id
                    cfg.bronze_bounty_channel_id = None
                    cfg.silver_bounty_channel_id = None
                    cfg.gold_bounty_channel_id = None
                    cfg.platinum_bounty_channel_id = None
                    cfg.image_channel_id = None
                    cfg.bounty_hunter_role_id = None
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

                # ----------------------------------------------------------
                # Read per-guild bounty config (with defaults)
                # ----------------------------------------------------------
                bounty_max_per_tier: dict[str, int] = getattr(config, "bounty_max_per_tier", None) or {
                    "bronze": 3,
                    "silver": 3,
                    "gold": 3,
                    "platinum": 3,
                }
                bounty_spawn_interval_minutes: int = getattr(config, "bounty_spawn_interval_minutes", None) or 60
                next_spawn_check_at = getattr(config, "next_spawn_check_at", None)
                bounty_expiry_minutes: int = getattr(config, "bounty_expiry_minutes", None) or 480
                division_temperatures: dict[str, float] = getattr(config, "division_temperatures", None) or {}

                # ----------------------------------------------------------
                # Eligibility guard: skip guilds that aren't fully configured
                # ----------------------------------------------------------
                if not _is_guild_fully_configured(config):
                    flogger.info(
                        f"BountySpawnJob[{job_id}] skipping guild={gid}: "
                        "guild not fully configured (missing channel/role IDs)"
                    )
                    continue

                # ----------------------------------------------------------
                # Interval gating: skip guild if next_spawn_check_at is in the future
                # ----------------------------------------------------------
                now_ts = datetime.now(UTC)
                if next_spawn_check_at is not None and now_ts < next_spawn_check_at:
                    flogger.debug(
                        f"BountySpawnJob[{job_id}] guild={gid}: "
                        f"next_spawn_check_at={next_spawn_check_at.isoformat()} is in the future, skipping"
                    )
                    continue

                guild_spawned = 0
                division_results: dict = {}

                for div in divisions_to_check:
                    # Normalise: repository stores lowercase division names but
                    # the executor accepts either case.  BountyService.spawn_bounty
                    # expects lowercase.
                    div_lower = div.lower()

                    # Per-division max, gated by temperature
                    guild_div_max: int = bounty_max_per_tier.get(div_lower, 3)
                    temp: float = division_temperatures.get(div_lower, _DEFAULT_TEMPERATURE)
                    max_bounties: int = min(guild_div_max, TemperatureService.get_max_bounties(temp))

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
                    spawned_bounty = await bounty_service.spawn_bounty(
                        db, gid, div_lower, expiry_minutes=bounty_expiry_minutes
                    )

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
                    # Announce to discord-gateway (per-division routing)
                    # ----------------------------------------------------------
                    await _announce_bounty(job_id, spawned_bounty, config, db)

                    division_results[div] = {
                        "spawned": 1,
                        "bounty_id": spawned_bounty.id,
                        "criminal": spawned_bounty.criminal_name,
                        "reward": spawned_bounty.reward,
                        "end_time": (spawned_bounty.end_time.isoformat() if spawned_bounty.end_time else None),
                    }

                # ----------------------------------------------------------
                # Update next_spawn_check_at with randomized interval
                # ----------------------------------------------------------
                if hasattr(config, "next_spawn_check_at"):
                    try:
                        base = bounty_spawn_interval_minutes or 60
                        randomized = base * random.uniform(0.75, 1.25)
                        config.next_spawn_check_at = datetime.now(UTC) + timedelta(minutes=randomized)
                        await db.commit()
                        await db.refresh(config)
                        flogger.debug(
                            f"BountySpawnJob[{job_id}] guild={gid}: "
                            f"next_spawn_check_at set to {config.next_spawn_check_at.isoformat()}"
                        )
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        flogger.warning(
                            f"BountySpawnJob[{job_id}] guild={gid}: failed to update next_spawn_check_at: {e}"
                        )
                        with contextlib.suppress(Exception):
                            await db.rollback()

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


async def _announce_bounty(parent_job_id: str, bounty, config, db) -> None:
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
    # ------------------------------------------------------------------
    route_map_url: str | None = None

    if image_channel_id is not None:
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
                    timeout=15,
                )
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
                route_map_url = upload_data.get("data", {}).get("attachment_url")
                flogger.debug(
                    f"BountySpawnJob[{parent_job_id}] uploaded route map for bounty id={bounty.id}: {route_map_url}"
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"BountySpawnJob[{parent_job_id}] route map upload failed for bounty id={bounty.id}: {e} — "
                "continuing without image"
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
