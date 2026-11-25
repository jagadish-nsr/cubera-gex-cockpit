"""Fetch NSE Clearing's end-of-day SPAN file and keep a short local history.

The file is ``nsccl.YYYYMMDD.s.zip`` on the NSE archive host, one ``.spn`` inside.
A download is only kept if it parses and its own ``<created>`` date is the day asked
for, so a wrong or truncated file fails loud instead of feeding the margin map.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import date
from pathlib import Path

from analytics.span_file import parse_futures_span

SPAN_URL = "https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{ymd}.s.zip"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
KEEP_FILES = 10  # each .spn is ~43 MB


class SpanNotPublished(RuntimeError):
    """NSE has no end-of-day SPAN file for that date (yet)."""


def span_filename(day: date) -> str:
    return f"nsccl.{day:%Y%m%d}.s.spn"


def _download(day: date, getter, attempts: int, timeout: int, sleep) -> bytes:
    url = SPAN_URL.format(ymd=f"{day:%Y%m%d}")
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = getter(url, headers=_HEADERS, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - any network failure is retried, then raised
            last = exc
        else:
            if resp.status_code == 404:
                raise SpanNotPublished(f"NSE has no SPAN file for {day} ({url})")
            if resp.status_code == 200 and resp.content:
                return resp.content
            last = RuntimeError(f"HTTP {resp.status_code}")
        if attempt + 1 < attempts:
            sleep(2 * (attempt + 1))
    raise RuntimeError(f"SPAN download failed for {day}: {last}")


def _validated(data: bytes, day: date) -> None:
    """Raise unless ``data`` is a SPAN file dated ``day`` that has NIFTY futures."""
    text = data.decode("utf-8", errors="replace").replace("\r", "")
    parsed = parse_futures_span(text)
    if parsed["file_date"] != day.isoformat():
        raise ValueError(f"SPAN file is dated {parsed['file_date']}, expected {day.isoformat()}")
    if "NIFTY" not in parsed["contracts"]:
        raise ValueError("SPAN file has no NIFTY futures")


def _extract(zip_bytes: bytes, day: date) -> bytes:
    name = span_filename(day)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members = zf.namelist()
        if name not in members:
            raise ValueError(f"zip has no {name}; it holds {members}")
        return zf.read(name)


def prune(span_dir: Path, keep: int = KEEP_FILES) -> list[str]:
    """Delete all but the newest ``keep`` SPAN files. Returns the names removed."""
    files = sorted(span_dir.glob("nsccl.*.spn"))
    removed = []
    for path in files[: max(0, len(files) - keep)]:
        path.unlink()
        removed.append(path.name)
    return removed


def fetch_span(
    day: date,
    span_dir: Path,
    *,
    getter=None,
    keep: int = KEEP_FILES,
    attempts: int = 3,
    timeout: int = 90,
    sleep=time.sleep,
) -> str:
    """Make sure ``span_dir`` holds the SPAN file for ``day``.

    Returns ``"holiday"`` (no file expected), ``"present"`` (already there and valid)
    or ``"fetched"``. Raises ``SpanNotPublished`` on a 404 and ``ValueError`` or
    ``RuntimeError`` on a bad or unreachable download.
    """
    from data.holidays import is_trading_day

    if not is_trading_day(day):
        return "holiday"
    span_dir.mkdir(parents=True, exist_ok=True)
    dest = span_dir / span_filename(day)
    if dest.exists():
        try:
            _validated(dest.read_bytes(), day)
            return "present"
        except ValueError:
            pass  # a bad local copy is replaced below

    if getter is None:
        import requests

        getter = requests.get
    data = _extract(_download(day, getter, attempts, timeout, sleep), day)
    _validated(data, day)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    prune(span_dir, keep)
    return "fetched"
