from typing import Any
from sqlalchemy.orm import Session

from persist.models.primary_weapon import PrimaryWeapon
from persist.repositories.generic_repository import GenericRepository

class PrimaryWeaponRepository(GenericRepository[PrimaryWeapon]):
    def __init__(self):
        super().__init__(PrimaryWeapon)

    # Add PrimaryWeapon-specific queries if needed