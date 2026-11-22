"""Paper session lifecycle + summary export (local paper only).

A session bounds the window while the paper server runs. Ticks / GEX /
orderflow / signals keep writing to their tables; this module aggregates
counts for OOS review and exports JSON/CSV. Never enables ``exec.live``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from crypto.config import get_active_underlying, get_flag, load_config
from crypto.providers.types import utcnow
from crypto.store.db import get_db_path, get_session, init_db, store_status
from crypto.store.schema import (
    GexFeature,
    OrderflowFeature,
    PaperSessionRow,
    PaperTradeRow,
    RawTick,
    SignalRow,
)

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_CURRENT: Optional[dict[str, Any]] = None


def _ser_dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _row_to_dict(row: PaperSessionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "mode": row.mode,
        "underlying": row.underlying,
        "started_at": _ser_dt(row.started_at),
        "ended_at": _ser_dt(row.ended_at),
        "db_path": row.db_path,
        "summary": dict(row.summary or {}),
        "notes": row.notes,
        "active": row.ended_at is None,
    }


def _resolve_mode() -> str:
    if get_flag("exec.live", default=False):
        # Manual Decision (decision-only) when confirm is on — never Live Auto.
        return "manual_decision" if get_flag("exec.confirm") else "manual"
    if get_flag("exec.paper", default=False) and get_flag("exec.confirm", default=False):
        return "paper_confirm"
    if get_flag("exec.paper", default=False):
        return "paper"
    return "advisory"


def recording_enabled() -> bool:
    """Recording is on unless explicitly disabled (default: on)."""
    cfg = load_config()
    rec = cfg.get("recording") or {}
    if "enabled" in rec:
        return bool(rec["enabled"])
    env = (os.environ.get("CRYPTO_RECORDING") or "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    return True


def new_session_id() -> str:
    env = (os.environ.get("CRYPTO_SESSION_ID") or "").strip()
    if env:
        return env[:64]
    return f"ps_{uuid.uuid4().hex[:20]}"


def start_session(
    *,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    underlying: Optional[str] = None,
    notes: Optional[str] = None,
    force_new: bool = False,
) -> dict[str, Any]:
    """Begin (or resume) a recording session. Idempotent for the same process."""
    global _CURRENT
    init_db()
    with _LOCK:
        if _CURRENT is not None and not force_new and _CURRENT.get("ended_at") is None:
            return dict(_CURRENT)

        sid = session_id or new_session_id()
        started = utcnow()
        m = mode or _resolve_mode()
        u = (underlying or get_active_underlying() or "BTC").upper()
        path = get_db_path()
        with get_session() as s:
            existing = s.execute(
                select(PaperSessionRow).where(PaperSessionRow.session_id == sid)
            ).scalar_one_or_none()
            if existing is not None and existing.ended_at is None and not force_new:
                _CURRENT = _row_to_dict(existing)
                return dict(_CURRENT)
            if existing is not None and force_new:
                sid = new_session_id()
            row = PaperSessionRow(
                session_id=sid,
                mode=m,
                underlying=u,
                started_at=started,
                ended_at=None,
                db_path=str(path) if path else None,
                summary={},
                notes=notes or ("local paper recording" if m.startswith("paper") else "recording"),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            _CURRENT = _row_to_dict(row)
        log.info(
            "Paper session started id=%s mode=%s underlying=%s db=%s",
            sid,
            m,
            u,
            path,
        )
        return dict(_CURRENT)


def current_session() -> Optional[dict[str, Any]]:
    global _CURRENT
    with _LOCK:
        if _CURRENT is not None:
            return dict(_CURRENT)
    # Recover open session from DB after reload
    init_db()
    with get_session() as s:
        row = (
            s.execute(
                select(PaperSessionRow)
                .where(PaperSessionRow.ended_at.is_(None))
                .order_by(PaperSessionRow.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        data = _row_to_dict(row)
    with _LOCK:
        _CURRENT = data
    return dict(data)


def end_session(
    *,
    session_id: Optional[str] = None,
    write_summary: bool = True,
) -> dict[str, Any]:
    """Close the active session and optionally snapshot the summary into the row."""
    global _CURRENT
    init_db()
    with _LOCK:
        cur = dict(_CURRENT) if _CURRENT else None
    if cur is None:
        cur = current_session()
    if cur is None:
        return {"ok": False, "error": "no_active_session"}

    sid = session_id or cur["session_id"]
    ended = utcnow()
    summary: dict[str, Any] = {}
    if write_summary:
        summary = build_summary(
            session_id=sid,
            started_at=_parse_dt(cur.get("started_at")),
            ended_at=ended,
        )

    with get_session() as s:
        row = s.execute(
            select(PaperSessionRow).where(PaperSessionRow.session_id == sid)
        ).scalar_one_or_none()
        if row is None:
            return {"ok": False, "error": "session_not_found", "session_id": sid}
        row.ended_at = ended
        if write_summary:
            row.summary = summary
        s.commit()
        s.refresh(row)
        data = _row_to_dict(row)

    with _LOCK:
        if _CURRENT and _CURRENT.get("session_id") == sid:
            _CURRENT = data
    log.info("Paper session ended id=%s", sid)
    return {"ok": True, "session": data, "summary": summary}


def reset_session_state() -> None:
    """Test helper — clear in-memory current session pointer."""
    global _CURRENT
    with _LOCK:
        _CURRENT = None


def _count_signals(
    started: datetime,
    ended: datetime,
) -> dict[str, Any]:
    with get_session() as s:
        rows = (
            s.execute(
                select(SignalRow).where(
                    SignalRow.created_at >= started,
                    SignalRow.created_at <= ended,
                )
            )
            .scalars()
            .all()
        )
    go = 0
    nogo = 0
    with_vetoes = 0
    veto_tallies: dict[str, int] = {}
    for r in rows:
        status = (r.status or "").upper()
        if status == "GO":
            go += 1
        else:
            nogo += 1
        reasons = list(r.veto_reasons or [])
        if reasons:
            with_vetoes += 1
            for reason in reasons:
                key = str(reason)
                veto_tallies[key] = veto_tallies.get(key, 0) + 1
    return {
        "signals_total": len(rows),
        "go_count": go,
        "nogo_count": nogo,
        "veto_signal_count": with_vetoes,
        "veto_tallies": veto_tallies,
    }


def _count_trades(started: datetime, ended: datetime) -> dict[str, Any]:
    with get_session() as s:
        rows = (
            s.execute(
                select(PaperTradeRow).where(
                    PaperTradeRow.submitted_at >= started,
                    PaperTradeRow.submitted_at <= ended,
                )
            )
            .scalars()
            .all()
        )
    confirms = len(rows)
    open_n = sum(
        1
        for r in rows
        if (r.status or "") in {"submitted", "open", "partial", "sim_filled"}
    )
    closed = [r for r in rows if r.closed_at is not None]
    pnl = float(sum(float(r.realized_pnl_usd or 0) for r in rows))
    cancelled = sum(1 for r in rows if (r.status or "").startswith("cancel"))
    return {
        "confirms": confirms,
        "open_intents": open_n,
        "closed_trades": len(closed),
        "cancelled": cancelled,
        "realized_pnl_usd": pnl,
        "pnl_available": any(r.closed_at is not None or float(r.realized_pnl_usd or 0) != 0 for r in rows),
    }


def _count_table(model: Any, ts_col: Any, started: datetime, ended: datetime) -> int:
    with get_session() as s:
        n = s.execute(
            select(func.count())
            .select_from(model)
            .where(ts_col >= started, ts_col <= ended)
        ).scalar_one()
        return int(n or 0)


def build_summary(
    *,
    session_id: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Aggregate GO / confirms / vetoes / PnL / tick counts for a session window."""
    init_db()
    sess = None
    if session_id:
        with get_session() as s:
            row = s.execute(
                select(PaperSessionRow).where(PaperSessionRow.session_id == session_id)
            ).scalar_one_or_none()
            if row is not None:
                sess = _row_to_dict(row)
                started_at = started_at or _parse_dt(row.started_at)
                ended_at = ended_at or _parse_dt(row.ended_at) or utcnow()
    if started_at is None or ended_at is None:
        cur = current_session()
        if cur is None:
            # Fall back to "everything in DB"
            started_at = started_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
            ended_at = ended_at or utcnow()
            sess = {"session_id": session_id or "all", "mode": _resolve_mode()}
        else:
            sess = cur
            started_at = started_at or _parse_dt(cur["started_at"])
            ended_at = ended_at or utcnow()

    assert started_at is not None and ended_at is not None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)

    signals = _count_signals(started_at, ended_at)
    trades = _count_trades(started_at, ended_at)
    ticks = _count_table(RawTick, RawTick.recv_ts, started_at, ended_at)
    gex_n = _count_table(GexFeature, GexFeature.computed_at, started_at, ended_at)
    of_n = _count_table(
        OrderflowFeature, OrderflowFeature.computed_at, started_at, ended_at
    )
    store = store_status()

    duration_s = max(0.0, (ended_at - started_at).total_seconds())
    return {
        "session_id": (sess or {}).get("session_id") or session_id or "all",
        "mode": (sess or {}).get("mode") or _resolve_mode(),
        "underlying": (sess or {}).get("underlying") or get_active_underlying(),
        "started_at": _ser_dt(started_at),
        "ended_at": _ser_dt(ended_at),
        "duration_s": duration_s,
        "active": bool((sess or {}).get("active", ended_at is None)),
        "go_count": signals["go_count"],
        "nogo_count": signals["nogo_count"],
        "signals_total": signals["signals_total"],
        "veto_signal_count": signals["veto_signal_count"],
        "veto_tallies": signals["veto_tallies"],
        "confirms": trades["confirms"],
        "open_intents": trades["open_intents"],
        "closed_trades": trades["closed_trades"],
        "cancelled": trades["cancelled"],
        "realized_pnl_usd": trades["realized_pnl_usd"],
        "pnl_available": trades["pnl_available"],
        "ticks": ticks,
        "gex_features": gex_n,
        "orderflow_features": of_n,
        "store": {
            "path": store.get("path"),
            "size_bytes": store.get("size_bytes"),
        },
        "exec_live": bool(get_flag("exec.live", default=False)),
        "note": "Local paper recording summary. exec.live must stay off.",
    }


def list_sessions(*, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with get_session() as s:
        rows = (
            s.execute(
                select(PaperSessionRow)
                .order_by(PaperSessionRow.started_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def export_summary_json(
    *,
    session_id: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
) -> str:
    summary = build_summary(
        session_id=session_id, started_at=started_at, ended_at=ended_at
    )
    return json.dumps(summary, indent=2, sort_keys=False)


def export_summary_csv(
    *,
    session_id: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
) -> str:
    summary = build_summary(
        session_id=session_id, started_at=started_at, ended_at=ended_at
    )
    fields = [
        "session_id",
        "mode",
        "underlying",
        "started_at",
        "ended_at",
        "duration_s",
        "go_count",
        "nogo_count",
        "signals_total",
        "veto_signal_count",
        "confirms",
        "open_intents",
        "closed_trades",
        "cancelled",
        "realized_pnl_usd",
        "pnl_available",
        "ticks",
        "gex_features",
        "orderflow_features",
        "exec_live",
    ]
    flat = {k: summary.get(k) for k in fields}
    # Flatten top vetoes into a compact cell
    tallies = summary.get("veto_tallies") or {}
    flat["veto_tallies"] = ";".join(f"{k}:{v}" for k, v in sorted(tallies.items()))
    fields_out = fields + ["veto_tallies"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields_out, extrasaction="ignore")
    writer.writeheader()
    writer.writerow(flat)
    return buf.getvalue()
