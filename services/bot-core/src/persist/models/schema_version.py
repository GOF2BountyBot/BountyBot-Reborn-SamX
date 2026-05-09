from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class SchemaVersion(Base):
    __tablename__ = TableNames.SchemaVersion.value  # Using the Enum to get the name of the table

    version: Mapped[str] = mapped_column(String(50), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    description: Mapped[str] = mapped_column(String, nullable=True)
