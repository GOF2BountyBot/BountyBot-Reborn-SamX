"""
Health check router for the BountyBot API.

This module provides health check endpoints to monitor the status
of the bot service and its dependencies, including database connectivity
and schema version information.
"""

from fastapi import APIRouter, status, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime, UTC
from typing import Dict, Any, Optional
import sys
import platform
import shared.bblogger as bblogger

flogger = bblogger.get_logger("bot-healthcheck-api-router")

router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={
        200: {"description": "Service is healthy"},
        503: {"description": "Service is unhealthy"}
    }
)

# Import response models from schemas
from api.schemas.health_schema import HealthResponse, SimpleHealthResponse

@router.get(
    "/",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Comprehensive Health Check",
    description="Returns detailed health information about the bot service, database, and schema version"
)
async def health_check(request: Request) -> HealthResponse:
    """
    Comprehensive health check endpoint.
    """
    flogger.debug("Inside health_check method...")
    
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "memory_available": True,
        "disk_space": True,
    }
    
    database_health = None
    schema_health = None
    database_accessible = False
    schema_current = False
    
    # Database health
    try:
        if hasattr(request.app.state, "db_manager"):
            db_manager = request.app.state.db_manager
            database_health = await db_manager.get_health_info()
            database_accessible = database_health.get("connectivity", False)
            checks["database_connectivity"] = database_accessible
        else:
            flogger.warning("Database manager not found in app state")
            checks["database_connectivity"] = False
            database_health = {
                "status": "not_initialized",
                "error": "Database manager not available"
            }
    except Exception as e:
        flogger.error(f"Database health check failed: {e}")
        checks["database_connectivity"] = False
        database_health = {"status": "error", "error": str(e)}
    
    # Schema health
    try:
        if hasattr(request.app.state, "schema_manager"):
            schema_manager = request.app.state.schema_manager
            schema_health = await schema_manager.get_schema_health_info()
            schema_current = schema_health.get("version_match", False)
            checks["schema_version_current"] = schema_current
        else:
            flogger.warning("Schema manager not found in app state")
            checks["schema_version_current"] = False
            schema_health = {
                "status": "not_initialized",
                "error": "Schema manager not available"
            }
    except Exception as e:
        flogger.error(f"Schema health check failed: {e}")
        checks["schema_version_current"] = False
        schema_health = {"status": "error", "error": str(e)}

    all_checks_passed = all(checks.values())
    service_status = "healthy" if all_checks_passed and database_accessible else "unhealthy"
    if not database_accessible:
        flogger.warning("Marking service as unhealthy due to database connectivity issues")
    
    flogger.debug("Exiting health_check method...")
    return HealthResponse(
        status=service_status,
        timestamp=datetime.now(UTC),
        version="1.0.0",
        service="BountyBot API",
        environment={
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.architecture()[0]
        },
        checks=checks,
        database_check=database_health,
        schema_check=schema_health
    )

@router.get(
    "/simple",
    response_model=SimpleHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Simple Health Check",
    description="Returns basic health status for load balancer checks"
)
async def simple_health_check() -> SimpleHealthResponse:
    return SimpleHealthResponse(
        status="healthy",
        timestamp=datetime.now(UTC)
    )

@router.get(
    "/readiness",
    status_code=status.HTTP_200_OK,
    summary="Readiness Check",
    description="Checks if the service is ready to accept requests (includes database connectivity)"
)
async def readiness_check(request: Request) -> Dict[str, str]:
    try:
        if hasattr(request.app.state, "db_manager"):
            db_manager = request.app.state.db_manager
            db_health = await db_manager.get_health_info()
            if not db_health.get("connectivity", False):
                flogger.warning("Service not ready: database not accessible")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Database not accessible"
                )
        return {"status": "ready"}
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Readiness check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service not ready: {str(e)}"
        )

@router.get(
    "/liveness",
    status_code=status.HTTP_200_OK,
    summary="Liveness Check", 
    description="Checks if the service is alive and responsive"
)
async def liveness_check() -> Dict[str, str]:
    return {"status": "alive"}

@router.get(
    "/database",
    status_code=status.HTTP_200_OK,
    summary="Database Health Check",
    description="Detailed database connectivity and schema information"
)
async def database_health_check(request: Request) -> Dict[str, Any]:
    """
    Database-specific health check endpoint.
    """
    try:
        health_info = {
            "timestamp": datetime.now(UTC),
            "database": None,
            "schema": None
        }
        
        # Database part
        if hasattr(request.app.state, "db_manager"):
            db_manager = request.app.state.db_manager
            health_info["database"] = await db_manager.get_health_info()
        else:
            health_info["database"] = {
                "status": "not_initialized",
                "error": "Database manager not available"
            }
        
        # Schema part
        if hasattr(request.app.state, "schema_manager"):
            schema_manager = request.app.state.schema_manager
            health_info["schema"] = await schema_manager.get_schema_health_info()
        else:
            health_info["schema"] = {
                "status": "not_initialized",
                "error": "Schema manager not available"
            }

        # Enforce connectivity
        if not health_info["database"].get("connectivity", False):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=health_info
            )
        
        return health_info
        
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Database health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": str(e), "timestamp": datetime.now(UTC)}
        )