from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.module import Module
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-module-repository")


class ModuleRepository(GenericRepository[Module]):
    def __init__(self):
        flogger.trace("Initializing ModuleRepository")
        super().__init__(Module)
        flogger.trace("ModuleRepository initialized successfully")

    async def get_by_name(self, db: AsyncSession, name: str) -> Module | None:
        flogger.trace(f"Querying module by name: {name}")
        try:
            result = await db.execute(select(self._model).filter_by(name=name))
            module = result.scalars().one_or_none()
            if module:
                flogger.trace(f"Found module by name {name}: id={module.id}")
            else:
                flogger.trace(f"No module found with name: {name}")
            return module
        except Exception as e:
            flogger.error(f"Error querying module by name {name}: {e}")
            raise

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
        module_name = raw.get("name", "unknown")
        flogger.trace(f"Creating or updating module: {module_name}")
        flogger.debug(
            f"Module data: name={module_name}, tech_level={raw.get('techLevel')}, max_equipped={raw.get('maxEquipped')}"
        )

        try:
            item_fields = {
                "name": raw["name"],
                "aliases": raw.get("aliases", []),
                "built_in": raw.get("builtIn", False),
                "emoji": raw.get("emoji"),
                "icon": raw.get("icon"),
                "value": raw.get("value"),
                "wiki": raw.get("wiki"),
                "type": raw.get("type"),
            }
            module_fields = {
                "tech_level": raw.get("techLevel"),
                "max_equipped": raw.get("maxEquipped"),
            }
            extra = {k: v for k, v in raw.items() if k not in (*item_fields, "techLevel", "maxEquipped")}

            obj = await self.get_by_name(db, item_fields["name"])
            if obj:
                flogger.debug(f"Updating existing module: id={obj.id}, name={module_name}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
                for k, v in module_fields.items():
                    setattr(obj, k, v)
                obj.extra_atts = extra
                action = "updated"
            else:
                flogger.debug(f"Creating new module: name={module_name}")
                obj = Module(
                    **item_fields,
                    **module_fields,
                    extra_atts=extra,
                )
                db.add(obj)
                action = "created"

            await db.commit()
            await db.refresh(obj)
            flogger.debug(f"Module {action} successfully: id={obj.id}, name={obj.name}")
            flogger.trace(
                f"Module {action}: id={obj.id}, name={obj.name}, "
                f"tech_level={obj.tech_level}, max_equipped={obj.max_equipped}"
            )
            return obj
        except Exception as e:
            flogger.error(f"Error creating or updating module {module_name}: {e}")
            await db.rollback()
            raise
