from persist.models.secondary_weapon import SecondaryWeapon
from persist.repositories.generic_repository import GenericRepository

class SecondaryWeaponRepository(GenericRepository[SecondaryWeapon]):
    def __init__(self):
        super().__init__(SecondaryWeapon)

    # Add SecondaryWeapon-specific queries if needed