"""
Bounty model for the BountyBot system.

Represents an active bounty posted within a specific guild. Each bounty
tracks a criminal's route through star systems, player checks, rewards,
and current status.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Portable JSON type: Postgres uses JSONB; SQLite unit-test suite falls back to JSON.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

from persist.database.tablenames import TableNames
from persist.models.base import Base


class Bounty(Base):
    __tablename__ = TableNames.Bounty.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    division: Mapped[str] = mapped_column(String(50), nullable=False)
    criminal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    criminal_faction: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Route and answer
    route: Mapped[list] = mapped_column(_JSONB, nullable=False)
    answer: Mapped[str] = mapped_column(String(255), nullable=False)

    # Rewards
    reward: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_per_sys: Mapped[int] = mapped_column(Integer, nullable=False)

    # Tracking dict: system_name -> user_id (-1 = unchecked)
    checked: Mapped[dict] = mapped_column(_JSONB, nullable=False)

    # Timing
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Criminal details
    tech_level: Mapped[int] = mapped_column(Integer, nullable=False)
    criminal_ship: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)

    # Status tracking
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    escape_count: Mapped[int] = mapped_column(Integer, default=0)
    win_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    respawn_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Criminal damage-state hooks (Phase-2 OOC recovery; Phase-1 is read-only).
    # Symmetric to Player.current_* — NULL == "at full HP, never damaged".
    # Populated by the tick-resolver after a failed bounty attempt; consumed
    # by the criminal-recovery scheduled job (12.5%/hr by default,
    # guild-configurable). Added in revision 0009.
    criminal_current_hull: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criminal_current_armour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criminal_current_shield: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criminal_last_damage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"<Bounty id={self.id} guild_id={self.guild_id} "
            f"division={self.division!r} criminal={self.criminal_name!r} "
            f"status={self.status!r}>"
        )
