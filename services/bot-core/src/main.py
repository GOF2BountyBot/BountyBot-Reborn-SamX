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
import shared.bblogger as bblogger
import logging as pyLogging
# Disabling Alembic as not working as intended...
#from alembic import command
#from alembic.config import Config

# NEW IMPORTS: Database management
from persist.database.manager import db_manager
from persist.schemas.schema_manager import initialize_schema

# from utils.emoji_service import EmojiService

# Import the routers package
import routers

flogger = bblogger.get_logger("bot-main-script")

# Handle app startup/shutdown as app lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown logic (replaces @app.on_event).
    
    UPDATED: Added database initialization during startup
    """
    flogger.info("🚀 BountyBot API starting up...")
    
    # NEW: Initialize database connection and schema
    try:
        flogger.info("🗄️ Initializing database connection...")
        await db_manager.initialize()
        
        flogger.info("📋 Checking and updating database schema...")
        schema_manager = await initialize_schema(db_manager)
        
        # Store schema manager reference for health checks
        app.state.schema_manager = schema_manager
        app.state.db_manager = db_manager

        # -------------------------------
        #  Alembic migrations (disabled)
        # -------------------------------
        #alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "persist", "database", "alembic.ini"))
        #flogger.info("🔄 Checking for pending database migrations...")
        #command.upgrade(alembic_cfg, "head")
        #flogger.info("✅ Database up-to-date with latest migrations")
        
        flogger.info("✅ Database initialization completed successfully")
        
    except Exception as e:
        flogger.error(f"❌ Database initialization failed: {e}")
        flogger.error("🛑 Application startup aborted due to database issues")
        raise  # This will prevent the application from starting

    ## Initialize Discord application emojis
    #try:
    #    flogger.info("🔄 Initializing Discord application emojis...")
    #    emoji_resolver.load_application_emojis()
    #    emoji_stats = emoji_resolver.get_emoji_stats()
    #    flogger.info(f"✅ Successfully loaded {emoji_stats['total_emojis']} Discord application emojis")
    #
    #    # Store emoji resolver reference for health checks
    #    app.state.emoji_resolver = emoji_resolver
    #
    #except Exception as e:
    #    flogger.error(f"⚠️ Emoji initialization failed: {e}")
    #    flogger.warning("🔄 Bot will continue without emoji resolution capability")
    #    # Note: We don't raise here as the bot can function without emojis
    
    flogger.info("📚 API Documentation available at: /docs")
    flogger.info("📖 ReDoc Documentation available at: /redoc")
    
    yield  # Application runs here
    
    # Shutdown logic
    flogger.info("🛑 BountyBot API shutting down...")
    
    # NEW: Cleanup database connections
    try:
        flogger.info("🗄️ Shutting down database connections...")
        db_manager.shutdown()
        flogger.info("✅ Database connections closed successfully")
    except Exception as e:
        flogger.error(f"⚠️ Error during database shutdown: {e}")
    
    flogger.info("👋 Goodbye!")

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    # Create FastAPI app with comprehensive metadata
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
                    flogger.info(f"✓ Included router from routers.{modname}")
                else:
                    flogger.info(f"⚠ No 'router' attribute found in routers.{modname}")

            except ImportError as e:
                flogger.error(f"✗ Failed to import routers.{modname}: {e}")

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
    flogger.info("Starting uvicorn...")
    # attach filter to uvicorn.access to filter health check API requests 
    # from being logged as they are particularly noisy
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
    uvicorn.run("main:app", 
                host=os.getenv("HOST", "0.0.0.0"), 
                port=int(os.getenv("PORT", os.getenv("PORT", "8000"))), 
                # access_log shows API requests in log output, can get a bit noisy tho
                access_log=os.getenv("ACCESS_LOG", "true").lower() == "true",
                # reload is useful for development but should be turned off for production
                # It will monitor the filesystem and restart the server when changes are detected.
                reload=True)
