from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.turret_weapon import TurretWeapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-turret-weapon-repository")

class TurretWeaponRepository(GenericRepository[TurretWeapon]):
    def __init__(self):
        super().__init__(TurretWeapon)

    async def get_by_name(self, db: AsyncSession, name: str) -> TurretWeapon | None:
        flogger.trace(f"get_by_name: entering with name={name}")
        try:
            result = await db.execute(
                select(self._model).filter_by(name=name)
            )
            weapon = result.scalars().one_or_none()
            if weapon:
                flogger.trace(f"get_by_name: found turret weapon id={weapon.id}, name={weapon.name}")
            else:
                flogger.trace(f"get_by_name: no turret weapon found for name={name}")
            return weapon
        except Exception as e:
            flogger.error(f"get_by_name: error querying turret weapon by name={name}: {e}")
            raise

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> TurretWeapon:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into extra_atts (JSON column)
        """
        weapon_name = raw.get("name", "UNKNOWN")
        flogger.trace(f"create_or_update: entering with weapon_name={weapon_name}")
        flogger.debug(f"create_or_update: creating or updating turret weapon from dict: {raw}")

        try:
            # common item fields
            item_fields = {
                "name":       raw["name"],
                "aliases":    raw.get("aliases", []),
                "built_in":   raw.get("builtIn", False),
                "emoji":      raw.get("emoji"),
                "icon":       raw.get("icon"),
                "value":      raw.get("value"),
                "wiki":       raw.get("wiki"),
                "type":       raw.get("type"),
            }
            # weapon-level fields
            weapon_fields = {
                "tech_level": raw.get("techLevel"),
            }
            # turret-weapon specific fields
            turret_fields = {
                "dps":        raw["dps"],
                "automatic":  raw.get("automatic"),
            }

            # everything else → JSON blob
            extra = {
                 k: v
                 for k, v in raw.items()
                 if k not in (*item_fields, *weapon_fields, *turret_fields)
            }
            flogger.trace(
                f"create_or_update: parsed fields for {weapon_name}: "
                f"item_fields={item_fields}, weapon_fields={weapon_fields}, "
                f"turret_fields={turret_fields}, extra_atts_keys={list(extra.keys())}"
            )

            obj = await self.get_by_name(db, item_fields["name"])
            if obj:
                # update existing
                flogger.debug(f"create_or_update: updating existing turret weapon id={obj.id}, name={weapon_name}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
                for k, v in weapon_fields.items():
                    setattr(obj, k, v)
                for k, v in turret_fields.items():
                    setattr(obj, k, v)
                obj.extra_atts = extra
                flogger.trace(f"create_or_update: updated turret weapon id={obj.id} attributes")
            else:
                # create new
                flogger.debug(f"create_or_update: creating new turret weapon: name={weapon_name}")
                obj = TurretWeapon(
                    **item_fields,
                    **weapon_fields,
                    **turret_fields,
                    extra_atts=extra,
                )
                db.add(obj)
                flogger.trace(f"create_or_update: added new turret weapon to session: {weapon_name}")

            await db.commit()
            await db.refresh(obj)
            flogger.debug(f"create_or_update: committed turret weapon id={obj.id}, name={obj.name}")
            flogger.trace(f"create_or_update: exiting with turret weapon id={obj.id}, name={obj.name}")
            return obj
        except Exception as e:
            flogger.error(
                f"create_or_update: error creating/updating turret weapon {weapon_name}: {e}"
            )
            await db.rollback()
            raise
