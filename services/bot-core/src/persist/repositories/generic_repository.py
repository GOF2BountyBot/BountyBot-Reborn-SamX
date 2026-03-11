from typing import Generic, Optional, Type, TypeVar

from persist.interfaces.repository_interface import IRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar('T')

class GenericRepository(IRepository[T], Generic[T]):
    def __init__(self, model: Type[T]):
        self._model = model

    async def add(self, db: AsyncSession, obj: T) -> T:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    async def create_or_update(self, db: AsyncSession, raw: dict) -> T:
        raise NotImplementedError("Subclasses must implement create_or_update")

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> Optional[T]:
        return await db.get(self._model, obj_id)

    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[T]:
        result = await db.execute(
            select(self._model).filter_by(name=name)
        )
        return result.scalars().one_or_none()

    async def get_by_alias(self, db: AsyncSession, alias: str) -> Optional[T]:
        result = await db.execute(
            select(self._model).where(self._model.aliases.any(alias))
        )
        return result.scalars().one_or_none()

    async def list_all(self, db: AsyncSession) -> list[T]:
        result = await db.execute(select(self._model))
        return result.scalars().all()

    async def remove(self, db: AsyncSession, obj: T) -> None:
        await db.delete(obj)
        await db.commit()
