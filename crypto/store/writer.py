"""Append write path for ticks / features / signals / events / flag audit."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from crypto.store.db import get_session, init_db
from crypto.store.schema import (
    EventRow,
    FeatureFlagAudit,
    GexFeature,
    OptionChainRow,
    OrderflowFeature,
    RawTick,
    SignalRow,
)
from crypto.providers.types import NewsEvent, OptionChainSnapshot, PerpSnapshot, utcnow

log = logging.getLogger(__name__)

T = TypeVar("T")
_WRITE_RETRIES = 4
_WRITE_BACKOFF_S = 0.05


def _with_db_retry(fn: Callable[[], T], *, what: str) -> T:
    """Retry briefly on SQLite lock contention; fail loud otherwise."""
    last: Exception | None = None
    for attempt in range(_WRITE_RETRIES):
        try:
            return fn()
        except OperationalError as exc:
            last = exc
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                log.error("Store write failed (%s): %s", what, exc)
                raise
            time.sleep(_WRITE_BACKOFF_S * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            log.error("Store write failed (%s): %s", what, exc)
            raise
    assert last is not None
    log.error("Store write failed after retries (%s): %s", what, last)
    raise last


class StoreWriter:
    def __init__(self) -> None:
        init_db()

    def write_tick(
        self,
        venue: str,
        channel: str,
        payload: dict[str, Any],
        *,
        exchange_ts: Optional[datetime] = None,
    ) -> int:
        def _do() -> int:
            with get_session() as s:
                row = RawTick(
                    venue=venue,
                    channel=channel,
                    payload=payload,
                    recv_ts=utcnow(),
                    exchange_ts=exchange_ts,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"tick:{venue}/{channel}")

    def write_chain(self, snap: OptionChainSnapshot) -> int:
        payload = {
            "spot": snap.spot,
            "mark_iv": snap.mark_iv,
            "dvol": snap.dvol,
            "contracts": [
                {
                    "instrument": c.instrument,
                    "expiry": c.expiry.isoformat(),
                    "strike": c.strike,
                    "option_type": c.option_type,
                    "oi": c.oi,
                    "iv": c.iv,
                    "gamma": c.gamma,
                    "delta": c.delta,
                    "mid": c.mid,
                }
                for c in snap.contracts
            ],
        }
        self.write_tick(snap.source, "option_chain", payload, exchange_ts=snap.exchange_ts)

        def _do() -> int:
            with get_session() as s:
                row = OptionChainRow(
                    underlying=snap.underlying,
                    source=snap.source,
                    spot=snap.spot,
                    snapshot=payload,
                    fetched_at=snap.fetched_at,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"chain:{snap.underlying}")

    def write_gex(self, feature: dict[str, Any]) -> int:
        computed_at = feature.get("computed_at") or utcnow()
        if isinstance(computed_at, datetime):
            pass
        by_strike = feature.get("by_strike") or {}

        def _do() -> int:
            with get_session() as s:
                row = GexFeature(
                    underlying=feature["underlying"],
                    spot=float(feature["spot"]),
                    flip=feature.get("flip"),
                    call_wall=feature.get("call_wall"),
                    put_wall=feature.get("put_wall"),
                    regime=feature["regime"],
                    convention_sign=feature["convention_sign"],
                    flow_sign=feature["flow_sign"],
                    models_agree=bool(feature["models_agree"]),
                    by_strike=by_strike,
                    computed_at=computed_at if isinstance(computed_at, datetime) else utcnow(),
                    source=feature.get("source") or "engine",
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"gex:{feature.get('underlying')}")

    def write_orderflow(self, perp: PerpSnapshot) -> int:
        """Persist from a PerpSnapshot (legacy / tests). Prefer write_orderflow_features."""
        payload = {
            "mark": perp.mark,
            "index": perp.index,
        }
        self.write_tick(perp.venue, "perp", payload, exchange_ts=perp.fetched_at)

        def _do() -> int:
            with get_session() as s:
                row = OrderflowFeature(
                    symbol=perp.symbol,
                    venue=perp.venue,
                    cvd_1m=perp.cvd_1m,
                    cvd_5m=perp.cvd_5m,
                    cvd_15m=perp.cvd_15m,
                    book_imbalance=perp.book_imbalance,
                    funding_rate=perp.funding_rate,
                    open_interest=perp.open_interest,
                    computed_at=perp.fetched_at,
                    payload=payload,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"orderflow:{perp.symbol}")

    def write_orderflow_features(self, features: dict[str, Any]) -> int:
        """Persist enriched orderflow features (CVD, OI Δ, funding/basis hooks)."""
        computed_at = features.get("computed_at") or utcnow()
        if not isinstance(computed_at, datetime):
            computed_at = utcnow()
        payload = {
            k: features[k]
            for k in (
                "mark",
                "index",
                "oi_delta",
                "basis",
                "basis_bps",
                "tape_source",
                "hooks",
                "options_signed_call_premium",
                "options_signed_put_premium",
            )
            if k in features
        }
        venue = str(features.get("venue") or "unknown")
        self.write_tick(venue, "orderflow", payload, exchange_ts=computed_at)

        def _do() -> int:
            with get_session() as s:
                row = OrderflowFeature(
                    symbol=str(features.get("symbol") or ""),
                    venue=venue,
                    cvd_1m=float(features.get("cvd_1m") or 0),
                    cvd_5m=float(features.get("cvd_5m") or 0),
                    cvd_15m=float(features.get("cvd_15m") or 0),
                    book_imbalance=float(features.get("book_imbalance") or 0),
                    funding_rate=float(features.get("funding_rate") or 0),
                    open_interest=float(features.get("open_interest") or 0),
                    computed_at=computed_at,
                    payload=payload,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"orderflow_features:{features.get('symbol')}")

    def write_signal(self, card: dict[str, Any]) -> int:
        created_at = card.get("created_at") or utcnow()
        valid_until = card.get("valid_until")
        # JSON column cannot hold raw datetime — serialize for storage
        card_json = {
            k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in card.items()
        }

        def _do() -> int:
            with get_session() as s:
                row = SignalRow(
                    status=card["status"],
                    setup=card.get("setup") or "",
                    instrument=card.get("instrument") or "",
                    side=card.get("side") or "",
                    score=int(card.get("score") or 0),
                    card=card_json,
                    veto_reasons=list(card.get("veto_reasons") or []),
                    model_version=card.get("model_version") or "rules-v0",
                    created_at=created_at if isinstance(created_at, datetime) else utcnow(),
                    valid_until=valid_until if isinstance(valid_until, datetime) else None,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return int(row.id)

        return _with_db_retry(_do, what=f"signal:{card.get('status')}")

    def write_events(self, events: list[NewsEvent]) -> int:
        n = 0
        with get_session() as s:
            for e in events:
                existing = s.execute(
                    select(EventRow).where(EventRow.event_id == e.event_id)
                ).scalar_one_or_none()
                payload = {
                    "assets": e.assets,
                    "url": e.url,
                    "impact_note": e.impact_note,
                    "event_type": getattr(e, "event_type", None) or "other",
                    "venues": list(getattr(e, "venues", None) or []),
                    "novelty": float(getattr(e, "novelty", 1.0) or 1.0),
                    "source_reliability": float(getattr(e, "source_reliability", 0.0) or 0.0),
                    "prior_move_proxy": float(getattr(e, "prior_move_proxy", 0.0) or 0.0),
                    "market_confirmation": float(getattr(e, "market_confirmation", 0.0) or 0.0),
                    "engine_action": getattr(e, "engine_action", None) or "",
                    "blackout_start": (
                        e.blackout_start.isoformat()
                        if getattr(e, "blackout_start", None)
                        else None
                    ),
                    "blackout_until": (
                        e.blackout_until.isoformat()
                        if getattr(e, "blackout_until", None)
                        else None
                    ),
                    "occurs_at": (
                        e.occurs_at.isoformat() if getattr(e, "occurs_at", None) else None
                    ),
                    "raw": dict(getattr(e, "raw", None) or {}),
                }
                event_type = payload["event_type"]
                novelty = payload["novelty"]
                venues = payload["venues"]
                occurs_at = getattr(e, "occurs_at", None)
                blackout_until = getattr(e, "blackout_until", None)
                if existing:
                    existing.title = e.title
                    existing.tier = e.tier
                    existing.score = e.score
                    existing.payload = payload
                    existing.source = e.source
                    existing.event_type = event_type
                    existing.novelty = novelty
                    existing.venues = venues
                    existing.occurs_at = occurs_at
                    existing.blackout_until = blackout_until
                    existing.published_at = e.published_at or existing.published_at
                else:
                    s.add(
                        EventRow(
                            event_id=e.event_id,
                            title=e.title,
                            source=e.source,
                            tier=e.tier,
                            score=e.score,
                            event_type=event_type,
                            novelty=novelty,
                            venues=venues,
                            occurs_at=occurs_at,
                            blackout_until=blackout_until,
                            payload=payload,
                            published_at=e.published_at,
                        )
                    )
                    n += 1
            s.commit()
        return n

    def audit_flag(self, name: str, previous: bool, value: bool, actor: str) -> int:
        with get_session() as s:
            row = FeatureFlagAudit(
                name=name,
                previous=previous,
                value=value,
                actor=actor,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return int(row.id)
