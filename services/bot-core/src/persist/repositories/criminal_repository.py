from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import shared.logging as logging

from persist.models.criminal import Criminal
from persist.repositories.generic_repository import GenericRepository

logger = logging.get_logger("bot-criminal-repository")

class CriminalRepository(GenericRepository[Criminal]):
    def __init__(self):
        super().__init__(Criminal)

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Criminal:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        """
        logger.trace(f"Creating or updating criminal from {raw}")

        # look up existing
        result = await db.execute(
            select(self._model).filter_by(name=raw["name"])
        )
        obj = result.scalars().one_or_none()

        # attribute name mapping
        mapping = {
            "builtIn":  "built_in",
            "isPlayer": "is_player",
        }
        def to_attr(k: str) -> str:
            return mapping.get(k, k.lower())

        if obj:
            for k, v in raw.items():
                setattr(obj, to_attr(k), v)
        else:
            attrs = { to_attr(k): v for k, v in raw.items() }
            obj = Criminal(**attrs)
            db.add(obj)

        await db.commit()
        await db.refresh(obj)
        return obj