"""
FastAPI application module for Discord Gateway API.

This module provides functions to create and run the FastAPI application
that can be imported and used within other applications like Discord bots.
"""

import os
import importlib
import pkgutil
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import threading
import uvicorn
import logging as pyLogging

import shared.logging as logging

# Import the routers package
import api.routers as routers

logger = logging.get_logger("discord-gateway-api-server")

# Configuration constants with environment variable support
GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", os.getenv("PORT", "8000")))
ACCESS_LOG = os.getenv("ACCESS_LOG", "true").lower() == "true"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown logic for FastAPI application.
    """
    logger.info("🚀 Discord Gateway API starting up...")
    logger.info("📚 API Documentation available at: /docs")
    logger.info("📖 ReDoc Documentation available at: /redoc")
    
    yield  # Application runs here
    
    # Shutdown logic
    logger.info("🛑 Discord Gateway API shutting down...")

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    logger.trace("Initializing FastAPI...")
    
    app = FastAPI(
        title="Discord Gateway API",
        description="""
Discord Gateway API provides endpoints for bot-initiated interactions with the Discord platform. 
This is useful for integrating bot mechanics that require the ability to execute Discord actions
(e.g. new posts, update existing posts, etc.) that are not initiated by an end-user via Discord
command. 

## Documentation

* **Interactive API Docs**: Available at `/docs`
* **ReDoc Documentation**: Available at `/redoc`
* **OpenAPI Schema**: Available at `/openapi.json`
""",
        summary="Discord Gateway API",
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
    
    # Root endpoint
    @app.get("/", tags=["root"])
    async def root():
        """Root endpoint with API information."""
        return {
            "message": "Discord Gateway API is running",
            "version": "1.0.0",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    
    return app

def include_routers(app: FastAPI) -> None:
    """
    Automatically discover and include all routers from the routers package.
    """
    routers_path = Path(__file__).parent / "routers"
    
    # Iterate through all modules in the routers package
    for importer, modname, ispkg in pkgutil.iter_modules(routers.__path__):
        if not ispkg:  # Only process modules, not packages
            try:
                # Import the module
                module = importlib.import_module(f"api.routers.{modname}")

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

class HealthFilter(pyLogging.Filter):
    """Filter to reduce noise from health check requests in logs."""
    def filter(self, record: pyLogging.LogRecord) -> bool:
        msg = record.getMessage()
        # Drop lines that mention the health path
        if "/api/v1/health/" in msg:
            return False
        return True

async def start_fastapi_server(host: str = None, port: int = None, 
                              access_log: bool = None) -> None:
    """
    Start the FastAPI server in the background.
    This function is designed to be called from within an existing event loop.
    """
    # Use environment variables as defaults if not specified
    if host is None:
        host = GATEWAY_HOST
    if port is None:
        port = GATEWAY_PORT
    if access_log is None:
        access_log = ACCESS_LOG

    logger.trace("Starting with host: " + host)
    logger.trace("Starting with port: " + str(port))
    logger.trace("Starting with access_log: " + str(access_log))

    def run_server():
        """Run the FastAPI server in this thread"""
        try:
            logger.info(f"🌐 FastAPI server thread starting on {host}:{port}")
            
            app = create_app()
            
            # Attach filter to uvicorn.access to filter health check API requests
            pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
            
            # Run the server (this will block in this thread)
            uvicorn.run(
                app,
                host=host,
                port=port,
                access_log=access_log,
                log_config=None  # Use existing logging configuration
            )
            
        except Exception as e:
            logger.critical("💥 FastAPI failed to start, aborting entire service", exc_info=True)
            # os._exit kills the whole process immediately
            os._exit(1)
    
    # Create and start the thread
    server_thread = threading.Thread(
        target=run_server,
        daemon=True,  # Dies when main thread dies
        name="FastAPI-Server"
    )

    server_thread.start()
    logger.info(f"✅ FastAPI server thread started successfully")
    logger.info(f"📚 API Documentation will be available at: http://{host}:{port}/docs")
    logger.info(f"📖 ReDoc Documentation will be available at: http://{host}:{port}/redoc")
    
    return server_thread


def run_standalone(host: str = "0.0.0.0", port: int = 8080):
    """
    Run FastAPI as a standalone application (for development/testing).
    """
    app = create_app()
    
    # Attach filter to uvicorn.access
    pyLogging.getLogger("uvicorn.access").addFilter(HealthFilter())
    
    logger.info("Starting uvicorn...")
    uvicorn.run("main:app", 
                host="0.0.0.0", 
                port=8000, 
                # access_log shows API requests in log output, can get a bit noisy tho
                access_log=True,
                # reload is useful for development but should be turned off for production
                # It will monitor the filesystem and restart the server when changes are detected.
                reload=True)

# Allow running as standalone script for development
if __name__ == "__main__":
    # Log configuration being used
    logger.info("=== FastAPI Configuration ===")
    logger.info(f"Host: {HOST}")
    logger.info(f"Port: {PORT}")
    logger.info(f"Access Log: {ACCESS_LOG}")
    logger.info("===============================")
    
    run_standalone()
