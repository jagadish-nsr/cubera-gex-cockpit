"""Resolve CryptoCubera SQLite paths relative to the repo root (not cwd).

Relative ``sqlite:///./data/crypto/cubera.db`` URLs must not depend on the
process working directory — paper scripts and uvicorn reload can change cwd.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# crypto/store/paths.py → crypto/ → repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELATIVE_DB = Path("data/crypto/cubera.db")


def _sqlite_url_to_raw(url: str) -> Optional[str]:
    if not url.startswith("sqlite:///"):
        return None
    return url[len("sqlite:///") :]


def resolve_sqlite_path(url: str, *, root: Path | None = None) -> Optional[Path]:
    """Return filesystem path for a sqlite URL, or None for ``:memory:`` / non-sqlite."""
    raw = _sqlite_url_to_raw(url)
    if raw is None:
        return None
    if raw == ":memory:" or raw.startswith(":memory:"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (root or REPO_ROOT) / path
    return path.resolve()


def path_to_sqlite_url(path: Path) -> str:
    """Build a SQLAlchemy sqlite URL from an absolute filesystem path."""
    resolved = path.resolve()
    # Three slashes + absolute path → sqlite:////abs/path
    return f"sqlite:///{resolved}"


def resolve_database_url(url: str, *, root: Path | None = None) -> str:
    """Normalize a configured DB URL so relative sqlite paths are absolute."""
    path = resolve_sqlite_path(url, root=root)
    if path is None:
        return url
    return path_to_sqlite_url(path)


def default_database_url() -> str:
    env = (os.environ.get("CRYPTO_DB_URL") or "").strip()
    if env:
        return resolve_database_url(env)
    return path_to_sqlite_url(REPO_ROOT / DEFAULT_RELATIVE_DB)
