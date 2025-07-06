from sqlalchemy.orm import Session
from persist.models.system import System
from persist.repositories.generic_repository import GenericRepository

class SystemRepository(GenericRepository[System]):
    def __init__(self):
        super().__init__(System)

    def create_or_update(self, db: Session, raw: dict) -> System:
        session = self._unwrap(db)
        obj = session.query(System).filter_by(name=raw["name"]).one_or_none()

        # no special JSON-to-attr mapping here
        def to_attr(k): return k.lower()

        if obj:
            for k, v in raw.items():
                setattr(obj, to_attr(k), v)
        else:
            attrs = { to_attr(k): v for k, v in raw.items() }
            obj = System(**attrs)
            session.add(obj)

        session.commit()
        session.refresh(obj)
        return obj