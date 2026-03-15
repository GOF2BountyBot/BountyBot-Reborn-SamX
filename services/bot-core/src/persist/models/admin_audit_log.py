"""
Admin audit log model for tracking administrative actions.

Records every admin mutation for traceability and security review.
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from persist.models.base import Base


class AdminAuditLog(Base):
    """Immutable audit record for admin mutations."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Discord user
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # null = system-wide
    action: Mapped[str] = mapped_column(String(64), nullable=False)           # e.g. "guild_reset"
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "player"
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)    # affected resource
    details: Mapped[str | None] = mapped_column(Text, nullable=True)               # JSON payload
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)

    def __repr__(self) -> str:
        return (
            f"<AdminAuditLog id={self.id} user={self.user_id} "
            f"action={self.action!r} status={self.status!r}>"
        )
