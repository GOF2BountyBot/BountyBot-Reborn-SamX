import random
from typing import Any

from shared import bblogger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.item import Item
from persist.models.module import Module
from persist.models.primary_weapon import PrimaryWeapon
from persist.models.secondary_weapon import SecondaryWeapon
from persist.models.ship import Ship
from persist.models.turret_weapon import TurretWeapon
from persist.repositories.generic_repository import GenericRepository

flogger = bblogger.get_logger("bot-item-repository")


class ItemRepository(GenericRepository[Item]):
    """Unified facade for looking up game items across all item types."""

    # Map item_type strings to model classes
    _TYPE_MAP: dict[str, type] = {
        "ship": Ship,
        "primary_weapon": PrimaryWeapon,
        "secondary_weapon": SecondaryWeapon,
        "turret_weapon": TurretWeapon,
        "module": Module,
    }

    # Models that have a tech_level column (Ship does NOT)
    _TECH_LEVEL_MODELS: list[type] = [
        PrimaryWeapon,
        SecondaryWeapon,
        TurretWeapon,
        Module,
    ]

    def __init__(self) -> None:
        super().__init__(Item)

    def _get_model(self, item_type: str) -> type:
        model = self._TYPE_MAP.get(item_type)
        if model is None:
            raise ValueError(f"Unknown item_type: {item_type!r}")
        return model

    def _get_all_models(self) -> list[type]:
        return list(self._TYPE_MAP.values())

    # ------------------------------------------------------------------
    # get_by_name_any_type
    # ------------------------------------------------------------------

    async def get_by_name_any_type(self, db: AsyncSession, name: str) -> Any | None:
        """Look up an item by name across all item types, returning the base Item record.

        This is the lightweight version of :meth:`get_by_name` that only queries
        the base ``item`` table.  It is useful when you only need the discriminator
        ``type`` column (e.g. ``"ArmourModule"``, ``"PrimaryWeapon"``) to determine
        what kind of item a given name belongs to.

        Returns the :class:`Item` row (with its ``type`` attribute populated) or
        ``None`` if no matching item exists.
        """
        flogger.trace(f"get_by_name_any_type entry: name={name!r}")
        try:
            result = await db.execute(select(Item).filter_by(name=name))
            obj = result.scalars().one_or_none()
            if obj is not None:
                flogger.trace(f"get_by_name_any_type exit: found item id={obj.id}, type={obj.type!r}")
            else:
                flogger.trace(f"get_by_name_any_type exit: no item found for name={name!r}")
            return obj
        except Exception as e:
            flogger.error(f"Error in get_by_name_any_type with name={name!r}: {e}")
            raise

    # ------------------------------------------------------------------
    # get_by_name
    # ------------------------------------------------------------------

    async def get_by_name(
        self,
        db: AsyncSession,
        name: str,
        item_type: str | None = None,
    ) -> Any | None:
        """Look up an item by name.

        If *item_type* is given, query only that model's table.
        Otherwise, search across all models and return the first match.
        """
        flogger.trace(f"get_by_name entry: name={name!r}, item_type={item_type!r}")
        try:
            if item_type is not None:
                model = self._get_model(item_type)
                result = await db.execute(select(model).filter_by(name=name))
                obj = result.scalars().one_or_none()
                if obj is not None:
                    flogger.trace(f"get_by_name exit: found item id={obj.id} (type={item_type})")
                else:
                    flogger.trace(f"get_by_name exit: no item found for name={name!r} (type={item_type})")
                return obj

            # Search all models
            for model in self._get_all_models():
                result = await db.execute(select(model).filter_by(name=name))
                obj = result.scalars().one_or_none()
                if obj is not None:
                    flogger.trace(f"get_by_name exit: found item id={obj.id} across all models")
                    return obj

            flogger.trace(f"get_by_name exit: no item found for name={name!r} across all models")
            return None
        except Exception as e:
            flogger.error(f"Error in get_by_name with name={name!r}, item_type={item_type!r}: {e}")
            raise

    # ------------------------------------------------------------------
    # get_all_by_tech_level
    # ------------------------------------------------------------------

    async def get_all_by_tech_level(
        self,
        db: AsyncSession,
        tech_level: int,
        item_type: str | None = None,
    ) -> list[Any]:
        """Return all items at the given tech level.

        Ships do not have a tech_level column and are skipped when
        *item_type* is None.  If *item_type* is ``"ship"`` this returns [].
        """
        flogger.trace(f"get_all_by_tech_level entry: tech_level={tech_level}, item_type={item_type!r}")
        try:
            if item_type is not None:
                model = self._get_model(item_type)
                if model is Ship:
                    flogger.trace("Ship model has no tech_level — returning []")
                    return []
                result = await db.execute(select(model).filter_by(tech_level=tech_level))
                items = list(result.scalars().all())
                flogger.trace(f"get_all_by_tech_level exit: found {len(items)} items (type={item_type})")
                return items

            # Aggregate across all models that have tech_level
            items: list[Any] = []
            for model in self._TECH_LEVEL_MODELS:
                result = await db.execute(select(model).filter_by(tech_level=tech_level))
                items.extend(result.scalars().all())

            flogger.trace(f"get_all_by_tech_level exit: found {len(items)} items across all models")
            return items
        except Exception as e:
            flogger.error(f"Error in get_all_by_tech_level with tech_level={tech_level}, item_type={item_type!r}: {e}")
            raise

    # ------------------------------------------------------------------
    # get_all
    # ------------------------------------------------------------------

    async def get_all(
        self,
        db: AsyncSession,
        item_type: str,
    ) -> list[Any]:
        """Return every item of *item_type*, across all tech levels.

        Unlike :meth:`get_all_by_tech_level`, this applies no tech-level
        filter — it is the "give me the whole catalog for this type" query,
        used by criminal loadout generation to gather all variants of a
        module type / weapon class before applying the nearest-TL / TL-band
        selection rules in-memory.

        Ships have no tech_level column but ARE a valid type here; passing
        ``"ship"`` returns every ship.
        """
        flogger.trace(f"get_all entry: item_type={item_type!r}")
        try:
            model = self._get_model(item_type)
            result = await db.execute(select(model))
            items = list(result.scalars().all())
            flogger.trace(f"get_all exit: found {len(items)} items (type={item_type})")
            return items
        except Exception as e:
            flogger.error(f"Error in get_all with item_type={item_type!r}: {e}")
            raise

    # ------------------------------------------------------------------
    # get_random_by_tech_level
    # ------------------------------------------------------------------

    async def get_random_by_tech_level(
        self,
        db: AsyncSession,
        tech_level: int,
        item_type: str | None = None,
    ) -> Any | None:
        """Return a random item at the given tech level.

        When *item_type* is ``"ship"``, the selection is weighted by each
        ship's ``shop_spawn_rate``.  For all other types a uniform random
        choice is used.  Returns None if no items exist at that tech level.
        """
        flogger.trace(f"get_random_by_tech_level entry: tech_level={tech_level}, item_type={item_type!r}")
        try:
            # Special case: ships are selected by shop_spawn_rate weight but
            # have no tech_level, so we fetch all ships and apply weighting.
            if item_type == "ship":
                result = await db.execute(select(Ship))
                ships = list(result.scalars().all())
                if not ships:
                    flogger.trace("get_random_by_tech_level exit: no ships found")
                    return None
                weights = [(s.shop_spawn_rate if s.shop_spawn_rate is not None else 0.0) for s in ships]
                # If all weights are 0, fall back to uniform selection
                if all(w == 0.0 for w in weights):
                    chosen = random.choice(ships)
                    flogger.trace(f"get_random_by_tech_level exit: selected ship id={chosen.id} (uniform)")
                    return chosen
                chosen = random.choices(ships, weights=weights, k=1)
                flogger.trace(f"get_random_by_tech_level exit: selected ship id={chosen[0].id} (weighted)")
                return chosen[0]

            items = await self.get_all_by_tech_level(db, tech_level, item_type)
            if not items:
                flogger.trace(f"get_random_by_tech_level exit: no items found (tech_level={tech_level})")
                return None
            chosen = random.choice(items)
            flogger.trace(f"get_random_by_tech_level exit: selected item id={chosen.id}")
            return chosen
        except Exception as e:
            flogger.error(
                f"Error in get_random_by_tech_level with tech_level={tech_level}, item_type={item_type!r}: {e}"
            )
            raise

    # ------------------------------------------------------------------
    # get_count
    # ------------------------------------------------------------------

    async def get_count(self, db: AsyncSession) -> int:
        """Return the total item count across all item types."""
        flogger.trace("get_count entry")
        try:
            total = 0
            for model in self._get_all_models():
                result = await db.execute(
                    select(func.count()).select_from(model)  # pylint: disable=not-callable
                )
                total += result.scalar() or 0
            flogger.trace(f"get_count exit: total={total}")
            return total
        except Exception as e:
            flogger.error(f"Error in get_count: {e}")
            raise

    # ------------------------------------------------------------------
    # create_or_update
    # ------------------------------------------------------------------

    async def create_or_update(
        self,
        db: AsyncSession,
        raw: dict[str, Any],
    ) -> Item:
        """Create or update an Item record from a raw dict.

        Looks up by name, updates if exists, creates if not.
        Maps the common Item fields; additional keys are ignored.
        """
        try:
            # validate required keys upfront
            if "name" not in raw:
                raise ValueError("Missing required key 'name' in data for item")

            item_name = raw.get("name", "unknown")
            flogger.trace(f"create_or_update entry: creating or updating item name={item_name!r}")
            flogger.debug(f"Item data: name={item_name!r}, type={raw.get('type')}, value={raw.get('value')}")

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

            result = await db.execute(select(self._model).filter_by(name=item_fields["name"]))
            obj = result.scalars().one_or_none()

            if obj:
                flogger.debug(f"Updating existing item id={obj.id}, name={item_name!r}")
                for k, v in item_fields.items():
                    setattr(obj, k, v)
            else:
                obj = Item(**item_fields)
                db.add(obj)
                flogger.debug(f"Created new item name={item_name!r}")

            await db.commit()
            await db.refresh(obj)
            flogger.trace(f"create_or_update exit: item id={obj.id}, name={item_name!r}")
            return obj
        except Exception as e:
            flogger.error(f"Error in create_or_update with item name={raw.get('name', 'unknown')!r}: {e}")
            await db.rollback()
            raise
