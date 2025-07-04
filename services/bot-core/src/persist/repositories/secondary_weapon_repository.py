from typing import Any
from sqlalchemy.orm import Session

from persist.models.secondary_weapon import SecondaryWeapon
from persist.repositories.generic_repository import GenericRepository

import shared.logging as logging

logger = logging.get_logger("bot-secondary-weapon-repository")

class SecondaryWeaponRepository(GenericRepository[SecondaryWeapon]):
    def __init__(self):
        super().__init__(SecondaryWeapon)

    # enable lookup by name
    def get_by_name(self, db: Session, name: str) -> SecondaryWeapon | None:
        return db.query(SecondaryWeapon).filter_by(name=name).one_or_none()

    def create_or_update(
        self,
        db: Session,
        raw: dict[str, Any],
    ) -> SecondaryWeapon:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into extra_atts (JSON column)
        """
        logger.trace(f"Creating or updating secondary weapon from {raw}")

        # common item fields:
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
        # weapon‐level fields:
        weapon_fields = {
            "tech_level": raw.get("techLevel"),
        }
        # secondary‐weapon specific:
        secondary_fields = {
            "damage":        raw["damage"],
            "loading_speed": raw.get("loadingSpeed"),
        }

        # anything else → JSON blob
        extra = {
            k: v
            for k, v in raw.items()
            if k
            not in (
                *item_fields.keys(),
                *weapon_fields.keys(),
                *secondary_fields.keys(),
            )
        }

        obj = self.get_by_name(db, item_fields["name"])
        if obj:
            # update existing
            for k, v in item_fields.items():
                setattr(obj, k, v)
            for k, v in weapon_fields.items():
                setattr(obj, k, v)
            for k, v in secondary_fields.items():
                setattr(obj, k, v)
            obj.extra_atts = extra
        else:
            # create new
            obj = SecondaryWeapon(
                **item_fields,
                **weapon_fields,
                **secondary_fields,
                extra_atts=extra,
            )
            db.add(obj)

        return obj