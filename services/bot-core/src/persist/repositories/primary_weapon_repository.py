from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import shared.bblogger as bblogger

from persist.models.primary_weapon import PrimaryWeapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-primary-weapon-repository")

class PrimaryWeaponRepository(GenericRepository[PrimaryWeapon]):
    def __init__(self):
        super().__init__(PrimaryWeapon)

    async def get_by_name(self, db: AsyncSession, name: str) -> PrimaryWeapon | None:
        result = await db.execute(
            select(self._model).filter_by(name=name)
        )
        return result.scalars().one_or_none()

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
        flogger.trace(f"Creating or updating primary weapon from {raw}")

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
        # weapon-level fields
        weapon_fields = {
            "tech_level": raw.get("techLevel"),
        }
        # primary-weapon specific fields
        primary_fields = {
            "dps": raw["dps"],
        }
        # anything else → JSON blob
        extra = {
            k: v
            for k, v in raw.items()
            if k not in (*item_fields.keys(), *weapon_fields.keys(), *primary_fields.keys())
        }

        obj = await self.get_by_name(db, item_fields["name"])
        if obj:
            # update existing
            for k, v in item_fields.items():
                setattr(obj, k, v)
            for k, v in weapon_fields.items():
                setattr(obj, k, v)
            for k, v in primary_fields.items():
                setattr(obj, k, v)
            obj.extra_atts = extra
        else:
            # create new
            obj = PrimaryWeapon(
                **item_fields,
                **weapon_fields,
                **primary_fields,
                extra_atts=extra,
            )
            db.add(obj)

        await db.commit()
        await db.refresh(obj)
        return obj