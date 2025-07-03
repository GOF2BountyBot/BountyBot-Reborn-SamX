from persist.models.module import Module
from persist.repositories.generic_repository import GenericRepository

class ModuleRepository(GenericRepository[Module]):
    def __init__(self):
        super().__init__(Module)

    # Add Module-specific queries here if needed