"""
Config API router for the BountyBot inventory system.

Handles REST API endpoints for guild configuration management including
settings persistence, validation, and default configurations.
"""

from __future__ import annotations

import typing
from types import NoneType
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from persist.database.manager import get_db_session
from persist.repositories.bounty_repository import BountyRepository
from services.config_service import ConfigService
from services.exceptions import GuildNotConfiguredError
from services.game_constants import GameConstants
from shared import bblogger

from api.config_metadata import DEPRECATED_FIELDS, FIELD_DESCRIPTIONS
from api.schemas.config_schema import (
    BountyConfigResponse,
    BountyConfigStatusResponse,
    ConfigMetadataResponse,
    ConfigValidationResponse,
    FieldMetadata,
    GameConstantsOverridesMixin,
    GuildConfigResponse,
    ResetGameConstantsRequest,
    UpdateBountyConfigRequest,
    UpdateConfigRequest,
    UpdateShopConfigRequest,
    UpdateXPThresholdsRequest,
)

flogger = bblogger.get_logger("config-api-router")

router = APIRouter(
    prefix="/config",
    tags=["config"],
    responses={404: {"description": "Configuration not found"}, 500: {"description": "Internal server error"}},
)

# ---------------------------------------------------------------------------
# B.49: per-guild game-constant override field names (extended over time)
# ---------------------------------------------------------------------------
_OVERRIDE_FIELDS: tuple[str, ...] = (
    # division_max_tl — RETIRED rev 0033 (JSONB dropped, backfilled to division_max_tl_{bronze,...})
    # ship_value_reward_percentage — RETIRED rev 0031
    # criminal_equip_damageless_weapon_chance — RETIRED rev 0031
    "criminal_max_gear_upgrade",
    "bounty_reward_to_xp_gain_mult",
    "bounty_winner_reserve_factor",
    # bounty_division_reward_mult — RETIRED rev 0033 (JSONB dropped; flat scalars added)
    # bounty_pvc_armour_buff_factor retired T10
    # duel_variance_percent retired T10
    # duel_cloak_chance — RETIRED rev 0031
    "close_bounty_threshold",
    "max_route_length",
    "min_route_systems",
    "recently_spotted_max_window",
    # bounty_delay_random_min — RETIRED rev 0031
    # bounty_delay_random_max — RETIRED rev 0031
    # bounty_spawn_jitter — RETIRED rev 0031
    "check_cooldown",
    "duel_request_expiry",
    "tier_change_cooldown",
    # guild_activity_decay_rate — RETIRED rev 0031
    # min_guild_activity — RETIRED rev 0031
    # activity_temp_per_player — RETIRED rev 0031
    # shop_default_ships_num — RETIRED rev 0031
    # shop_default_weapons_num — RETIRED rev 0031
    # shop_default_modules_num — RETIRED rev 0031
    # shop_default_turrets_num — RETIRED rev 0031
    # turret_spawn_probability — RETIRED rev 0031
    "classic_credits_per_check",
    "demotion_credit_penalty_pct",
    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
    "long_range_threshold_m",
    "criminal_long_range_pct",
    # primary_tl_band_weights — RETIRED rev 0033 (JSONB dropped; flat scalars added)
    # criminal_cloak_chance_by_division — RETIRED rev 0033 (JSONB dropped; flat scalars added)
    # criminal_booster_chance_by_division — RETIRED rev 0033 (JSONB dropped)
    # criminal_emergency_chance_by_division — RETIRED rev 0033 (JSONB dropped)
    # criminal_weaponmod_chance_by_division — RETIRED rev 0033 (JSONB dropped)
    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 6)
    "criminal_exclude_emp_weapons",
    # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2)
    "loot_chance_tractor_t1",
    "loot_chance_tractor_t2",
    "loot_chance_tractor_t3",
    "loot_chance_tractor_t4",
    "loot_chance_no_tractor",
    "loot_band1_select_pct",
    "loot_band2_select_pct",
    "loot_band3_select_pct",
    "loot_band1_tl_window",
    "loot_band1_qty_min",
    "loot_band1_qty_max",
    "loot_band1_qty_mode",
    "loot_band2_qty_min",
    "loot_band2_qty_max",
    "loot_band2_qty_mode",
    "loot_band3_qty_min",
    "loot_band3_qty_max",
    "loot_band3_qty_mode",
    "loot_commodity_sell_fraction",
    # Shop module-draw combat/filler split (NULL == GameConstants.SHOP_COMBAT_MODULE_PROB)
    "shop_combat_module_prob",
    # D-trivial + DIVISION_TL_CENTERS scalar overrides (issue #70, revision 0028)
    # Criminal loadout — secondary selection
    "criminal_secondary_min_damage",
    # Shop — secondary weapon quantity scalers
    "shop_secondary_qty_scaler_heavy",
    "shop_secondary_qty_scaler_standard",
    # Shop — per-tier in-band TL range bounds
    "shop_tl_band_lo_bronze",
    "shop_tl_band_hi_bronze",
    "shop_tl_band_lo_silver",
    "shop_tl_band_hi_silver",
    "shop_tl_band_lo_gold",
    "shop_tl_band_hi_gold",
    "shop_tl_band_lo_platinum",
    "shop_tl_band_hi_platinum",
    # Shop — batch TL draw parameters
    "shop_banded_tl_weight",
    "shop_uptier_tl_decay",
    "shop_downtier_tl_decay",
    # Division TL draw centres
    "division_tl_center_bronze",
    "division_tl_center_silver",
    "division_tl_center_gold",
    "division_tl_center_platinum",
    # Previously column-only orphans (columns from 0026; schema exposure added here)
    "bounty_single_waypoint_prob",
    "bounty_dual_waypoint_prob",
    "bounty_waypoint_attempts",
    "bounty_waypoint_min_degree",
    "pvc_damage_reduction",
    # Bronze combat bonus per-guild overrides (issue #70 Unit C, revision 0029)
    "bronze_combat_bonus_base_mult",
    "bronze_combat_bonus_per_prestige",
    "bronze_combat_bonus_cap",
    # JSONB flatten scalars (issue #70, revision 0030) — 27 new fields
    # division_max_tl flat scalars
    "division_max_tl_bronze",
    "division_max_tl_silver",
    "division_max_tl_gold",
    "division_max_tl_platinum",
    # bounty_division_reward_mult flat scalars
    "bounty_division_reward_mult_bronze",
    "bounty_division_reward_mult_silver",
    "bounty_division_reward_mult_gold",
    "bounty_division_reward_mult_platinum",
    # primary_tl_band_weights flat scalars
    "primary_tl_band_weight_center",
    "primary_tl_band_weight_minus1",
    "primary_tl_band_weight_plus1",
    # criminal chance flat scalars
    "criminal_cloak_chance_bronze",
    "criminal_cloak_chance_silver",
    "criminal_cloak_chance_gold",
    "criminal_cloak_chance_platinum",
    "criminal_booster_chance_bronze",
    "criminal_booster_chance_silver",
    "criminal_booster_chance_gold",
    "criminal_booster_chance_platinum",
    "criminal_emergency_chance_bronze",
    "criminal_emergency_chance_silver",
    "criminal_emergency_chance_gold",
    "criminal_emergency_chance_platinum",
    "criminal_weaponmod_chance_bronze",
    "criminal_weaponmod_chance_silver",
    "criminal_weaponmod_chance_gold",
    "criminal_weaponmod_chance_platinum",
    # Combat engine per-guild overrides, wired (issue #70 unit A1, revision 0032) — 22 new fields
    # Accuracy system (§5)
    "cloak_set_value",
    "booster_accuracy_debuff_factor",
    "thruster_accuracy_bonus_factor",
    "auto_turret_accuracy_multiplier",
    "player_base_accuracy",
    "npc_base_accuracy",
    "scanner_tier_b_bonus_pp",
    "scanner_tier_c_bonus_pp",
    # Distance model (§2)
    "starting_distance_m",
    "base_ship_speed_mps",
    "min_distance_m",
    "thruster_window_m",
    # Emergency system (§7.7)
    "emergency_system_invuln_s",
    # Nuke (§6.2)
    "nuke_magnitude_scale",
    "nuke_friendly_factor",
    "nuke_range_regime_threshold_m",
    "nuke_lr_near_frac",
    "nuke_cr_short_m",
    "nuke_cr_overshoot_m",
    "nuke_stack_falloff",
    # Shock-blast (§6.2 / D6)
    "shock_blast_trigger_range_m",
    # Shield / armour regen reemission (CI-21)
    "combat_layer_reemit_fraction",
    # _OVERRIDE_FIELDS == _GAME_CONSTANT_FIELDS (adminCog): 110 fields as of rev 0033 (dropped 7 JSONB dicts).
)


def _build_config_response(config: dict[str, Any]) -> GuildConfigResponse:
    """Assemble a GuildConfigResponse from a config summary dict."""
    override_kwargs = {field: config.get(field) for field in _OVERRIDE_FIELDS}
    return GuildConfigResponse(
        guild_id=config["guild_id"],
        configured=config["configured"],
        admin_role_configured=config["admin_role_configured"],
        starting_credits=config["starting_credits"],
        sale_price_factor=config["sale_price_factor"],
        xp_thresholds=config["xp_thresholds"],
        shop_config=config["shop_config"],
        created_at=config["created_at"],
        updated_at=config["updated_at"],
        category_id=config.get("category_id"),
        shop_channel_id=config.get("shop_channel_id"),
        bronze_bounty_channel_id=config.get("bronze_bounty_channel_id"),
        silver_bounty_channel_id=config.get("silver_bounty_channel_id"),
        gold_bounty_channel_id=config.get("gold_bounty_channel_id"),
        platinum_bounty_channel_id=config.get("platinum_bounty_channel_id"),
        hunting_channel_id=config.get("hunting_channel_id"),
        discussion_channel_id=config.get("discussion_channel_id"),
        image_channel_id=config.get("image_channel_id"),
        admin_role_id=config.get("admin_role_id"),
        bounty_hunter_role_id=config.get("bounty_hunter_role_id"),
        bronze_role_id=config.get("bronze_role_id"),
        silver_role_id=config.get("silver_role_id"),
        gold_role_id=config.get("gold_role_id"),
        platinum_role_id=config.get("platinum_role_id"),
        shop_announcements_role_id=config.get("shop_announcements_role_id"),
        **override_kwargs,
    )


# Dependency injection
async def get_config_service():
    return ConfigService()


@router.get("/guild/{guild_id}", response_model=GuildConfigResponse)
async def get_guild_config(guild_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Get guild configuration. Returns 404 if guild has not been set up via /admin_setup."""
    flogger.debug(f"Getting config for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.get_guild_config(db, guild_id)
            return _build_config_response(config)

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild {guild_id} not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.",
        ) from e
    except Exception as e:
        flogger.error(f"Error getting guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get guild configuration"
        ) from e


@router.put("/guild/{guild_id}", response_model=GuildConfigResponse)
async def update_guild_config(
    guild_id: int, request: UpdateConfigRequest, config_service: ConfigService = Depends(get_config_service)
):
    """Update guild configuration."""
    flogger.info(f"Updating config for guild {guild_id}")

    try:
        # Ensure guild_id matches
        request.guild_id = guild_id

        async with get_db_session() as db:
            config = await config_service.create_or_update_config(db, request.model_dump(exclude_unset=True))
            return _build_config_response(config)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update guild configuration"
        ) from e


@router.put("/guild/{guild_id}/shop", response_model=GuildConfigResponse)
async def update_shop_config(
    guild_id: int, request: UpdateShopConfigRequest, config_service: ConfigService = Depends(get_config_service)
):
    """Update shop-specific configuration parameters."""
    flogger.info(f"Updating shop config for guild {guild_id}")

    try:
        # Ensure guild_id matches
        request.guild_id = guild_id

        async with get_db_session() as db:
            config = await config_service.update_shop_config(db, request.model_dump(exclude_unset=True))
            return _build_config_response(config)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating shop config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update shop configuration"
        ) from e


@router.post("/guild/{guild_id}/reset", response_model=GuildConfigResponse)
async def reset_guild_config(guild_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Reset guild configuration to default values."""
    flogger.info(f"Resetting config to defaults for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.reset_to_defaults(db, guild_id)
            return _build_config_response(config)

    except Exception as e:
        flogger.error(f"Error resetting guild config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset guild configuration"
        ) from e


@router.put("/guild/{guild_id}/admin-role/{role_id}", response_model=GuildConfigResponse)
async def update_admin_role(guild_id: int, role_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Update the admin role for a guild."""
    flogger.info(f"Updating admin role for guild {guild_id}: {role_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_admin_role(db, guild_id, role_id)
            return _build_config_response(config)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating admin role: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update admin role"
        ) from e


@router.put("/guild/{guild_id}/starting-credits/{starting_credits}", response_model=GuildConfigResponse)
async def update_starting_credits(
    guild_id: int, starting_credits: int = Path(..., ge=0), config_service: ConfigService = Depends(get_config_service)
):
    """Update the starting credits amount for new players."""
    flogger.info(f"Updating starting credits for guild {guild_id}: {starting_credits}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_starting_credits(db, guild_id, starting_credits)
            return _build_config_response(config)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating starting credits: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update starting credits"
        ) from e


@router.put("/guild/{guild_id}/xp-thresholds", response_model=GuildConfigResponse)
async def update_xp_thresholds(
    guild_id: int, request: UpdateXPThresholdsRequest, config_service: ConfigService = Depends(get_config_service)
):
    """Update XP thresholds for tier advancement."""
    flogger.info(f"Updating XP thresholds for guild {guild_id}")
    # Ensure body guild_id matches path (defensive — matches other config endpoints)
    request.guild_id = guild_id

    try:
        async with get_db_session() as db:
            config = await config_service.update_xp_thresholds(db, guild_id, request.thresholds)
            return _build_config_response(config)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating XP thresholds: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update XP thresholds"
        ) from e


@router.get("/guild/{guild_id}/validate", response_model=ConfigValidationResponse)
async def validate_guild_config(guild_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Validate that current configuration is compatible with system requirements."""
    flogger.debug(f"Validating config for guild {guild_id}")

    try:
        async with get_db_session() as db:
            validation = await config_service.validate_config_compatibility(db, guild_id)

            return ConfigValidationResponse(
                valid=validation["valid"],
                errors=validation["errors"],
                warnings=validation["warnings"],
                guild_id=validation["guild_id"],
            )

    except Exception as e:
        flogger.error(f"Error validating config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to validate configuration"
        ) from e


@router.get("/guilds", response_model=list[dict[str, Any]])
async def get_all_guild_configs(
    skip: int = 0, limit: int = 100, config_service: ConfigService = Depends(get_config_service)
):
    """Get summary information for all configured guilds."""
    flogger.debug(f"Getting all guild configs: skip={skip}, limit={limit}")

    try:
        async with get_db_session() as db:
            configs = await config_service.get_all_guild_configs(db)

            # Apply pagination
            paginated_configs = configs[skip : skip + limit]

            return paginated_configs

    except Exception as e:
        flogger.error(f"Error getting all guild configs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get guild configurations"
        ) from e


@router.get("/guild/{guild_id}/bounty", response_model=BountyConfigStatusResponse)
async def get_bounty_config(guild_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Get bounty configuration and current active bounty counts for a guild."""
    flogger.debug(f"Getting bounty config for guild {guild_id}")

    try:
        async with get_db_session() as db:
            bounty_config = await config_service.get_bounty_config(db, guild_id)

            # Count active bounties per tier
            bounty_repo = BountyRepository()
            active_per_tier: dict[str, int] = {}
            for tier in ["bronze", "silver", "gold", "platinum"]:
                active_per_tier[tier] = await bounty_repo.count_active_by_guild_and_division(db, guild_id, tier)

            return BountyConfigStatusResponse(
                guild_id=bounty_config["guild_id"],
                max_bounties_per_tier=bounty_config["max_bounties_per_tier"],
                bounty_expiry_minutes=bounty_config["bounty_expiry_minutes"],
                bounty_spawn_interval_minutes=bounty_config["bounty_spawn_interval_minutes"],
                next_spawn_check_at=bounty_config.get("next_spawn_check_at"),
                active_bounties_per_tier=active_per_tier,
            )

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild {guild_id} not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.",
        ) from e
    except Exception as e:
        flogger.error(f"Error getting bounty config for guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get bounty configuration"
        ) from e


@router.put("/guild/{guild_id}/bounty", response_model=BountyConfigResponse)
async def update_bounty_config(
    guild_id: int,
    request: UpdateBountyConfigRequest,
    config_service: ConfigService = Depends(get_config_service),
):
    """Update bounty configuration for a guild."""
    flogger.info(f"Updating bounty config for guild {guild_id}")

    try:
        request.guild_id = guild_id

        async with get_db_session() as db:
            updates = request.model_dump(exclude_unset=True)
            updates.pop("guild_id", None)
            bounty_config = await config_service.update_bounty_config(db, guild_id, updates)

            return BountyConfigResponse(
                guild_id=bounty_config["guild_id"],
                max_bounties_per_tier=bounty_config["max_bounties_per_tier"],
                bounty_expiry_minutes=bounty_config["bounty_expiry_minutes"],
                bounty_spawn_interval_minutes=bounty_config["bounty_spawn_interval_minutes"],
                next_spawn_check_at=bounty_config.get("next_spawn_check_at"),
            )

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild {guild_id} not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.",
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating bounty config for guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update bounty configuration"
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
        "tech_level_probabilities": {"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10},
        "sale_price_factor": 0.8,
        "starting_credits": 0,
        "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000, "Prestige": 50000},
    }


@router.get("/guild/{guild_id}/game-constants", response_model=GameConstantsOverridesMixin)
async def get_game_constants(guild_id: int, config_service: ConfigService = Depends(get_config_service)):
    """Get per-guild game-constant overrides (B.49). NULL fields use global defaults."""
    flogger.debug(f"Getting game constants overrides for guild {guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.get_guild_config(db, guild_id)
            override_data = {field: config.get(field) for field in _OVERRIDE_FIELDS}
            return GameConstantsOverridesMixin(**override_data)

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild {guild_id} not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.",
        ) from e
    except Exception as e:
        flogger.error(f"Error getting game constants for guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get game constants"
        ) from e


@router.post("/guild/{guild_id}/game-constants/reset", response_model=GuildConfigResponse)
async def reset_game_constants(
    guild_id: int,
    request: ResetGameConstantsRequest,
    config_service: ConfigService = Depends(get_config_service),
):
    """Reset per-guild game-constant overrides to NULL (global defaults).

    Pass ``fields`` list to reset specific fields, or omit/pass null to reset all per-guild overrides.
    """
    flogger.info(f"Resetting game constants for guild {guild_id}: fields={request.fields}")

    try:
        # Determine fields to reset
        if request.fields is not None:
            # Validate that all requested fields are valid override fields
            invalid = [f for f in request.fields if f not in _OVERRIDE_FIELDS]
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown game-constant fields: {invalid}. Valid fields are: {list(_OVERRIDE_FIELDS)}",
                )
            fields_to_reset = request.fields
        else:
            fields_to_reset = list(_OVERRIDE_FIELDS)

        async with get_db_session() as db:
            config = await config_service.reset_game_constants(db, guild_id, fields_to_reset)
            return _build_config_response(config)

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild {guild_id} not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error resetting game constants for guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset game constants"
        ) from e


# ---------------------------------------------------------------------------
# GET /config/metadata — guild-independent field type/bounds/default/description
# ---------------------------------------------------------------------------

# Hard-coded defaults for fields that are config columns, not GameConstants.
_CONFIG_COLUMN_DEFAULTS: dict[str, Any] = {
    "starting_credits": 0,
    "sale_price_factor": 0.8,
}

# Complete settable surface: the 110 _OVERRIDE_FIELDS + 2 core config scalars (112).
_METADATA_FIELDS: tuple[str, ...] = (*_OVERRIDE_FIELDS, "starting_credits", "sale_price_factor")


def _get_field_type(fi: Any) -> str:
    """Return 'int'|'float'|'bool'|'dict' for a Pydantic v2 FieldInfo."""
    ann = fi.annotation
    # Unwrap X | None  →  X
    args = typing.get_args(ann)
    if args:
        non_none = [a for a in args if a is not NoneType]
        if non_none:
            ann = non_none[0]
    if ann is bool:
        return "bool"
    if ann is int:
        return "int"
    if ann is float:
        return "float"
    if typing.get_origin(ann) is dict:
        return "dict"
    return "int"  # safe fallback for unexpected annotations


def _get_ge_le(fi: Any) -> tuple[int | float | None, int | float | None]:
    """Return (ge, le) from a Pydantic v2 FieldInfo metadata list."""
    ge: int | float | None = None
    le: int | float | None = None
    for m in fi.metadata:
        if getattr(m, "ge", None) is not None:
            ge = m.ge
        if getattr(m, "le", None) is not None:
            le = m.le
    return ge, le


def _build_metadata() -> ConfigMetadataResponse:
    """Build the full metadata response at module load time (cached in _METADATA_CACHE)."""
    mixin_fields = GameConstantsOverridesMixin.model_fields
    result: dict[str, FieldMetadata] = {}

    for field in _METADATA_FIELDS:
        description = FIELD_DESCRIPTIONS.get(field, f"No description available for {field}.")
        deprecated = field in DEPRECATED_FIELDS

        # default: GameConstants class attribute (UPPERCASE field name) or hard-coded override
        if field in _CONFIG_COLUMN_DEFAULTS:
            default = _CONFIG_COLUMN_DEFAULTS[field]
            field_type: str = "int" if isinstance(default, int) else "float"
            ge: int | float | None = None
            le: int | float | None = None
        elif field in mixin_fields:
            fi = mixin_fields[field]
            field_type = _get_field_type(fi)
            ge, le = _get_ge_le(fi)
            gc_attr = field.upper()
            default = getattr(GameConstants, gc_attr, None)
        else:
            # Field exists in _METADATA_FIELDS but not in mixin (shouldn't happen after guard tests)
            field_type = "int"
            ge, le = None, None
            default = None

        result[field] = FieldMetadata(
            type=field_type,  # type: ignore[arg-type]
            min=ge,
            max=le,
            default=default,
            description=description,
            deprecated=deprecated,
        )

    return ConfigMetadataResponse(fields=result)


# Build once at import time; the metadata is static (no DB access needed).
_METADATA_CACHE: ConfigMetadataResponse = _build_metadata()


@router.get("/metadata", response_model=ConfigMetadataResponse)
async def get_config_metadata() -> ConfigMetadataResponse:
    """Return field-level metadata for every per-guild override setting.

    Guild-independent — no guild_id parameter.  Response is pre-computed at
    import time from GameConstantsOverridesMixin (bounds/type) and GameConstants
    (defaults), so it never touches the database.

    Useful for: autocomplete labels, local bounds pre-checks, help text.
    """
    flogger.debug("GET /config/metadata")
    return _METADATA_CACHE
