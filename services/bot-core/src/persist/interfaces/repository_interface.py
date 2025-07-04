from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional
from sqlalchemy.orm import Session

T = TypeVar('T')

class IRepository(ABC, Generic[T]):

    @abstractmethod
    def get_by_id(self, db: Session, obj_id: int) -> Optional[T]:
        pass

    @abstractmethod
    def get_by_name(self, db: Session, name: str) -> Optional[T]:
        """Fetch a T by its unique name"""
        pass

    @abstractmethod
    def list_all(self, db: Session) -> list[T]:
        pass

    @abstractmethod
    def add(self, db: Session, obj: T) -> T:
        pass

    @abstractmethod
    def create_or_update(self, db: Session, raw: dict) -> T:
        """
        Upsert from a raw JSON dict.
        """
        pass

    @abstractmethod
    def remove(self, db: Session, obj: T) -> None:
        pass