
from shared import bblogger

from persist.models.weapon import Weapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("weapon-repository")


class WeaponRepository(GenericRepository[Weapon]):
    """Repository for Weapon entities with inherited generic CRUD operations.

    Inherits from GenericRepository:
    - async get_by_id(db, obj_id) -> Weapon | None
    - async get_by_name(db, name) -> Weapon | None
    - async get_by_alias(db, alias) -> Weapon | None
    - async list_all(db) -> list[Weapon]
    - async add(db, obj) -> Weapon
    - async remove(db, obj) -> None
    """

    def __init__(self):
        """Initialize WeaponRepository with Weapon model class.

        Raises:
            Exception: Any initialization errors from parent GenericRepository.
        """
        flogger.trace("Initializing WeaponRepository")
        try:
            super().__init__(Weapon)
            flogger.debug(f"WeaponRepository initialized with model class: {Weapon.__name__}")
            flogger.trace("WeaponRepository initialization complete")
        except Exception as e:
            flogger.error(f"Failed to initialize WeaponRepository: {e}")
            raise

    # Add generic async weapon queries if needed
    # e.g.:
    # async def get_all(self, db: AsyncSession) -> list[Weapon]:
    #     result = await db.execute(select(self._model))
    #     return result.scalars().all()
