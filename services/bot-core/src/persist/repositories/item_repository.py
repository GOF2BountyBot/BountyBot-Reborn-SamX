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
        if item_type is not None:
            model = self._get_model(item_type)
            result = await db.execute(select(model).filter_by(name=name))
            return result.scalars().one_or_none()

        # Search all models
        for model in self._get_all_models():
            result = await db.execute(select(model).filter_by(name=name))
            obj = result.scalars().one_or_none()
            if obj is not None:
                return obj

        return None

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
        if item_type is not None:
            model = self._get_model(item_type)
            if model is Ship:
                flogger.trace("Ship model has no tech_level — returning []")
                return []
            result = await db.execute(
                select(model).filter_by(tech_level=tech_level)
            )
            return list(result.scalars().all())

        # Aggregate across all models that have tech_level
        items: list[Any] = []
        for model in self._TECH_LEVEL_MODELS:
            result = await db.execute(
                select(model).filter_by(tech_level=tech_level)
            )
            items.extend(result.scalars().all())

        return items

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
        # Special case: ships are selected by shop_spawn_rate weight but
        # have no tech_level, so we fetch all ships and apply weighting.
        if item_type == "ship":
            result = await db.execute(select(Ship))
            ships = list(result.scalars().all())
            if not ships:
                return None
            weights = [
                (s.shop_spawn_rate if s.shop_spawn_rate is not None else 0.0)
                for s in ships
            ]
            # If all weights are 0, fall back to uniform selection
            if all(w == 0.0 for w in weights):
                return random.choice(ships)
            chosen = random.choices(ships, weights=weights, k=1)
            return chosen[0]

        items = await self.get_all_by_tech_level(db, tech_level, item_type)
        if not items:
            return None
        return random.choice(items)

    # ------------------------------------------------------------------
    # get_count
    # ------------------------------------------------------------------

    async def get_count(self, db: AsyncSession) -> int:
        """Return the total item count across all item types."""
        total = 0
        for model in self._get_all_models():
            result = await db.execute(
                select(func.count()).select_from(model)  # pylint: disable=not-callable
            )
            total += result.scalar() or 0
        return total

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
        flogger.trace(f"Creating or updating item from {raw}")

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

        result = await db.execute(
            select(self._model).filter_by(name=item_fields["name"])
        )
        obj = result.scalars().one_or_none()

        if obj:
            for k, v in item_fields.items():
                setattr(obj, k, v)
        else:
            obj = Item(**item_fields)
            db.add(obj)

        await db.commit()
        await db.refresh(obj)
        return obj
