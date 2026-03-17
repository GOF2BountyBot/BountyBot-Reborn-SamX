from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.primary_weapon import PrimaryWeapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-primary-weapon-repository")

class PrimaryWeaponRepository(GenericRepository[PrimaryWeapon]):
    def __init__(self):
        flogger.trace("Initializing PrimaryWeaponRepository")
        super().__init__(PrimaryWeapon)
        flogger.trace("PrimaryWeaponRepository initialized")

    async def get_by_name(self, db: AsyncSession, name: str) -> PrimaryWeapon | None:
        flogger.trace(f"get_by_name() called with name={name}")
        try:
            result = await db.execute(
                select(self._model).filter_by(name=name)
            )
            weapon = result.scalars().one_or_none()
            if weapon:
                flogger.trace(f"get_by_name() found weapon id={weapon.id}, name={weapon.name}")
            else:
                flogger.trace(f"get_by_name() no weapon found for name={name}")
            return weapon
        except Exception as e:
            flogger.error(f"Error in get_by_name() for name={name}: {e}")
            raise

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> PrimaryWeapon:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into extra_atts (JSON column)
        """
        weapon_name = raw.get("name", "UNKNOWN")
        flogger.trace(f"create_or_update() entry: weapon_name={weapon_name}")

        try:
            # common item fields
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
            flogger.trace(
                f"create_or_update() parsed item_fields for {weapon_name}: "
                f"aliases={len(item_fields['aliases'])}, value={item_fields['value']}"
            )

            # weapon-level fields
            weapon_fields = {
                "tech_level": raw.get("techLevel"),
            }
            flogger.trace(
                f"create_or_update() parsed weapon_fields for {weapon_name}: tech_level={weapon_fields['tech_level']}"
            )

            # primary-weapon specific fields
            primary_fields = {
                "dps": raw["dps"],
            }
            flogger.trace(f"create_or_update() parsed primary_fields for {weapon_name}: dps={primary_fields['dps']}")

            # anything else → JSON blob
            extra = {
                 k: v
                 for k, v in raw.items()
                 if k not in (*item_fields, *weapon_fields, *primary_fields)
            }
            flogger.trace(f"create_or_update() parsed extra_atts for {weapon_name}: {len(extra)} extra fields")

            obj = await self.get_by_name(db, item_fields["name"])
            if obj:
                # update existing
                flogger.debug(f"create_or_update() updating existing primary weapon id={obj.id}, name={weapon_name}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
                for k, v in weapon_fields.items():
                    setattr(obj, k, v)
                for k, v in primary_fields.items():
                    setattr(obj, k, v)
                obj.extra_atts = extra
                flogger.trace(f"create_or_update() all fields updated for id={obj.id}")
            else:
                # create new
                flogger.debug(f"create_or_update() creating new primary weapon: {weapon_name}")
                obj = PrimaryWeapon(
                    **item_fields,
                    **weapon_fields,
                    **primary_fields,
                    extra_atts=extra,
                )
                db.add(obj)
                flogger.trace(f"create_or_update() added new object to session for {weapon_name}")

            await db.commit()
            flogger.trace(f"create_or_update() commit successful for weapon_name={weapon_name}")

            await db.refresh(obj)
            flogger.debug(
                f"create_or_update() success: weapon id={obj.id}, name={obj.name}, dps={obj.dps}, "
                f"tech_level={obj.tech_level}"
            )
            flogger.trace(f"create_or_update() exit: weapon_name={weapon_name}, id={obj.id}")
            return obj
        except Exception as e:
            flogger.error(
                f"create_or_update() failed for weapon_name={weapon_name}: {type(e).__name__}: {e}"
            )
            await db.rollback()
            flogger.trace(f"create_or_update() rollback executed for weapon_name={weapon_name}")
            raise
