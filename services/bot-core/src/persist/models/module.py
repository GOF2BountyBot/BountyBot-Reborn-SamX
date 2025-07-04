from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey
from persist.models.item import Item
from persist.database.tablenames import TableNames

class Module(Item):
    __tablename__ = TableNames.Module.value   # Using the Enum to get the name of the table

    id: Mapped[int] = mapped_column(Integer, ForeignKey('items.id'), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String)

    __mapper_args__ = {
        'polymorphic_identity': 'module',
        'concrete': False,
    }