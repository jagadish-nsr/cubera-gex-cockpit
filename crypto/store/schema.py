"""SQLAlchemy models for CryptoCubera durable storage.

Schema is evolvable; start with SQLite locally, migrate URL to Postgres later.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RawTick(Base):
    __tablename__ = "raw_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    recv_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    exchange_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OptionChainRow(Base):
    __tablename__ = "option_chain_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(64))
    spot: Mapped[float] = mapped_column(Float)
    snapshot: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GexFeature(Base):
    __tablename__ = "gex_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    spot: Mapped[float] = mapped_column(Float)
    flip: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime: Mapped[str] = mapped_column(String(32))
    convention_sign: Mapped[str] = mapped_column(String(16))
    flow_sign: Mapped[str] = mapped_column(String(16))
    models_agree: Mapped[bool] = mapped_column(Boolean)
    by_strike: Mapped[dict] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64))


class OrderflowFeature(Base):
    __tablename__ = "orderflow_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    venue: Mapped[str] = mapped_column(String(64))
    cvd_1m: Mapped[float] = mapped_column(Float)
    cvd_5m: Mapped[float] = mapped_column(Float)
    cvd_15m: Mapped[float] = mapped_column(Float)
    book_imbalance: Mapped[float] = mapped_column(Float)
    funding_rate: Mapped[float] = mapped_column(Float)
    open_interest: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(8), index=True)  # GO | NO-GO
    setup: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    score: Mapped[int] = mapped_column(Integer)
    card: Mapped[dict] = mapped_column(JSON)
    veto_reasons: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(64), default="rules-v0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float] = mapped_column(Float)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    novelty: Mapped[float | None] = mapped_column(Float, nullable=True)
    venues: Mapped[list | None] = mapped_column(JSON, nullable=True)
    occurs_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blackout_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutcomeRow(Base):
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    barrier: Mapped[str] = mapped_column(String(16))  # target | stop | expiry
    hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class FeatureFlagAudit(Base):
    __tablename__ = "feature_flag_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    previous: Mapped[bool] = mapped_column(Boolean)
    value: Mapped[bool] = mapped_column(Boolean)
    actor: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperSessionRow(Base):
    """Local paper / advisory recording session window (start→end).

    Feed ticks, GEX/orderflow features, and signals continue to land in their
    own tables; this row bounds a reviewable session for OOS summary export.
    Never enables live execution.
    """

    __tablename__ = "paper_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True, default="advisory")
    underlying: Mapped[str] = mapped_column(String(16), default="BTC")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    db_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaperTradeRow(Base):
    """Paper / demo trade ledger — never live capital fills."""

    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    venue: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(String(32))  # sim | demo
    status: Mapped[str] = mapped_column(String(32), index=True)
    instrument: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    setup: Mapped[str] = mapped_column(String(64), default="")
    entry: Mapped[float] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_usd: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    signal_status: Mapped[str] = mapped_column(String(8), default="GO")
    mode: Mapped[str] = mapped_column(String(32), default="paper_confirm")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
