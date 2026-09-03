"""
Config Service for the BountyBot inventory system.

Handles business logic for guild configuration management including
settings persistence, validation, and default configurations.
"""

from typing import Any

from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.combat_log_repository import CombatLogRepository
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.shop_repository import ShopRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.exceptions import GuildNotConfiguredError

flogger = bblogger.get_logger("config-service")

# Re-export so callers can import from either config_service or exceptions
__all__ = ["ConfigService", "GuildNotConfiguredError"]


class ConfigService:
    def __init__(self):
        self.config_repo = ConfigRepository()
        self.player_repo = PlayerRepository()
        self.shop_repo = ShopRepository()
        self.bounty_repo = BountyRepository()
        self.combat_log_repo = CombatLogRepository()

    async def get_guild_config(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Get guild configuration.

        Raises GuildNotConfiguredError if no config exists (no auto-create).
        """
        try:
            config = await self.config_repo.get_by_guild_id(db, guild_id)

            if not config:
                flogger.warning(f"Guild {guild_id} has no config row (admin_setup not run)")
                raise GuildNotConfiguredError(guild_id)

            return await self.config_repo.get_config_summary(db, guild_id)

        except GuildNotConfiguredError:
            raise
        except Exception as e:
            flogger.error(f"Error getting guild config for {guild_id}: {e}")
            raise

    async def create_or_update_config(self, db: AsyncSession, config_data: dict[str, Any]) -> dict[str, Any]:
        """Create or update guild configuration."""
        try:
            guild_id = config_data.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            # Validate configuration data
            validated_config = await self._validate_config_data(config_data)

            # Create or update configuration
            await self.config_repo.create_or_update(db, validated_config)

            # Return summary
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error creating/updating config: {e}")
            raise

    async def update_shop_config(self, db: AsyncSession, config_updates: dict[str, Any]) -> dict[str, Any]:
        """Update shop-specific configuration parameters."""
        try:
            guild_id = config_updates.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")

            # Validate shop configuration updates
            validated_updates = await self._validate_shop_config(config_updates)

            # Update configuration
            await self.config_repo.update_shop_config(db, validated_updates)

            flogger.info(f"Updated shop config for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error updating shop config: {e}")
            raise

    async def reset_to_defaults(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Reset guild configuration to default values."""
        try:
            await self.config_repo.reset_to_defaults(db, guild_id)

            flogger.info(f"Reset config to defaults for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error resetting config for guild {guild_id}: {e}")
            raise

    async def update_admin_role(self, db: AsyncSession, guild_id: int, role_id: int) -> dict[str, Any]:
        """Update the admin role for a guild."""
        try:
            if role_id <= 0:
                raise ValueError("Invalid role ID")

            await self.config_repo.update_admin_role(db, guild_id, role_id)

            flogger.info(f"Updated admin role for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error updating admin role for guild {guild_id}: {e}")
            raise

    async def update_starting_credits(self, db: AsyncSession, guild_id: int, new_credits: int) -> dict[str, Any]:
        """Update the starting new_credits amount for new players."""
        try:
            if new_credits < 0:
                raise ValueError("Starting new_credits cannot be negative")

            await self.config_repo.update_starting_credits(db, guild_id, new_credits)

            flogger.info(f"Updated starting new_credits for guild {guild_id}: {new_credits}")
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error updating starting new_credits for guild {guild_id}: {e}")
            raise

    async def update_xp_thresholds(self, db: AsyncSession, guild_id: int, thresholds: dict[str, int]) -> dict[str, Any]:
        """Update XP thresholds for tier advancement."""
        try:
            # Validate thresholds
            required_tiers = ["Silver", "Gold", "Platinum"]
            for tier in required_tiers:
                if tier not in thresholds:
                    raise ValueError(f"Missing threshold for {tier}")
                if thresholds[tier] <= 0:
                    raise ValueError(f"Threshold for {tier} must be positive")

            # Ensure ascending order
            if not thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]:
                raise ValueError("XP thresholds must be in ascending order")

            # Optional Prestige threshold; if provided, must exceed Platinum.
            if "Prestige" in thresholds:
                if thresholds["Prestige"] <= 0:
                    raise ValueError("Threshold for Prestige must be positive")
                if thresholds["Prestige"] <= thresholds["Platinum"]:
                    raise ValueError("Prestige threshold must be greater than Platinum threshold")

            await self.config_repo.update_xp_thresholds(db, guild_id, thresholds)

            flogger.info(f"Updated XP thresholds for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)

        except Exception as e:
            flogger.error(f"Error updating XP thresholds for guild {guild_id}: {e}")
            raise

    async def clear_guild_players(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Clear all player data for a guild (used in reset operations)."""
        try:
            # Get all players in the guild
            players = await self.player_repo.get_players_by_guild(db, guild_id)

            cleared_counts = {"players": 0, "ships": 0, "inventory_items": 0}

            # Remove each player (cascade will handle ships and inventory)
            for player in players:
                await self.player_repo.remove(db, player)
                cleared_counts["players"] += 1

            flogger.warning(f"Cleared {cleared_counts['players']} players from guild {guild_id}")
            return cleared_counts

        except Exception as e:
            flogger.error(f"Error clearing players for guild {guild_id}: {e}")
            raise

    async def uninstall_guild(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Completely remove all data for a guild.

        Deletes (in order):
        - All players for the guild (cascades to player_ships, player_inventories).
        - All guild_shops rows.
        - All bounty rows (hard-delete — NOT a status-only 'cleared' update).
        - All combat_log rows (privacy: re-registering users must not see prior fights).
        - The guild_configs row.

        This is the shared code path for both ``DELETE /admin/guilds/{id}/uninstall``
        and ``DELETE /admin/guilds/{id}/cleanup`` (on_guild_remove event). Both
        endpoints benefit from the full cascade including bounty + combat_log deletion.
        """
        try:
            removed_counts: dict[str, Any] = {
                "players": 0,
                "shop_items": "all",
                "bounties": 0,
                "combat_log": 0,
                "config": 0,
            }

            # Clear players (this will cascade to ships and inventory)
            player_counts = await self.clear_guild_players(db, guild_id)
            removed_counts["players"] = player_counts["players"]

            # Clear all shop items
            await self.shop_repo.clear_all_guild_shops(db, guild_id)

            # Hard-delete all bounty rows for this guild
            removed_counts["bounties"] = await self.bounty_repo.delete_by_guild_id(db, guild_id)

            # Hard-delete all combat_log rows for this guild
            removed_counts["combat_log"] = await self.combat_log_repo.delete_by_guild_id(db, guild_id)

            # Remove guild config
            config_deleted = await self.config_repo.delete_guild_config(db, guild_id)
            removed_counts["config"] = 1 if config_deleted else 0

            flogger.warning(f"Completely uninstalled guild {guild_id}: {removed_counts}")
            return removed_counts

        except Exception as e:
            flogger.error(f"Error uninstalling guild {guild_id}: {e}")
            raise

    async def get_all_guild_configs(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Get summary information for all configured guilds."""
        try:
            configs = await self.config_repo.get_all_guild_configs(db)

            flogger.debug(f"Retrieved {len(configs)} guild configurations")
            return configs

        except Exception as e:
            flogger.error(f"Error getting all guild configs: {e}")
            raise

    async def validate_config_compatibility(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Validate that current configuration is compatible with system requirements."""
        try:
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                return {"valid": False, "errors": ["No configuration found"]}

            errors = []
            warnings = []

            # Validate XP thresholds
            thresholds = config.xp_thresholds
            if thresholds["Silver"] >= thresholds["Gold"]:
                errors.append("Silver XP threshold must be less than Gold")
            if thresholds["Gold"] >= thresholds["Platinum"]:
                errors.append("Gold XP threshold must be less than Platinum")

            # Validate probabilities sum to 1.0
            probs = config.tech_level_probabilities
            total_prob = probs.get("same_level", 0) + probs.get("one_lower", 0) + probs.get("two_lower", 0)
            if abs(total_prob - 1.0) > 0.01:  # Allow small floating point errors
                errors.append(f"Tech level probabilities must sum to 1.0 (current: {total_prob})")

            # Validate sale price factor
            if config.sale_price_factor <= 0 or config.sale_price_factor > 1:
                errors.append("Sale price factor must be between 0 and 1")

            # Validate item count ranges
            for item_type in ["ship", "weapon", "module", "turret", "secondary_weapon"]:
                count_range = config.get_count_range(item_type)
                if count_range["min"] > count_range["max"]:
                    errors.append(f"{item_type} count range min > max")
                if count_range["min"] < 1:
                    errors.append(f"{item_type} count range min must be >= 1")

            # Check for warnings
            if config.starting_credits == 0:
                warnings.append("Starting credits is 0 - new players will have no credits")

            validation_result = {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "guild_id": guild_id,
            }

            return validation_result

        except Exception as e:
            flogger.error(f"Error validating config for guild {guild_id}: {e}")
            raise

    async def _validate_config_data(self, config_data: dict[str, Any]) -> dict[str, Any]:
        """Validate configuration data before saving."""
        validated_config = config_data.copy()

        # Validate starting credits
        if "starting_credits" in validated_config and validated_config["starting_credits"] < 0:
            raise ValueError("Starting credits cannot be negative")

        # Validate sale price factor
        if "sale_price_factor" in validated_config:
            factor = validated_config["sale_price_factor"]
            if factor <= 0 or factor > 1:
                raise ValueError("Sale price factor must be between 0 and 1")

        if "event_min_duel_stakes" in validated_config:
            stakes = validated_config["event_min_duel_stakes"]
            if stakes < 0:
                raise ValueError("event_min_duel_stakes cannot be negative")

        # Validate XP thresholds if provided
        if "xp_thresholds" in validated_config:
            thresholds = validated_config["xp_thresholds"]
            if not isinstance(thresholds, dict):
                raise ValueError("XP thresholds must be a dictionary")

            required_tiers = ["Silver", "Gold", "Platinum"]
            for tier in required_tiers:
                if tier not in thresholds or thresholds[tier] <= 0:
                    raise ValueError(f"Invalid or missing threshold for {tier}")

            if not thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]:
                raise ValueError("XP thresholds must be in ascending order")

            # Prestige is optional but if provided must exceed Platinum.
            if "Prestige" in thresholds:
                if thresholds["Prestige"] <= 0:
                    raise ValueError("Invalid threshold for Prestige")
                if thresholds["Prestige"] <= thresholds["Platinum"]:
                    raise ValueError("Prestige threshold must be greater than Platinum threshold")

        return validated_config

    async def get_bounty_config(self, db: AsyncSession, guild_id: int) -> dict[str, Any]:
        """Get bounty configuration for a guild.

        Raises GuildNotConfiguredError if no config exists (no auto-create).
        """
        try:
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                flogger.warning(f"Guild {guild_id} has no config row (admin_setup not run)")
                raise GuildNotConfiguredError(guild_id)

            return {
                "guild_id": guild_id,
                "max_bounties_per_tier": config.bounty_max_per_tier or {"bronze": 3, "silver": 3, "gold": 3},
                "bounty_expiry_minutes": config.bounty_expiry_minutes
                if config.bounty_expiry_minutes is not None
                else 480,
                "bounty_spawn_interval_minutes": (
                    config.bounty_spawn_interval_minutes if config.bounty_spawn_interval_minutes is not None else 60
                ),
                "next_spawn_check_at": (config.next_spawn_check_at.isoformat() if config.next_spawn_check_at else None),
            }

        except GuildNotConfiguredError:
            raise
        except Exception as e:
            flogger.error(f"Error getting bounty config for guild {guild_id}: {e}")
            raise

    async def update_bounty_config(self, db: AsyncSession, guild_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist bounty configuration for a guild.

        Args:
            db: Async database session.
            guild_id: Discord guild ID.
            updates: Dict with optional keys: max_bounties_per_tier, bounty_expiry_minutes,
                     bounty_spawn_interval_minutes.

        Returns:
            Updated bounty config dict.

        Raises:
            GuildNotConfiguredError if no config row exists.
        """
        try:
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                flogger.warning(f"Guild {guild_id} has no config row (admin_setup not run)")
                raise GuildNotConfiguredError(guild_id)

            if "max_bounties_per_tier" in updates and updates["max_bounties_per_tier"] is not None:
                tier_map = updates["max_bounties_per_tier"]
                valid_tiers = {"bronze", "silver", "gold", "platinum"}
                if not set(tier_map.keys()).issubset(valid_tiers):
                    invalid = set(tier_map.keys()) - valid_tiers
                    raise ValueError(f"Invalid tier keys: {invalid}. Must be bronze, silver, gold, or platinum.")
                for tier, val in tier_map.items():
                    if not isinstance(val, int) or val < 0 or val > 20:
                        raise ValueError(f"bounty_max_per_tier[{tier!r}] must be an integer between 0 and 20")
                config.bounty_max_per_tier = tier_map

            if "bounty_expiry_minutes" in updates and updates["bounty_expiry_minutes"] is not None:
                val = updates["bounty_expiry_minutes"]
                if val < 10 or val > 10080:
                    raise ValueError("bounty_expiry_minutes must be between 10 and 10080")
                config.bounty_expiry_minutes = val

            if "bounty_spawn_interval_minutes" in updates and updates["bounty_spawn_interval_minutes"] is not None:
                val = updates["bounty_spawn_interval_minutes"]
                if val < 5 or val > 1440:
                    raise ValueError("bounty_spawn_interval_minutes must be between 5 and 1440")
                config.bounty_spawn_interval_minutes = val

            try:
                await db.commit()
                await db.refresh(config)
            except Exception:
                await db.rollback()
                raise

            flogger.info(f"Updated bounty config for guild {guild_id}: {updates}")
            return await self.get_bounty_config(db, guild_id)

        except GuildNotConfiguredError:
            raise
        except Exception as e:
            flogger.error(f"Error updating bounty config for guild {guild_id}: {e}")
            raise

    async def reset_game_constants(self, db: AsyncSession, guild_id: int, fields: list[str]) -> dict[str, Any]:
        """Reset per-guild game-constant overrides to NULL (global defaults).

        Args:
            db:       Async database session.
            guild_id: Discord guild ID.
            fields:   List of field names to reset to NULL.

        Returns:
            Updated config summary dict.

        Raises:
            GuildNotConfiguredError if no config row exists.
        """
        config = await self.config_repo.get_by_guild_id(db, guild_id)
        if config is None:
            raise GuildNotConfiguredError(guild_id)
        for f in fields:
            if hasattr(config, f):
                setattr(config, f, None)
        try:
            await db.commit()
            await db.refresh(config)
        except Exception:
            await db.rollback()
            raise
        flogger.info(f"Reset game constants for guild {guild_id}: {fields}")
        return await self.config_repo.get_config_summary(db, guild_id)

    async def _validate_shop_config(self, config_updates: dict[str, Any]) -> dict[str, Any]:
        """Validate shop configuration updates.

        Accepts either the flat ORM field names (e.g. ``ship_count_range``) or
        the nested schema fields ``item_count_ranges`` / ``quantity_ranges`` that
        the ``UpdateShopConfigRequest`` Pydantic schema exposes.  Nested forms are
        unpacked into flat fields before validation so that the repository layer
        receives the format it expects.
        """
        validated_updates = config_updates.copy()

        # ------------------------------------------------------------------
        # Unpack nested schema fields into the flat ORM field names that the
        # repository understands.
        # ------------------------------------------------------------------

        # item_count_ranges: {"ships": {"min": 3, "max": 5}, ...}
        # → ship_count_range, weapon_count_range, secondary_weapon_count_range,
        #   module_count_range, turret_count_range
        _item_key_map = {
            "ships": "ship_count_range",
            "weapons": "weapon_count_range",
            "secondary_weapons": "secondary_weapon_count_range",
            "modules": "module_count_range",
            "turrets": "turret_count_range",
        }
        if "item_count_ranges" in validated_updates:
            item_ranges = validated_updates.pop("item_count_ranges") or {}
            for schema_key, orm_field in _item_key_map.items():
                if schema_key in item_ranges:
                    validated_updates[orm_field] = item_ranges[schema_key]

        # quantity_ranges: {"ships": {"min": 1, "max": 1}, ...}
        # → ship_quantity_range, weapon_quantity_range, secondary_weapon_quantity_range,
        #   module_quantity_range, turret_quantity_range
        _qty_key_map = {
            "ships": "ship_quantity_range",
            "weapons": "weapon_quantity_range",
            "secondary_weapons": "secondary_weapon_quantity_range",
            "modules": "module_quantity_range",
            "turrets": "turret_quantity_range",
        }
        if "quantity_ranges" in validated_updates:
            qty_ranges = validated_updates.pop("quantity_ranges") or {}
            for schema_key, orm_field in _qty_key_map.items():
                if schema_key in qty_ranges:
                    validated_updates[orm_field] = qty_ranges[schema_key]

        # ------------------------------------------------------------------
        # Validate tech level probabilities
        # ------------------------------------------------------------------
        if "tech_level_probabilities" in validated_updates:
            probs = validated_updates["tech_level_probabilities"]
            if not isinstance(probs, dict):
                raise ValueError("Tech level probabilities must be a dictionary")

            required_keys = ["same_level", "one_lower", "two_lower"]
            for key in required_keys:
                if key not in probs or probs[key] < 0 or probs[key] > 1:
                    raise ValueError(f"Invalid probability for {key}")

            total_prob = sum(probs[key] for key in required_keys)
            if abs(total_prob - 1.0) > 0.01:
                raise ValueError("Probabilities must sum to 1.0")

        # ------------------------------------------------------------------
        # Validate count / quantity range fields (flat ORM names)
        # ------------------------------------------------------------------
        range_fields = [
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

        for field in range_fields:
            if field in validated_updates:
                range_data = validated_updates[field]
                if not isinstance(range_data, dict) or "min" not in range_data or "max" not in range_data:
                    raise ValueError(f"Invalid range format for {field}")

                if range_data["min"] > range_data["max"]:
                    raise ValueError(f"Min cannot be greater than max for {field}")

                if range_data["min"] < 1:
                    raise ValueError(f"Min value must be >= 1 for {field}")

        return validated_updates
