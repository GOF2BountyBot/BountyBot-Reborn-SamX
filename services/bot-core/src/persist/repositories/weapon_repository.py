from persist.models.weapon import Weapon
from persist.repositories.generic_repository import GenericRepository

class WeaponRepository(GenericRepository[Weapon]):
    def __init__(self):
        super().__init__(Weapon)

    # Add generic weapon queries if needed