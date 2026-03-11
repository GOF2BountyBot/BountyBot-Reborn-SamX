from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.module import Module
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-module-repository")

class ModuleRepository(GenericRepository[Module]):
    def __init__(self):
        super().__init__(Module)

    async def get_by_name(self, db: AsyncSession, name: str) -> Module | None:
        result = await db.execute(
            select(self._model).filter_by(name=name)
        )
        return result.scalars().one_or_none()

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Module:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into Module.extra_atts (JSON column)
        """
        flogger.trace(f"Creating or updating module from {raw}")

        item_fields = {
            "name":     raw["name"],
            "aliases":  raw.get("aliases", []),
            "built_in": raw.get("builtIn", False),
            "emoji":    raw.get("emoji"),
            "icon":     raw.get("icon"),
            "value":    raw.get("value"),
            "wiki":     raw.get("wiki"),
            "type":     raw.get("type"),
        }
        module_fields = {
            "tech_level": raw.get("techLevel"),
            "max_equipped": raw.get("maxEquipped"),
        }
        extra = {
             k: v
             for k, v in raw.items()
             if k not in (*item_fields, "techLevel", "maxEquipped")
        }

        obj = await self.get_by_name(db, item_fields["name"])
        if obj:
            for k, v in item_fields.items():
                setattr(obj, k, v)
            for k, v in module_fields.items():
                setattr(obj, k, v)
            obj.extra_atts = extra
        else:
            obj = Module(
                **item_fields,
                **module_fields,
                extra_atts=extra,
            )
            db.add(obj)

        await db.commit()
        await db.refresh(obj)
        return obj
