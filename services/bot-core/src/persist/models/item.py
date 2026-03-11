from persist.database.tablenames import TableNames
from persist.models.base import Base
from sqlalchemy import ARRAY, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Item(Base):
    __tablename__ = TableNames.Item.value  # Using the Enum to get the name of the table


    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String))
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    emoji: Mapped[str] = mapped_column(String, nullable=True)
    icon: Mapped[str] = mapped_column(String, nullable=True)
    value: Mapped[int] = mapped_column(Integer)
    wiki: Mapped[str] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String)
