"""
GuildShop model for the BountyBot inventory system.

Represents shop items available for purchase in each guild's tier-based shops.
Each guild has four shops (one per tier) with separate inventories and refresh schedules.
"""

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, Integer, String, DateTime, ForeignKey
from datetime import datetime, UTC
from typing import Optional
from persist.models.base import Base
from persist.database.tablenames import TableNames

class GuildShop(Base):
    __tablename__ = TableNames.GuildShops.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{TableNames.GuildConfigs.value}.guild_id"),
        nullable=False
    )
    tier: Mapped[str] = mapped_column(String(20), nullable=False)  # Bronze, Silver, Gold, Platinum
    tech_level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-9 (randomly selected on refresh)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'ship', 'weapon', 'module', 'turret'
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)  # References static item data
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # Cost in credits
    last_restocked: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    refresh_interval_hours: Mapped[int] = mapped_column(Integer, default=12)  # Hours between refreshes

    # Relationships
    guild_config: Mapped["GuildConfig"] = relationship(
        "GuildConfig",
        back_populates="shops",
        foreign_keys=[guild_id]
    )

    def __repr__(self) -> str:
        return f"<GuildShop(guild_id={self.guild_id}, tier='{self.tier}', item='{self.item_name}', qty={self.quantity}, price={self.price})>"

    def is_refresh_due(self) -> bool:
        """Check if this shop item is due for refresh based on its interval."""
        if not self.last_restocked:
            return True
        hours_since_restock = (datetime.now(UTC) - self.last_restocked).total_seconds() / 3600
        return hours_since_restock >= self.refresh_interval_hours