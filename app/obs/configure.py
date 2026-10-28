"""One logging setup: a short stderr line and a rotated JSON file."""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.admin.clock import now_ist
from app.obs.context import current
from app.obs.redact import redact_mapping, redact_text

ROOT = Path(__file__).resolve().parents[2]
_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 5

_configured = False
_process = "unknown"
_log_dir = ROOT / "logs"


def process_name() -> str:
    return _process


def reset(log_dir: Path | None = None) -> None:
    """Drop handlers this package attached. Tests use this between cases."""
    global _configured, _process, _log_dir
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_cubera_obs", False):
            root.removeHandler(handler)
            handler.close()
    _configured = False
    _process = "unknown"
    _log_dir = Path(log_dir) if log_dir is not None else ROOT / "logs"


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        args = record.args
        if isinstance(args, dict):
            record.args = {
                key: redact_text(val) if isinstance(val, str) else val
                for key, val in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                redact_text(item) if isinstance(item, str) else item for item in args
            )
        obs = getattr(record, "obs", None)
        if isinstance(obs, dict):
            record.obs = redact_mapping(obs)
        return True


class _IstFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return now_ist().strftime("%Y-%m-%d %H:%M:%S")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obs = getattr(record, "obs", None)
        if not isinstance(obs, dict):
            obs = {}
        ctx = current()
        payload = {
            "ts": now_ist().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "process": _process,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
            "event": obs.get("event"),
            "trace_id": obs.get("trace_id") or ctx.get("trace_id"),
            "symbol": obs.get("symbol") or ctx.get("symbol"),
        }
        for key, value in obs.items():
            if key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))[:1000]
        return json.dumps(payload, default=str)


class _FlushingRotatingFileHandler(RotatingFileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def configure(process: str, *, log_dir: Path | None = None) -> None:
    """Attach stderr and ``logs/<process>.jsonl``. A second call does nothing."""
    global _configured, _process, _log_dir
    if _configured:
        return
    _configured = True
    _process = process or "unknown"
    if log_dir is not None:
        _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    redact = _RedactFilter()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(_IstFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    stderr.addFilter(redact)
    stderr._cubera_obs = True  # type: ignore[attr-defined]
    root.addHandler(stderr)

    path = _log_dir / f"{_process}.jsonl"
    file_handler = _FlushingRotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8", delay=True,
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.addFilter(redact)
    file_handler._cubera_obs = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
