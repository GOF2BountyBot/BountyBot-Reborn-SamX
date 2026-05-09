"""
PlayerShip model for the BountyBot inventory system.

Represents ships owned by a player with their equipped loadouts.
Each player can own multiple ships, but only one can be active at a time.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persist.database.tablenames import TableNames
from persist.models.base import Base


class PlayerShip(Base):
    __tablename__ = TableNames.PlayerShips.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Players.value}.id"), nullable=False)
    ship_name: Mapped[str] = mapped_column(String(100), nullable=False)  # References static ship data
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Custom ship name
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Equipment loadouts (stored as JSON arrays of item names)
    weapons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # Array of equipped primary weapon names
    modules: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # Array of equipped module names
    turrets: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # Array of equipped turret weapon names
    secondary_weapons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # Equipped secondary weapons

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

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
            "secondary_weapons": self.secondary_weapons,
            "modules": self.modules,
            "turrets": self.turrets,
        }
        equipment_list = equipment_map.get(equipment_type, [])
        return len(equipment_list) if equipment_list else 0
