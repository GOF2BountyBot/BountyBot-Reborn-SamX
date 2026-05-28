from typing import Any

from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.commodity import Commodity
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-commodity-repository")

# Top-level provenance keys copied verbatim from the raw JSON into extra_atts.
_PROVENANCE_KEYS = ("in_game_description", "mechanics_text", "wiki_categories")


class CommodityRepository(GenericRepository[Commodity]):
    def __init__(self):
        flogger.trace("Initializing CommodityRepository")
        super().__init__(Commodity)
        flogger.trace("CommodityRepository initialized successfully")

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Commodity:
        """
        Pure, deterministic mapper from a commodity seed dict to a Commodity row.

        Commodity seed files use underscore-prefixed top-level keys (``_name``,
        ``_url``, ``_subcategory``). ``stats`` carries the numeric/price fields:
        ``value`` and ``tech_level`` become columns; every other stats key flows
        into ``extra_atts`` (where the model's read-only properties read it from),
        alongside the copied provenance keys.
        """
        commodity_name = raw.get("_name", "unknown")
        flogger.trace(f"Creating or updating commodity: {commodity_name}")
        flogger.debug(
            f"Commodity data: name={commodity_name}, subcategory={raw.get('_subcategory')}"
        )

        try:
            # validate required keys upfront
            if "_name" not in raw:
                raise ValueError("Missing required key '_name' in data for commodity")

            stats = raw.get("stats") or {}

            item_fields = {
                "name": raw["_name"],
                "type": "commodity",
                "wiki": raw.get("_url"),
                "aliases": raw.get("aliases", []),
                "built_in": raw.get("built_in", False),
                "emoji": raw.get("emoji"),
                "icon": raw.get("icon"),
                "value": stats.get("value"),
            }
            commodity_fields = {
                "tech_level": stats.get("tech_level"),
                "subcategory": raw["_subcategory"],
            }
            extra = {k: v for k, v in stats.items() if k not in ("value", "tech_level")}
            for key in _PROVENANCE_KEYS:
                if key in raw:
                    extra[key] = raw[key]

            obj = await self.get_by_name(db, item_fields["name"])
            if obj:
                flogger.debug(f"Updating existing commodity: id={obj.id}, name={commodity_name}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
                for k, v in commodity_fields.items():
                    setattr(obj, k, v)
                obj.extra_atts = extra
                action = "updated"
            else:
                flogger.debug(f"Creating new commodity: name={commodity_name}")
                obj = Commodity(
                    **item_fields,
                    **commodity_fields,
                    extra_atts=extra,
                )
                db.add(obj)
                action = "created"

            await db.commit()
            await db.refresh(obj)
            flogger.debug(f"Commodity {action} successfully: id={obj.id}, name={obj.name}")
            flogger.trace(
                f"Commodity {action}: id={obj.id}, name={obj.name}, "
                f"tech_level={obj.tech_level}, subcategory={obj.subcategory}"
            )
            return obj
        except Exception as e:
            flogger.error(f"Error creating or updating commodity {commodity_name}: {e}")
            await db.rollback()
            raise
