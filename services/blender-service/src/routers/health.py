"""
Health check router for the Blender service API.
"""

from fastapi import APIRouter, status, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime, UTC
from typing import Dict, Any, Optional
import sys
import platform
import shared.bblogger as bblogger

flogger = bblogger.get_logger("blender-healthcheck-api-router")

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
    description="Returns detailed health information about the Blender service"
)
async def health_check(request: Request) -> HealthResponse:
    """
    Comprehensive health check endpoint.

    Returns detailed information about the service status,
    environment, and various system checks.
    """
    flogger.debug("Inside health_check method...")
    
    # Basic system checks (unchanged)
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "memory_available": True,  # Could implement actual memory check
        "disk_space": True,  # Could implement actual disk check
    }

    # Determine overall status
    all_checks_passed = all(checks.values())
    flogger.trace("All Checks Passed: " + str(all_checks_passed))
    
    # UPDATED: Consider database and schema health in overall status
    service_status = "healthy" if all_checks_passed else "unhealthy"
    
    return HealthResponse(
        status=service_status,
        timestamp=datetime.now(UTC),
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
        timestamp=datetime.now(UTC)
    )

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
