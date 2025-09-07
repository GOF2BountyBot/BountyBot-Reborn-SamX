"""
PlayerShip model for the BountyBot inventory system.

Represents ships owned by a player with their equipped loadouts.
Each player can own multiple ships, but only one can be active at a time.
"""

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, DateTime, Boolean, JSON, ForeignKey
from datetime import datetime
from typing import List, Optional
from persist.models.base import Base
from persist.database.tablenames import TableNames

class PlayerShip(Base):
    __tablename__ = TableNames.PlayerShips.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{TableNames.Players.value}.id"),
        nullable=False
    )
    ship_name: Mapped[str] = mapped_column(String(100), nullable=False)  # References static ship data
    nickname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Custom ship name
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Equipment loadouts (stored as JSON arrays of item names)
    weapons: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)  # Array of equipped weapon names
    modules: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)  # Array of equipped module names
    turrets: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)  # Array of equipped turret names

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship("Player", back_populates="ships", foreign_keys=[player_id])

    def __repr__(self) -> str:
        display_name = self.nickname or self.ship_name
        return f"<PlayerShip(id={self.id}, player_id={self.player_id}, ship='{display_name}', active={self.is_active})>"

    @property
    def display_name(self) -> str:
        """Get the display name (nickname if set, otherwise ship name)."""
        return self.nickname or self.ship_name

    def get_equipped_count(self, equipment_type: str) -> int:
        """Get the count of equipped items of a specific type."""
        equipment_map = {
            "weapons": self.weapons,
            "modules": self.modules,
            "turrets": self.turrets
        }
        equipment_list = equipment_map.get(equipment_type, [])
        return len(equipment_list) if equipment_list else 0