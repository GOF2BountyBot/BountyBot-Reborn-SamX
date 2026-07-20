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
from urllib.parse import quote

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

# Timeout (seconds) for gateway calls that POST a Discord message (announce +
# cap-payout notice). These depend on Discord's own response latency: the POST
# can hang after Discord has already created the message. A 10s ceiling was too
# tight — a slow-but-successful announce tripped it, and the resulting rollback
# orphaned the post (bounty 11754). Configurable so it can be tuned without a
# redeploy; defaults to a more forgiving 30s.
_ANNOUNCE_TIMEOUT = float(os.getenv("BOUNTY_ANNOUNCE_TIMEOUT", "30"))


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
# Fix C: gap-aware fire-time scheduling with collision nudge
# ---------------------------------------------------------------------------

# Maximum +10s nudge iterations before we give up and accept the fire time.
# A cap prevents a non-terminating loop in pathological cases where the
# queued schedule is densely packed (which itself is highly unlikely).
_MAX_NUDGE_ITERATIONS = 10
# A new fire that lands within this many seconds of an already-queued fire
# is "too close" and gets nudged forward by +10s.
_COLLISION_THRESHOLD_SECONDS = 2.0
_NUDGE_INCREMENT_SECONDS = 10.0
# Minimum lead time between "now" and the scheduled fire — prevents a target
# that lands in the past (e.g. very recent active issues) from being scheduled
# behind the current clock.
_MIN_LEAD_SECONDS = 5.0


def _compute_next_fire_time(
    now_dt: datetime,
    interval_minutes: float,
    queued_fire_times: list[datetime],
    active_issue_times: list[datetime],
) -> datetime:
    """Compute the next fire time for a one-time bounty spawn job using
    gap-aware scheduling with bounded jitter and collision avoidance.

    Replaces the legacy i.i.d. ``uniform(interval-window, interval+window)``
    offset, which produced co-located fire times when multiple slots opened
    in the same orchestrator pass (the root cause of the same-criminal
    duplicate observed in production — two jobs scheduled within ms of each
    other could both run select_criminal before either committed).

    Algorithm
    ---------
    1. Build the set of "anchor" timestamps that already occupy the timeline
       for this (guild, tier): existing queued bounty_spawn_one fire times
       plus active bounty issue_times. (Anchors define when the previous
       spawns happened or will happen.)
    2. Target = ``max(anchors) + interval_minutes`` so the new fire is one
       ideal-spacing step after the most recent occupied point. If no
       anchors exist, target = ``now + interval_minutes / 2`` (mild stagger
       on cold start instead of firing immediately).
    3. Clamp target to ``now + MIN_LEAD_SECONDS`` so we never schedule in
       the past.
    4. Apply bounded jitter: ``target + uniform(-window, +window)`` where
       ``window = min(15, 0.25 * interval_minutes)`` (matches legacy intent).
    5. Collision avoidance: while the computed fire time is within
       ``COLLISION_THRESHOLD_SECONDS`` of any already-queued fire time,
       nudge it forward by ``NUDGE_INCREMENT_SECONDS``. Capped at
       ``MAX_NUDGE_ITERATIONS`` to prevent a non-terminating loop.

    Args:
        now_dt: Current time (UTC). Passed in for deterministic testing.
        interval_minutes: Ideal spacing between consecutive spawns (minutes).
            Typically ``config.bounty_spawn_interval_minutes``.
        queued_fire_times: Fire times of already-queued bounty_spawn_one jobs
            for this (guild, tier) — used both as anchors and for collision
            detection.
        active_issue_times: Issue times of currently-active bounties for this
            (guild, tier) — used as anchors only (they are in the past, not
            collision candidates).

    Returns:
        The fire time (timezone-aware datetime in UTC) for the new spawn job.
    """
    # Step 1: anchors = queued fires + active issues
    anchors: list[datetime] = list(queued_fire_times) + list(active_issue_times)

    # Step 2: target = max(anchors) + interval, or now + interval/2 on cold start
    if anchors:
        target_time = max(anchors) + timedelta(minutes=interval_minutes)
    else:
        target_time = now_dt + timedelta(minutes=interval_minutes / 2.0)

    # Step 3: never schedule in the past
    min_fire = now_dt + timedelta(seconds=_MIN_LEAD_SECONDS)
    target_time = max(target_time, min_fire)

    # Step 4: bounded jitter — matches legacy window calc
    window_minutes = min(15.0, 0.25 * interval_minutes)
    jitter_seconds = uniform(-window_minutes * 60.0, window_minutes * 60.0)
    fire_time = target_time + timedelta(seconds=jitter_seconds)

    # Re-clamp post-jitter so jitter can never push us into the past either.
    fire_time = max(fire_time, min_fire)

    # Step 5: collision-avoidance nudge against queued fires.
    for _ in range(_MAX_NUDGE_ITERATIONS):
        too_close = any(
            abs((fire_time - qt).total_seconds()) < _COLLISION_THRESHOLD_SECONDS for qt in queued_fire_times
        )
        if not too_close:
            return fire_time
        fire_time += timedelta(seconds=_NUDGE_INCREMENT_SECONDS)

    # Loop exhausted without finding a clear slot — accept current fire_time.
    flogger.warning(
        f"_compute_next_fire_time: exhausted {_MAX_NUDGE_ITERATIONS} nudge iterations; "
        f"using fire_time={fire_time.isoformat()} (queued_count={len(queued_fire_times)})"
    )
    return fire_time


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
                    # Count active bounties + collect their issue_times for
                    # gap-aware scheduling (Fix C).
                    # --------------------------------------------------
                    active_bounties = await bounty_repo.get_active_by_guild_and_division(db, gid, tier_lower)
                    active_count = len(active_bounties)
                    active_issue_times: list[datetime] = [
                        b.issue_time for b in active_bounties if b.issue_time is not None
                    ]

                    # --------------------------------------------------
                    # Read already-queued one-time spawn jobs in scheduler.
                    # We need BOTH the count (for capacity gate) AND the
                    # fire times (for gap-aware scheduling, Fix C).
                    # Uses the APScheduler API instead of raw SQL so that all
                    # DB access goes through SQLAlchemy / the scheduler layer.
                    # --------------------------------------------------
                    from utils.scheduler_holder import get_scheduler

                    _scheduler = get_scheduler()
                    _job_id_prefix = f"bounty_spawn_{gid}_{tier_lower}_"
                    if _scheduler is not None:
                        queued_fire_times: list[datetime] = [
                            job.next_run_time
                            for job in _scheduler.get_jobs()
                            if job.id.startswith(_job_id_prefix) and job.next_run_time is not None
                        ]
                    else:
                        queued_fire_times = []
                    queued_count = len(queued_fire_times)

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
                    # Fix C: gap-aware fire-time scheduling with bounded
                    # jitter and collision-nudge. Replaces the legacy i.i.d.
                    # uniform(interval-window, interval+window) offset.
                    # --------------------------------------------------
                    now_dt = datetime.now(UTC)
                    fire_time = _compute_next_fire_time(
                        now_dt=now_dt,
                        interval_minutes=float(interval_minutes),
                        queued_fire_times=queued_fire_times,
                        active_issue_times=active_issue_times,
                    )

                    # --------------------------------------------------
                    # Schedule one-time bounty_spawn_one job — direct
                    # in-process call (P6-T8: no HTTP loopback).
                    # Uses the same scheduler instance registered by
                    # main.py lifespan via scheduler_holder.set_scheduler.
                    # --------------------------------------------------
                    spawn_job_id = f"bounty_spawn_{gid}_{tier_lower}_{uuid.uuid4()}"
                    one_time_payload = {
                        "job_type": "bounty_spawn_one",
                        "guild_id": gid,
                        "tier": tier_lower,
                    }

                    try:
                        from utils.job_executor import run_job
                        from utils.scheduler_holder import get_scheduler

                        _spawn_scheduler = get_scheduler()
                        if _spawn_scheduler is None:
                            raise RuntimeError("scheduler not available via holder")
                        _spawn_scheduler.add_job(
                            run_job,
                            trigger="date",
                            run_date=fire_time,
                            args=[spawn_job_id, one_time_payload],
                            id=spawn_job_id,
                        )
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

    # Session A: all DB work — config, eligibility, spawn, commit, expiry schedule,
    # cache push, and pre-fetching of announcement data.  The session is released
    # back to the pool as soon as this block exits, BEFORE any external httpx call
    # is made (P6-T7: don't pin a DB connection across network I/O).
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
        # 5. Re-check active count (cadence-overlap / restart guard)
        # ------------------------------------------------------------------
        active_count = await bounty_repo.count_active_by_guild_and_division(db, guild_id, tier_lower)
        bounty_max_per_tier: dict = getattr(config, "bounty_max_per_tier", None) or {}
        max_for_tier: int = (bounty_max_per_tier or {}).get(tier_lower, DEFAULT_MAX) or DEFAULT_MAX

        if active_count >= max_for_tier:
            # Slot already full — a prior cadence tick filled it, or the
            # scheduler re-fired this job after a restart.  Not a warning.
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

        # ------------------------------------------------------------------
        # Fix B: EARLY COMMIT. Make the new bounty visible to subsequent DB
        # reads *before* the long-running announce step. Shrinks the TOCTOU
        # window for a back-to-back cadence tick (two spawn jobs scheduled
        # close together) from "until session exits after Discord round-trip"
        # (which can be 10+ s on slow Discord) to roughly the duration of the
        # spawn_bounty INSERT itself (~tens of ms). Combined with the
        # gap-aware fire-time spacing (Fix C, ≥10s apart minimum), same-
        # criminal double-selection is effectively impossible without DB outage.
        # ------------------------------------------------------------------
        await db.commit()
        bounty_id = spawned_bounty.id  # capture in case rollback expires the instance

        flogger.info(
            f"BountySpawnOne[{job_id}] spawned bounty id={bounty_id} "
            f"guild={guild_id} tier={tier_lower} criminal={spawned_bounty.criminal_name} (committed)"
        )

        # ------------------------------------------------------------------
        # 7. Schedule expiry job (non-fatal — failsafe :30 cleanup catches
        # bounties whose expiry was missed). Capture expiry_job_id so the
        # compensating rollback can cancel it if the announce fails.
        # ------------------------------------------------------------------
        expiry_job_id: str | None = None
        try:
            expiry_job_id = await _schedule_expiry_job(job_id, spawned_bounty)
        except Exception as expiry_err:  # pylint: disable=broad-exception-caught
            flogger.error(f"BountySpawnOne[{job_id}] failed to schedule expiry for bounty id={bounty_id}: {expiry_err}")

        # ------------------------------------------------------------------
        # 8. Push bounty cache to gateway autocomplete (Phase 5b, non-fatal)
        # Push BEFORE announce so cache is populated when users react to
        # the Discord notification (B-P1).
        # ------------------------------------------------------------------
        await _push_bounty_cache(job_id, guild_id, db)

        # ------------------------------------------------------------------
        # P6-T7: Pre-fetch announcement data while the session is still open
        # so the session can be released before the external httpx announce
        # call.  criminal_icon and pre_built_announcement are passed to
        # _announce_bounty which will use them directly (skipping its own DB
        # reads) and open a fresh short-lived session only for the
        # DiscordMessage write.
        # ------------------------------------------------------------------
        _pre_criminal_icon: str | None = None
        try:
            from persist.repositories.criminal_repository import CriminalRepository

            _criminal_repo = CriminalRepository()
            _criminal = await _criminal_repo.get_by_name(db, spawned_bounty.criminal_name)
            if _criminal is not None:
                _pre_criminal_icon = getattr(_criminal, "icon", None) or None
        except Exception as _icon_exc:  # pylint: disable=broad-exception-caught
            flogger.debug(
                f"BountySpawnOne[{job_id}] pre-fetch: could not fetch criminal icon "
                f"for {spawned_bounty.criminal_name!r}: {_icon_exc}"
            )

        _pre_bounty_hunter_role_id: int | None = _get_division_role_id(config, tier_lower)
        _pre_announcement: dict | None = None
        try:
            from utils.bounty_announcement_payload import build_bounty_announcement_request

            _pre_announcement = await build_bounty_announcement_request(
                db,
                spawned_bounty,
                criminal_icon=_pre_criminal_icon,
                route_map_url=None,  # route map upload happens outside the session
                bounty_hunter_role_id=_pre_bounty_hunter_role_id,
                captured=False,
            )
        except Exception as _ann_pre_exc:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"BountySpawnOne[{job_id}] pre-fetch: announcement pre-build failed "
                f"for bounty id={bounty_id}: {_ann_pre_exc} — announce will fall back to inline build"
            )

    # Session A is now closed; DB connection returned to pool (P6-T7).

    # ------------------------------------------------------------------
    # 9. Announce to Discord. Fix B: now CRITICAL. The announce helper
    # returns a structured result so the executor can perform a
    # compensating rollback (delete post, delete bounty row, cancel
    # expiry) if either the HTTP announce OR the DiscordMessage write
    # fails. Without this, the bounty would be orphaned in the DB and
    # users would see an unmanageable post until the failsafe cleanup
    # at :30 reaps it.
    #
    # P6-T7: called with pre_built_announcement + db=None so no DB
    # connection is held across the external httpx announce POST.
    # _announce_bounty opens its own short-lived session only for the
    # DiscordMessage write (step 5 of its flow).
    # ------------------------------------------------------------------
    try:
        announce_result = await _announce_bounty(
            job_id,
            spawned_bounty,
            config,
            db=None,
            pre_built_announcement=_pre_announcement,
        )
    except Exception as ann_err:  # pylint: disable=broad-exception-caught
        # Unexpected exception from announce — treat as announce failure.
        flogger.error(
            f"BountySpawnOne[{job_id}] unexpected exception in _announce_bounty "
            f"for bounty id={bounty_id}: {type(ann_err).__name__}: {ann_err}"
        )
        flogger.trace(traceback.format_exc())
        announce_result = {
            "success": False,
            "failure_phase": "announce",
            "discord_message_id": None,
            "channel_id": _get_division_channel_id(config, tier_lower),
        }

    if not announce_result.get("success"):
        failure_phase = announce_result.get("failure_phase") or "unknown"
        flogger.error(
            f"BountySpawnOne[{job_id}] announce failed (phase={failure_phase}) "
            f"for bounty id={bounty_id} guild={guild_id} tier={tier_lower} — "
            f"performing compensating rollback"
        )
        # P6-T7: _compensate_failed_spawn opens its own session (no db passed).
        rollback_result = await _compensate_failed_spawn(
            parent_job_id=job_id,
            bounty_id=bounty_id,
            guild_id=guild_id,
            expiry_job_id=expiry_job_id,
            discord_message_id=announce_result.get("discord_message_id"),
            channel_id=announce_result.get("channel_id"),
        )
        return {
            "success": False,
            "reason": "announce_failed_rolled_back",
            "failure_phase": failure_phase,
            "bounty_id": bounty_id,
            "rollback": rollback_result,
            "tier": tier_lower,
        }

    return {"success": True, "bounty_id": bounty_id, "tier": tier_lower}


# ---------------------------------------------------------------------------
# Helper: schedule bounty expiry
# ---------------------------------------------------------------------------


async def _schedule_expiry_job(parent_job_id: str, bounty) -> str | None:
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

    Returns
    -------
    str | None
        The scheduled expiry job's APScheduler ID on success, or None if
        scheduling could not be completed (no end_time or scheduling failed).
        The caller uses this ID to cancel the job during compensating
        rollback (Fix B).
    """
    if bounty.end_time is None:
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] bounty id={bounty.id} has no end_time; skipping expiry scheduling"
        )
        return None

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
            return expiry_job_id
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
        return expiry_job_id
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountySpawnJob[{parent_job_id}] failed to schedule expiry for bounty id={bounty.id}: {e}")
        flogger.trace(traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# Helper: announce bounty to discord-gateway (per-division routing, SEG-07)
# ---------------------------------------------------------------------------


async def _announce_bounty(
    parent_job_id: str,
    bounty,
    config,
    db=None,
    pre_resolved_route_map_url: str | None = None,
    pre_built_announcement: dict | None = None,
) -> dict:
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

    Returns a structured result so the caller can perform compensating
    rollback (Fix B) if either the announce HTTP call or the DiscordMessage
    DB write fails. Route-map upload failure is still non-fatal (the
    announcement proceeds without an image).

    Args:
        parent_job_id: The job ID for log correlation.
        bounty: Bounty ORM object (or mock with same attributes).
        config: GuildConfig object with per-division channel IDs,
                image_channel_id, and bounty_hunter_role_id.
        db: AsyncSession for persisting the DiscordMessage record, or
            ``None`` (P6-T7 executor path).  When ``None``, steps 2/3/4
            (DB reads) are skipped if ``pre_built_announcement`` is
            provided; step 6 (DiscordMessage write) opens its own
            short-lived session so no DB connection is held across the
            external httpx announce POST.
        pre_resolved_route_map_url: Already-uploaded route map CDN URL;
            when provided the per-bounty upload (step 2) is skipped.
        pre_built_announcement: Announcement dict pre-built by the
            executor inside its session block (P6-T7).  When provided,
            steps 3 and 4 (criminal icon + build_bounty_announcement_request)
            are skipped.  The route map URL (step 2) is still fetched if
            needed — it updates ``pre_built_announcement["metadata"]["image_url"]``
            in-place so the announce POST includes the image.

    Returns:
        Dict with keys:
            - ``success`` (bool): True only if both HTTP announce AND
              DiscordMessage DB write succeeded.
            - ``failure_phase`` (str | None): ``"announce"`` if HTTP failed,
              ``"msg_db"`` if HTTP succeeded but DB write failed,
              ``"misconfigured"`` if no channel configured, else None.
            - ``discord_message_id`` (int | None): The posted message ID
              if the HTTP call succeeded, else None. Used by compensating
              rollback to also DELETE the post.
            - ``channel_id`` (int | None): The channel the post landed in
              (or was attempted in), if any.
    """
    # Deferred imports to match the executor's deferred-import pattern.
    from persist.repositories.discord_message_repository import DiscordMessageRepository

    target_channel_id = _get_division_channel_id(config, bounty.division)

    if target_channel_id is None:
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] guild={bounty.guild_id} div={bounty.division}: "
            "division channel not configured, skipping announcement"
        )
        return {
            "success": False,
            "failure_phase": "misconfigured",
            "discord_message_id": None,
            "channel_id": None,
        }

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

    if pre_built_announcement is not None:
        # P6-T7 executor path: announcement was built inside the session block
        # (with route_map_url=None).  Patch the image_url now that the route
        # map upload has resolved (or remained None).
        if route_map_url is not None:
            meta = pre_built_announcement.get("metadata")
            if isinstance(meta, dict):
                meta["image_url"] = route_map_url
        announcement = pre_built_announcement
    else:
        # Standard path (router caller with a live db session).
        from persist.repositories.criminal_repository import CriminalRepository

        from utils.bounty_announcement_payload import build_bounty_announcement_request

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
                f"BountySpawnJob[{parent_job_id}] could not fetch criminal icon "
                f"for {bounty.criminal_name!r}: {_icon_exc}"
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
    # Step 4: POST announcement to the gateway's bounty-announcement endpoint.
    # No DB session is held across this call on the P6-T7 executor path
    # (session A was already released before _announce_bounty was invoked;
    # session B for the DiscordMessage write opens only AFTER this POST).
    # ------------------------------------------------------------------
    discord_message_id: int | None = None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/announcements/bounty/channel/{target_channel_id}",
                json=announcement,
                timeout=_ANNOUNCE_TIMEOUT,
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
        return {
            "success": False,
            "failure_phase": "announce",
            "discord_message_id": None,
            "channel_id": target_channel_id,
        }

    # ------------------------------------------------------------------
    # Step 5: Persist DiscordMessage record.
    # P6-T7 executor path (db=None): open a short-lived session for this
    # write only — no connection has been held across the announce POST.
    # Router path (db provided): use the caller's session as before.
    # ------------------------------------------------------------------
    if discord_message_id is not None:
        import json

        msg_repo = DiscordMessageRepository()
        msg_data = {
            "guild_id": bounty.guild_id,
            "channel_id": target_channel_id,
            "message_id": discord_message_id,
            "message_type": "bounty_announcement",
            "reference_id": bounty.id,
            # Persist the structured request body (loadout_response + metadata)
            # rather than a rendered embed dict; the gateway is now the
            # rendering authority.
            "embed_payload": json.dumps(announcement),
        }

        if db is not None:
            # Router path: use caller's session.
            try:
                await msg_repo.create_or_update(db, msg_data)
                flogger.debug(
                    f"BountySpawnJob[{parent_job_id}] persisted DiscordMessage for "
                    f"bounty id={bounty.id} guild={bounty.guild_id}"
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(
                    f"BountySpawnJob[{parent_job_id}] failed to persist DiscordMessage for "
                    f"bounty id={bounty.id} guild={bounty.guild_id} "
                    f"channel={target_channel_id} msg_id={discord_message_id}: {e} "
                    f"— triggering compensating rollback"
                )
                flogger.trace(traceback.format_exc())
                return {
                    "success": False,
                    "failure_phase": "msg_db",
                    "discord_message_id": discord_message_id,
                    "channel_id": target_channel_id,
                }
        else:
            # P6-T7 executor path: open own session for the DiscordMessage write.
            from persist.database.manager import db_manager

            try:
                async with db_manager.get_session() as msg_db:
                    await msg_repo.create_or_update(msg_db, msg_data)
                flogger.debug(
                    f"BountySpawnJob[{parent_job_id}] persisted DiscordMessage for "
                    f"bounty id={bounty.id} guild={bounty.guild_id}"
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(
                    f"BountySpawnJob[{parent_job_id}] failed to persist DiscordMessage for "
                    f"bounty id={bounty.id} guild={bounty.guild_id} "
                    f"channel={target_channel_id} msg_id={discord_message_id}: {e} "
                    f"— triggering compensating rollback"
                )
                flogger.trace(traceback.format_exc())
                return {
                    "success": False,
                    "failure_phase": "msg_db",
                    "discord_message_id": discord_message_id,
                    "channel_id": target_channel_id,
                }

    return {
        "success": True,
        "failure_phase": None,
        "discord_message_id": discord_message_id,
        "channel_id": target_channel_id,
    }


# ---------------------------------------------------------------------------
# Fix B: Compensating rollback for failed bounty spawn announce
# ---------------------------------------------------------------------------


async def _compensate_failed_spawn(
    parent_job_id: str,
    bounty_id: int,
    guild_id: int,
    expiry_job_id: str | None,
    discord_message_id: int | None,
    channel_id: int | None,
) -> dict:
    """Compensating rollback after Fix B's early commit.

    Called when the announce phase (either HTTP POST to gateway or the
    DiscordMessage DB write) fails AFTER the bounty row has already been
    committed. Without this, the bounty would remain in the DB without a
    live/manageable Discord post — a state that the failsafe cleanup would
    eventually catch at :30, but only after up to an hour of user confusion.

    Each step runs in its own try/except so a failure in one does not
    prevent the others from running. No retries — orphans from this path
    are caught by the failsafe cleanup at :30 as a last resort.

    P6-T7: the ``db`` parameter was removed.  Step 3 (bounty row DELETE)
    and step 4 (cache re-push) now open their own short-lived sessions so
    no DB connection is held across the external httpx calls in steps 1/2.

    Args:
        parent_job_id: Job ID for log correlation.
        bounty_id: The committed bounty row's ID — must be DELETEd.
        guild_id: Guild ID — used for the cache re-push.
        expiry_job_id: APScheduler ID of the previously-scheduled expiry
            job, or None if scheduling failed earlier. Will be cancelled.
        discord_message_id: ID of the live Discord post, or None if the
            announce HTTP call did not succeed. If present, the post
            will be DELETEd via the gateway.
        channel_id: Channel that the post lives in. Required alongside
            discord_message_id to issue the gateway DELETE.

    Returns:
        Dict summarising which steps succeeded / failed (used by tests
        and by the executor's structured return value).
    """
    from persist.database.manager import db_manager

    result = {
        "post_deleted": False,
        "expiry_cancelled": False,
        "bounty_deleted": False,
        "cache_repushed": False,
    }

    # Step 1: DELETE the Discord post (if it exists). Independent try/except.
    if discord_message_id is not None and channel_id is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages/{discord_message_id}",
                    timeout=10,
                )
            # 200/204 success; 404 means post already gone (acceptable).
            if resp.status_code in (200, 204, 404):
                result["post_deleted"] = True
                flogger.info(
                    f"BountySpawnRollback[{parent_job_id}] deleted Discord post "
                    f"channel={channel_id} msg_id={discord_message_id} status={resp.status_code}"
                )
            else:
                resp.raise_for_status()
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"BountySpawnRollback[{parent_job_id}] failed to delete Discord post "
                f"channel={channel_id} msg_id={discord_message_id}: {type(e).__name__}: {e}"
            )

    # Step 2: Cancel scheduled expiry job (if any). Independent try/except.
    if expiry_job_id is not None:
        try:
            from utils.scheduler_holder import get_scheduler

            scheduler = get_scheduler()
            if scheduler is not None:
                # remove_job raises if the job no longer exists; that's fine.
                try:
                    scheduler.remove_job(expiry_job_id)
                    result["expiry_cancelled"] = True
                    flogger.info(
                        f"BountySpawnRollback[{parent_job_id}] cancelled expiry job "
                        f"id={expiry_job_id} for bounty id={bounty_id}"
                    )
                except Exception as remove_err:
                    # Job already fired or does not exist — acceptable.
                    flogger.debug(
                        f"BountySpawnRollback[{parent_job_id}] expiry job {expiry_job_id} "
                        f"could not be removed (already fired or missing): {remove_err}"
                    )
                    result["expiry_cancelled"] = True
            else:
                # No in-process scheduler — fall back to HTTP DELETE via the
                # scheduler API.
                async with httpx.AsyncClient() as client:
                    resp = await client.delete(
                        f"{_SELF_BASE_URL}/jobs/{expiry_job_id}",
                        timeout=10,
                    )
                if resp.status_code in (200, 204, 404):
                    result["expiry_cancelled"] = True
                    flogger.info(
                        f"BountySpawnRollback[{parent_job_id}] cancelled expiry job "
                        f"id={expiry_job_id} via HTTP status={resp.status_code}"
                    )
                else:
                    resp.raise_for_status()
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"BountySpawnRollback[{parent_job_id}] failed to cancel expiry job "
                f"id={expiry_job_id}: {type(e).__name__}: {e}"
            )

    # Step 3: DELETE the bounty row. Independent try/except.
    # P6-T7: opens own session (no db parameter any more).
    try:
        from persist.repositories.bounty_repository import BountyRepository

        bounty_repo = BountyRepository()
        async with db_manager.get_session() as del_db:
            bounty_row = await bounty_repo.get_by_id(del_db, bounty_id)
            if bounty_row is not None:
                await del_db.delete(bounty_row)
                await del_db.commit()
                result["bounty_deleted"] = True
                flogger.info(f"BountySpawnRollback[{parent_job_id}] deleted bounty row id={bounty_id}")
            else:
                # Already gone — treat as success.
                result["bounty_deleted"] = True
                flogger.debug(
                    f"BountySpawnRollback[{parent_job_id}] bounty row id={bounty_id} already absent — nothing to delete"
                )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"BountySpawnRollback[{parent_job_id}] failed to delete bounty row "
            f"id={bounty_id}: {type(e).__name__}: {e} — failsafe cleanup will reap"
        )

    # Step 4: Re-push the now-shortened bounty cache to the gateway so
    # autocomplete reflects the rollback. Independent try/except.
    # P6-T7: db=None path — _push_bounty_cache opens its own session.
    try:
        await _push_bounty_cache(parent_job_id, guild_id, db=None)
        result["cache_repushed"] = True
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountySpawnRollback[{parent_job_id}] cache re-push failed: "
            f"{type(e).__name__}: {e} — gateway 6min refresh will reconcile"
        )

    return result


# ---------------------------------------------------------------------------
# Helper: push active bounty list to gateway autocomplete cache (Phase 5b)
# ---------------------------------------------------------------------------


async def _push_bounty_cache(parent_job_id: str, guild_id: int, db=None) -> None:
    """Non-fatal push of the current active bounty list to the gateway autocomplete cache.

    Fetches the full active bounty list for the guild and POSTs it to the
    gateway's internal autocomplete endpoint so that the next bounty_autocomplete
    keystroke returns fresh data without a GET round-trip to bot-core.

    Args:
        parent_job_id: Job ID for log correlation.
        guild_id: The Discord guild ID to push bounties for.
        db: An open AsyncSession, or ``None`` (P6-T7 executor path).  When
            ``None`` the function opens its own short-lived session for the
            DB read so that no connection is held across the gateway POST.
    """
    try:
        from persist.repositories.bounty_repository import BountyRepository

        bounty_repo = BountyRepository()

        if db is None:
            from persist.database.manager import db_manager

            async with db_manager.get_session() as own_db:
                bounties_raw = await bounty_repo.get_active_by_guild(own_db, guild_id)
        else:
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

        # SSRF guard: coerce to int — non-numeric values raise ValueError,
        # caught by the surrounding try/except as a warning.
        safe_guild = int(guild_id)
        gateway_url = f"{_GATEWAY_BASE_URL_SPAWN}/internal/autocomplete/bounty-cache/{quote(str(safe_guild), safe='')}"
        token = os.getenv("INTERNAL_AUTH_TOKEN", "")
        headers = {"X-Internal-Auth": token} if token else {}
        async with httpx.AsyncClient() as client:
            from shared.http_retry import with_transient_retry  # deferred — avoids forkserver mock-shared collision

            await with_transient_retry(
                client.post,
                gateway_url,
                json={"bounties": bounty_dicts},
                headers=headers,
                timeout=5.0,
            )
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
                timeout=_ANNOUNCE_TIMEOUT,
            )
        resp.raise_for_status()
        flogger.debug(
            f"BountySpawnJob[{parent_job_id}] posted payout embed for guild={guild_id} tier={tier} channel={channel_id}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountySpawnJob[{parent_job_id}] failed to post payout embed for guild={guild_id} tier={tier}: {e}"
        )
