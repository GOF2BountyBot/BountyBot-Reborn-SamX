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

    async def add(self, db: AsyncSession, obj: GuildConfig) -> GuildConfig:
        """Add new config to database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added config for guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding config: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> GuildConfig:
        """Create or update config from raw data."""
        try:
            guild_id = raw.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            # Check if config already exists
            existing_config = await self.get_by_guild_id(db, guild_id)

            if existing_config:
                # Update existing config
                for key, value in raw.items():
                    if hasattr(existing_config, key) and key not in ['id', 'guild_id', 'created_at']:
                        setattr(existing_config, key, value)
                try:
                    await db.commit()
                    await db.refresh(existing_config)
                except Exception:
                    await db.rollback()
                    raise
                flogger.debug(f"Updated config for guild {guild_id}")
                return existing_config
            # Create new config
            config = GuildConfig(**raw)
            return await self.add(db, config)

        except Exception as e:
            flogger.error(f"Error creating/updating config: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: GuildConfig) -> None:
        """Remove config from database."""
        try:
            await db.delete(obj)
            await db.commit()
            flogger.info(f"Removed config for guild {obj.guild_id}")
        except Exception as e:
            flogger.error(f"Error removing config: {e}")
            await db.rollback()
            raise

    async def get_by_guild_id(self, db: AsyncSession, guild_id: int) -> GuildConfig | None:
        """Get config by guild ID."""
        try:
            result = await db.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild_id)
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting config for guild {guild_id}: {e}")
            raise

    async def create_default_config(self, db: AsyncSession, guild_id: int) -> GuildConfig:
        """Create a default configuration for a guild."""
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

            config = await self.create_or_update(db, default_config)
            flogger.info(f"Created default config for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error creating default config for guild {guild_id}: {e}")
            raise

    async def update_shop_config(self, db: AsyncSession, config_updates: dict[str, Any]) -> GuildConfig:
        """Update shop-related configuration."""
        try:
            guild_id = config_updates.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                raise ValueError(f"Config not found for guild {guild_id}")

            # Update shop configuration fields
            updatable_fields = [
                "tech_level_probabilities", "sale_price_factor",
                "ship_count_range", "weapon_count_range", "module_count_range", "turret_count_range",
                "ship_quantity_range", "weapon_quantity_range", "module_quantity_range", "turret_quantity_range"
            ]

            for field in updatable_fields:
                if field in config_updates:
                    setattr(config, field, config_updates[field])

            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
                await db.rollback()
                raise

            flogger.info(f"Updated shop config for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error updating shop config: {e}")
            raise

    async def reset_to_defaults(self, db: AsyncSession, guild_id: int) -> GuildConfig:
        """Reset guild configuration to default values."""
        try:
            # Remove existing config
            existing_config = await self.get_by_guild_id(db, guild_id)
            if existing_config:
                await self.remove(db, existing_config)

            # Create new default config
            config = await self.create_default_config(db, guild_id)

            flogger.info(f"Reset config to defaults for guild {guild_id}")
            return config

        except Exception as e:
            flogger.error(f"Error resetting config for guild {guild_id}: {e}")
            raise

    async def update_admin_role(self, db: AsyncSession, guild_id: int, role_id: int) -> GuildConfig:
        """Update the admin role for a guild."""
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                # Create config if it doesn't exist
                config = await self.create_default_config(db, guild_id)

            config.admin_role_id = role_id
            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
                await db.rollback()
                raise

            flogger.info(f"Updated admin role for guild {guild_id}: {role_id}")
            return config

        except Exception as e:
            flogger.error(f"Error updating admin role for guild {guild_id}: {e}")
            raise

    async def update_starting_credits(self, db: AsyncSession, guild_id: int, new_credits: int) -> GuildConfig:
        """Update the starting new_credits amount for a guild."""
        try:
            if new_credits < 0:
                raise ValueError("Starting new_credits cannot be negative")

            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                config = await self.create_default_config(db, guild_id)

            config.starting_credits = new_credits
            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
                await db.rollback()
                raise

            flogger.info(f"Updated starting new_credits for guild {guild_id}: {new_credits}")
            return config

        except Exception as e:
            flogger.error(f"Error updating starting new_credits for guild {guild_id}: {e}")
            raise

    async def update_xp_thresholds(self, db: AsyncSession, guild_id: int, thresholds: dict[str, int]) -> GuildConfig:
        """Update XP thresholds for tier advancement."""
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                config = await self.create_default_config(db, guild_id)

            # Validate thresholds
            required_tiers = ["Silver", "Gold", "Platinum"]
            for tier in required_tiers:
                if tier not in thresholds or thresholds[tier] < 0:
                    raise ValueError(f"Invalid threshold for {tier}")

            # Ensure ascending order
            if not thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]:
                raise ValueError("XP thresholds must be in ascending order")

            config.xp_thresholds = thresholds
            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
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
                        "modules": config.module_count_range,
                        "turrets": config.turret_count_range
                    },
                    "quantity_ranges": {
                        "ships": config.ship_quantity_range,
                        "weapons": config.weapon_quantity_range,
                        "modules": config.module_quantity_range,
                        "turrets": config.turret_quantity_range
                    },
                    "tech_level_probabilities": config.tech_level_probabilities
                },
                "created_at": config.created_at.isoformat(),
                "updated_at": config.updated_at.isoformat()
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
                    "created_at": config.created_at.isoformat()
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
    ) -> GuildConfig:
        """Persist *temperatures* for the given guild.

        Creates a default config if one does not yet exist.

        Args:
            db: Async database session.
            guild_id: Discord guild snowflake ID.
            temperatures: Mapping of division name (lowercase) → temperature float.
                Example: ``{"bronze": 3.3, "silver": 1.0, "gold": 2.0}``

        Returns:
            Updated :class:`GuildConfig` instance.
        """
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if not config:
                config = await self.create_default_config(db, guild_id)

            config.division_temperatures = temperatures
            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
                await db.rollback()
                raise

            flogger.debug(
                f"Updated division_temperatures for guild {guild_id}: {temperatures}"
            )
            return config

        except Exception as e:
            flogger.error(
                f"Error updating division_temperatures for guild {guild_id}: {e}"
            )
            raise

    async def delete_guild_config(self, db: AsyncSession, guild_id: int) -> bool:
        """Delete all configuration for a guild."""
        try:
            config = await self.get_by_guild_id(db, guild_id)
            if config:
                await self.remove(db, config)
                flogger.info(f"Deleted config for guild {guild_id}")
                return True
            return False

        except Exception as e:
            flogger.error(f"Error deleting config for guild {guild_id}: {e}")
            raise
