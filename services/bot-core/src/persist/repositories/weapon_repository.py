
from shared import bblogger

from persist.models.weapon import Weapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-weapon-repository")

class WeaponRepository(GenericRepository[Weapon]):
    def __init__(self):
        super().__init__(Weapon)

    # Add generic async weapon queries if needed
    # e.g.:
    # async def get_all(self, db: AsyncSession) -> list[Weapon]:
    #     result = await db.execute(select(self._model))
    #     return result.scalars().all()
