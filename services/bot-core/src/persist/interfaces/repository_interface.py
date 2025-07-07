from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession  # switched

T = TypeVar('T')

class IRepository(ABC, Generic[T]):

    @abstractmethod
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> Optional[T]:
        pass

    @abstractmethod
    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[T]:
        """Fetch a T by its unique name"""
        pass

    @abstractmethod
    async def list_all(self, db: AsyncSession) -> List[T]:
        pass

    @abstractmethod
    async def add(self, db: AsyncSession, obj: T) -> T:
        pass

    @abstractmethod
    async def create_or_update(self, db: AsyncSession, raw: dict) -> T:
        """
        Upsert from a raw JSON dict.
        """
        pass

    @abstractmethod
    async def remove(self, db: AsyncSession, obj: T) -> None:
        pass