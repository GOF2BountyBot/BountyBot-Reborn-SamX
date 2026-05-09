from typing import Generic, TypeVar

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository

T = TypeVar("T")

flogger = bblogger.get_logger("generic-repository")


class GenericRepository(IRepository[T], Generic[T]):  # noqa: UP046
    def __init__(self, model: type[T]):
        self._model = model

    async def add(self, db: AsyncSession, obj: T, *, commit: bool = True) -> T:
        """Add an entity to the database.

        Args:
            commit: When False, flush without committing (caller owns transaction);
                rollback on exception is also the caller's responsibility.
        """
        flogger.trace(f"add() called: model={self._model.__name__}, obj={obj}")
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            obj_id = getattr(obj, "id", None)
            flogger.debug(f"Successfully added {self._model.__name__}: id={obj_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding {self._model.__name__}: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> T:
        flogger.trace(f"create_or_update() called: model={self._model.__name__}, raw={raw}")
        raise NotImplementedError("Subclasses must implement create_or_update")

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> T | None:
        flogger.trace(f"get_by_id() called: model={self._model.__name__}, obj_id={obj_id}")
        try:
            result = await db.get(self._model, obj_id)
            if result:
                flogger.debug(f"Retrieved {self._model.__name__}: id={obj_id}")
            else:
                flogger.debug(f"No {self._model.__name__} found: id={obj_id}")
            return result
        except Exception as e:
            flogger.error(f"Error getting {self._model.__name__} by id {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> T | None:
        flogger.trace(f"get_by_name() called: model={self._model.__name__}, name={name}")
        try:
            result = await db.execute(select(self._model).filter_by(name=name))
            entity = result.scalars().one_or_none()
            if entity:
                entity_id = getattr(entity, "id", None)
                flogger.debug(f"Retrieved {self._model.__name__} by name: name={name}, id={entity_id}")
            else:
                flogger.debug(f"No {self._model.__name__} found by name: name={name}")
            return entity
        except Exception as e:
            flogger.error(f"Error getting {self._model.__name__} by name {name}: {e}")
            raise

    async def get_by_alias(self, db: AsyncSession, alias: str) -> T | None:
        flogger.trace(f"get_by_alias() called: model={self._model.__name__}, alias={alias}")
        try:
            result = await db.execute(select(self._model).where(self._model.aliases.any(alias)))
            entity = result.scalars().one_or_none()
            if entity:
                entity_id = getattr(entity, "id", None)
                flogger.debug(f"Retrieved {self._model.__name__} by alias: alias={alias}, id={entity_id}")
            else:
                flogger.debug(f"No {self._model.__name__} found by alias: alias={alias}")
            return entity
        except Exception as e:
            flogger.error(f"Error getting {self._model.__name__} by alias {alias}: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> list[T]:
        flogger.trace(f"list_all() called: model={self._model.__name__}")
        try:
            result = await db.execute(select(self._model))
            entities = result.scalars().all()
            flogger.debug(f"Retrieved all {self._model.__name__}: count={len(entities)}")
            return entities
        except Exception as e:
            flogger.error(f"Error listing all {self._model.__name__}: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: T, *, commit: bool = True) -> None:
        """Remove an entity from the database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        obj_id = getattr(obj, "id", None)
        flogger.trace(f"remove() called: model={self._model.__name__}, obj_id={obj_id}")
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.debug(f"Successfully removed {self._model.__name__}: id={obj_id}")
        except Exception as e:
            flogger.error(f"Error removing {self._model.__name__} with id {obj_id}: {e}")
            if commit:
                await db.rollback()
            raise
