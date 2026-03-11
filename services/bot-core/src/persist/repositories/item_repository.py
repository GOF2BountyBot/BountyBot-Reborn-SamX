
from persist.models.item import Item
from persist.repositories.generic_repository import GenericRepository


class ItemRepository(GenericRepository[Item]):
    def __init__(self):
        super().__init__(Item)

    # Add Item-specific async queries here if needed. All inherited methods are now async:
    # e.g.
    # async def get_by_type(self, db: AsyncSession, item_type: str) -> list[Item]:
    #     result = await db.execute(select(self._model).filter_by(type=item_type))
    #     return result.scalars().all()
