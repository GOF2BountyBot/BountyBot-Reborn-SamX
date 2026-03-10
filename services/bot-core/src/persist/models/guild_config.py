"""
GuildConfig model for the BountyBot inventory system.

Stores all configurable parameters for each guild including shop settings,
economic factors, progression thresholds, and administrative settings.
"""

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, Integer, String, DateTime, Float, JSON
from datetime import datetime, UTC
from typing import List, Dict, Any
from persist.models.base import Base
from persist.database.tablenames import TableNames

class GuildConfig(Base):
    __tablename__ = TableNames.GuildConfigs.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    
    # Admin role configuration
    admin_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    
    # Shop inventory size ranges (JSON objects with min/max values)
    ship_count_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 3, "max": 5})
    weapon_count_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 3, "max": 5})
    module_count_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 3, "max": 5})
    turret_count_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 3, "max": 5})
    
    # Quantity ranges for each item type
    ship_quantity_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 1, "max": 1})
    weapon_quantity_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 2, "max": 4})
    module_quantity_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 2, "max": 4})
    turret_quantity_range: Mapped[Dict[str, int]] = mapped_column(JSON, default={"min": 2, "max": 4})
    
    # Tech level probabilities (JSON objects)
    tech_level_probabilities: Mapped[Dict[str, float]] = mapped_column(JSON, default={
        "same_level": 0.70,
        "one_lower": 0.20,
        "two_lower": 0.10
    })
    
    # Economic settings
    sale_price_factor: Mapped[float] = mapped_column(Float, default=0.8)
    starting_credits: Mapped[int] = mapped_column(Integer, default=0)
    
    # XP and tier thresholds
    xp_thresholds: Mapped[Dict[str, int]] = mapped_column(JSON, default={
        "Silver": 1000,
        "Gold": 5000,
        "Platinum": 15000
    })
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    
    # Relationships
    shops: Mapped[List["GuildShop"]] = relationship("GuildShop", back_populates="guild_config")

    def __repr__(self) -> str:
        return f"<GuildConfig(guild_id={self.guild_id}, starting_credits={self.starting_credits})>"
        
    def get_tier_threshold(self, tier: str) -> int:
        """Get XP threshold for a specific tier."""
        return self.xp_thresholds.get(tier, 0)
        
    def get_count_range(self, item_type: str) -> Dict[str, int]:
        """Get item count range for shop generation."""
        range_map = {
            "ship": self.ship_count_range,
            "weapon": self.weapon_count_range,
            "module": self.module_count_range,
            "turret": self.turret_count_range
        }
        return range_map.get(item_type, {"min": 1, "max": 1})
        
    def get_quantity_range(self, item_type: str) -> Dict[str, int]:
        """Get quantity range for shop items of a specific type."""
        range_map = {
            "ship": self.ship_quantity_range,
            "weapon": self.weapon_quantity_range,
            "module": self.module_quantity_range,
            "turret": self.turret_quantity_range
        }
        return range_map.get(item_type, {"min": 1, "max": 1})