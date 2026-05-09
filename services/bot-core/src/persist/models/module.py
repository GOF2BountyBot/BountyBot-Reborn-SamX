from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.item import Item


class Module(Item):
    __tablename__ = TableNames.Module.value  # Using the Enum to get the name of the table

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Item.value}.id"), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer)
    max_equipped: Mapped[int] = mapped_column(Integer, nullable=True)
    extra_atts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True, default=dict)

    __mapper_args__ = {
        "polymorphic_identity": "module",
        "concrete": False,
    }
