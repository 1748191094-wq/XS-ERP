from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    def __init__(self, db: Session, model: type[ModelT]):
        self.db = db
        self.model = model

    def get(self, object_id: int) -> ModelT | None:
        return self.db.get(self.model, object_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        return list(self.db.scalars(select(self.model).offset(offset).limit(min(limit, 500))))

    def add(self, item: ModelT) -> ModelT:
        self.db.add(item)
        self.db.flush()
        return item
