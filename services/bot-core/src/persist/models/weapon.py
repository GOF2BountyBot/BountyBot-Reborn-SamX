from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey
from persist.models.item import Item
from persist.database.tablenames import TableNames

class Weapon(Item):
    __tablename__ = TableNames.Weapon.value   # Enum for 'weapon'

    id: Mapped[int] = mapped_column(Integer, ForeignKey('items.id'), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)

    __mapper_args__ = {
        'polymorphic_identity': 'weapon',
        'concrete': False,
    }