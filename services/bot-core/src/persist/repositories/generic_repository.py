from typing import TypeVar, Generic, Optional, Type
from sqlalchemy.orm import Session
from persist.interfaces.repository_interface import IRepository
from contextlib import AbstractContextManager  # new

T = TypeVar('T')

class GenericRepository(IRepository[T], Generic[T]):
    def __init__(self, model: Type[T]):
        self._model = model

    def _unwrap(self, db) -> Session:
        """
        If we got a context‐manager instead of a raw Session,
        enter it to get the real Session.
        """
        if hasattr(db, "__enter__") and hasattr(db, "__exit__"):
            return db.__enter__()
        return db

    def add(self, db: Session, obj: T) -> T:
        session = self._unwrap(db)
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj

    def create_or_update(self, db: Session, raw: dict) -> T:
        raise NotImplementedError("Subclasses must implement create_or_update")

    def get_by_id(self, db: Session, obj_id: int) -> Optional[T]:
        session = self._unwrap(db)
        return session.query(self._model).get(obj_id)

    def get_by_name(self, db: Session, name: str) -> Optional[T]:
        session = self._unwrap(db)
        return session.query(self._model).filter_by(name=name).one_or_none()
    
    def get_by_alias(self, db: Session, alias: str) -> Optional[T]:
        session = self._unwrap(db)
        # assumes your model has an ARRAY/Text‐list column or relationship called .aliases
        return session.query(self._model).filter(self._model.aliases.any(alias)).one_or_none()

    def list_all(self, db: Session) -> list[T]:
        session = self._unwrap(db)
        return session.query(self._model).all()

    def remove(self, db: Session, obj: T) -> None:
        session = self._unwrap(db)
        session.delete(obj)
        session.commit()