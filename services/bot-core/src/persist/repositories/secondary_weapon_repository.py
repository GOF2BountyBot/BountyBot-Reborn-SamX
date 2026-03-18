from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.secondary_weapon import SecondaryWeapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-secondary-weapon-repository")


class SecondaryWeaponRepository(GenericRepository[SecondaryWeapon]):
    def __init__(self):
        super().__init__(SecondaryWeapon)

    async def get_by_name(self, db: AsyncSession, name: str) -> SecondaryWeapon | None:
        flogger.trace(f"Querying SecondaryWeapon by name: {name}")
        try:
            result = await db.execute(select(self._model).filter_by(name=name))
            weapon = result.scalars().one_or_none()
            if weapon:
                flogger.trace(f"Found SecondaryWeapon: id={weapon.id}, name={weapon.name}")
            else:
                flogger.trace(f"No SecondaryWeapon found with name: {name}")
            return weapon
        except Exception as e:
            flogger.error(f"Error querying SecondaryWeapon by name '{name}': {e}")
            raise

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> SecondaryWeapon:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into extra_atts (JSON column)
        """
        flogger.debug(f"Starting create_or_update for secondary weapon: {raw.get('name')}")

        try:
            # common item fields
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
            # weapon-level fields
            weapon_fields = {
                "tech_level": raw.get("techLevel"),
            }
            # secondary-weapon specific fields
            secondary_fields = {
                "damage": raw["damage"],
                "loading_speed": raw.get("loadingSpeed"),
            }

            # anything else → JSON blob
            extra = {k: v for k, v in raw.items() if k not in (*item_fields, *weapon_fields, *secondary_fields)}
            flogger.trace(f"Parsed fields for {item_fields['name']}: extra_keys={list(extra.keys())}")

            obj = await self.get_by_name(db, item_fields["name"])
            if obj:
                # update existing
                flogger.debug(f"Updating existing SecondaryWeapon: id={obj.id}, name={item_fields['name']}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
                for k, v in weapon_fields.items():
                    setattr(obj, k, v)
                for k, v in secondary_fields.items():
                    setattr(obj, k, v)
                obj.extra_atts = extra
            else:
                # create new
                flogger.debug(
                    f"Creating new SecondaryWeapon: name={item_fields['name']}, "
                    f"damage={secondary_fields['damage']}, "
                    f"loading_speed={secondary_fields['loading_speed']}"
                )
                obj = SecondaryWeapon(
                    **item_fields,
                    **weapon_fields,
                    **secondary_fields,
                    extra_atts=extra,
                )
                db.add(obj)

            await db.commit()
            await db.refresh(obj)
            flogger.debug(f"Successfully saved SecondaryWeapon: id={obj.id}, name={obj.name}")
            return obj
        except Exception as e:
            flogger.error(f"Error in create_or_update for secondary weapon '{raw.get('name', 'UNKNOWN')}': {e}")
            await db.rollback()
            raise
