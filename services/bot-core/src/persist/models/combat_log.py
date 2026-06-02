"""CombatLog model — persisted fight records for the tick-based combat resolver.

One row per resolved fight. The `data` JSON blob carries the full event-tick
timeline + summary (§12). Combatant identity is denormalized (no FK) so NPC
fights (NULL user_id) are handled natively.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class CombatLog(Base):
    __tablename__ = TableNames.CombatLog.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context: Mapped[str] = mapped_column(String(20), nullable=False)

    combatant1_name: Mapped[str] = mapped_column(String(255), nullable=False)
    combatant2_name: Mapped[str] = mapped_column(String(255), nullable=False)
    combatant1_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    combatant2_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    winner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_stalemate: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Full event-tick timeline + summary (§12). Generic JSON — never queried internally.
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Retention key (§12 / Appendix A: COMBAT_LOG_RETENTION_HOURS = 72)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_combat_log_combatant1_user_id", "combatant1_user_id"),
        Index("ix_combat_log_combatant2_user_id", "combatant2_user_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<CombatLog(id={self.id}, context={self.context!r}, "
            f"combatant1={self.combatant1_name!r}, combatant2={self.combatant2_name!r})>"
        )
