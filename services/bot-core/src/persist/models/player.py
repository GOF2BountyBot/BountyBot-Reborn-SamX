"""
Player model for the BountyBot inventory system.

Represents a player instance within a specific guild. Each user can have
multiple players (one per guild) with completely isolated game state.
"""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persist.database.tablenames import TableNames
from persist.models.base import Base


class Player(Base):
    __tablename__ = TableNames.Players.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{TableNames.Users.value}.id"), nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Economy
    credits: Mapped[int] = mapped_column(Integer, nullable=False)  # Set from guild config during creation
    lifetime_credits: Mapped[int] = mapped_column(Integer, default=0)

    # Game statistics
    systems_checked: Mapped[int] = mapped_column(Integer, default=0)
    bounty_wins: Mapped[int] = mapped_column(Integer, default=0)

    # Player progression
    xp: Mapped[int] = mapped_column(Integer, default=0)
    tier: Mapped[str] = mapped_column(String(20), default="Bronze")  # Bronze, Silver, Gold, Platinum
    prestige_count: Mapped[int] = mapped_column(Integer, default=0)

    # Duel statistics
    duel_wins: Mapped[int] = mapped_column(Integer, default=0)
    duel_losses: Mapped[int] = mapped_column(Integer, default=0)
    duel_credits_won: Mapped[int] = mapped_column(Integer, default=0)
    duel_credits_lost: Mapped[int] = mapped_column(Integer, default=0)

    # Display name (server nickname or global display name, updated on each interaction)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)

    # Extended progression fields
    xp_surplus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    guild_transfer_cooldown: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classic_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bounty_cooldown_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tier_change_cooldown_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Active ship reference
    active_ship_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{TableNames.PlayerShips.value}.id", use_alter=True), nullable=True
    )

    # Combat damage-state hooks (Phase-2 OOC recovery; Phase-1 is read-only).
    # All nullable: NULL == "at full HP, never been damaged". Populated by the
    # tick-resolver post-fight; consumed by the OOC-recovery scheduled job
    # (25%/hr player recovery, guild-configurable). Added in revision 0009.
    current_hull: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_armour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_shield: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_damage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="players")
    inventory: Mapped[list["PlayerInventory"]] = relationship(
        "PlayerInventory", back_populates="player", cascade="all, delete-orphan"
    )
    ships: Mapped[list["PlayerShip"]] = relationship(
        "PlayerShip", back_populates="player", cascade="all, delete-orphan", foreign_keys="PlayerShip.player_id"
    )
    active_ship: Mapped[Optional["PlayerShip"]] = relationship(
        "PlayerShip", foreign_keys=[active_ship_id], post_update=True
    )

    def __repr__(self) -> str:
        return f"<Player(id={self.id}, user_id={self.user_id}, guild_id={self.guild_id}, tier='{self.tier}')>"

    @property
    def tier_level(self) -> int:
        """Get numeric tier level for comparisons."""
        tier_levels = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
        return tier_levels.get(self.tier, 1)
