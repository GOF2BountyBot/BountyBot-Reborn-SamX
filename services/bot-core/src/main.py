"""
Main FastAPI application for BountyBot API.

This module sets up the FastAPI application with automatic
router discovery and comprehensive API documentation.

CHANGES MADE:
- Added database initialization during startup
- Minimal changes to preserve existing functionality
- Database manager and schema manager integration
- Proper shutdown handling for database connections
- Option 2: Use SQLAlchemy AsyncEngine and a real sync_engine for APScheduler
"""

import importlib
import logging as pyLogging
import os
import pkgutil
from contextlib import asynccontextmanager

from api import routers
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from persist.database.manager import db_manager
from persist.schemas.schema_manager import initialize_schema
from services.game_constants import GameConstants
from shared import bblogger
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from utils.auto_seeder import auto_seed_data
from utils.job_executor import run_job

flogger = bblogger.get_logger("bot-main-script")


# ---------------------------------------------------------------------------
# Default recurring job definitions
# ---------------------------------------------------------------------------

DEFAULT_SCHEDULER_JOBS: list[dict] = [
    {
        "job_id": "bounty_spawn_default",
        "cron": f"*/{GameConstants.BOUNTY_DELAY_RANDOM_MIN} * * * *",
        "payload": {"job_type": "bounty_spawn_orchestrate"},
        "jitter": GameConstants.BOUNTY_SPAWN_JITTER,  # seconds of random offset
    },
    {
        "job_id": "shop_refresh_default",
        "cron": "0 */6 * * *",
        "payload": {"job_type": "shop_refresh"},
    },
    {
        "job_id": "temperature_decay_default",
        "cron": "0 * * * *",
        "payload": {"job_type": "temperature_decay"},
    },
    {
        "job_id": "bounty_failsafe_cleanup_default",
        "cron": "30 * * * *",  # :30 past every hour (offset from temperature_decay at :00)
        "payload": {"job_type": "bounty_failsafe_cleanup"},
    },
    {
        "job_id": "pg_backup_default",
        "cron": "15 */3 * * *",  # :15 past every 3rd hour; offset from shop_refresh (:00) and temperature_decay (:30)
        "payload": {"job_type": "pg_backup"},
    },
    {
        "job_id": "db_retention_default",
        "cron": "45 3 * * *",  # daily at 03:45 UTC — well clear of all hourly/3-hourly jobs
        "payload": {"job_type": "db_retention"},
    },
]


def register_default_jobs(scheduler) -> None:
    """Register default recurring scheduler jobs if they are not already present.

    This function is idempotent: calling it on an already-configured scheduler
    (e.g. after a service restart when APScheduler persists jobs in the DB)
    will skip any job whose ``job_id`` already exists.

    Args:
        scheduler: An ``AsyncIOScheduler`` (or compatible) instance that has
                   already been started.
    """
    existing_job_ids = {j.id for j in scheduler.get_jobs()}

    for job_def in DEFAULT_SCHEDULER_JOBS:
        jid = job_def["job_id"]
        if jid in existing_job_ids:
            flogger.info(f"⏭️ Default job '{jid}' already registered — skipping")
            continue
        trigger = CronTrigger.from_crontab(job_def["cron"], timezone="UTC")
        jitter = job_def.get("jitter")
        if jitter:
            trigger.jitter = jitter
        scheduler.add_job(
            run_job,
            trigger=trigger,
            args=[jid, job_def["payload"]],
            id=jid,
        )
        jitter_info = f", jitter={jitter}s" if jitter else ""
        flogger.info(f"📅 Registered default job '{jid}' with cron '{job_def['cron']}'{jitter_info}")


async def run_stale_state_recovery_sweep() -> None:
    """Recovery sweep: mark stale active bounties/duels as expired on startup.

    Runs once after migrations complete and BEFORE the scheduler starts.

    Scenario this fixes (B.14):  When the bot is offline at a bounty/duel
    expiry time, APScheduler's one-time job is skipped (past-scheduled jobs
    are not back-fired by default).  On the next startup the rows remain
    status='active'/'pending' even though end_time/expires_at is in the past.

    This sweep performs a single bulk UPDATE per entity type so the DB is in
    a consistent state before any live traffic is served.

    B.23b: After marking bounties expired, the sweep also deletes their Discord
    announcement messages so stale bounties don't leave zombie announcements in
    Discord channels indefinitely.  Announcement deletion is best-effort and
    non-fatal — gateway may not be ready at startup time.
    """
    from types import SimpleNamespace

    from persist.models.bounty import Bounty
    from persist.models.duel_request import DuelRequest
    from sqlalchemy import and_, func, select, update

    flogger.info("🔄 Running stale-state recovery sweep (B.14)...")

    # B.23b: collect stale bounty identifiers BEFORE the bulk UPDATE so we can
    # clean up their Discord announcements after marking them expired.
    stale_bounty_refs: list = []

    async with db_manager.get_session() as db:
        try:
            # ------------------------------------------------------------------ #
            # B.23b: select stale bounty (id, guild_id) pairs before UPDATE      #
            # ------------------------------------------------------------------ #
            stale_select_result = await db.execute(
                select(Bounty.id, Bounty.guild_id).where(
                    and_(
                        Bounty.status == "active",
                        Bounty.end_time < func.now(),  # pylint: disable=not-callable
                    )
                )
            )
            stale_bounty_refs = [SimpleNamespace(id=row[0], guild_id=row[1]) for row in stale_select_result.all()]

            # ------------------------------------------------------------------ #
            # Bounties: status='active' AND end_time < NOW()                     #
            # ------------------------------------------------------------------ #
            bounty_result = await db.execute(
                update(Bounty)
                .where(
                    and_(
                        Bounty.status == "active",
                        Bounty.end_time < func.now(),  # pylint: disable=not-callable
                    )
                )
                .values(status="expired")
                .execution_options(synchronize_session="fetch")
            )
            stale_bounty_count = bounty_result.rowcount

            # ------------------------------------------------------------------ #
            # Duels: status='pending' AND expires_at IS NOT NULL AND             #
            #        expires_at < NOW()                                           #
            # ------------------------------------------------------------------ #
            duel_result = await db.execute(
                update(DuelRequest)
                .where(
                    and_(
                        DuelRequest.status == "pending",
                        DuelRequest.expires_at.isnot(None),
                        DuelRequest.expires_at < func.now(),  # pylint: disable=not-callable
                    )
                )
                .values(status="expired")
                .execution_options(synchronize_session="fetch")
            )
            stale_duel_count = duel_result.rowcount

            await db.commit()

            flogger.info(
                f"✅ Recovery sweep complete: marked {stale_bounty_count} stale bounties "
                f"and {stale_duel_count} stale duels as expired"
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"⚠️ Recovery sweep failed (non-fatal, continuing): {e}")
            await db.rollback()
            return  # skip announcement cleanup if the sweep itself failed

    # ------------------------------------------------------------------ #
    # B.23b: delete Discord announcement messages for stale bounties      #
    # (best-effort, non-fatal — gateway may not be reachable at startup)  #
    # ------------------------------------------------------------------ #
    if stale_bounty_refs:
        flogger.info(f"🧹 Cleaning up announcements for {len(stale_bounty_refs)} stale bounty(ies)...")
        from utils.executors.bounty_expire_executor import _delete_bounty_announcement

        announcement_cleaned = 0
        for bounty_ref in stale_bounty_refs:
            try:
                async with db_manager.get_session() as db:
                    await _delete_bounty_announcement("recovery-sweep", bounty_ref, db)
                announcement_cleaned += 1
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"⚠️ Recovery sweep: failed to clean announcement for bounty id={bounty_ref.id}: {e}")

        flogger.info(f"✅ Announcement cleanup complete: {announcement_cleaned}/{len(stale_bounty_refs)} cleaned")


async def run_stale_respawn_recovery() -> None:
    """Recovery sweep: re-fire missed bounty respawns on startup.

    Sibling to ``run_stale_state_recovery_sweep`` that addresses the
    ``bounty_respawn_executor`` reliability gap.

    Scenario this fixes:
        ``BountyService.escape_bounty()`` sets ``status='escaped'`` and stamps
        ``respawn_time = now + (1 min × route length)``.  A one-shot
        APScheduler job is then scheduled to fire ``execute_bounty_respawn_job``
        at that ``respawn_time``.  If the bot is offline at that moment, the
        job is silently dropped (past-scheduled jobs do not back-fire by
        default), leaving the bounty stuck in ``status='escaped'`` forever.

    Recovery strategy:
        1. Select bounties with ``status='escaped'`` where ``respawn_time``
           has passed (or is NULL — defensive guard against an aborted
           ``escape_bounty`` call).
        2. For each, directly invoke ``execute_bounty_respawn_job`` which
           regenerates the route, flips status back to ``active``, and
           announces the respawn through the gateway.
        3. Failures are logged but never propagate — startup must not fail.

    Runs AFTER the scheduler has started so the executor's HTTP announcement
    path is consistent with the live runtime path.
    """
    from persist.models.bounty import Bounty
    from sqlalchemy import func, or_, select
    from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

    flogger.info("🔄 Running stale-respawn recovery sweep...")

    stale_ids: list[int] = []
    async with db_manager.get_session() as db:
        try:
            result = await db.execute(
                select(Bounty.id).where(
                    Bounty.status == "escaped",
                    or_(
                        Bounty.respawn_time.is_(None),
                        Bounty.respawn_time < func.now(),  # pylint: disable=not-callable
                    ),
                )
            )
            stale_ids = [row[0] for row in result.all()]
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"⚠️ Stale-respawn query failed (non-fatal, continuing): {e}")
            return

    if not stale_ids:
        flogger.info("✅ Stale-respawn sweep complete: no escaped bounties needing respawn")
        return

    flogger.info(f"🔁 Stale-respawn sweep found {len(stale_ids)} escaped bounty(ies); re-firing respawn")

    succeeded = 0
    failed = 0
    for bounty_id in stale_ids:
        try:
            outcome = await execute_bounty_respawn_job(
                job_id=f"recovery-respawn-{bounty_id}",
                payload={"job_type": "bounty_respawn", "bounty_id": bounty_id},
            )
            if outcome.get("status") == "success":
                succeeded += 1
            else:
                failed += 1
                flogger.warning(f"Stale-respawn for bounty_id={bounty_id} returned {outcome}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            failed += 1
            flogger.error(f"Stale-respawn for bounty_id={bounty_id} raised: {e}")

    flogger.info(f"✅ Stale-respawn sweep complete: {succeeded} respawned, {failed} failed")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """
    Startup / shutdown logic (replaces @app.on_event).
    Includes DB init, schema checks, and scheduler startup/shutdown.
    """
    flogger.info("🚀 BountyBot API starting up...")

    # Initialize database
    try:
        flogger.info("🗄️ Initializing database connection...")
        await db_manager.initialize()

        # Run Alembic migrations (replaces the legacy create_all() approach)
        flogger.info("📋 Running database migrations...")
        from persist.database.migration_manager import MigrationManager

        migration_mgr = MigrationManager.from_async_url(db_manager._connection_string)
        migration_mgr.ensure_current()
        flogger.info("✅ Database migrations applied successfully")

        # Build a schema manager for health-check endpoints (skips create_all).
        flogger.info("📋 Initialising schema manager (informational only)...")
        schema_manager = await initialize_schema(db_manager)
        fastapi_app.state.schema_manager = schema_manager
        fastapi_app.state.db_manager = db_manager

        flogger.info("✅ Database initialization completed successfully")
    except Exception as e:
        flogger.error(f"❌ Database initialization failed: {e}")
        flogger.error("🛑 Application startup aborted due to database issues")
        raise

    # Recovery sweep: mark stale active bounties/duels as expired (B.14 — Layer 2).
    # Runs AFTER migrations and BEFORE the scheduler, so the DB is clean before
    # any jobs or live requests are processed.
    await run_stale_state_recovery_sweep()

    # Auto-seed game data tables if they are empty
    try:
        await auto_seed_data()
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"⚠️ Auto-seed encountered an unexpected error (continuing): {e}")

    # Initialize scheduler with a true sync_engine for APScheduler
    try:
        flogger.info("⏰ Initializing Scheduler…")

        # existing async engine (if you need it elsewhere)
        create_async_engine(
            db_manager._connection_string,  # asyncpg URL
            echo=False,
            future=True,
        )

        # derive a sync URL and engine for APScheduler
        sync_url = db_manager._connection_string.replace("postgresql+asyncpg", "postgresql")
        sync_engine = create_engine(
            sync_url,
            echo=False,
            future=True,
        )

        jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine, tablename="apscheduler_jobs")}
        scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
        fastapi_app.state.scheduler = scheduler
        scheduler.start()
        flogger.info("✅ Scheduler started")

        # B.23a: expose the scheduler instance via the module-level holder so that
        # executor modules can schedule jobs directly (no HTTP round-trip needed).
        from utils.scheduler_holder import set_scheduler

        set_scheduler(scheduler)
        flogger.info("📌 Scheduler registered in scheduler_holder")

        # Register default recurring jobs (idempotent — skip if already present)
        register_default_jobs(scheduler)

        # Recovery: re-fire bounty respawns missed while the bot was offline.
        # Runs after the scheduler is up so the executor's behavior (DB +
        # gateway announcement) matches the live runtime path.
        await run_stale_respawn_recovery()

    except Exception as e:
        flogger.error(f"❌ Scheduler initialization failed: {e}")
        flogger.error("🛑 Application startup aborted due to scheduler issues")
        raise

    flogger.info("📚 API Documentation available at: /docs")
    flogger.info("📖 ReDoc Documentation available at: /redoc")

    yield  # run the application

    # Shutdown logic
    flogger.info("🛑 BountyBot API shutting down...")
    try:
        flogger.info("⏰ Shutting down Scheduler…")
        fastapi_app.state.scheduler.shutdown(wait=False)
        flogger.info("✅ Scheduler stopped")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"⚠️ Error shutting down scheduler: {e}")

    try:
        flogger.info("🗄️ Shutting down database connections...")
        db_manager.shutdown()
        flogger.info("✅ Database connections closed successfully")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"⚠️ Error during database shutdown: {e}")

    flogger.info("👋 Goodbye!")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    flogger.trace("Initializing FastAPI...")
    fastapi_app = FastAPI(
        title="BountyBot API",
        description="""
        BountyBot API provides endpoints for managing the Galaxy on Fire 2
        Discord bot functionality including bounty hunting, trading,
        dueling, and ship management.

        ## Features

        * **Health Monitoring**: Comprehensive health check endpoints with database status
        * **Bot Management**: Discord bot control and status
        * **Game Features**: Bounty hunting, trading, dueling systems
        * **Database Management**: Automatic schema versioning and migrations

        ## Documentation

        * **Interactive API Docs**: Available at `/docs`
        * **ReDoc Documentation**: Available at `/redoc`
        * **OpenAPI Schema**: Available at `/openapi.json`
        """,
        summary="Discord bot API for Galaxy on Fire 2 game features",
        version="1.0.0",
        contact={
            "name": "BountyBot Team",
            "url": "https://github.com/GOF2BountyBot/BountyBot-Reborn-SamX",
            "email": "support@bountybot.com",
        },
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    include_routers(fastapi_app)
    return fastapi_app


def include_routers(fastapi_app: FastAPI) -> None:
    """
    Auto-discover and include all APIRouter instances
    under api/routers.
    """
    success = skipped = failed = 0

    # Iterate modules in the api.routers package
    for _finder, name, _ispkg in pkgutil.iter_modules(routers.__path__):
        fullname = f"{routers.__name__}.{name}"
        try:
            module = importlib.import_module(fullname)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"✗ Failed to import module '{fullname}': {e}")
            failed += 1
            continue

        router = getattr(module, "router", None)
        if router is None:
            flogger.debug(f"⚠ No router in '{fullname}', skipping.")
            skipped += 1
            continue

        tag = name
        fastapi_app.include_router(router, prefix="/api/v1")
        flogger.info(f"✓ Included router '{tag}' from '{fullname}'")
        success += 1

    flogger.info(f"Router discovery complete: {success} included, {skipped} skipped, {failed} failed.")


app = create_app()


@app.get("/", tags=["root"])
async def root():
    return {"message": "BountyBot API is running", "version": "1.0.0", "docs": "/docs", "redoc": "/redoc"}


class HealthFilter(pyLogging.Filter):
    def filter(self, record: pyLogging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/v1/health/" not in msg


if __name__ == "__main__":
    import uvicorn

    flogger.info("Starting uvicorn...")
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
    uvicorn.run(
        "main:app",
        host=os.getenv("BOT_HOST", "0.0.0.0"),
        port=int(os.getenv("BOT_PORT", os.getenv("PORT", "8000"))),
        access_log=os.getenv("ACCESS_LOG", "true").lower() == "true",
        workers=4,
        # Explicit: uvloop + httptools are bundled with uvicorn[standard];
        # "auto" picks them up but pinning makes the dependency obvious.
        loop="uvloop",
        http="httptools",
    )
