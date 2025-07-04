from typing import TypeVar, Generic, Optional, Type
from sqlalchemy.orm import Session
from persist.interfaces.repository_interface import IRepository

T = TypeVar('T')

class GenericRepository(IRepository[T], Generic[T]):
    def __init__(self, model: Type[T]):
        self._model = model

    def add(self, db: Session, obj: T) -> T:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def create_or_update(self, db: Session, raw: dict) -> T:
        """
        Default stubs to force subclasses to implement their own.
        """
        raise NotImplementedError("Subclasses must implement create_or_update")

    def get_by_id(self, db: Session, obj_id: int) -> Optional[T]:
        return db.query(self._model).get(obj_id)

    def get_by_name(self, db: Session, name: str) -> Optional[T]:
        """
        Generic lookup by `name` column. Model must define `name`.
        """
        return db.query(self._model).filter_by(name=name).one_or_none()

    def list_all(self, db: Session) -> list[T]:
        return db.query(self._model).all()

    def remove(self, db: Session, obj: T) -> None:
        db.delete(obj)
        db.commit()