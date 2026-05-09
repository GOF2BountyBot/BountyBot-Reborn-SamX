from sqlalchemy import ARRAY, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class Criminal(Base):
    __tablename__ = TableNames.Criminal.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    faction: Mapped[str] = mapped_column(String)
    icon: Mapped[str] = mapped_column(String, nullable=True)
    is_player: Mapped[bool] = mapped_column(Boolean, default=False)
    wiki: Mapped[str] = mapped_column(String, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "criminal",
        "concrete": False,
    }
