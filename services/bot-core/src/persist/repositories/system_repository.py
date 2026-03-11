from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.system import System
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-system-repository")

class SystemRepository(GenericRepository[System]):
    def __init__(self):
        super().__init__(System)

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> System:
        """
        raw is your parsed JSON. We first look up by name,
        then insert or patch fields and commit.
        """
        flogger.trace(f"Creating or updating system from {raw}")

        # look up existing
        result = await db.execute(
            select(self._model).filter_by(name=raw["name"])
        )
        obj = result.scalars().one_or_none()

        # no special JSON-to-attr mapping here
        def to_attr(k: str) -> str:
            return k.lower()

        if obj:
            for k, v in raw.items():
                setattr(obj, to_attr(k), v)
        else:
            attrs = { to_attr(k): v for k, v in raw.items() }
            obj = System(**attrs)
            db.add(obj)

        await db.commit()
        await db.refresh(obj)
        return obj
