"""Shop refresh executor — refreshes guild shop stock on schedule.

Invoked by APScheduler via the JobExecutor dispatch.  The executor
delegates all shop business-logic to ShopService.refresh_shop() and uses
ConfigRepository.list_all() to enumerate guilds for bulk refreshes.

After a successful refresh, one announcement per refreshed tier is posted to
the discord-gateway ``POST /api/v1/channels/{shop_channel_id}/messages``
endpoint so players are notified that new stock is available for their tier.

After the announcement, the refreshed shop stock is also pushed to the
gateway's autocomplete cache endpoint so autocomplete keystrokes reflect
the new inventory without a GET round-trip (Phase 5b).

Announcement logic is implemented in ``utils.shop_announcement.announce_shop_refresh``
(the shared module) and forwarded here via the private ``_announce_shop_refresh``
wrapper to preserve the existing test surface.

Imports of service/repository classes are deferred to function scope so that
the executor module can be imported in test environments without requiring a
live database or all ORM dependencies to be present.
"""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from shared.bblogger import get_logger

from utils.shop_announcement import (
    announce_shop_refresh as _shared_announce,
)

flogger = get_logger("shop-refresh-executor")

# Tiers supported by the shop system.
_SHOP_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]

# Gateway base URL for push endpoints (shared with shop_announcement module)
_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def execute_shop_refresh_job(job_id: str, payload: dict) -> dict:
    """Execute a shop refresh job.

    Payload fields
    --------------
    guild_id : int, optional
        The guild to refresh.  When omitted, every guild whose shop schedule is
        due (``guild_configs.shop_next_refresh_at``) is refreshed and its next
        refresh time rolled forward; guilds not yet due are skipped.
    tier : str, optional
        Division tier (Bronze / Silver / Gold).  When omitted all three
        tiers are refreshed for the given guild.
    force_tech_level : int or None, optional
        Override the randomly selected tech level (1-10).

    Returns
    -------
    dict
        Summary of the refresh operation.

    WARNING — NO IDEMPOTENCY GUARD
    --------------------------------
    This job has no guard against double-execution.  It is correct only
    at WORKERS=1 (the default single-uvicorn-worker deployment).  If
    multi-worker is ever reintroduced, re-entrant invocations (from
    cadence overlap or a post-restart scheduler re-fire) would refresh
    shop stock twice in the same tick — visible to players and wasteful.
    The bulk path's due check stops sequential re-fires (the first run rolls
    ``shop_next_refresh_at`` forward) but not two runs racing at once.
    """
    # Deferred imports to avoid transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.models.guild_config import GuildConfig
    from persist.models.guild_shop import GuildShop
    from persist.repositories.config_repository import ConfigRepository
    from services.game_constants import GameConstants, resolve_constant
    from services.shop_service import ShopService, shop_refresh_gap
    from sqlalchemy import func, select, update

    start_ts = datetime.now(UTC)
    flogger.info(f"ShopRefreshJob[{job_id}] START")
    flogger.trace(f"ShopRefreshJob[{job_id}] payload: {payload}")

    guild_id = payload.get("guild_id")
    tier = payload.get("tier")
    force_tech_level = payload.get("force_tech_level")

    try:
        async with db_manager.get_session() as db:
            shop_service = ShopService()

            if guild_id and tier:
                # ── Single guild + single tier ──────────────────────────────
                result = await shop_service.refresh_shop(db, guild_id, tier, force_tech_level)
                flogger.info(f"ShopRefreshJob[{job_id}] completed for guild {guild_id}, tier {tier}")
                return {
                    "status": "success",
                    "guild_id": guild_id,
                    "tier": tier,
                    "result": result,
                }

            if guild_id:
                # ── Single guild, all tiers ────────────────────────────────
                results: dict = {}
                for t in _SHOP_TIERS:
                    results[t] = await shop_service.refresh_shop(db, guild_id, t, force_tech_level)
                flogger.info(f"ShopRefreshJob[{job_id}] completed for guild {guild_id}, all tiers")
                return {
                    "status": "success",
                    "guild_id": guild_id,
                    "results": results,
                }

            # ── Bulk refresh: every guild whose schedule is due, all tiers ──
            guild_configs = await ConfigRepository().list_all(db)
            rows = await db.execute(
                select(GuildShop.guild_id, func.max(GuildShop.last_restocked)).group_by(GuildShop.guild_id)
            )
            last_restocked = dict(rows.all())

        # Configs are detached from here on; expire_on_commit=False keeps their loaded attributes readable.
        now = datetime.now(UTC)
        due: list = []
        for config in guild_configs:
            due_at = config.shop_next_refresh_at
            if due_at is None:
                # Never scheduled (first cycle after rev 0038): last restock + interval; no shops yet → due now.
                last = last_restocked.get(config.guild_id)
                interval = resolve_constant(
                    config, "shop_refresh_interval_hours", GameConstants.SHOP_REFRESH_INTERVAL_HOURS
                )
                due_at = last + timedelta(hours=interval) if last else now
            if due_at <= now:
                due.append((config, due_at))
            else:
                flogger.debug(f"ShopRefreshJob[{job_id}] guild {config.guild_id} not due until {due_at.isoformat()}")

        skipped = len(guild_configs) - len(due)
        if not due:
            flogger.debug(f"ShopRefreshJob[{job_id}] no guild due ({skipped} checked)")
            return {"status": "success", "guilds_refreshed": 0, "guilds_skipped": skipped, "results": {}}

        # Pre-load all static game data once to avoid repeated DB queries per
        # guild x tier (at 1000 guilds ~420K item queries down to 4).  Own session,
        # so a failing guild's rollback can't expire the cached rows.
        async with db_manager.get_session() as db:
            await shop_service.preload_static_data(db)

        bulk_results: dict = {}
        try:
            for config, due_at in due:
                gid = config.guild_id
                try:
                    async with db_manager.get_session() as db:
                        tier_results = {
                            t: await shop_service.refresh_shop(db, gid, t, force_tech_level) for t in _SHOP_TIERS
                        }
                        # Roll from the previous due time (no drift); after missed cycles restart from now.
                        gap = shop_refresh_gap(config)
                        next_at = due_at + gap
                        if next_at <= now:
                            next_at = now + gap
                        await db.execute(
                            update(GuildConfig).where(GuildConfig.guild_id == gid).values(shop_next_refresh_at=next_at)
                        )
                        await db.commit()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    # One broken guild must not starve the rest; it stays due and retries next tick.
                    flogger.error(f"ShopRefreshJob[{job_id}] guild {gid} refresh failed: {e}")
                    continue
                flogger.info(f"ShopRefreshJob[{job_id}] guild {gid} refreshed, next refresh {next_at.isoformat()}")
                bulk_results[gid] = tier_results

                # ── Push + announce, only after the guild's refresh committed ──
                shop_channel_id = getattr(config, "shop_channel_id", None)
                # Prefer shop_announcements_role_id over bounty_hunter_role_id.
                # Only use it when it's a real integer ID (guards against MagicMock attrs in tests).
                _shop_ann_id = getattr(config, "shop_announcements_role_id", None)
                _bh_role_id = getattr(config, "bounty_hunter_role_id", None)
                mention_role_id = _shop_ann_id if isinstance(_shop_ann_id, int) else _bh_role_id
                for i, t in enumerate(_SHOP_TIERS):
                    items = tier_results[t].get("items") or []
                    flogger.info("ShopRefresh: guild=%s tier=%s — refreshed %d items", gid, t, len(items))
                    flogger.info("ShopRefresh: announcing %d items for guild=%s tier=%s", len(items), gid, t)
                    # Push BEFORE announce so the cache is populated when users
                    # react to the Discord notification (B-P1).
                    await _push_shop_cache(job_id, gid, t, items)
                    # Role mention only on the first tier (Bronze) to avoid 4 pings per refresh cycle.
                    await _announce_shop_refresh(
                        job_id,
                        gid,
                        shop_channel_id,
                        mention_role_id if i == 0 else None,
                        tier=t,
                        items=items,
                        tech_level=tier_results[t].get("tech_level"),
                    )
        finally:
            shop_service.clear_static_cache()

        duration = (datetime.now(UTC) - start_ts).total_seconds()
        flogger.info(
            f"ShopRefreshJob[{job_id}] bulk refresh completed: {len(bulk_results)} guilds refreshed,"
            f" {skipped} not due, in {duration:.2f}s"
        )
        return {
            "status": "success",
            "guilds_refreshed": len(bulk_results),
            "guilds_skipped": skipped,
            "results": bulk_results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"ShopRefreshJob[{job_id}] failed: {e}")
        raise


# ---------------------------------------------------------------------------
# Helper: announce shop refresh to discord-gateway
# ---------------------------------------------------------------------------


async def _announce_shop_refresh(
    parent_job_id: str,
    guild_id: int,
    channel_id: int | None,
    bounty_hunter_role_id: int | None = None,
    tier: str | None = None,
    items: list | None = None,
    tech_level: int | None = None,
) -> None:
    """Thin wrapper around the shared ``announce_shop_refresh`` helper.

    Delegates to ``utils.shop_announcement.announce_shop_refresh`` so that
    the same announcement logic is available to the admin router without
    duplicating the HTTP call or embed-payload construction.

    See ``utils.shop_announcement`` for full parameter documentation.
    """
    await _shared_announce(
        caller_label=f"ShopRefreshJob[{parent_job_id}]",
        guild_id=guild_id,
        channel_id=channel_id,
        bounty_hunter_role_id=bounty_hunter_role_id,
        tier=tier,
        items=items,
        tech_level=tech_level,
    )


# ---------------------------------------------------------------------------
# Helper: push shop stock to gateway autocomplete cache (Phase 5b)
# ---------------------------------------------------------------------------

# Maps every accepted tier spelling to its canonical URL path segment.
# Using a dict-value lookup (not str/quote of the user input) breaks CodeQL's
# taint chain: d[user_key] does not propagate taint from the key to the value
# when d is a compile-time constant.  The VALUES here are string literals, so
# the selected segment is never considered user-derived by static analysis.
_TIER_URL_SEGMENTS: dict[str, str] = {
    "Bronze": "Bronze",
    "bronze": "Bronze",
    "Silver": "Silver",
    "silver": "Silver",
    "Gold": "Gold",
    "gold": "Gold",
    "Platinum": "Platinum",
    "platinum": "Platinum",
}


async def _push_shop_cache(parent_job_id: str, guild_id: int, tier: str, items: list) -> None:
    """Non-fatal push of refreshed shop stock to the gateway autocomplete cache.

    Follows the same pattern as _announce_shop_refresh — errors are logged
    but never propagate so a failed push never aborts the refresh operation.

    Args:
        parent_job_id: Job ID for log correlation.
        guild_id: The Discord guild ID.
        tier: The shop tier (e.g. "Bronze").
        items: The refreshed list of shop items (as dicts or ORM objects
               serialised to dicts by refresh_shop).
    """
    # SSRF guard: guild_id is coerced to int (non-numeric raises ValueError).
    # tier is resolved via a compile-time constant dict: the URL segment comes
    # from the dict's own string literal, not from the user-supplied value, so
    # CodeQL cannot track taint from the caller into the URL path.
    try:
        safe_guild = int(guild_id)
        tier_segment = _TIER_URL_SEGMENTS.get(str(tier))
        if tier_segment is None:
            raise ValueError(f"unrecognised tier {tier!r}")
        gateway_url = (
            f"{_GATEWAY_BASE_URL}/internal/autocomplete/shop-cache/{quote(str(safe_guild), safe='')}/{tier_segment}"
        )
        token = os.getenv("INTERNAL_AUTH_TOKEN", "")
        headers = {"X-Internal-Auth": token} if token else {}
        # Serialise items: support both plain dicts and objects with __dict__
        serialised: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                d = dict(item)
            elif hasattr(item, "__dict__"):
                # ORM objects: exclude SQLAlchemy internal keys
                d = {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
            else:
                d = dict(vars(item))
            # Convert any datetime values to ISO strings for JSON serialisation
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            serialised.append(d)
        async with httpx.AsyncClient() as client:
            from shared.http_retry import with_transient_retry  # deferred — avoids forkserver mock-shared collision

            await with_transient_retry(
                client.post,
                gateway_url,
                json={"items": serialised},
                headers=headers,
                timeout=5.0,
            )
        flogger.debug(
            f"ShopRefreshJob[{parent_job_id}] pushed shop cache for guild={guild_id} tier={tier} "
            f"items={len(serialised)}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"ShopRefreshJob[{parent_job_id}] failed to push shop cache to gateway for "
            f"guild={guild_id} tier={tier}: {e}"
        )
