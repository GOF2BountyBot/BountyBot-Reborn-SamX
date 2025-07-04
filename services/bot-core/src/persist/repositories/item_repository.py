from typing import Any
from sqlalchemy.orm import Session

from persist.models.item import Item
from persist.repositories.generic_repository import GenericRepository

class ItemRepository(GenericRepository[Item]):
    def __init__(self):
        super().__init__(Item)

    # Add Item-specific queries here if needed