from typing import Any
from sqlalchemy.orm import Session

from persist.models.module import Module
from persist.repositories.generic_repository import GenericRepository

import shared.logging as logging

logger = logging.get_logger("bot-module-repository")

class ModuleRepository(GenericRepository[Module]):
    def __init__(self):
        super().__init__(Module)

    # enable lookup by name
    def get_by_name(self, db: Session, name: str) -> Module | None:
        return db.query(Module).filter_by(name=name).one_or_none()

    def create_or_update(
        self,
        db: Session,
        raw: dict[str, Any],
    ) -> Module:
        """
        raw: the dict loaded from JSON file
        - maps known fields into model attrs
        - stashes the rest into Module.extra_atts (JSON column)
        """
        logger.trace(f"Creating or updating module from {raw}")

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
        module_fields = {
            "tech_level":   raw.get("techLevel"),
            "max_equipped": raw.get("maxEquipped"),
        }

        # anything else goes into our JSON blob
        extra = {
            k: v
            for k, v in raw.items()
            if k not in (*item_fields.keys(), "techLevel", "maxEquipped")
        }

        obj = self.get_by_name(db, item_fields["name"])
        if obj:
            # update existing
            for k, v in item_fields.items():
                setattr(obj, k, v)
            for k, v in module_fields.items():
                setattr(obj, k, v)
            obj.extra_atts = extra
        else:
            # create new
            obj = Module(
                **item_fields,
                **module_fields,
                extra_atts=extra,
            )
            db.add(obj)

        return obj