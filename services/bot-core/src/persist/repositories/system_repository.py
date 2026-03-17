from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.system import System
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-system-repository")

class SystemRepository(GenericRepository[System]):
    def __init__(self):
        flogger.trace("Initializing SystemRepository")
        super().__init__(System)
        flogger.debug("SystemRepository initialized with model: System")

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> System:
        """
        raw is your parsed JSON. We first look up by name,
        then insert or patch fields and commit.
        """
        system_name = raw.get("name", "unknown")
        flogger.trace(
            f"create_or_update() called: model={self._model.__name__}, system_name={system_name}, "
            f"raw_keys={list(raw.keys())}"
        )

        try:
            # look up existing
            flogger.trace(f"Querying for existing system by name: {system_name}")
            result = await db.execute(
                select(self._model).filter_by(name=raw["name"])
            )
            obj = result.scalars().one_or_none()

            # no special JSON-to-attr mapping here
            def to_attr(k: str) -> str:
                return k.lower()

            if obj:
                obj_id = getattr(obj, "id", None)
                flogger.debug(
                    f"Found existing system: id={obj_id}, name={system_name}. Updating fields."
                )
                for k, v in raw.items():
                    setattr(obj, to_attr(k), v)
                flogger.trace(f"Updated system attributes for id={obj_id}")
            else:
                flogger.debug(f"No existing system found for name={system_name}. Creating new.")
                attrs = {to_attr(k): v for k, v in raw.items()}
                obj = System(**attrs)
                db.add(obj)
                flogger.trace(f"Added new system to session: name={system_name}")

            await db.commit()
            await db.refresh(obj)
            obj_id = getattr(obj, "id", None)
            flogger.debug(
                f"Successfully created or updated system: id={obj_id}, name={system_name}"
            )
            return obj
        except Exception as e:
            flogger.error(
                f"Error in create_or_update for system name={system_name}: {e}"
            )
            await db.rollback()
            raise
