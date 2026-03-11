"""
Health check router for the Discord Gateway API.

This module provides health check endpoints to monitor the status
of the bot service using the new consolidated response schemas.
"""

import platform
import sys
from datetime import UTC, datetime
from typing import Any, Dict

from shared import bblogger
from api.schemas.base_schemas import BaseResponse
from fastapi import APIRouter, Request, status

flogger = bblogger.get_logger("gateway-healthcheck-api-router")

router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={
        200: {"description": "Service is healthy"},
        503: {"description": "Service is unhealthy"}
    }
)

class HealthCheckResponse(BaseResponse):
    """Comprehensive health check response model."""
    version: str
    service: str
    environment: Dict[str, Any]
    checks: Dict[str, bool]

class SimpleHealthResponse(BaseResponse):
    """Simple health check response for load balancers."""

@router.get(
    "",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Comprehensive Health Check",
    description="Returns detailed health information about the Discord Gateway API service"
)
async def health_check(_request: Request) -> HealthCheckResponse:
    """
    Comprehensive health check endpoint.
    Returns detailed information about the service status,
    environment, and various system checks.
    """
    flogger.debug("Inside health_check method...")

    # Basic system checks
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "memory_available": True,  # Could implement actual memory check
        "disk_space": True,  # Could implement actual disk check
    }

    # Determine overall status
    all_checks_passed = all(checks.values())
    flogger.trace(f"All Checks Passed: {all_checks_passed}")

    service_status = "healthy" if all_checks_passed else "unhealthy"

    return HealthCheckResponse(
        status=service_status,
        timestamp=datetime.now(UTC),
        version="1.0.0",  # Should come from your app config
        service="Discord Gateway API",
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
    "liveness",
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
