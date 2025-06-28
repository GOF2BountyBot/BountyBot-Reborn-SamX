"""
Health check router for the BountyBot API.

This module provides health check endpoints to monitor the status
of the bot service and its dependencies.
"""

from fastapi import APIRouter, status
from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any
import sys
import platform
import shared.logging as logging

logger = logging.get_logger("bot-healthcheck-api-router")

router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={
        200: {"description": "Service is healthy"},
        503: {"description": "Service is unhealthy"}
    }
)

class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    version: str
    service: str
    environment: Dict[str, Any]
    checks: Dict[str, bool]

class SimpleHealthResponse(BaseModel):
    """Simple health check response."""
    status: str
    timestamp: datetime

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
    environment, and various system checks.
    """
    # Perform various health checks
    logger.debug("Inside health_check method...")
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "memory_available": True,  # Could implement actual memory check
        "disk_space": True,        # Could implement actual disk check
        # Add more checks as needed for bot dependencies
    }

    # Determine overall status
    all_checks_passed = all(checks.values())
    logger.trace("All Checks Passed: " + str(all_checks_passed))
    service_status = "healthy" if all_checks_passed else "unhealthy"

    return HealthResponse(
        status=service_status,
        timestamp=datetime.utcnow(),
        version="1.0.0",  # Should come from your app config
        service="BountyBot API",
        environment={
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.architecture()[0]
        },
        checks=checks
    )

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

    Returns minimal response for quick health verification.
    """
    return SimpleHealthResponse(
        status="healthy",
        timestamp=datetime.utcnow()
    )

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
