from persist.models.turret_weapon import TurretWeapon
from persist.repositories.generic_repository import GenericRepository

class TurretWeaponRepository(GenericRepository[TurretWeapon]):
    def __init__(self):
        super().__init__(TurretWeapon)

    # Add TurretWeapon-specific queries if needed