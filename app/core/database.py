from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _build_engine(database_url: str) -> Engine:
    engine_kwargs: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    created = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        event.listen(created, "connect", _set_sqlite_pragmas)
    return created


def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


engine = _build_engine(settings.database_url)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def configure_database(database_url: str) -> None:
    """Rebind the shared engine without recreating SQLAlchemy's model registry.

    Production configuration is loaded once. Tests use this explicit hook to point
    the same declarative Base and session factory at isolated databases instead of
    deleting and re-importing ``app.*`` modules, which corrupts relationship maps.
    """

    global engine
    previous = engine
    engine = _build_engine(database_url)
    SessionLocal.configure(bind=engine)
    object.__setattr__(settings, "database_url", database_url)
    previous.dispose()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    from app.models import entities  # noqa: F401
    from app.models import client  # noqa: F401

    Base.metadata.create_all(bind=engine)
