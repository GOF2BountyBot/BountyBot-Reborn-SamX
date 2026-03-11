"""
PlayerInventory model for the BountyBot inventory system.

Represents items owned by a player that are not currently equipped to ships.
"""

from datetime import UTC, datetime

from persist.database.tablenames import TableNames
from persist.models.base import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship


class PlayerInventory(Base):
    __tablename__ = TableNames.PlayerInventories.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{TableNames.Players.value}.id"),
        nullable=False
    )
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'ship', 'weapon', 'module', 'turret'
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)  # References static item data
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    # Relationships
    player: Mapped["Player"] = relationship("Player", back_populates="inventory")

    def __repr__(self) -> str:
        return (
            f"<PlayerInventory(player_id={self.player_id}, item_type='{self.item_type}', "
            f"item_name='{self.item_name}', quantity={self.quantity})>"
        )
