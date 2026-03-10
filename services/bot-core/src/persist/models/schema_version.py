from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, ARRAY, DateTime
from datetime import datetime, UTC
from persist.models.base import Base
from persist.database.tablenames import TableNames

class SchemaVersion(Base):
    __tablename__ = TableNames.SchemaVersion.value  # Using the Enum to get the name of the table

    version: Mapped[str] = mapped_column(String(50), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    description: Mapped[str] = mapped_column(String, nullable=True)
