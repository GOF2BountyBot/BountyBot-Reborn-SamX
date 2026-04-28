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
    """
    from persist.models.bounty import Bounty
    from persist.models.duel_request import DuelRequest
    from sqlalchemy import and_, func, update

    flogger.info("🔄 Running stale-state recovery sweep (B.14)...")

    async with db_manager.get_session() as db:
        try:
            # ------------------------------------------------------------------ #
            # Bounties: status='active' AND end_time < NOW()                     #
            # ------------------------------------------------------------------ #
            bounty_result = await db.execute(
                update(Bounty)
                .where(
                    and_(
                        Bounty.status == "active",
                        Bounty.end_time < func.now(),
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
                        DuelRequest.expires_at < func.now(),
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

        # Register default recurring jobs (idempotent — skip if already present)
        register_default_jobs(scheduler)

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
        reload=True,
    )
