from sqlalchemy.orm import Session
from persist.models.criminal import Criminal
from persist.repositories.generic_repository import GenericRepository

class CriminalRepository(GenericRepository[Criminal]):
    def __init__(self):
        super().__init__(Criminal)

    def create_or_update(self, db: Session, raw: dict) -> Criminal:
        session = self._unwrap(db)
        obj = session.query(Criminal).filter_by(name=raw["name"]).one_or_none()

        mapping = {
            "builtIn":  "built_in",
            "isPlayer": "is_player",
        }
        def to_attr(k): return mapping.get(k, k.lower())

        if obj:
            for k, v in raw.items():
                setattr(obj, to_attr(k), v)
        else:
            attrs = { to_attr(k): v for k, v in raw.items() }
            obj = Criminal(**attrs)
            session.add(obj)

        session.commit()
        session.refresh(obj)
        return obj