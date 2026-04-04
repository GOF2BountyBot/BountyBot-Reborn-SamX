"""Shop refresh executor — refreshes guild shop stock on schedule.

Invoked by APScheduler via the JobExecutor dispatch.  The executor
delegates all shop business-logic to ShopService.refresh_shop() and uses
ConfigRepository.list_all() to enumerate guilds for bulk refreshes.

After a successful refresh, an announcement is posted to the discord-gateway
``POST /api/v1/channels/{shop_channel_id}/messages`` endpoint so players are
notified that new stock is available.

Imports of service/repository classes are deferred to function scope so that
the executor module can be imported in test environments without requiring a
live database or all ORM dependencies to be present.
"""

import os
import traceback
from datetime import UTC, datetime

import httpx
from shared.bblogger import get_logger

flogger = get_logger("shop-refresh-executor")

# Tiers supported by the shop system.
_SHOP_TIERS = ["Bronze", "Silver", "Gold"]

# ---------------------------------------------------------------------------
# Service endpoints (configurable via environment variables)
# ---------------------------------------------------------------------------
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
                    tier_results: dict = {}
                    for t in _SHOP_TIERS:
                        tier_results[t] = await shop_service.refresh_shop(db, gid, t, force_tech_level)
                    bulk_results[gid] = tier_results

                    # ── Announce shop refresh to discord-gateway ───────────
                    shop_channel_id = getattr(config, "shop_channel_id", None)
                    await _announce_shop_refresh(job_id, gid, shop_channel_id)

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
        flogger.trace(traceback.format_exc())
        raise


# ---------------------------------------------------------------------------
# Helper: announce shop refresh to discord-gateway
# ---------------------------------------------------------------------------


async def _announce_shop_refresh(parent_job_id: str, guild_id: int, shop_channel_id: int | None) -> None:
    """POST a shop-refresh announcement to the discord-gateway channel messages endpoint.

    POSTs to ``POST /api/v1/channels/{shop_channel_id}/messages`` with an
    EmbedPayload as the request body (matching ``MessageCreateRequest`` schema).

    If ``shop_channel_id`` is None, a warning is logged and the announcement
    is skipped — no shop channel has been configured for this guild yet.

    Failures are logged but do NOT propagate — a failed announcement is
    non-fatal for the refresh operation.
    """
    if shop_channel_id is None:
        flogger.warning(
            f"ShopRefreshJob[{parent_job_id}] guild={guild_id}: shop_channel_id not configured, skipping announcement"
        )
        return

    announcement = {
        "content": {
            "title": "🛒 Shop Refreshed!",
            "description": (
                "The guild shop has been restocked with new items across all tiers. "
                "Check out the latest offerings and upgrade your loadout!"
            ),
            "color": 3447003,  # Blue (#3498DB)
            "fields": [
                {"name": "Tiers Available", "value": "Bronze · Silver · Gold", "inline": False},
            ],
            "footer_text": "Use /shop to browse!",
        },
        "message_type": "default",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{shop_channel_id}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.info(
            f"ShopRefreshJob[{parent_job_id}] announced shop refresh for guild={guild_id} to channel {shop_channel_id}"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"ShopRefreshJob[{parent_job_id}] failed to announce shop refresh for guild={guild_id} "
            f"to channel {shop_channel_id}: {e}"
        )
        flogger.trace(traceback.format_exc())
