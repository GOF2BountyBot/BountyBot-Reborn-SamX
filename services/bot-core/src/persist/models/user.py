"""
User model for the BountyBot inventory system.

Represents Discord users in the system. This is the top-level entity
that can have multiple players across different guilds.
"""

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, String, DateTime
from datetime import datetime, UTC
from typing import List
from persist.models.base import Base
from persist.database.tablenames import TableNames

class User(Base):
    __tablename__ = TableNames.Users.value

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord user ID
    discord_username: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    
    # Relationships
    players: Mapped[List["Player"]] = relationship("Player", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.discord_username}')>"