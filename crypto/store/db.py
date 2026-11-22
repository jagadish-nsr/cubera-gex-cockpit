"""Engine / session factory for CryptoCubera store."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from crypto.config import load_config
from crypto.store.schema import Base

_ENGINE: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _sqlite_url_to_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///") :]
    if raw == ":memory:":
        return None
    return Path(raw)


def get_engine(*, reload: bool = False) -> Engine:
    global _ENGINE, _SessionLocal
    if _ENGINE is not None and not reload:
        return _ENGINE

    cfg = load_config(reload=reload)
    url = (cfg.get("database") or {}).get("url") or "sqlite:///./data/crypto/cubera.db"
    path = _sqlite_url_to_path(url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    _ENGINE = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine


def init_db(*, reload: bool = False) -> Engine:
    engine = get_engine(reload=reload)
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()
