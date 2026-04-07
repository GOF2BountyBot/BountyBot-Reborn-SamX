from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.criminal import Criminal
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-criminal-repository")


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
        flogger.trace(f"create_or_update: entry with raw={raw}")

        try:
            # validate required keys upfront
            if "name" not in raw:
                raise ValueError("Missing required key 'name' in data for criminal")

            # look up existing
            result = await db.execute(select(self._model).filter_by(name=raw["name"]))
            obj = result.scalars().one_or_none()

            # attribute name mapping
            mapping = {
                "builtIn": "built_in",
                "isPlayer": "is_player",
            }

            def to_attr(k: str) -> str:
                return mapping.get(k, k.lower())

            if obj:
                flogger.debug(f"Updating existing criminal id={obj.id}, name={obj.name}")
                for k, v in raw.items():
                    setattr(obj, to_attr(k), v)
            else:
                flogger.debug(f"Creating new criminal from raw data: name={raw.get('name')}")
                attrs = {to_attr(k): v for k, v in raw.items()}
                obj = Criminal(**attrs)
                db.add(obj)

            await db.commit()
            await db.refresh(obj)
            flogger.trace(f"create_or_update: exit id={obj.id}, name={obj.name}")
            return obj
        except Exception as e:
            flogger.error(f"Error in create_or_update for criminal name={raw.get('name')}: {e}")
            await db.rollback()
            raise
