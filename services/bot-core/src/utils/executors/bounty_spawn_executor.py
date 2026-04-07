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
  6. Posts an announcement to the discord-gateway REST API using the per-division
     bounty board channel and the BountyAnnouncementBuilder rich embed.
  7. Optionally uploads a route map PNG to the guild's image channel.
  8. Persists the Discord message ID in the DiscordMessage table.

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

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-spawn-executor")

# ---------------------------------------------------------------------------
# Supported bounty divisions (matches BountyService / GameConstants)
# ---------------------------------------------------------------------------
_BOUNTY_DIVISIONS = ["Bronze", "Silver", "Gold", "Platinum"]

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
# Helper: announce bounty to discord-gateway (per-division routing, SEG-07)
# ---------------------------------------------------------------------------


async def _announce_bounty(parent_job_id: str, bounty, config, db) -> None:
    """POST a bounty announcement to the discord-gateway per-division channel.

    Flow:
    1. Determine the target channel from config based on bounty.division.
    2. Optionally upload a route map PNG to config.image_channel_id.
    3. Build the rich embed payload via BountyAnnouncementBuilder.
    4. POST the embed to the division bounty board channel.
    5. Persist the Discord message ID in the DiscordMessage table.

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
    from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder
    from persist.repositories.discord_message_repository import DiscordMessageRepository

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
    # Step 2: Build rich embed via BountyAnnouncementBuilder
    # ------------------------------------------------------------------
    end_time_unix = int(bounty.end_time.timestamp()) if bounty.end_time else 0

    builder = BountyAnnouncementBuilder()
    embed_data = builder.build_payload(
        {
            "criminal_name": bounty.criminal_name,
            "criminal_faction": bounty.criminal_faction or "Unknown",
            "division": bounty.division,
            "tech_level": bounty.tech_level,
            "reward": bounty.reward,
            "route": bounty.route or [],
            "end_time_unix": end_time_unix,
            "criminal_icon": None,
            "criminal_ship": getattr(bounty, "criminal_ship", None),
            "checked": getattr(bounty, "checked", None),
            "bounty_hunter_role_id": bounty_hunter_role_id,
            "route_map_url": route_map_url,
        }
    )

    # Map builder output → MessageCreateRequest body.
    # embed_data = {"content": str|None, "embed": dict}
    # The channel messages endpoint expects {"content": EmbedPayload, "text_content": str, "message_type": "default"}.
    announcement = {
        "content": embed_data["embed"],
        "text_content": embed_data.get("content"),  # Role mention (e.g. "<@&123>")
        "message_type": "default",
    }

    # ------------------------------------------------------------------
    # Step 3: POST announcement to the division bounty board channel
    # ------------------------------------------------------------------
    discord_message_id: int | None = None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{target_channel_id}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        resp_data = resp.json()
        discord_message_id = resp_data.get("data", {}).get("message_id")
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
    # Step 4: Persist DiscordMessage record
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
                    "embed_payload": json.dumps(embed_data["embed"]),
                },
            )
            flogger.debug(
                f"BountySpawnJob[{parent_job_id}] persisted DiscordMessage for "
                f"bounty id={bounty.id} guild={bounty.guild_id}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"BountySpawnJob[{parent_job_id}] failed to persist DiscordMessage for bounty id={bounty.id}: {e}"
            )
            flogger.trace(traceback.format_exc())
