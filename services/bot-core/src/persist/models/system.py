from sqlalchemy import ARRAY, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class System(Base):
    __tablename__ = TableNames.System.value

    id:           Mapped[int]       = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:         Mapped[str]       = mapped_column(String, unique=True, nullable=False)
    aliases:      Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    coordinates:  Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=True)
    faction:      Mapped[str]       = mapped_column(String)
    neighbours:   Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    security:     Mapped[int]       = mapped_column(Integer)
    wiki:         Mapped[str]       = mapped_column(String, nullable=True)

    __mapper_args__ = {
        'polymorphic_identity': 'system',
        'concrete': False,
    }
