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
from datetime import UTC, datetime

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
        The guild to refresh.  When omitted all guilds are refreshed.
    tier : str, optional
        Division tier (Bronze / Silver / Gold).  When omitted all three
        tiers are refreshed for the given guild.
    force_tech_level : int or None, optional
        Override the randomly selected tech level (1-9).

    Returns
    -------
    dict
        Summary of the refresh operation.
    """
    # Deferred imports to avoid transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.config_repository import ConfigRepository
    from services.shop_service import ShopService

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

            # ── Bulk refresh: all guilds, all tiers ────────────────────────
            flogger.info(f"ShopRefreshJob[{job_id}] bulk refresh started")
            config_repo = ConfigRepository()
            guild_configs = await config_repo.list_all(db)

            if not guild_configs:
                flogger.info(f"ShopRefreshJob[{job_id}] no guilds configured, nothing to do")
                return {"status": "success", "guilds_refreshed": 0, "results": {}}

            # Pre-load all static game data once to avoid repeated DB
            # queries per guild x tier.  At 1000 guilds this reduces
            # ~420K item queries down to 4 (one per item type).
            await shop_service.preload_static_data(db)

            bulk_results: dict = {}
            try:
                for config in guild_configs:
                    gid = config.guild_id

                    # ── Resolve channel + role once per guild, before tier loop ──
                    shop_channel_id = getattr(config, "shop_channel_id", None)
                    # Prefer shop_announcements_role_id over bounty_hunter_role_id.
                    # Only use it when it's a real integer ID (guards against MagicMock attrs in tests).
                    _shop_ann_id = getattr(config, "shop_announcements_role_id", None)
                    _bh_role_id = getattr(config, "bounty_hunter_role_id", None)
                    mention_role_id = _shop_ann_id if isinstance(_shop_ann_id, int) else _bh_role_id

                    tier_results: dict = {}
                    for i, t in enumerate(_SHOP_TIERS):
                        tier_results[t] = await shop_service.refresh_shop(db, gid, t, force_tech_level)

                        # Diagnostic: log item count immediately after refresh
                        items = tier_results[t].get("items") or []
                        flogger.info(
                            "ShopRefresh: guild=%s tier=%s — refreshed %d items",
                            gid,
                            t,
                            len(items),
                        )

                        # ── Announce per tier ──────────────────────────────
                        # Role mention only on the first tier (Bronze) to avoid
                        # 4 pings per refresh cycle.
                        role_for_this_tier = mention_role_id if i == 0 else None
                        # Diagnostic: log announce item count (must equal refresh count)
                        announce_items = items
                        flogger.info(
                            "ShopRefresh: announcing %d items for guild=%s tier=%s",
                            len(announce_items),
                            gid,
                            t,
                        )
                        await _announce_shop_refresh(
                            job_id,
                            gid,
                            shop_channel_id,
                            role_for_this_tier,
                            tier=t,
                            items=announce_items,
                            tech_level=tier_results[t].get("tech_level"),
                        )

                        # ── Push to gateway autocomplete cache (Phase 5b) ──
                        await _push_shop_cache(job_id, gid, t, items)

                    bulk_results[gid] = tier_results

            finally:
                shop_service.clear_static_cache()

            end_ts = datetime.now(UTC)
            duration = (end_ts - start_ts).total_seconds()
            flogger.info(
                f"ShopRefreshJob[{job_id}] bulk refresh completed: {len(bulk_results)} guilds in {duration:.2f}s"
            )
            return {
                "status": "success",
                "guilds_refreshed": len(bulk_results),
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
    gateway_url = f"{_GATEWAY_BASE_URL}/internal/autocomplete/shop-cache/{guild_id}/{tier}"
    try:
        token = os.getenv("INTERNAL_AUTH_TOKEN", "")
        headers = {"X-Internal-Auth": token} if token else {}
        # Serialise items: support both plain dicts and objects with __dict__
        serialised: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                serialised.append(item)
            elif hasattr(item, "__dict__"):
                # ORM objects: exclude SQLAlchemy internal keys
                serialised.append({k: v for k, v in item.__dict__.items() if not k.startswith("_")})
            else:
                serialised.append(vars(item))
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                gateway_url,
                json={"items": serialised},
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
        flogger.debug(
            f"ShopRefreshJob[{parent_job_id}] pushed shop cache for guild={guild_id} tier={tier} "
            f"items={len(serialised)}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"ShopRefreshJob[{parent_job_id}] failed to push shop cache to gateway for "
            f"guild={guild_id} tier={tier}: {e}"
        )
