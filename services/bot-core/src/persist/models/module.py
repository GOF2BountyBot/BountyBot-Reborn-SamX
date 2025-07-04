from typing import Any
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, JSON
from persist.models.item import Item
from persist.database.tablenames import TableNames

class Module(Item):
    __tablename__ = TableNames.Module.value   # Using the Enum to get the name of the table

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Item.value}.id"), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer)
    max_equipped: Mapped[int] = mapped_column(Integer)
    extra_atts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True, default=dict)

    __mapper_args__ = {
        'polymorphic_identity': 'module',
        'concrete': False,
    }