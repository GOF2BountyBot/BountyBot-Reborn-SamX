from typing import Any

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.ship import Ship
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-ship-repository")

# JSON-key → model-attribute mapping for known camelCase fields.
# All other keys lowercase to their model attribute name; unknown keys
# (not present as a column on Ship) are routed into ``extra_atts``.
_JSON_TO_ATTR: dict[str, str] = {
    "builtIn": "built_in",
    "compatibleSkins": "compatible_skins",
    "shopSpawnRate": "shop_spawn_rate",
    "textureRegions": "texture_regions",
    "maxModules": "max_modules",
    "maxPrimaries": "max_primaries",
    "maxSecondaries": "max_secondaries",
    "maxTurrets": "max_turrets",
    "saveDue": "save_due",
    "normSpec": "norm_spec",
    "builtinModules": "builtin_modules",
}


def _to_attr(json_key: str) -> str:
    """Map a JSON key to its model-attribute name."""
    return _JSON_TO_ATTR.get(json_key, json_key.lower())


def _ship_column_names() -> set[str]:
    """Set of column names on the Ship table (cached on first call)."""
    # Lazy-cache on the function object — avoids module-load-time SQLAlchemy
    # introspection and keeps the test patch surface unchanged.
    cache: set[str] | None = getattr(_ship_column_names, "_cache", None)
    if cache is None:
        cache = {col.name for col in Ship.__table__.columns}
        _ship_column_names._cache = cache  # type: ignore[attr-defined]
    return cache


class ShipRepository(GenericRepository[Ship]):
    def __init__(self):
        super().__init__(Ship)

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Ship:
        """Idempotent UPSERT from a parsed seed JSON.

        Known keys (those that map to an actual column on the Ship table after
        camelCase→snake_case translation) are applied as ORM attributes. Any
        unrecognised key is routed into ``extra_atts`` so the loader is
        tolerant of new wiki-sourced fields (mechanics prose, DLC tag,
        Android price, sentinel markers, etc.) without needing a code change
        per field.

        Pre-existing behaviour preserved:
        - lookup-by-name, single SELECT
        - error path rolls back and re-raises
        - missing ``name`` raises ValueError

        Behaviour added by PR-2 L1:
        - unknown JSON keys land in ``extra_atts`` (was: setattr on a
          non-existent attribute, which crashed on insert)
        - if the JSON itself carries an ``extra_atts`` blob, its contents
          win on key conflicts with discovered unknowns (explicit > implicit)
        """
        ship_name = raw.get("name", "UNKNOWN")
        flogger.trace(f"create_or_update() entry: ship_name={ship_name}, raw_keys={list(raw.keys())}")

        try:
            if "name" not in raw:
                raise ValueError("Missing required key 'name' in data for ship")

            flogger.trace(f"Querying existing ship by name: {ship_name}")
            result = await db.execute(select(self._model).filter_by(name=raw["name"]))
            obj = result.scalars().one_or_none()

            if obj:
                flogger.debug(f"Updating existing ship: id={obj.id}, name={ship_name}, fields={list(raw.keys())}")
            else:
                flogger.debug(f"Creating new ship: name={ship_name}")

            # Partition raw into known-column kwargs vs discovered extras.
            valid_columns = _ship_column_names()
            known_kwargs: dict[str, Any] = {}
            discovered_extras: dict[str, Any] = {}
            explicit_extra_atts: dict[str, Any] = {}

            for json_key, value in raw.items():
                if json_key == "extra_atts":
                    # Explicit extra_atts blob from PR-3 enrichment; handle separately
                    if isinstance(value, dict):
                        explicit_extra_atts = value
                    continue
                attr = _to_attr(json_key)
                if attr in valid_columns:
                    known_kwargs[attr] = value
                else:
                    # Preserve original JSON key in the JSON blob so downstream
                    # readers can recover the source-of-truth name.
                    discovered_extras[json_key] = value

            # Merge: explicit (PR-3) wins on conflict, discovered fills the rest.
            merged_extras: dict[str, Any] = {**discovered_extras, **explicit_extra_atts}
            if merged_extras:
                known_kwargs["extra_atts"] = merged_extras

            if discovered_extras:
                flogger.trace(
                    f"Ship {ship_name}: routed {len(discovered_extras)} unknown key(s) to extra_atts: "
                    f"{list(discovered_extras.keys())}"
                )

            if obj:
                for k, v in known_kwargs.items():
                    setattr(obj, k, v)
                flogger.trace(f"Updated ship attributes for id={obj.id}")
            else:
                obj = Ship(**known_kwargs)
                db.add(obj)
                flogger.trace(f"Added new Ship object to session: name={ship_name}")

            await db.commit()
            await db.refresh(obj)
            flogger.debug(f"Ship successfully persisted: id={obj.id}, name={ship_name}")
            flogger.trace(f"create_or_update() exit: ship_id={obj.id}")
            return obj

        except Exception as e:
            flogger.error(f"Error in create_or_update for ship '{ship_name}': {type(e).__name__}: {e}")
            await db.rollback()
            raise
