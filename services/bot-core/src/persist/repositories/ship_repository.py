from sqlalchemy.orm import Session
from persist.models.ship import Ship
from persist.repositories.generic_repository import GenericRepository

class ShipRepository(GenericRepository[Ship]):
    def __init__(self):
        super().__init__(Ship)

    def create_or_update(self, db: Session, raw: dict) -> Ship:
        """
        raw is your parsed JSON.  We first look up by name,
        then insert or patch fields and commit.
        """
        session = self._unwrap(db)
        obj = session.query(Ship).filter_by(name=raw["name"]).one_or_none()

        # Map JSON keys -> model attributes
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
            # all others map 1:1 by lower‐snake
        }

        def to_attr(k):
            return mapping.get(k, k.lower())

        if obj:
            for k, v in raw.items():
                setattr(obj, to_attr(k), v)
        else:
            attrs = { to_attr(k): v for k, v in raw.items() }
            obj = Ship(**attrs)
            session.add(obj)

        session.commit()
        session.refresh(obj)
        return obj