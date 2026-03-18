"""Shop refresh executor — refreshes guild shop stock on schedule.

Invoked by APScheduler via the JobExecutor dispatch.  The executor
delegates all shop business-logic to ShopService.refresh_shop() and uses
ConfigRepository.list_all() to enumerate guilds for bulk refreshes.

Imports of service/repository classes are deferred to function scope so that
the executor module can be imported in test environments without requiring a
live database or all ORM dependencies to be present.
"""

import traceback
from datetime import UTC, datetime

from shared.bblogger import get_logger

flogger = get_logger("shop-refresh-executor")

# Tiers supported by the shop system.
_SHOP_TIERS = ["Bronze", "Silver", "Gold"]


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
