"""Engine / session factory for CryptoCubera store."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from crypto.config import load_config
from crypto.store.paths import (
    DEFAULT_RELATIVE_DB,
    REPO_ROOT,
    resolve_database_url,
    resolve_sqlite_path,
)
from crypto.store.rotate import maybe_rotate_sqlite, rotation_settings
from crypto.store.schema import Base

log = logging.getLogger(__name__)

_ENGINE: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_RESOLVED_URL: str | None = None
_LAST_ROTATION: dict[str, Any] | None = None


def get_resolved_db_url(*, reload: bool = False) -> str:
    """Return the normalized database URL (absolute sqlite path when file-backed)."""
    global _RESOLVED_URL
    if _RESOLVED_URL is not None and not reload:
        return _RESOLVED_URL
    cfg = load_config(reload=reload)
    raw = (cfg.get("database") or {}).get("url") or f"sqlite:///{DEFAULT_RELATIVE_DB.as_posix()}"
    _RESOLVED_URL = resolve_database_url(raw, root=REPO_ROOT)
    return _RESOLVED_URL


def get_db_path(*, reload: bool = False) -> Optional[Path]:
    """Filesystem path of the active sqlite DB, or None for memory / non-sqlite."""
    return resolve_sqlite_path(get_resolved_db_url(reload=reload), root=REPO_ROOT)


def last_rotation_status() -> dict[str, Any] | None:
    return dict(_LAST_ROTATION) if _LAST_ROTATION else None


def dispose_engine() -> None:
    """Dispose the global engine so rotation / tests can reopen cleanly."""
    global _ENGINE, _SessionLocal, _RESOLVED_URL
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SessionLocal = None
    _RESOLVED_URL = None


def get_engine(*, reload: bool = False, rotate: bool | None = None) -> Engine:
    global _ENGINE, _SessionLocal, _LAST_ROTATION
    if _ENGINE is not None and not reload:
        return _ENGINE

    if reload:
        dispose_engine()

    # Do not reload YAML here — callers may have set_flag() after load_config().
    # Only refresh the DB URL cache when the engine itself is being rebuilt.
    cfg = load_config(reload=False)
    url = get_resolved_db_url(reload=True)
    path = resolve_sqlite_path(url, root=REPO_ROOT)

    do_rotate = rotate if rotate is not None else True
    if path is not None and do_rotate and not url.startswith("sqlite:///:memory:"):
        max_bytes, keep = rotation_settings(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        _LAST_ROTATION = maybe_rotate_sqlite(path, max_bytes=max_bytes, keep=keep)
        if _LAST_ROTATION.get("rotated"):
            log.info("SQLite rotation: %s", _LAST_ROTATION)
        path.parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # Allow FastAPI + feed threads to share connections; wait on locks.
        connect_args = {"check_same_thread": False, "timeout": 30.0}

    engine = create_engine(url, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    _ENGINE = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine


def init_db(*, reload: bool = False, rotate: bool | None = None) -> Engine:
    engine = get_engine(reload=reload, rotate=rotate)
    Base.metadata.create_all(engine)
    _migrate_events_columns(engine)
    return engine


def _migrate_events_columns(engine: Engine) -> None:
    """Add intel columns on existing SQLite ``events`` tables (create_all won't)."""
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return

    alters = [
        ("event_type", "ALTER TABLE events ADD COLUMN event_type VARCHAR(32)"),
        ("novelty", "ALTER TABLE events ADD COLUMN novelty FLOAT"),
        ("venues", "ALTER TABLE events ADD COLUMN venues JSON"),
        ("occurs_at", "ALTER TABLE events ADD COLUMN occurs_at DATETIME"),
        ("blackout_until", "ALTER TABLE events ADD COLUMN blackout_until DATETIME"),
    ]
    with engine.begin() as conn:
        existing = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(events)")).fetchall()
        }
        if not existing:
            return
        for col, sql in alters:
            if col not in existing:
                conn.execute(text(sql))


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


def store_status() -> dict[str, Any]:
    """Operator-facing DB path / size / rotation snapshot (no secrets)."""
    url = get_resolved_db_url()
    path = get_db_path()
    size = int(path.stat().st_size) if path is not None and path.exists() else 0
    cfg = load_config()
    max_bytes, keep = rotation_settings(cfg)
    return {
        "url": url if url.startswith("sqlite:///:memory:") else (
            f"sqlite:///{path}" if path else url
        ),
        "path": str(path) if path else None,
        "exists": bool(path and path.exists()),
        "size_bytes": size,
        "rotate_max_bytes": max_bytes,
        "rotate_keep": keep,
        "last_rotation": last_rotation_status(),
        "repo_root": str(REPO_ROOT),
    }
