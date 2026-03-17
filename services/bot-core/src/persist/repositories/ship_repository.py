from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.ship import Ship
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-ship-repository")

class ShipRepository(GenericRepository[Ship]):
    def __init__(self):
        super().__init__(Ship)

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Ship:
        """
        raw is your parsed JSON.  We first look up by name,
        then insert or patch fields and commit.
        """
        ship_name = raw.get("name", "UNKNOWN")
        flogger.trace(f"create_or_update() entry: ship_name={ship_name}, raw_keys={list(raw.keys())}")

        try:
            # look up existing
            flogger.trace(f"Querying existing ship by name: {ship_name}")
            result = await db.execute(
                select(self._model).filter_by(name=raw["name"])
            )
            obj = result.scalars().one_or_none()

            if obj:
                flogger.debug(
                    f"Updating existing ship: id={obj.id}, name={ship_name}, "
                    f"fields={list(raw.keys())}"
                )
            else:
                flogger.debug(f"Creating new ship: name={ship_name}")

            # Map JSON keys → model attributes
            mapping = {
                "builtIn":           "built_in",
                "compatibleSkins":   "compatible_skins",
                "shopSpawnRate":     "shop_spawn_rate",
                "textureRegions":    "texture_regions",
                "maxModules":        "max_modules",
                "maxPrimaries":      "max_primaries",
                "maxSecondaries":    "max_secondaries",
                "maxTurrets":        "max_turrets",
                "saveDue":           "save_due",
                "normSpec":          "norm_spec",
                # all others map 1:1 by lower-snake
            }

            def to_attr(k: str) -> str:
                return mapping.get(k, k.lower())

            if obj:
                for k, v in raw.items():
                    setattr(obj, to_attr(k), v)
                flogger.trace(f"Updated ship attributes for id={obj.id}")
            else:
                attrs = { to_attr(k): v for k, v in raw.items() }
                obj = Ship(**attrs)
                db.add(obj)
                flogger.trace(f"Added new Ship object to session: name={ship_name}")

            await db.commit()
            await db.refresh(obj)
            flogger.debug(
                f"Ship successfully persisted: id={obj.id}, name={ship_name}"
            )
            flogger.trace(f"create_or_update() exit: ship_id={obj.id}")
            return obj

        except Exception as e:
            flogger.error(
                f"Error in create_or_update for ship '{ship_name}': {type(e).__name__}: {e}"
            )
            await db.rollback()
            raise
