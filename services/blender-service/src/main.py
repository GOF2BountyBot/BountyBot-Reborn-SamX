"""
Main FastAPI application for Blendering Renderer Service API.

This module sets up the FastAPI application with automatic
router discovery and comprehensive API documentation.

"""

import importlib
import logging as pyLogging
import os
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path

# Import the routers package
import routers
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared import bblogger

flogger = bblogger.get_logger("blender-main-script")

# Handle app startup/shutdown as app lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown logic (replaces @app.on_event).
    """
    flogger.info("🚀 Blender API starting up...")
    flogger.info("📚 API Documentation available at: /docs")
    flogger.info("📖 ReDoc Documentation available at: /redoc")

    yield  # Application runs here

    # Shutdown logic
    flogger.info("🛑 Blender API shutting down...")

    flogger.info("👋 Goodbye!")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    # Create FastAPI app with comprehensive metadata
    flogger.trace("Initializing FastAPI...")
    app = FastAPI(
        title="Blender API",
        description="""
        Blender API provides endpoints for executing rendering requests, primarily applying custom skins to objects.

        ## Documentation

        * **Interactive API Docs**: Available at `/docs`
        * **ReDoc Documentation**: Available at `/redoc`
        * **OpenAPI Schema**: Available at `/openapi.json`
        """,
        summary="Blender API for Galaxy on Fire 2 game features",
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
    """
    Path(__file__).parent / "routers"

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
        "message": "Blender API is running",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Health check filter
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
                host=os.getenv("BLENDER_HOST", "0.0.0.0"),
                port=int(os.getenv("BLENDER_PORT", os.getenv("PORT", "8001"))),
                # access_log shows API requests in log output, can get a bit noisy tho
                access_log=os.getenv("ACCESS_LOG", "true").lower() == "true",
                # reload is useful for development but should be turned off for production
                # It will monitor the filesystem and restart the server when changes are detected.
                reload=True)
