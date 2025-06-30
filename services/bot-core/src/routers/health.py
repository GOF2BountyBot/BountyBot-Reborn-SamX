"""
Health check router for the BountyBot API.

This module provides health check endpoints to monitor the status
of the bot service and its dependencies, including database connectivity
and schema version information.

CHANGES MADE:
- Added database health information to comprehensive health check
- Added schema version reporting
- Enhanced error handling for database failures
- All existing API endpoints preserved
"""

from fastapi import APIRouter, status, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any, Optional
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
    """Health check response model - UPDATED with database fields."""
    status: str
    timestamp: datetime
    version: str
    service: str
    environment: Dict[str, Any]
    checks: Dict[str, bool]
    # NEW: Database health information
    database: Optional[Dict[str, Any]] = None
    schema: Optional[Dict[str, Any]] = None

class SimpleHealthResponse(BaseModel):
    """Simple health check response - NO CHANGES."""
    status: str
    timestamp: datetime

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

    Returns detailed information about the service status,
    environment, database connectivity, schema version, and various system checks.
    
    UPDATED: Now includes database and schema health information
    """
    logger.debug("Inside health_check method...")
    
    # Basic system checks (unchanged)
    checks = {
        "python_version": sys.version_info >= (3, 8),
        "memory_available": True,  # Could implement actual memory check
        "disk_space": True,  # Could implement actual disk check
    }
    
    # NEW: Database and schema health checks
    database_health = None
    schema_health = None
    database_accessible = False
    schema_current = False
    
    try:
        # Get database manager from app state
        if hasattr(request.app.state, 'db_manager'):
            db_manager = request.app.state.db_manager
            database_health = db_manager.get_health_info()
            database_accessible = database_health.get("connectivity", False)
            
            # Add database connectivity to checks
            checks["database_connectivity"] = database_accessible
            
        else:
            logger.warning("Database manager not found in app state")
            checks["database_connectivity"] = False
            database_health = {
                "status": "not_initialized",
                "error": "Database manager not available"
            }
            
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        checks["database_connectivity"] = False
        database_health = {
            "status": "error",
            "error": str(e)
        }
    
    try:
        # Get schema manager from app state
        if hasattr(request.app.state, 'schema_manager'):
            schema_manager = request.app.state.schema_manager
            schema_health = schema_manager.get_schema_health_info()
            schema_current = schema_health.get("version_match", False)
            
            # Add schema version to checks
            checks["schema_version_current"] = schema_current
            
        else:
            logger.warning("Schema manager not found in app state")
            checks["schema_version_current"] = False
            schema_health = {
                "status": "not_initialized",
                "error": "Schema manager not available"
            }
            
    except Exception as e:
        logger.error(f"Schema health check failed: {e}")
        checks["schema_version_current"] = False
        schema_health = {
            "status": "error",
            "error": str(e)
        }
    
    # Determine overall status
    all_checks_passed = all(checks.values())
    logger.trace("All Checks Passed: " + str(all_checks_passed))
    
    # UPDATED: Consider database and schema health in overall status
    service_status = "healthy" if all_checks_passed else "unhealthy"
    
    # If database is completely inaccessible, mark as unhealthy
    if not database_accessible:
        service_status = "unhealthy"
        logger.warning("Marking service as unhealthy due to database connectivity issues")
    
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
        checks=checks,
        database=database_health,  # NEW: Database health info
        schema=schema_health       # NEW: Schema health info
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
    
    NO CHANGES: Preserved for load balancer compatibility
    """
    return SimpleHealthResponse(
        status="healthy",
        timestamp=datetime.utcnow()
    )

@router.get(
    "/readiness",
    status_code=status.HTTP_200_OK,
    summary="Readiness Check",
    description="Checks if the service is ready to accept requests (includes database connectivity)"
)
async def readiness_check(request: Request) -> Dict[str, str]:
    """
    Readiness probe endpoint.

    Used by orchestrators to determine if the service
    is ready to receive traffic.
    
    UPDATED: Now checks database connectivity for readiness
    """
    try:
        # Check if database is accessible
        if hasattr(request.app.state, 'db_manager'):
            db_manager = request.app.state.db_manager
            db_health = db_manager.get_health_info()
            
            if not db_health.get("connectivity", False):
                logger.warning("Service not ready: database not accessible")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Database not accessible"
                )
        
        return {"status": "ready"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
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
    """
    Liveness probe endpoint.

    Used by orchestrators to determine if the service
    should be restarted.
    
    NO CHANGES: Basic liveness check unchanged
    """
    return {"status": "alive"}

# NEW: Database-specific health endpoint
@router.get(
    "/database",
    status_code=status.HTTP_200_OK,
    summary="Database Health Check",
    description="Detailed database connectivity and schema information"
)
async def database_health_check(request: Request) -> Dict[str, Any]:
    """
    Database-specific health check endpoint.
    
    Returns detailed information about database connectivity,
    connection pool status, and schema version.
    """
    try:
        health_info = {
            "timestamp": datetime.utcnow(),
            "database": None,
            "schema": None
        }
        
        # Get database health
        if hasattr(request.app.state, 'db_manager'):
            db_manager = request.app.state.db_manager
            health_info["database"] = db_manager.get_health_info()
        else:
            health_info["database"] = {
                "status": "not_initialized",
                "error": "Database manager not available"
            }
        
        # Get schema health
        if hasattr(request.app.state, 'schema_manager'):
            schema_manager = request.app.state.schema_manager
            health_info["schema"] = schema_manager.get_schema_health_info()
        else:
            health_info["schema"] = {
                "status": "not_initialized", 
                "error": "Schema manager not available"
            }
        
        # Determine if we should return error status
        db_healthy = health_info["database"].get("connectivity", False)
        schema_healthy = health_info["schema"].get("version_match", False)
        
        if not db_healthy:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=health_info
            )
            
        return health_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": str(e), "timestamp": datetime.utcnow()}
        )
