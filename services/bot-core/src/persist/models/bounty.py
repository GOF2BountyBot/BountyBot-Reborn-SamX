"""
Bounty model for the BountyBot system.

Represents an active bounty posted within a specific guild. Each bounty
tracks a criminal's route through star systems, player checks, rewards,
and current status.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

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
    route: Mapped[list] = mapped_column(JSON, nullable=False)
    answer: Mapped[str] = mapped_column(String(255), nullable=False)

    # Rewards
    reward: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_per_sys: Mapped[int] = mapped_column(Integer, nullable=False)

    # Tracking dict: system_name -> user_id (-1 = unchecked)
    checked: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Timing
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Criminal details
    tech_level: Mapped[int] = mapped_column(Integer, nullable=False)
    criminal_ship: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Status tracking
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    escape_count: Mapped[int] = mapped_column(Integer, default=0)
    win_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    respawn_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
