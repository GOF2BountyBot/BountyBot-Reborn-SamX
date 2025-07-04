from typing import Any
from sqlalchemy.orm import Session

from persist.models.primary_weapon import PrimaryWeapon
from persist.repositories.generic_repository import GenericRepository

import shared.logging as logging

logger = logging.get_logger("bot-primary-weapon-repository")

class PrimaryWeaponRepository(GenericRepository[PrimaryWeapon]):
    def __init__(self):
        super().__init__(PrimaryWeapon)

    # enable lookup by name
    def get_by_name(self, db: Session, name: str) -> PrimaryWeapon | None:
        return db.query(PrimaryWeapon).filter_by(name=name).one_or_none()
 
    def create_or_update(
        self,
        db: Session,
        raw: dict[str, Any],
    ) -> PrimaryWeapon:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into extra_atts (JSON column)
        """
        logger.trace(f"Creating or updating primary weapon from {raw}")
 
        # common item fields:
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
        # weapon-level fields:
        weapon_fields = {
            "tech_level": raw.get("techLevel"),
        }
        # primary-weapon specific (adjust keys to your model):
        primary_fields = {
            "dps":    raw["dps"]
            # Maybe more someday...
            # "fire_rate": raw.get("fireRate"),
            # "range":     raw.get("range"),
        }
 
        # everything else → JSON blob
        extra = {
            k: v
            for k, v in raw.items()
            if k not in (*item_fields.keys(), *weapon_fields.keys(), *primary_fields.keys())
        }
 
        obj = self.get_by_name(db, item_fields["name"])
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
 
        return obj