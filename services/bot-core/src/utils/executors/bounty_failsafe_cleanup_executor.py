"""Bounty failsafe cleanup executor — hourly Discord-driven stale-post sweep.

Problem (B.83)
--------------
The primary cleanup path (``bounty_expire_executor``) fires a one-time job
scheduled at ``bounty.end_time``.  When this job is dropped (gateway
timeout, APScheduler restart, transient DB error) the Discord post stays
visible in the channel forever even though the bounty has expired or been
captured.

Strategy (Discord-first, per your design)
-----------------------------------------
Rather than relying on DB state alone this executor works *from the Discord
side*:

1. For each guild config iterate the configured bounty channel IDs (one per
   division: bronze / silver / gold / platinum).
2. Fetch the most recent messages from each channel via the gateway
   ``GET /channels/{cid}/messages?limit=100`` endpoint.
3. For every message ID returned, look it up in the ``discord_message`` table
   (type = "bounty_announcement").  This gives us the ``reference_id``
   (bounty ID).
4. Fetch the referenced bounty from the DB.  Classify it:
   - ``active``  AND ``end_time`` > now  → legitimately live, leave alone
   - ``active``  AND ``end_time`` ≤ now  → stale active (expire job was lost)
   - any non-active status (``expired``, ``captured``, ``cleared``, …)
   - ``None``  (bounty row deleted / never existed)                    → orphan
5. For all non-live cases: delete the Discord post and clean the DB record.

The sweep never touches bounty rows that are genuinely active; it only
cleans up the *visual* orphans in Discord.

Imports
-------
All ORM-related imports are deferred to function scope (executor pattern).

Non-fatal design
----------------
Failures in any individual guild, channel, or message are logged and
skipped; they never abort the sweep for the remaining items.
"""

import os as _os
import traceback
from datetime import UTC, datetime

import httpx
from shared.bblogger import get_logger

flogger = get_logger("bounty-failsafe-cleanup-executor")

# ---------------------------------------------------------------------------
# Service endpoints
# ---------------------------------------------------------------------------

_GATEWAY_HOST = _os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = _os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"

# Maximum messages to fetch per channel per sweep (Discord API max is 100).
_CHANNEL_MESSAGE_LIMIT = 100


# ---------------------------------------------------------------------------
# Division → channel-ID helper (mirrors bounty_spawn_executor)
# ---------------------------------------------------------------------------


def _get_division_channel_id(config, division: str) -> int | None:
    """Return the bounty channel ID for a division from a GuildConfig."""
    mapping = {
        "bronze": getattr(config, "bronze_bounty_channel_id", None),
        "silver": getattr(config, "silver_bounty_channel_id", None),
        "gold": getattr(config, "gold_bounty_channel_id", None),
        "platinum": getattr(config, "platinum_bounty_channel_id", None),
    }
    return mapping.get(division.lower())


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


async def execute_bounty_failsafe_cleanup_job(job_id: str, payload: dict) -> dict:
    """Hourly failsafe sweep: remove stale bounty posts from Discord channels.

    Payload fields (all optional)
    ------------------------------
    guild_id : int, optional
        Restrict sweep to this guild only.  Omit for all guilds.

    Returns
    -------
    dict
        Summary with counts of messages inspected, posts cleaned, and
        any errors encountered.
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.config_repository import ConfigRepository

    start_ts = datetime.now(UTC)
    flogger.info(f"BountyFailsafeCleanup[{job_id}] START")

    target_guild_id: int | None = payload.get("guild_id")

    total_messages_inspected = 0
    total_cleaned = 0
    total_errors = 0
    guild_results: dict = {}

    try:
        async with db_manager.get_session() as db:
            config_repo = ConfigRepository()

            if target_guild_id is not None:
                all_configs = await config_repo.list_all(db)
                guild_configs = [c for c in all_configs if c.guild_id == target_guild_id]
            else:
                guild_configs = await config_repo.list_all(db)

        if not guild_configs:
            flogger.info(f"BountyFailsafeCleanup[{job_id}] no guild configs found — nothing to do")
            return {"status": "success", "guilds_processed": 0, "total_cleaned": 0}

        for config in guild_configs:
            gid = config.guild_id
            g_inspected = 0
            g_cleaned = 0
            g_errors = 0

            for division in ("bronze", "silver", "gold", "platinum"):
                channel_id = _get_division_channel_id(config, division)
                if channel_id is None:
                    flogger.trace(
                        f"BountyFailsafeCleanup[{job_id}] guild={gid} div={division}: no channel configured — skip"
                    )
                    continue

                flogger.debug(
                    f"BountyFailsafeCleanup[{job_id}] guild={gid} div={division}: "
                    f"scanning channel={channel_id}"
                )

                try:
                    inspected, cleaned, errors = await _sweep_channel(
                        job_id=job_id,
                        guild_id=gid,
                        division=division,
                        channel_id=channel_id,
                    )
                    g_inspected += inspected
                    g_cleaned += cleaned
                    g_errors += errors
                except Exception as chan_err:  # pylint: disable=broad-exception-caught
                    flogger.error(
                        f"BountyFailsafeCleanup[{job_id}] guild={gid} div={division} "
                        f"channel={channel_id}: unhandled error — {chan_err}"
                    )
                    flogger.trace(traceback.format_exc())
                    g_errors += 1

            total_messages_inspected += g_inspected
            total_cleaned += g_cleaned
            total_errors += g_errors
            guild_results[gid] = {
                "messages_inspected": g_inspected,
                "cleaned": g_cleaned,
                "errors": g_errors,
            }

            flogger.info(
                f"BountyFailsafeCleanup[{job_id}] guild={gid}: "
                f"inspected={g_inspected} cleaned={g_cleaned} errors={g_errors}"
            )

        duration = (datetime.now(UTC) - start_ts).total_seconds()
        flogger.info(
            f"BountyFailsafeCleanup[{job_id}] DONE in {duration:.2f}s — "
            f"guilds={len(guild_results)} inspected={total_messages_inspected} "
            f"cleaned={total_cleaned} errors={total_errors}"
        )
        return {
            "status": "success",
            "guilds_processed": len(guild_results),
            "total_messages_inspected": total_messages_inspected,
            "total_cleaned": total_cleaned,
            "total_errors": total_errors,
            "results": guild_results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"BountyFailsafeCleanup[{job_id}] fatal error: {e}")
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Per-channel sweep
# ---------------------------------------------------------------------------


async def _sweep_channel(
    job_id: str,
    guild_id: int,
    division: str,
    channel_id: int,
) -> tuple[int, int, int]:
    """Sweep one bounty channel and clean up any non-live bounty posts.

    Returns
    -------
    tuple[int, int, int]
        (messages_inspected, posts_cleaned, errors)
    """
    # Deferred imports

    inspected = 0
    cleaned = 0
    errors = 0

    # ------------------------------------------------------------------
    # Step 1: Fetch recent messages from the Discord channel via gateway
    # ------------------------------------------------------------------
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages",
                params={"limit": _CHANNEL_MESSAGE_LIMIT},
                timeout=15,
            )
        resp.raise_for_status()
        messages_data = resp.json().get("data", [])
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
            f"channel={channel_id}: failed to fetch messages — {e}"
        )
        return 0, 0, 1

    if not messages_data:
        flogger.debug(
            f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
            f"channel={channel_id}: no messages returned"
        )
        return 0, 0, 0

    flogger.debug(
        f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
        f"channel={channel_id}: got {len(messages_data)} messages to inspect"
    )

    # ------------------------------------------------------------------
    # Step 2: For each message, look it up as a bounty_announcement
    # ------------------------------------------------------------------
    for msg in messages_data:
        discord_message_id: int | None = None
        try:
            raw_id = msg.get("id")
            if raw_id is None:
                continue
            discord_message_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        inspected += 1

        try:
            action, bounty_id = await _classify_and_clean_message(
                job_id=job_id,
                guild_id=guild_id,
                channel_id=channel_id,
                discord_message_id=discord_message_id,
            )
            if action == "cleaned":
                cleaned += 1
                flogger.info(
                    f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
                    f"channel={channel_id}: cleaned post msg_id={discord_message_id} "
                    f"bounty_id={bounty_id}"
                )
            elif action == "live":
                flogger.trace(
                    f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
                    f"channel={channel_id}: msg_id={discord_message_id} is live — skip"
                )
            elif action == "skip":
                flogger.trace(
                    f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
                    f"channel={channel_id}: msg_id={discord_message_id} not a bounty announcement — skip"
                )
        except Exception as msg_err:  # pylint: disable=broad-exception-caught
            errors += 1
            flogger.warning(
                f"BountyFailsafeCleanup[{job_id}] guild={guild_id} div={division} "
                f"channel={channel_id}: error processing msg_id={discord_message_id} — {msg_err}"
            )
            flogger.trace(traceback.format_exc())

    return inspected, cleaned, errors


# ---------------------------------------------------------------------------
# Per-message classification and cleanup
# ---------------------------------------------------------------------------


async def _classify_and_clean_message(
    job_id: str,
    guild_id: int,
    channel_id: int,
    discord_message_id: int,
) -> tuple[str, int | None]:
    """Classify a Discord message and clean it up if it is a non-live bounty post.

    Classification logic
    --------------------
    1. Look up the message in ``discord_message`` table by composite key
       (guild_id, channel_id, discord_message_id) with message_type =
       "bounty_announcement".
    2. If not found in our records: not our bounty post → ``skip``.
    3. Fetch the referenced bounty from the DB.
    4. If bounty is active AND end_time > now: legitimately live → ``live``.
    5. All other cases (expired, captured, cleared, missing, stale active):
       delete the Discord post and clean the DB record → ``cleaned``.

    Returns
    -------
    tuple[str, int | None]
        (action, bounty_id) where action is "live", "skip", or "cleaned".
    """
    # Deferred imports
    from persist.database.manager import db_manager
    from persist.repositories.bounty_repository import BountyRepository
    from persist.repositories.discord_message_repository import DiscordMessageRepository

    async with db_manager.get_session() as db:
        msg_repo = DiscordMessageRepository()
        bounty_repo = BountyRepository()

        # ------------------------------------------------------------------
        # Step 1: Look up the discord_message record by composite key
        # ------------------------------------------------------------------
        discord_msg = await msg_repo.get_by_composite_key(db, guild_id, channel_id, discord_message_id)

        if discord_msg is None:
            # No record for this message — not a managed bounty announcement.
            return "skip", None

        if discord_msg.message_type != "bounty_announcement":
            # Managed message but not a bounty announcement.
            return "skip", None

        bounty_id: int | None = discord_msg.reference_id

        if bounty_id is None:
            # Announcement record has no bounty reference — treat as orphan.
            flogger.warning(
                f"BountyFailsafeCleanup[{job_id}] discord_message id={discord_msg.id} "
                f"has null reference_id — treating as orphan"
            )
            await _delete_post_and_db_record(
                job_id, channel_id, discord_message_id, db, msg_repo, discord_msg
            )
            return "cleaned", None

        # ------------------------------------------------------------------
        # Step 2: Fetch the bounty
        # ------------------------------------------------------------------
        bounty = await bounty_repo.get_by_id(db, bounty_id)

        # ------------------------------------------------------------------
        # Step 3: Classify
        # ------------------------------------------------------------------
        now_utc = datetime.now(UTC)

        if bounty is not None and bounty.status == "active":
            end_time = bounty.end_time
            if end_time is not None:
                # Make end_time timezone-aware for comparison if needed
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=UTC)
                if end_time > now_utc:
                    # Genuinely live — leave it alone.
                    return "live", bounty_id

            # Active but end_time has passed (or is None — defensive): stale active.
            flogger.info(
                f"BountyFailsafeCleanup[{job_id}] bounty_id={bounty_id} is active but "
                f"end_time={bounty.end_time} has passed — marking expired"
            )
            # Mark the bounty expired in DB
            try:
                bounty.status = "expired"
                await db.commit()
                flogger.info(
                    f"BountyFailsafeCleanup[{job_id}] bounty_id={bounty_id} status set to 'expired'"
                )
            except Exception as upd_err:  # pylint: disable=broad-exception-caught
                flogger.error(
                    f"BountyFailsafeCleanup[{job_id}] failed to expire bounty_id={bounty_id}: {upd_err}"
                )
                await db.rollback()

        # For all non-live cases: delete the Discord post and clean the DB record.
        await _delete_post_and_db_record(
            job_id, channel_id, discord_message_id, db, msg_repo, discord_msg
        )

    return "cleaned", bounty_id


# ---------------------------------------------------------------------------
# Helper: delete Discord post + DiscordMessage DB record
# ---------------------------------------------------------------------------


async def _delete_post_and_db_record(
    job_id: str,
    channel_id: int,
    discord_message_id: int,
    db,
    msg_repo,
    discord_msg,
) -> None:
    """Delete the Discord post via gateway and remove the DiscordMessage DB record.

    Both steps are best-effort and non-fatal.
    """
    # ------------------------------------------------------------------
    # 1. Delete the Discord post
    # ------------------------------------------------------------------
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages/{discord_message_id}",
                timeout=10,
            )
        # 404 = already deleted — treat as success
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()
        flogger.debug(
            f"BountyFailsafeCleanup[{job_id}] deleted Discord post "
            f"channel={channel_id} msg_id={discord_message_id} (HTTP {resp.status_code})"
        )
    except Exception as del_err:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountyFailsafeCleanup[{job_id}] failed to delete Discord post "
            f"channel={channel_id} msg_id={discord_message_id}: {del_err}"
        )

    # ------------------------------------------------------------------
    # 2. Delete the DiscordMessage DB record
    # ------------------------------------------------------------------
    try:
        await msg_repo.delete_by_composite_key(
            db,
            discord_msg.guild_id,
            channel_id,
            discord_message_id,
        )
        flogger.debug(
            f"BountyFailsafeCleanup[{job_id}] deleted DiscordMessage record "
            f"id={discord_msg.id} for msg_id={discord_message_id}"
        )
    except Exception as db_err:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"BountyFailsafeCleanup[{job_id}] failed to delete DiscordMessage record "
            f"id={discord_msg.id}: {db_err}"
        )
