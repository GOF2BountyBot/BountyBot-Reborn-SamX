"""
Config Service for the BountyBot inventory system.

Handles business logic for guild configuration management including
settings persistence, validation, and default configurations.
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import shared.bblogger as bblogger
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.shop_repository import ShopRepository

flogger = bblogger.get_logger("config-service")

class ConfigService:
    def __init__(self):
        self.config_repo = ConfigRepository()
        self.player_repo = PlayerRepository()
        self.shop_repo = ShopRepository()

    async def get_guild_config(self, db: AsyncSession, guild_id: int) -> Dict[str, Any]:
        """Get guild configuration, creating default if none exists."""
        try:
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            
            if not config:
                # Create default configuration
                config = await self.config_repo.create_default_config(db, guild_id)
                flogger.info(f"Created default config for guild {guild_id}")
                
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error getting guild config for {guild_id}: {e}")
            raise

    async def create_or_update_config(self, db: AsyncSession, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update guild configuration."""
        try:
            guild_id = config_data.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")
                
            # Validate configuration data
            validated_config = await self._validate_config_data(config_data)
            
            # Create or update configuration
            config = await self.config_repo.create_or_update(db, validated_config)
            
            # Return summary
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error creating/updating config: {e}")
            raise

    async def update_shop_config(self, db: AsyncSession, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update shop-specific configuration parameters."""
        try:
            guild_id = config_updates.get("guild_id")
            if not guild_id:
                raise ValueError("guild_id is required")
                
            # Validate shop configuration updates
            validated_updates = await self._validate_shop_config(config_updates)
            
            # Update configuration
            config = await self.config_repo.update_shop_config(db, validated_updates)
            
            flogger.info(f"Updated shop config for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error updating shop config: {e}")
            raise

    async def reset_to_defaults(self, db: AsyncSession, guild_id: int) -> Dict[str, Any]:
        """Reset guild configuration to default values."""
        try:
            config = await self.config_repo.reset_to_defaults(db, guild_id)
            
            flogger.info(f"Reset config to defaults for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error resetting config for guild {guild_id}: {e}")
            raise

    async def update_admin_role(self, db: AsyncSession, guild_id: int, role_id: int) -> Dict[str, Any]:
        """Update the admin role for a guild."""
        try:
            if role_id <= 0:
                raise ValueError("Invalid role ID")
                
            config = await self.config_repo.update_admin_role(db, guild_id, role_id)
            
            flogger.info(f"Updated admin role for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error updating admin role for guild {guild_id}: {e}")
            raise

    async def update_starting_credits(self, db: AsyncSession, guild_id: int, credits: int) -> Dict[str, Any]:
        """Update the starting credits amount for new players."""
        try:
            if credits < 0:
                raise ValueError("Starting credits cannot be negative")
                
            config = await self.config_repo.update_starting_credits(db, guild_id, credits)
            
            flogger.info(f"Updated starting credits for guild {guild_id}: {credits}")
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error updating starting credits for guild {guild_id}: {e}")
            raise

    async def update_xp_thresholds(
        self, 
        db: AsyncSession, 
        guild_id: int, 
        thresholds: Dict[str, int]
    ) -> Dict[str, Any]:
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
            if not (thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]):
                raise ValueError("XP thresholds must be in ascending order")
                
            config = await self.config_repo.update_xp_thresholds(db, guild_id, thresholds)
            
            flogger.info(f"Updated XP thresholds for guild {guild_id}")
            return await self.config_repo.get_config_summary(db, guild_id)
            
        except Exception as e:
            flogger.error(f"Error updating XP thresholds for guild {guild_id}: {e}")
            raise

    async def clear_guild_players(self, db: AsyncSession, guild_id: int) -> Dict[str, Any]:
        """Clear all player data for a guild (used in reset operations)."""
        try:
            # Get all players in the guild
            players = await self.player_repo.get_players_by_guild(db, guild_id)
            
            cleared_counts = {
                "players": 0,
                "ships": 0,
                "inventory_items": 0
            }
            
            # Remove each player (cascade will handle ships and inventory)
            for player in players:
                await self.player_repo.remove(db, player)
                cleared_counts["players"] += 1
                
            flogger.warning(f"Cleared {cleared_counts['players']} players from guild {guild_id}")
            return cleared_counts
            
        except Exception as e:
            flogger.error(f"Error clearing players for guild {guild_id}: {e}")
            raise

    async def uninstall_guild(self, db: AsyncSession, guild_id: int) -> Dict[str, Any]:
        """Completely remove all data for a guild."""
        try:
            removed_counts = {
                "players": 0,
                "shop_items": 0,
                "config": 0
            }
            
            # Clear players (this will cascade to ships and inventory)
            player_counts = await self.clear_guild_players(db, guild_id)
            removed_counts["players"] = player_counts["players"]
            
            # Clear all shop items
            await self.shop_repo.clear_all_guild_shops(db, guild_id)
            # Get count would require a separate query, so we'll estimate
            removed_counts["shop_items"] = "all"
            
            # Remove guild config
            config_deleted = await self.config_repo.delete_guild_config(db, guild_id)
            removed_counts["config"] = 1 if config_deleted else 0
            
            flogger.warning(f"Completely uninstalled guild {guild_id}: {removed_counts}")
            return removed_counts
            
        except Exception as e:
            flogger.error(f"Error uninstalling guild {guild_id}: {e}")
            raise

    async def get_all_guild_configs(self, db: AsyncSession) -> List[Dict[str, Any]]:
        """Get summary information for all configured guilds."""
        try:
            configs = await self.config_repo.get_all_guild_configs(db)
            
            flogger.debug(f"Retrieved {len(configs)} guild configurations")
            return configs
            
        except Exception as e:
            flogger.error(f"Error getting all guild configs: {e}")
            raise

    async def validate_config_compatibility(
        self, 
        db: AsyncSession, 
        guild_id: int
    ) -> Dict[str, Any]:
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
            for item_type in ["ship", "weapon", "module", "turret"]:
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
                "guild_id": guild_id
            }
            
            return validation_result
            
        except Exception as e:
            flogger.error(f"Error validating config for guild {guild_id}: {e}")
            raise

    async def _validate_config_data(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate configuration data before saving."""
        validated_config = config_data.copy()
        
        # Validate starting credits
        if "starting_credits" in validated_config:
            if validated_config["starting_credits"] < 0:
                raise ValueError("Starting credits cannot be negative")
                
        # Validate sale price factor
        if "sale_price_factor" in validated_config:
            factor = validated_config["sale_price_factor"]
            if factor <= 0 or factor > 1:
                raise ValueError("Sale price factor must be between 0 and 1")
                
        # Validate XP thresholds if provided
        if "xp_thresholds" in validated_config:
            thresholds = validated_config["xp_thresholds"]
            if not isinstance(thresholds, dict):
                raise ValueError("XP thresholds must be a dictionary")
                
            required_tiers = ["Silver", "Gold", "Platinum"]
            for tier in required_tiers:
                if tier not in thresholds or thresholds[tier] <= 0:
                    raise ValueError(f"Invalid or missing threshold for {tier}")
                    
            if not (thresholds["Silver"] < thresholds["Gold"] < thresholds["Platinum"]):
                raise ValueError("XP thresholds must be in ascending order")
                
        return validated_config

    async def _validate_shop_config(self, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Validate shop configuration updates."""
        validated_updates = config_updates.copy()
        
        # Validate tech level probabilities
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
                
        # Validate count ranges
        range_fields = [
            "ship_count_range", "weapon_count_range", "module_count_range", "turret_count_range",
            "ship_quantity_range", "weapon_quantity_range", "module_quantity_range", "turret_quantity_range"
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