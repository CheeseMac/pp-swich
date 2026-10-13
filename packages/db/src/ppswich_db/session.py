"""Engine and session helpers.

Dev default is SQLite; production baseline is PostgreSQL (docs/02 section 6).
Every repository query must filter by ``workspace_id`` explicitly - there is
no implicit tenant filter in this layer.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine_for_url(url: str, *, echo: bool = False) -> Engine:
    engine = create_engine(url, echo=echo, future=True)
    if url.startswith("sqlite"):
        # SQLite ignores FK constraints unless told; composite FKs are a
        # second line of defense for tenancy, so enforce them in dev too.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def make_session(engine: Engine) -> Session:
    return create_session_factory(engine)()


def default_db_url() -> str:
    env_url = os.environ.get("PP_SWICH_DB_URL")
    if env_url:
        return env_url
    return "sqlite:///./data/pp-swich.db"


def sessions(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Context-managed session helper for scripts and tests."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
