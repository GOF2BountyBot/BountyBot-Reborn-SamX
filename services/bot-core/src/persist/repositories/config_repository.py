"""
Config repository for the BountyBot inventory system.

Handles database operations for GuildConfig entities including
guild configuration management, settings persistence, and defaults.
"""

from typing import Any

from shared import bblogger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.guild_config import GuildConfig

flogger = bblogger.get_logger("config-repository")


class ConfigRepository(IRepository[GuildConfig]):
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> GuildConfig | None:
        """Get config by ID."""
        try:
            return await db.get(GuildConfig, obj_id)
        except Exception as e:
            flogger.error(f"Error getting config by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> GuildConfig | None:
        """Not applicable for configs."""
        raise NotImplementedError("Configs don't have searchable names")

    async def count(self, db: AsyncSession) -> int:
        """Return total number of guild configs."""
        try:
            result = await db.execute(select(func.count()).select_from(GuildConfig))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting guild configs: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> list[GuildConfig]:
        """Get all guild configs."""
        try:
            result = await db.execute(select(GuildConfig))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all configs: {e}")
            raise

    async def add(self, db: AsyncSession, obj: GuildConfig, *, commit: bool = True) -> GuildConfig:
        """Add new config to database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            flogger.info(f"Added config for guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding config: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, *, commit: bool = True) -> GuildConfig:
        """Create or update config from raw data.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            guild_id = raw.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            # Check if config already exists
            existing_config = await self.get_by_guild_id(db, guild_id)

            if existing_config:
                # Update existing config
                for key, value in raw.items():
                    if hasattr(existing_config, key) and key not in ["id", "guild_id", "created_at"]:
                        setattr(existing_config, key, value)
                try:
                    if commit:
                        await db.commit()
                    else:
                        await db.flush()
                    await db.refresh(existing_config)
                except Exception:
                    if commit:
                        await db.rollback()
                    raise
                flogger.debug(f"Updated config for guild {guild_id}")
                return existing_config
            # Create new config
            config = GuildConfig(**raw)
            return await self.add(db, config, commit=commit)

        except Exception as e:
            flogger.error(f"Error creating/updating config: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: GuildConfig, *, commit: bool = True) -> None:
        """Remove config from database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed config for guild {obj.guild_id}")
        except Exception as e:
            flogger.error(f"Error removing config: {e}")
            if commit:
                await db.rollback()
            raise

    async def get_by_guild_id(self, db: AsyncSession, guild_id: int) -> GuildConfig | None:
        """Get config by guild ID."""
        try:
            result = await db.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting config for guild {guild_id}: {e}")
            raise

    async def create_default_config(self, db: AsyncSession, guild_id: int, *, commit: bool = True) -> GuildConfig:
        """Create a default configuration for a guild.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            default_config = {
                "guild_id": guild_id,
                "admin_role_id": None,
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

            config = await self.create_or_update(db, default_config, commit=commit)
            flogger.info(f"Created default config for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error creating default config for guild {guild_id}: {e}")
            raise

    async def update_shop_config(
        self, db: AsyncSession, config_updates: dict[str, Any], *, commit: bool = True
    ) -> GuildConfig:
        """Update shop-related configuration.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            guild_id = config_updates.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                raise ValueError(f"Config not found for guild {guild_id}")

            # Update shop configuration fields
            updatable_fields = [
                "tech_level_probabilities",
                "sale_price_factor",
                "ship_count_range",
                "weapon_count_range",
                "secondary_weapon_count_range",
                "module_count_range",
                "turret_count_range",
                "ship_quantity_range",
                "weapon_quantity_range",
                "secondary_weapon_quantity_range",
                "module_quantity_range",
                "turret_quantity_range",
            ]

            for field in updatable_fields:
                if field in config_updates:
                    setattr(config, field, config_updates[field])

            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(config)
            except Exception:
                if commit:
                    await db.rollback()
                raise

            flogger.info(f"Updated shop config for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error updating shop config: {e}")
            raise

    async def reset_to_defaults(self, db: AsyncSession, guild_id: int, *, commit: bool = True) -> GuildConfig:
        """Reset guild configuration to default values, preserving infrastructure settings.

        Infrastructure settings preserved: admin_role_id, all channel IDs, and all role IDs
        (category_id, shop_channel_id, bronze/silver/gold/platinum_bounty_channel_id,
        hunting_channel_id, discussion_channel_id, image_channel_id,
        bounty_hunter_role_id, bronze/silver/gold/platinum_role_id).

        Game settings reset: starting_credits, xp_thresholds, shop ranges, etc.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        # Fields to preserve across a reset — infrastructure config that must not be cleared
        _PRESERVED_FIELDS = [
            "admin_role_id",
            "category_id",
            "shop_channel_id",
            "bronze_bounty_channel_id",
            "silver_bounty_channel_id",
            "gold_bounty_channel_id",
            "platinum_bounty_channel_id",
            "hunting_channel_id",
            "discussion_channel_id",
            "image_channel_id",
            "bounty_hunter_role_id",
            "bronze_role_id",
            "silver_role_id",
            "gold_role_id",
            "platinum_role_id",
            "shop_announcements_role_id",
        ]

        try:
            # Preserve infrastructure config before reset
            existing_config = await self.get_by_guild_id(db, guild_id)
            preserved = {}
            if existing_config:
                for field in _PRESERVED_FIELDS:
                    if hasattr(existing_config, field):
                        preserved[field] = getattr(existing_config, field)
                await self.remove(db, existing_config, commit=commit)

            # Create new default config
            config = await self.create_default_config(db, guild_id, commit=False)

            # Re-apply preserved infrastructure values
            for field, value in preserved.items():
                if value is not None and hasattr(config, field):
                    setattr(config, field, value)

            if commit:
                await db.commit()
                await db.refresh(config)
            else:
                await db.flush()

            flogger.info(f"Reset config to defaults for guild {guild_id} (preserved: {list(preserved.keys())})")
            return config

        except Exception as e:
            flogger.error(f"Error resetting config for guild {guild_id}: {e}")
            raise

    async def update_admin_role(
        self, db: AsyncSession, guild_id: int, role_id: int, *, commit: bool = True
    ) -> GuildConfig:
        """Update the admin role for a guild.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                raise ValueError(f"Config not found for guild {guild_id}")

            config.admin_role_id = role_id
            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(config)
            except Exception:
                if commit:
                    await db.rollback()
                raise

            flogger.info(f"Updated admin role for guild {guild_id}: {role_id}")
            return config

        except Exception as e:
            flogger.error(f"Error updating admin role for guild {guild_id}: {e}")
            raise

    async def update_starting_credits(
        self, db: AsyncSession, guild_id: int, new_credits: int, *, commit: bool = True
    ) -> GuildConfig:
        """Update the starting new_credits amount for a guild.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            if new_credits < 0:
                raise ValueError("Starting new_credits cannot be negative")

            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                raise ValueError(f"Config not found for guild {guild_id}")

            config.starting_credits = new_credits
            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(config)
            except Exception:
                if commit:
                    await db.rollback()
                raise

            flogger.info(f"Updated starting new_credits for guild {guild_id}: {new_credits}")
            return config

        except Exception as e:
            flogger.error(f"Error updating starting new_credits for guild {guild_id}: {e}")
            raise

    async def update_xp_thresholds(
        self, db: AsyncSession, guild_id: int, thresholds: dict[str, int], *, commit: bool = True
    ) -> GuildConfig:
        """Update XP thresholds for tier advancement.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                raise ValueError(f"Config not found for guild {guild_id}")

            # Validate thresholds: Silver/Gold/Platinum required; Prestige optional but
            # must be > Platinum if provided.
            required_tiers = ["Silver", "Gold", "Platinum"]
            for tier in required_tiers:
                if tier not in thresholds or thresholds[tier] < 0:
                    raise ValueError(f"Invalid threshold for {tier}")

            # Ensure ascending order
            if not thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]:
                raise ValueError("XP thresholds must be in ascending order")

            if "Prestige" in thresholds:
                # B.48 (F.5): rejected <= 0 to align with config_service.py's
                # validation, which requires Prestige > 0.
                if thresholds["Prestige"] <= 0:
                    raise ValueError("Invalid threshold for Prestige")
                if thresholds["Prestige"] <= thresholds["Platinum"]:
                    raise ValueError("Prestige threshold must be greater than Platinum threshold")

            config.xp_thresholds = thresholds
            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(config)
            except Exception:
                if commit:
                    await db.rollback()
                raise

            flogger.info(f"Updated XP thresholds for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error updating XP thresholds for guild {guild_id}: {e}")
            raise

    async def get_config_summary(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Get a summary of guild configuration."""
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                return {"guild_id": guild_id, "configured": False}

            return {
                "guild_id": guild_id,
                "configured": True,
                "admin_role_configured": config.admin_role_id is not None,
                "starting_credits": config.starting_credits,
                "sale_price_factor": config.sale_price_factor,
                "xp_thresholds": config.xp_thresholds,
                "shop_config": {
                    "item_count_ranges": {
                        "ships": config.ship_count_range,
                        "weapons": config.weapon_count_range,
                        "secondary_weapons": config.secondary_weapon_count_range,
                        "modules": config.module_count_range,
                        "turrets": config.turret_count_range,
                    },
                    "quantity_ranges": {
                        "ships": config.ship_quantity_range,
                        "weapons": config.weapon_quantity_range,
                        "secondary_weapons": config.secondary_weapon_quantity_range,
                        "modules": config.module_quantity_range,
                        "turrets": config.turret_quantity_range,
                    },
                    "tech_level_probabilities": config.tech_level_probabilities,
                },
                "created_at": config.created_at.isoformat(),
                "updated_at": config.updated_at.isoformat(),
                "admin_role_id": config.admin_role_id,
                "category_id": config.category_id,
                "shop_channel_id": config.shop_channel_id,
                "bronze_bounty_channel_id": config.bronze_bounty_channel_id,
                "silver_bounty_channel_id": config.silver_bounty_channel_id,
                "gold_bounty_channel_id": config.gold_bounty_channel_id,
                "platinum_bounty_channel_id": config.platinum_bounty_channel_id,
                "hunting_channel_id": config.hunting_channel_id,
                "discussion_channel_id": config.discussion_channel_id,
                "image_channel_id": config.image_channel_id,
                "bounty_hunter_role_id": config.bounty_hunter_role_id,
                "bronze_role_id": config.bronze_role_id,
                "silver_role_id": config.silver_role_id,
                "gold_role_id": config.gold_role_id,
                "platinum_role_id": config.platinum_role_id,
                "shop_announcements_role_id": config.shop_announcements_role_id,
                "bounty_max_per_tier": config.bounty_max_per_tier,
                "bounty_expiry_minutes": config.bounty_expiry_minutes,
                "bounty_spawn_interval_minutes": config.bounty_spawn_interval_minutes,
                "next_spawn_check_at": (config.next_spawn_check_at.isoformat() if config.next_spawn_check_at else None),
                # B.49: per-guild game-constant overrides (all nullable)
                "division_max_tl": config.division_max_tl,
                "ship_value_reward_percentage": config.ship_value_reward_percentage,
                "criminal_equip_damageless_weapon_chance": config.criminal_equip_damageless_weapon_chance,
                "criminal_max_gear_upgrade": config.criminal_max_gear_upgrade,
                "bounty_reward_to_xp_gain_mult": config.bounty_reward_to_xp_gain_mult,
                "bounty_winner_reserve_factor": config.bounty_winner_reserve_factor,
                "bounty_division_reward_mult": config.bounty_division_reward_mult,
                # bounty_pvc_armour_buff_factor retired T10
                # duel_variance_percent retired T10
                "duel_cloak_chance": config.duel_cloak_chance,
                "close_bounty_threshold": config.close_bounty_threshold,
                "max_route_length": config.max_route_length,
                "min_route_systems": config.min_route_systems,
                "recently_spotted_max_window": config.recently_spotted_max_window,
                "bounty_delay_random_min": config.bounty_delay_random_min,
                "bounty_delay_random_max": config.bounty_delay_random_max,
                "bounty_spawn_jitter": config.bounty_spawn_jitter,
                "check_cooldown": config.check_cooldown,
                "duel_request_expiry": config.duel_request_expiry,
                "guild_activity_decay_rate": config.guild_activity_decay_rate,
                "min_guild_activity": config.min_guild_activity,
                "activity_temp_per_player": config.activity_temp_per_player,
                "shop_default_ships_num": config.shop_default_ships_num,
                "shop_default_weapons_num": config.shop_default_weapons_num,
                "shop_default_modules_num": config.shop_default_modules_num,
                "shop_default_turrets_num": config.shop_default_turrets_num,
                "turret_spawn_probability": config.turret_spawn_probability,
                "classic_credits_per_check": config.classic_credits_per_check,
                "demotion_credit_penalty_pct": config.demotion_credit_penalty_pct,
                "tier_change_cooldown": config.tier_change_cooldown,
                # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
                "long_range_threshold_m": config.long_range_threshold_m,
                "criminal_long_range_pct": config.criminal_long_range_pct,
                "primary_tl_band_weights": config.primary_tl_band_weights,
                "criminal_cloak_chance_by_division": config.criminal_cloak_chance_by_division,
                "criminal_booster_chance_by_division": config.criminal_booster_chance_by_division,
                "criminal_emergency_chance_by_division": config.criminal_emergency_chance_by_division,
                "criminal_weaponmod_chance_by_division": config.criminal_weaponmod_chance_by_division,
                # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 6)
                "criminal_exclude_emp_weapons": config.criminal_exclude_emp_weapons,
                # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2)
                "loot_chance_tractor_t1": config.loot_chance_tractor_t1,
                "loot_chance_tractor_t2": config.loot_chance_tractor_t2,
                "loot_chance_tractor_t3": config.loot_chance_tractor_t3,
                "loot_chance_tractor_t4": config.loot_chance_tractor_t4,
                "loot_chance_no_tractor": config.loot_chance_no_tractor,
                "loot_band1_select_pct": config.loot_band1_select_pct,
                "loot_band2_select_pct": config.loot_band2_select_pct,
                "loot_band3_select_pct": config.loot_band3_select_pct,
                "loot_band1_tl_window": config.loot_band1_tl_window,
                "loot_band1_qty_min": config.loot_band1_qty_min,
                "loot_band1_qty_max": config.loot_band1_qty_max,
                "loot_band1_qty_mode": config.loot_band1_qty_mode,
                "loot_band2_qty_min": config.loot_band2_qty_min,
                "loot_band2_qty_max": config.loot_band2_qty_max,
                "loot_band2_qty_mode": config.loot_band2_qty_mode,
                "loot_band3_qty_min": config.loot_band3_qty_min,
                "loot_band3_qty_max": config.loot_band3_qty_max,
                "loot_band3_qty_mode": config.loot_band3_qty_mode,
                "loot_commodity_sell_fraction": config.loot_commodity_sell_fraction,
                # Shop module-draw combat/filler split
                "shop_combat_module_prob": config.shop_combat_module_prob,
                # D-trivial + DIVISION_TL_CENTERS scalar overrides (revision 0028)
                "criminal_secondary_min_damage": config.criminal_secondary_min_damage,
                "shop_secondary_qty_scaler_heavy": config.shop_secondary_qty_scaler_heavy,
                "shop_secondary_qty_scaler_standard": config.shop_secondary_qty_scaler_standard,
                "shop_tl_band_lo_bronze": config.shop_tl_band_lo_bronze,
                "shop_tl_band_hi_bronze": config.shop_tl_band_hi_bronze,
                "shop_tl_band_lo_silver": config.shop_tl_band_lo_silver,
                "shop_tl_band_hi_silver": config.shop_tl_band_hi_silver,
                "shop_tl_band_lo_gold": config.shop_tl_band_lo_gold,
                "shop_tl_band_hi_gold": config.shop_tl_band_hi_gold,
                "shop_tl_band_lo_platinum": config.shop_tl_band_lo_platinum,
                "shop_tl_band_hi_platinum": config.shop_tl_band_hi_platinum,
                "shop_banded_tl_weight": config.shop_banded_tl_weight,
                "shop_uptier_tl_decay": config.shop_uptier_tl_decay,
                "shop_downtier_tl_decay": config.shop_downtier_tl_decay,
                "division_tl_center_bronze": config.division_tl_center_bronze,
                "division_tl_center_silver": config.division_tl_center_silver,
                "division_tl_center_gold": config.division_tl_center_gold,
                "division_tl_center_platinum": config.division_tl_center_platinum,
                # Previously column-only orphans (columns from 0026; summary exposure added here)
                "bounty_single_waypoint_prob": config.bounty_single_waypoint_prob,
                "bounty_dual_waypoint_prob": config.bounty_dual_waypoint_prob,
                "bounty_waypoint_attempts": config.bounty_waypoint_attempts,
                "bounty_waypoint_min_degree": config.bounty_waypoint_min_degree,
                "pvc_damage_reduction": config.pvc_damage_reduction,
                # Bronze combat bonus per-guild overrides (issue #70 Unit C, revision 0029)
                "bronze_combat_bonus_base_mult": config.bronze_combat_bonus_base_mult,
                "bronze_combat_bonus_per_prestige": config.bronze_combat_bonus_per_prestige,
                "bronze_combat_bonus_cap": config.bronze_combat_bonus_cap,
                # JSONB flatten scalars (issue #70, revision 0030)
                # division_max_tl flat scalars
                "division_max_tl_bronze": config.division_max_tl_bronze,
                "division_max_tl_silver": config.division_max_tl_silver,
                "division_max_tl_gold": config.division_max_tl_gold,
                "division_max_tl_platinum": config.division_max_tl_platinum,
                # bounty_division_reward_mult flat scalars
                "bounty_division_reward_mult_bronze": config.bounty_division_reward_mult_bronze,
                "bounty_division_reward_mult_silver": config.bounty_division_reward_mult_silver,
                "bounty_division_reward_mult_gold": config.bounty_division_reward_mult_gold,
                "bounty_division_reward_mult_platinum": config.bounty_division_reward_mult_platinum,
                # primary_tl_band_weights flat scalars
                "primary_tl_band_weight_center": config.primary_tl_band_weight_center,
                "primary_tl_band_weight_minus1": config.primary_tl_band_weight_minus1,
                "primary_tl_band_weight_plus1": config.primary_tl_band_weight_plus1,
                # criminal chance flat scalars
                "criminal_cloak_chance_bronze": config.criminal_cloak_chance_bronze,
                "criminal_cloak_chance_silver": config.criminal_cloak_chance_silver,
                "criminal_cloak_chance_gold": config.criminal_cloak_chance_gold,
                "criminal_cloak_chance_platinum": config.criminal_cloak_chance_platinum,
                "criminal_booster_chance_bronze": config.criminal_booster_chance_bronze,
                "criminal_booster_chance_silver": config.criminal_booster_chance_silver,
                "criminal_booster_chance_gold": config.criminal_booster_chance_gold,
                "criminal_booster_chance_platinum": config.criminal_booster_chance_platinum,
                "criminal_emergency_chance_bronze": config.criminal_emergency_chance_bronze,
                "criminal_emergency_chance_silver": config.criminal_emergency_chance_silver,
                "criminal_emergency_chance_gold": config.criminal_emergency_chance_gold,
                "criminal_emergency_chance_platinum": config.criminal_emergency_chance_platinum,
                "criminal_weaponmod_chance_bronze": config.criminal_weaponmod_chance_bronze,
                "criminal_weaponmod_chance_silver": config.criminal_weaponmod_chance_silver,
                "criminal_weaponmod_chance_gold": config.criminal_weaponmod_chance_gold,
                "criminal_weaponmod_chance_platinum": config.criminal_weaponmod_chance_platinum,
            }

        except Exception as e:
            flogger.error(f"Error getting config summary for guild {guild_id}: {e}")
            raise

    async def get_all_guild_configs(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Get summary information for all guild configs."""
        try:
            configs = await self.list_all(db)

            return [
                {
                    "guild_id": config.guild_id,
                    "admin_role_configured": config.admin_role_id is not None,
                    "starting_credits": config.starting_credits,
                    "created_at": config.created_at.isoformat(),
                }
                for config in configs
            ]

        except Exception as e:
            flogger.error(f"Error getting all guild configs: {e}")
            raise

    async def update_division_temperatures(
        self,
        db: AsyncSession,
        guild_id: int,
        temperatures: dict[str, float],
        *,
        commit: bool = True,
    ) -> GuildConfig:
        """Persist *temperatures* for the given guild.

        Creates a default config if one does not yet exist.

        Args:
            db: Async database session.
            guild_id: Discord guild snowflake ID.
            temperatures: Mapping of division name (lowercase) → temperature float.
                Example: ``{"bronze": 3.3, "silver": 1.0, "gold": 2.0}``
            commit: When False, flush without committing (caller owns transaction).

        Returns:
            Updated :class:`GuildConfig` instance.
        """
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                flogger.warning(f"update_division_temperatures: no config for guild {guild_id}, skipping")
                return None  # type: ignore[return-value]

            config.division_temperatures = temperatures
            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(config)
            except Exception:
                if commit:
                    await db.rollback()
                raise

            flogger.debug(f"Updated division_temperatures for guild {guild_id}: {temperatures}")
            return config

        except Exception as e:
            flogger.error(f"Error updating division_temperatures for guild {guild_id}: {e}")
            raise

    async def delete_guild_config(self, db: AsyncSession, guild_id: int, *, commit: bool = True) -> bool:
        """Delete all configuration for a guild.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if config:
                await self.remove(db, config, commit=commit)
                flogger.info(f"Deleted config for guild {guild_id}")
                return True
            return False

        except Exception as e:
            flogger.error(f"Error deleting config for guild {guild_id}: {e}")
            raise
