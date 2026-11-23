"""Durable paper trade ledger + CSV/JSON export."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from crypto.providers.types import utcnow
from crypto.store.db import get_session, init_db
from crypto.store.schema import PaperTradeRow


def _ser_dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_notes(payload: dict[str, Any] | None) -> str:
    """Operator notes for Manual Decision / paper journal exports."""
    if not payload:
        return ""
    notes = payload.get("notes")
    if notes:
        return str(notes)
    decision = payload.get("decision") or {}
    if isinstance(decision, dict) and decision.get("notes"):
        return str(decision.get("notes"))
    return ""


def _row_to_dict(row: PaperTradeRow) -> dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "id": row.id,
        "client_order_id": row.client_order_id,
        "venue": row.venue,
        "adapter": row.adapter,
        "status": row.status,
        "instrument": row.instrument,
        "side": row.side,
        "setup": row.setup,
        "entry": row.entry,
        "target": row.target,
        "stop": row.stop,
        "size_usd": row.size_usd,
        "fill_price": row.fill_price,
        "realized_pnl_usd": row.realized_pnl_usd,
        "signal_status": row.signal_status,
        "mode": row.mode,
        "notes": _row_notes(payload),
        "submitted_at": _ser_dt(row.submitted_at),
        "filled_at": _ser_dt(row.filled_at),
        "closed_at": _ser_dt(row.closed_at),
        "cancel_reason": row.cancel_reason,
        "payload": payload,
    }


class TradeLedger:
    def __init__(self) -> None:
        init_db()

    def new_client_order_id(self) -> str:
        # Delta client_order_id max 32 chars
        return f"cc{uuid.uuid4().hex[:30]}"

    def record_submission(self, trade: dict[str, Any]) -> dict[str, Any]:
        with get_session() as s:
            row = PaperTradeRow(
                client_order_id=str(trade["client_order_id"]),
                venue=str(trade.get("venue") or "paper"),
                adapter=str(trade.get("adapter") or "sim"),
                status=str(trade.get("status") or "submitted"),
                instrument=str(trade.get("instrument") or ""),
                side=str(trade.get("side") or ""),
                setup=str(trade.get("setup") or ""),
                entry=float(trade.get("entry") or 0),
                target=float(trade["target"]) if trade.get("target") is not None else None,
                stop=float(trade["stop"]) if trade.get("stop") is not None else None,
                size_usd=float(trade.get("size_usd") or 0),
                fill_price=float(trade["fill_price"]) if trade.get("fill_price") is not None else None,
                realized_pnl_usd=float(trade.get("realized_pnl_usd") or 0),
                signal_status=str(trade.get("signal_status") or "GO"),
                mode=str(trade.get("mode") or "paper_confirm"),
                submitted_at=trade.get("submitted_at")
                if isinstance(trade.get("submitted_at"), datetime)
                else utcnow(),
                filled_at=trade.get("filled_at")
                if isinstance(trade.get("filled_at"), datetime)
                else None,
                closed_at=trade.get("closed_at")
                if isinstance(trade.get("closed_at"), datetime)
                else None,
                cancel_reason=trade.get("cancel_reason"),
                payload=dict(trade.get("payload") or {}),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_dict(row)

    def update_status(
        self,
        client_order_id: str,
        *,
        status: str,
        fill_price: Optional[float] = None,
        realized_pnl_usd: Optional[float] = None,
        cancel_reason: Optional[str] = None,
        filled_at: Optional[datetime] = None,
        closed_at: Optional[datetime] = None,
        payload_patch: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        with get_session() as s:
            row = s.execute(
                select(PaperTradeRow).where(PaperTradeRow.client_order_id == client_order_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            if fill_price is not None:
                row.fill_price = float(fill_price)
            if realized_pnl_usd is not None:
                row.realized_pnl_usd = float(realized_pnl_usd)
            if cancel_reason is not None:
                row.cancel_reason = cancel_reason
            if filled_at is not None:
                row.filled_at = filled_at
            if closed_at is not None:
                row.closed_at = closed_at
            if payload_patch:
                merged = dict(row.payload or {})
                merged.update(payload_patch)
                row.payload = merged
            s.commit()
            s.refresh(row)
            return _row_to_dict(row)

    def list_trades(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with get_session() as s:
            q = select(PaperTradeRow).order_by(PaperTradeRow.submitted_at.desc()).limit(limit)
            if status:
                q = (
                    select(PaperTradeRow)
                    .where(PaperTradeRow.status == status)
                    .order_by(PaperTradeRow.submitted_at.desc())
                    .limit(limit)
                )
            rows = s.execute(q).scalars().all()
            return [_row_to_dict(r) for r in rows]

    def open_intents(self) -> list[dict[str, Any]]:
        open_statuses = ("submitted", "open", "partial", "sim_filled")
        with get_session() as s:
            rows = (
                s.execute(
                    select(PaperTradeRow)
                    .where(PaperTradeRow.status.in_(open_statuses))
                    .order_by(PaperTradeRow.submitted_at.desc())
                )
                .scalars()
                .all()
            )
            return [_row_to_dict(r) for r in rows]

    def realized_pnl_on(self, day: Optional[datetime] = None) -> float:
        """Sum realized PnL for closed trades on the UTC calendar day."""
        day = day or utcnow()
        if day.tzinfo is None:
            day = day.replace(tzinfo=timezone.utc)
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        with get_session() as s:
            rows = (
                s.execute(
                    select(PaperTradeRow).where(
                        PaperTradeRow.closed_at.is_not(None),
                        PaperTradeRow.closed_at >= start,
                        PaperTradeRow.closed_at <= end,
                    )
                )
                .scalars()
                .all()
            )
            return float(sum(float(r.realized_pnl_usd or 0) for r in rows))

    def export_json(self, *, limit: int = 500) -> str:
        return json.dumps({"trades": self.list_trades(limit=limit)}, indent=2)

    def export_csv(self, *, limit: int = 500) -> str:
        trades = self.list_trades(limit=limit)
        fields = [
            "id",
            "client_order_id",
            "venue",
            "adapter",
            "status",
            "instrument",
            "side",
            "setup",
            "entry",
            "target",
            "stop",
            "size_usd",
            "fill_price",
            "realized_pnl_usd",
            "mode",
            "notes",
            "submitted_at",
            "filled_at",
            "closed_at",
            "cancel_reason",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow({k: t.get(k) for k in fields})
        return buf.getvalue()
