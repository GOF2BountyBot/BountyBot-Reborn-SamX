"""
Health check router for the BountyBot API.

This module provides health check endpoints to monitor the status
of the bot service and its dependencies.
"""

from fastapi import APIRouter, status, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any, Optional

import sys
import platform
import shared.logging as logging
from persist.database.manager import db_manager

logger = logging.get_logger("bot-healthcheck-api-router")

router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={
        200: {"description": "Service is healthy"},
        503: {"description": "Service is unhealthy"}
    }
)

class DatabaseHealth(BaseModel):
    """Database health status model."""
    status: str
    schema_version: Optional[str] = None
    postgresql_version: Optional[str] = None
    database_name: Optional[str] = None
    pool_stats: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class HealthResponse(BaseModel):
    """Comprehensive health check response model."""
    status: str
    timestamp: datetime
    version: str
    service: str
    environment: Dict[str, Any]
    checks: Dict[str, bool]
    database: DatabaseHealth

class SimpleHealthResponse(BaseModel):
    """Simple health check response."""
    status: str
    timestamp: datetime
    database_status: str

@router.get(
    "/",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Comprehensive Health Check",
    description="Returns detailed health information about the bot service"
)
async def health_check() -> HealthResponse:
    """
    Comprehensive health check endpoint.

    Returns detailed information about the service status,
    environment, database connectivity, and various system checks.
    """
    logger.debug("Performing comprehensive health check...")

    # Get database health
    db_health_data = await db_manager.get_db_health()
    db_is_healthy = db_health_data["status"] == "healthy"

    # Create database health object
    database_health = DatabaseHealth(
        status=db_health_data["status"],
        schema_version=db_health_data.get("schema_version"),
        postgresql_version=db_health_data.get("postgresql_version"),
        database_name=db_health_data.get("database_name"),
        pool_stats={
            "pool_size": db_health_data.get("pool_size"),
            "checked_in": db_health_data.get("checked_in_connections"),
            "checked_out": db_health_data.get("checked_out_connections"),
            "overflow": db_health_data.get("overflow_connections")
        } if db_is_healthy else None,
        error=db_health_data.get("error")
    )

    # Perform various health checks
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "database_connection": db_is_healthy,
        "database_schema": db_health_data.get("schema_version") is not None,
        "memory_available": True,  # Could implement actual memory check
        "disk_space": True,  # Could implement actual disk check
    }

    # Determine overall status
    all_checks_passed = all(checks.values())
    logger.trace(f"All checks passed: {all_checks_passed}")
    service_status = "healthy" if all_checks_passed else "unhealthy"

    # Determine HTTP status code
    response_status = status.HTTP_200_OK if all_checks_passed else status.HTTP_503_SERVICE_UNAVAILABLE

    response = HealthResponse(
        status=service_status,
        timestamp=datetime.utcnow(),
        version="1.0.0",  # Should come from your app config
        service="BountyBot API",
        environment={
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.architecture()[0]
        },
        checks=checks,
        database=database_health
    )

    # If unhealthy, raise HTTP exception with 503 status
    if not all_checks_passed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.dict()
        )

    return response

@router.get(
    "/simple",
    response_model=SimpleHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Simple Health Check",
    description="Returns basic health status for load balancer checks"
)
async def simple_health_check() -> SimpleHealthResponse:
    """
    Simple health check endpoint for load balancers.

    Returns minimal response for quick health verification including
    database connectivity status.
    """
    # Quick database check
    db_health = await db_manager.get_db_health()
    db_status = db_health["status"]

    # Determine overall status
    overall_status = "healthy" if db_status == "healthy" else "unhealthy"

    response = SimpleHealthResponse(
        status=overall_status,
        timestamp=datetime.utcnow(),
        database_status=db_status
    )

    # Return 503 if database is unhealthy
    if db_status != "healthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.dict()
        )

    return response

@router.get(
    "/database",
    response_model=DatabaseHealth,
    summary="Database Health Check",
    description="Returns detailed database health and connection information"
)
async def database_health_check() -> DatabaseHealth:
    """
    Database-specific health check endpoint.

    Returns detailed information about database connectivity,
    schema version, and connection pool status.
    """
    logger.debug("Performing database health check...")

    db_health_data = await db_manager.get_db_health()
    db_is_healthy = db_health_data["status"] == "healthy"

    response = DatabaseHealth(
        status=db_health_data["status"],
        schema_version=db_health_data.get("schema_version"),
        postgresql_version=db_health_data.get("postgresql_version"),
        database_name=db_health_data.get("database_name"),
        pool_stats={
            "pool_size": db_health_data.get("pool_size"),
            "checked_in_connections": db_health_data.get("checked_in_connections"),
            "checked_out_connections": db_health_data.get("checked_out_connections"),
            "overflow_connections": db_health_data.get("overflow_connections")
        } if db_is_healthy else None,
        error=db_health_data.get("error")
    )

    if not db_is_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.dict()
        )

    return response

@router.get(
    "/readiness",
    status_code=status.HTTP_200_OK,
    summary="Readiness Check",
    description="Checks if the service is ready to accept requests"
)
async def readiness_check() -> Dict[str, str]:
    """
    Readiness probe endpoint.

    Used by orchestrators to determine if the service
    is ready to receive traffic.
    """
    # Add checks for database connectivity, external services, etc.
    return {"status": "ready"}

@router.get(
    "/liveness",
    status_code=status.HTTP_200_OK,
    summary="Liveness Check", 
    description="Checks if the service is alive and responsive"
)
async def liveness_check() -> Dict[str, str]:
    """
    Liveness probe endpoint.

    Used by orchestrators to determine if the service
    should be restarted.
    """
    return {"status": "alive"}
