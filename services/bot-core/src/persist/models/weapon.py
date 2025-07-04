from typing import Any
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, JSON
from persist.models.item import Item
from persist.database.tablenames import TableNames

class Weapon(Item):
    __tablename__ = TableNames.Weapon.value   # Enum for 'weapon'

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Item.value}.id"), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer, nullable=True)
    # type: Mapped[str] = mapped_column(String, nullable=False) # Inherited from Item
    extra_atts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True, default=dict)

    __mapper_args__ = {
        'polymorphic_identity': 'weapon',
        'concrete': False,
    }