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

    def get_by_id(self, db: Session, obj_id: int) -> Optional[T]:
        return db.query(self._model).get(obj_id)

    def list_all(self, db: Session) -> list[T]:
        return db.query(self._model).all()

    def remove(self, db: Session, obj: T) -> None:
        db.delete(obj)
        db.commit()