"""
Main FastAPI application for BountyBot API.

This module sets up the FastAPI application with automatic
router discovery and comprehensive API documentation.

CHANGES MADE:
- Added database initialization during startup
- Minimal changes to preserve existing functionality
- Database manager and schema manager integration
- Proper shutdown handling for database connections
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

# NEW IMPORTS: Database management
from persist.database.manager import db_manager
from persist.schemas.schema_manager import initialize_schema

# Import the routers package
import routers

logger = logging.get_logger("bot-main-script")

# Handle app startup/shutdown as app lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown logic (replaces @app.on_event).
    
    UPDATED: Added database initialization during startup
    """
    logger.info("🚀 BountyBot API starting up...")
    
    # NEW: Initialize database connection and schema
    try:
        logger.info("🗄️ Initializing database connection...")
        db_manager.initialize()
        
        logger.info("📋 Checking and updating database schema...")
        schema_manager = initialize_schema(db_manager)
        
        # Store schema manager reference for health checks
        app.state.schema_manager = schema_manager
        app.state.db_manager = db_manager
        
        logger.info("✅ Database initialization completed successfully")
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        logger.error("🛑 Application startup aborted due to database issues")
        raise  # This will prevent the application from starting
    
    logger.info("📚 API Documentation available at: /docs")
    logger.info("📖 ReDoc Documentation available at: /redoc")
    
    yield  # Application runs here
    
    # Shutdown logic
    logger.info("🛑 BountyBot API shutting down...")
    
    # NEW: Cleanup database connections
    try:
        logger.info("🗄️ Shutting down database connections...")
        db_manager.shutdown()
        logger.info("✅ Database connections closed successfully")
    except Exception as e:
        logger.error(f"⚠️ Error during database shutdown: {e}")
    
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
        lifespan=lifespan  # This includes our database initialization
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
    
    NO CHANGES: This function remains unchanged
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
                        tags=[modname]  # Add module name as tag
                    )
                    logger.info(f"✓ Included router from routers.{modname}")
                else:
                    logger.info(f"⚠ No 'router' attribute found in routers.{modname}")

            except ImportError as e:
                logger.error(f"✗ Failed to import routers.{modname}: {e}")

# Create the app instance
app = create_app()

# Root endpoint - NO CHANGES
@app.get("/", tags=["root"])
async def root():
    """Root endpoint with API information."""
    return {
        "message": "BountyBot API is running",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Health check filter - NO CHANGES
class HealthFilter(pyLogging.Filter):
    def filter(self, record: pyLogging.LogRecord) -> bool:
        msg = record.getMessage()
        # drop lines that mention the health path
        if "/api/v1/health/" in msg:
            return False
        return True

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting uvicorn...")
    # attach filter to uvicorn.access to filter health check API requests 
    # from being logged as they are particularly noisy
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
    uvicorn.run("main:app", 
                host="0.0.0.0", 
                port=8000, 
                # access_log shows API requests in log output, can get a bit noisy tho
                access_log=True,
                # reload is useful for development but should be turned off for production
                # It will monitor the filesystem and restart the server when changes are detected.
                reload=True)
