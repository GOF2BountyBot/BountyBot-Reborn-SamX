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

import os
import importlib
import pkgutil
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import shared.bblogger as bblogger
import logging as pyLogging

# Database management
from persist.database.manager import db_manager
from persist.schemas.schema_manager import initialize_schema

# Scheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import create_engine

# Import the routers package
import routers

flogger = bblogger.get_logger("bot-main-script")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown logic (replaces @app.on_event).
    Includes DB init, schema checks, and scheduler startup/shutdown.
    """
    flogger.info("🚀 BountyBot API starting up...")

    # Initialize database
    try:
        flogger.info("🗄️ Initializing database connection...")
        await db_manager.initialize()

        flogger.info("📋 Checking and updating database schema...")
        schema_manager = await initialize_schema(db_manager)
        app.state.schema_manager = schema_manager
        app.state.db_manager = db_manager

        flogger.info("✅ Database initialization completed successfully")
    except Exception as e:
        flogger.error(f"❌ Database initialization failed: {e}")
        flogger.error("🛑 Application startup aborted due to database issues")
        raise

    # Initialize scheduler with a true sync_engine for APScheduler
    try:
        flogger.info("⏰ Initializing Scheduler…")

        # existing async engine (if you need it elsewhere)
        async_engine = create_async_engine(
            db_manager._connection_string,  # asyncpg URL
            echo=False,
            future=True,
        )

        # derive a sync URL and engine for APScheduler
        sync_url = db_manager._connection_string.replace(
            "postgresql+asyncpg", "postgresql"
        )
        sync_engine = create_engine(
            sync_url,
            echo=False,
            future=True,
        )

        jobstores = {
            "default": SQLAlchemyJobStore(
                engine=sync_engine,
                tablename="apscheduler_jobs"
            )
        }
        scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
        app.state.scheduler = scheduler
        scheduler.start()
        flogger.info("✅ Scheduler started")
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
        app.state.scheduler.shutdown(wait=False)
        flogger.info("✅ Scheduler stopped")
    except Exception as e:
        flogger.error(f"⚠️ Error shutting down scheduler: {e}")

    try:
        flogger.info("🗄️ Shutting down database connections...")
        db_manager.shutdown()
        flogger.info("✅ Database connections closed successfully")
    except Exception as e:
        flogger.error(f"⚠️ Error during database shutdown: {e}")

    flogger.info("👋 Goodbye!")

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    flogger.trace("Initializing FastAPI...")
    app = FastAPI(
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
            "email": "support@bountybot.com"
        },
        license_info={
            "name": "MIT",
            "url": "https://opensource.org/licenses/MIT"
        },
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    include_routers(app)
    return app

def include_routers(app: FastAPI) -> None:
    """
    Auto-discover and include all routers (even in subpackages) from the routers package.
    Logs successes, warnings, and errors in detail.
    """
    import pkgutil, importlib, routers
    success, skipped, failed = 0, 0, 0

    for finder, fullname, ispkg in pkgutil.walk_packages(routers.__path__, routers.__name__ + "."):
        try:
            module = importlib.import_module(fullname)
        except Exception as e:
            flogger.error(f"✗ Failed to import module '{fullname}': {e}")
            failed += 1
            continue

        router_obj = getattr(module, "router", None)
        if router_obj is None:
            flogger.debug(f"⚠ No router in '{fullname}', skipping.")
            skipped += 1
            continue

        try:
            tag = fullname.rsplit(".", 1)[-1]
            app.include_router(router_obj, prefix="/api/v1", tags=[tag])
            flogger.info(f"✓ Included router '{tag}' from module '{fullname}'")
            success += 1
        except Exception as e:
            flogger.error(f"✗ Error including router from '{fullname}': {e}")
            failed += 1

    flogger.info(
        f"Router discovery complete: {success} included, {skipped} skipped, {failed} failed."
    )

app = create_app()

@app.get("/", tags=["root"])
async def root():
    return {
        "message": "BountyBot API is running",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }

class HealthFilter(pyLogging.Filter):
    def filter(self, record: pyLogging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/api/v1/health/" in msg:
            return False
        return True

if __name__ == "__main__":
    import uvicorn
    flogger.info("Starting uvicorn...")
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", os.getenv("PORT", "8000"))),
        access_log=os.getenv("ACCESS_LOG", "true").lower() == "true",
        reload=True
    )