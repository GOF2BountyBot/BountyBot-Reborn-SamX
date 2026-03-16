"""
Config API router for the BountyBot inventory system.

Handles REST API endpoints for guild configuration management including
settings persistence, validation, and default configurations.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from shared import bblogger

from api.schemas.config_schema import (
    ConfigValidationResponse,
    GuildConfigResponse,
    UpdateConfigRequest,
    UpdateShopConfigRequest,
    UpdateXPThresholdsRequest,
)
from persist.database.manager import get_db_session
from services.config_service import ConfigService

flogger = bblogger.get_logger("config-api-router")

router = APIRouter(
    prefix="/config",
    tags=["config"],
    responses={
        404: {"description": "Configuration not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_config_service():
    return ConfigService()

@router.get("/guild/{guild_id}", response_model=GuildConfigResponse)
async def get_guild_config(
    guild_id: int,
    config_service: ConfigService = Depends(get_config_service)
):
    """Get guild configuration, creating default if none exists."""
    flogger.debug(f"Getting config for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.get_guild_config(db, guild_id)

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except Exception as e:
        flogger.error(f"Error getting guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get guild configuration"
        ) from e

@router.put("/guild/{guild_id}", response_model=GuildConfigResponse)
async def update_guild_config(
    guild_id: int,
    request: UpdateConfigRequest,
    config_service: ConfigService = Depends(get_config_service)
):
    """Update guild configuration."""
    flogger.info(f"Updating config for guild {guild_id}")

    try:
        # Ensure guild_id matches
        request.guild_id = guild_id

        async with get_db_session() as db:
            config = await config_service.create_or_update_config(db, request.model_dump(exclude_unset=True))

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update guild configuration"
        ) from e

@router.put("/guild/{guild_id}/shop", response_model=GuildConfigResponse)
async def update_shop_config(
    guild_id: int,
    request: UpdateShopConfigRequest,
    config_service: ConfigService = Depends(get_config_service)
):
    """Update shop-specific configuration parameters."""
    flogger.info(f"Updating shop config for guild {guild_id}")

    try:
        # Ensure guild_id matches
        request.guild_id = guild_id

        async with get_db_session() as db:
            config = await config_service.update_shop_config(db, request.model_dump(exclude_unset=True))

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating shop config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update shop configuration"
        ) from e

@router.post("/guild/{guild_id}/reset", response_model=GuildConfigResponse)
async def reset_guild_config(
    guild_id: int,
    config_service: ConfigService = Depends(get_config_service)
):
    """Reset guild configuration to default values."""
    flogger.info(f"Resetting config to defaults for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.reset_to_defaults(db, guild_id)

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except Exception as e:
        flogger.error(f"Error resetting guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset guild configuration"
        ) from e

@router.put("/guild/{guild_id}/admin-role/{role_id}", response_model=GuildConfigResponse)
async def update_admin_role(
    guild_id: int,
    role_id: int,
    config_service: ConfigService = Depends(get_config_service)
):
    """Update the admin role for a guild."""
    flogger.info(f"Updating admin role for guild {guild_id}: {role_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_admin_role(db, guild_id, role_id)

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating admin role: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update admin role"
        ) from e

@router.put("/guild/{guild_id}/starting-credits/{credits}", response_model=GuildConfigResponse)
async def update_starting_credits(
    guild_id: int,
    starting_credits: int = Path(..., ge=0),
    config_service: ConfigService = Depends(get_config_service)
):
    """Update the starting credits amount for new players."""
    flogger.info(f"Updating starting credits for guild {guild_id}: {starting_credits}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_starting_credits(db, guild_id, starting_credits)

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating starting credits: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update starting credits"
        ) from e

@router.put("/guild/{guild_id}/xp-thresholds", response_model=GuildConfigResponse)
async def update_xp_thresholds(
    guild_id: int,
    request: UpdateXPThresholdsRequest,
    config_service: ConfigService = Depends(get_config_service)
):
    """Update XP thresholds for tier advancement."""
    flogger.info(f"Updating XP thresholds for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_xp_thresholds(db, guild_id, request.thresholds)

            return GuildConfigResponse(
                guild_id=config["guild_id"],
                configured=config["configured"],
                admin_role_configured=config["admin_role_configured"],
                starting_credits=config["starting_credits"],
                sale_price_factor=config["sale_price_factor"],
                xp_thresholds=config["xp_thresholds"],
                shop_config=config["shop_config"],
                created_at=config["created_at"],
                updated_at=config["updated_at"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating XP thresholds: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update XP thresholds"
        ) from e

@router.get("/guild/{guild_id}/validate", response_model=ConfigValidationResponse)
async def validate_guild_config(
    guild_id: int,
    config_service: ConfigService = Depends(get_config_service)
):
    """Validate that current configuration is compatible with system requirements."""
    flogger.debug(f"Validating config for guild {guild_id}")

    try:
        async with get_db_session() as db:
            validation = await config_service.validate_config_compatibility(db, guild_id)

            return ConfigValidationResponse(
                valid=validation["valid"],
                errors=validation["errors"],
                warnings=validation["warnings"],
                guild_id=validation["guild_id"]
            )

    except Exception as e:
        flogger.error(f"Error validating config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate configuration"
        ) from e

@router.get("/guilds", response_model=list[dict[str, Any]])
async def get_all_guild_configs(
    skip: int = 0,
    limit: int = 100,
    config_service: ConfigService = Depends(get_config_service)
):
    """Get summary information for all configured guilds."""
    flogger.debug(f"Getting all guild configs: skip={skip}, limit={limit}")

    try:
        async with get_db_session() as db:
            configs = await config_service.get_all_guild_configs(db)

            # Apply pagination
            paginated_configs = configs[skip:skip + limit]

            return paginated_configs

    except Exception as e:
        flogger.error(f"Error getting all guild configs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get guild configurations"
        ) from e

@router.get("/defaults")
async def get_default_config():
    """Get the default configuration values."""
    return {
        "ship_count_range": {"min": 3, "max": 5},
        "weapon_count_range": {"min": 3, "max": 5},
        "module_count_range": {"min": 3, "max": 5},
        "turret_count_range": {"min": 3, "max": 5},
        "ship_quantity_range": {"min": 1, "max": 1},
        "weapon_quantity_range": {"min": 2, "max": 4},
        "module_quantity_range": {"min": 2, "max": 4},
        "turret_quantity_range": {"min": 2, "max": 4},
        "tech_level_probabilities": {
            "same_level": 0.70,
            "one_lower": 0.20,
            "two_lower": 0.10
        },
        "sale_price_factor": 0.8,
        "starting_credits": 0,
        "xp_thresholds": {
            "Silver": 1000,
            "Gold": 5000,
            "Platinum": 15000
        }
    }
