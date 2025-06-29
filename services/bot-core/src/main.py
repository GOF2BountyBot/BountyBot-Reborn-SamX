"""
Main FastAPI application for BountyBot API.

This module sets up the FastAPI application with automatic
router discovery and comprehensive API documentation.
"""

import os
import importlib
import pkgutil
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import shared.logging as logging
import logging as pyLogging
from persist.database.manager import db_manager
# Import the routers package
import routers

logger = logging.get_logger("bot-main-script")

# Handle app startup/shutdown as app lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown logic (replaces @app.on_event)."""
    logger.info("🚀 BountyBot API starting up...")
    
    # Initialize database connection
    logger.info("📊 Initializing database connection...")
    db_success = await db_manager.initialize()
    if not db_success:
        logger.error("❌ Database initialization failed - application will not start")
        raise RuntimeError("Database initialization failed")

    logger.info("✅ Database initialized successfully")
    logger.info("📚 API Documentation available at: /docs")
    logger.info("📖 ReDoc Documentation available at: /redoc")
    yield
    logger.info("🛑 BountyBot API shutting down...")
    logger.info("📊 Closing database connections...")
    await db_manager.close()
    logger.info("👋 Goodbye!")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    # Create FastAPI app with comprehensive metadata
    logger.trace("Initializing FastAPI...")
    app = FastAPI(
        title="BountyBot API",
        description="""
        BountyBot API provides endpoints for managing the Galaxy on Fire 2 
        Discord bot functionality including bounty hunting, trading, 
        dueling, and ship management.

        ## Features

        * **Health Monitoring**: Comprehensive health check endpoints
        * **Bot Management**: Discord bot control and status
        * **Game Features**: Bounty hunting, trading, dueling systems
        * **Database Integration**: PostgreSQL with SQLAlchemy ORM and schema versioning

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

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auto-discover and include routers
    include_routers(app)

    return app

def include_routers(app: FastAPI) -> None:
    """
    Automatically discover and include all routers from the routers package.

    This function iterates through all modules in the routers package
    and includes any APIRouter instances found.
    """
    routers_path = Path(__file__).parent / "routers"

    # Iterate through all modules in the routers package
    for importer, modname, ispkg in pkgutil.iter_modules(routers.__path__):
        if not ispkg:  # Only process modules, not packages
            try:
                # Import the module
                module = importlib.import_module(f"routers.{modname}")

                # Look for router attribute in the module
                if hasattr(module, 'router'):
                    router = getattr(module, 'router')

                    # Include the router with appropriate prefix
                    app.include_router(
                        router,
                        prefix="/api/v1",  # Global API version prefix
                        tags=[modname]     # Add module name as tag
                    )
                    logger.info(f"✓ Included router from routers.{modname}")
                else:
                    logger.info(f"⚠ No 'router' attribute found in routers.{modname}")

            except ImportError as e:
                logger.error(f"✗ Failed to import routers.{modname}: {e}")

# Create the app instance
app = create_app()

# Root endpoint
@app.get("/", tags=["root"])
async def root():
    """Root endpoint with API information and system status."""
    # Get basic database status for root endpoint
    try:
        db_health = await db_manager.get_db_health()
        db_status = db_health["status"]
        schema_version = db_health.get("schema_version", "unknown")
    except Exception:
        db_status = "unknown"
        schema_version = "unknown"

    return {
        "message": "BountyBot API is running",
        "version": "1.0.0",
        "database_status": db_status,
        "schema_version": schema_version,
        "docs": "/docs",
        "redoc": "/redoc",
        "health_check": "/api/v1/health/"
    }

class HealthFilter(pyLogging.Filter):
    """Filter to reduce noise from health check endpoints in logs."""
    def filter(self, record: pyLogging.LogRecord) -> bool:
        msg = record.getMessage()
        # Drop lines that mention health check paths to reduce log noise
        health_paths = ["/api/v1/health/", "/health"]
        return not any(path in msg for path in health_paths)

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting uvicorn server...")

    # Attach filter to uvicorn.access to filter health check API requests 
    # from being logged as they can be particularly noisy
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())

    # Get configuration from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    # reload is useful for development but should be turned off for production
    # It will monitor the filesystem and restart the server when changes are detected.
    reload = os.getenv("RELOAD", "true").lower() == "true"
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    logger.info(f"Starting server on {host}:{port}")
    logger.info(f"Reload mode: {reload}")
    logger.info(f"Log level: {log_level}")
    uvicorn.run(
        "main:app", 
        host=host, 
        port=port,
        # Access log shows API requests in log output, can get noisy
        access_log=True,
        reload=reload,
        log_level=log_level
    )
