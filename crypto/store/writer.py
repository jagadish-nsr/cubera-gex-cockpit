"""Append write path for ticks / features / signals / events / flag audit."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

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

    def write_gex(self, feature: dict[str, Any]) -> int:
        computed_at = feature.get("computed_at") or utcnow()
        if isinstance(computed_at, datetime):
            pass
        by_strike = feature.get("by_strike") or {}
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

    def write_orderflow(self, perp: PerpSnapshot) -> int:
        payload = {
            "mark": perp.mark,
            "index": perp.index,
        }
        self.write_tick(perp.venue, "perp", payload, exchange_ts=perp.fetched_at)
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

    def write_signal(self, card: dict[str, Any]) -> int:
        created_at = card.get("created_at") or utcnow()
        valid_until = card.get("valid_until")
        # JSON column cannot hold raw datetime — serialize for storage
        card_json = {
            k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in card.items()
        }
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
                }
                if existing:
                    existing.title = e.title
                    existing.tier = e.tier
                    existing.score = e.score
                    existing.payload = payload
                    existing.source = e.source
                else:
                    s.add(
                        EventRow(
                            event_id=e.event_id,
                            title=e.title,
                            source=e.source,
                            tier=e.tier,
                            score=e.score,
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
