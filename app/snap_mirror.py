"""EXTERNAL serve: keep in-memory books aligned with ingest's pickle.

Ingest owns Dhan and writes ``latest_snap_*.pkl`` + meta. Serve must not pull
the broker. A zombie memory snap (17 Nov) made readiness and the desk age a
frozen 09:01 book while ingest published all day.

This daemon polls meta on a background thread, adopts a newer pickle into
``live_fetcher`` memory, and drops leftovers older than meta. Unpickle stays
off the asyncio event loop.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 15.0
_DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")

_thread: threading.Thread | None = None
_stop = threading.Event()


def _external() -> bool:
    return os.environ.get("COCKPIT_INGEST_EXTERNAL", "").strip() == "1"


def mirror_once(symbols: Iterable[str] = _DEFAULT_SYMBOLS) -> dict[str, bool]:
    """Adopt newer disk snaps; drop memory that is older than meta."""
    from app import live_fetcher
    from app.latest_snap import read_snap_meta

    out: dict[str, bool] = {}
    for raw in symbols:
        sym = (raw or "").upper().strip()
        if not sym:
            continue
        meta = read_snap_meta(sym)
        try:
            # Keep EXTERNAL serve's in-process _REC_BATCH aligned with ingest's
            # latest_rec_{SYM}.pkl off the asyncio event loop as well.
            import sys
            ws_mod = sys.modules.get("web_server")
            if ws_mod is not None and hasattr(ws_mod, "_refresh_external_rec_batch"):
                ws_mod._refresh_external_rec_batch(sym, force_check=True)
        except Exception:
            log.debug("snap mirror rec refresh %s failed", sym, exc_info=True)
        if not meta:
            out[sym] = False
            continue
        try:
            meta_ts = float(meta["fetched_at_epoch"])
        except (TypeError, ValueError, KeyError):
            out[sym] = False
            continue
        adopted = live_fetcher.adopt_disk_snap(sym)
        if adopted:
            out[sym] = True
            continue
        # Memory present but older than meta and adopt failed — drop the zombie.
        rec = live_fetcher.latest(sym)
        if rec and rec.get("data"):
            cur = live_fetcher._fetched_epoch(rec.get("fetched"))
            if cur is not None and cur < meta_ts - 1.0:
                live_fetcher.drop_symbol(sym)
                out[sym] = bool(live_fetcher.adopt_disk_snap(sym))
                continue
        out[sym] = False
    return out


def start_background(
    *,
    symbols: Iterable[str] = _DEFAULT_SYMBOLS,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> None:
    """Idempotent. No-op unless ``COCKPIT_INGEST_EXTERNAL=1``."""
    global _thread
    if not _external():
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    syms = tuple(s for s in symbols if s)

    def _loop() -> None:
        log.info("snap mirror armed (EXTERNAL serve, every %.0fs)", interval_s)
        while not _stop.is_set():
            try:
                mirror_once(syms)
            except Exception:
                log.warning("snap mirror tick failed", exc_info=True)
            _stop.wait(interval_s)

    _thread = threading.Thread(target=_loop, name="snap-mirror", daemon=True)
    _thread.start()
    try:
        mirror_once(syms)
    except Exception:
        log.warning("snap mirror initial adopt failed", exc_info=True)


def stop() -> None:
    _stop.set()
