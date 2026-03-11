from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession  # switched

T = TypeVar('T')

class IRepository(ABC, Generic[T]):

    @abstractmethod
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> T | None:
        pass

    @abstractmethod
    async def get_by_name(self, db: AsyncSession, name: str) -> T | None:
        """Fetch a T by its unique name"""

    @abstractmethod
    async def list_all(self, db: AsyncSession) -> list[T]:
        pass

    @abstractmethod
    async def add(self, db: AsyncSession, obj: T) -> T:
        pass

    @abstractmethod
    async def create_or_update(self, db: AsyncSession, raw: dict) -> T:
        """
        Upsert from a raw JSON dict.
        """

    @abstractmethod
    async def remove(self, db: AsyncSession, obj: T) -> None:
        pass
