from typing import Any
from sqlalchemy.orm import Session

from persist.models.turret_weapon import TurretWeapon
from persist.repositories.generic_repository import GenericRepository

import shared.logging as logging

logger = logging.get_logger("bot-turret-weapon-repository")

class TurretWeaponRepository(GenericRepository[TurretWeapon]):
    def __init__(self):
        super().__init__(TurretWeapon)

+   # enable lookup by name
+   def get_by_name(self, db: Session, name: str) -> TurretWeapon | None:
+       return db.query(TurretWeapon).filter_by(name=name).one_or_none()
+
+   def create_or_update(
+       self,
+       db: Session,
+       raw: dict[str, Any],
+   ) -> TurretWeapon:
+       """
+       raw: the dict loaded from JSON file
+       - maps known fields into model attrs
+       - stashes the rest into extra_atts (JSON column)
+       """
+       logger.trace(f"Creating or updating turret weapon from {raw}")
+
+       # common item fields
+       item_fields = {
+           "name":       raw["name"],
+           "aliases":    raw.get("aliases", []),
+           "built_in":   raw.get("builtIn", False),
+           "emoji":      raw.get("emoji"),
+           "icon":       raw.get("icon"),
+           "value":      raw.get("value"),
+           "wiki":       raw.get("wiki"),
+           "type":       raw.get("type"),
+       }
+       # weapon-level fields
+       weapon_fields = {
+           "tech_level": raw.get("techLevel"),
+       }
+       # turret-weapon specific fields
+       turret_fields = {
+           "dps":         raw["dps"],
+           "automatic":  raw.get("automatic")
+       }
+
+       # everything else → JSON blob
+       extra = {
+           k: v
+           for k, v in raw.items()
+           if k not in (*item_fields.keys(), *weapon_fields.keys(), *turret_fields.keys())
+       }
+
+       obj = self.get_by_name(db, item_fields["name"])
+       if obj:
+           # update existing
+           for k, v in item_fields.items():
+               setattr(obj, k, v)
+           for k, v in weapon_fields.items():
+               setattr(obj, k, v)
+           for k, v in turret_fields.items():
+               setattr(obj, k, v)
+           obj.extra_atts = extra
+       else:
+           # create new
+           obj = TurretWeapon(
+               **item_fields,
+               **weapon_fields,
+               **turret_fields,
+               extra_atts=extra,
+           )
+           db.add(obj)
+
+       return obj