"""
Duel request model for the BountyBot system.

Represents a duel challenge between two players within a specific guild.
Tracks the challenger, target, stakes, status, and expiration.
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class DuelRequest(Base):
    __tablename__ = TableNames.DuelRequest.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    challenger_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stakes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Timing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DuelRequest id={self.id} guild_id={self.guild_id} "
            f"challenger={self.challenger_id} target={self.target_id} "
            f"stakes={self.stakes} status={self.status!r}>"
        )
