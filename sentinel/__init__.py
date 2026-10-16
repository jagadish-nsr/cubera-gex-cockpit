"""sentinel — OrderFlow Sentinel v2.0 Microstructure & Signal System.

Subsystems:
  - models: Bar, SentinelSignal, SignalOutcome
  - feed: Dhan binary packet parser, tick structures
  - engine: BarAggregator, SessionVWAPTracker, MarketContext, compute_tod_rvol, SignalClassifierEngine, outcomes
  - storage: SQLite WAL database persistence
"""

from __future__ import annotations

from sentinel.models import Bar, SentinelSignal, SignalOutcome
from sentinel.feed.parser import parse_dhan_binary_packet, Tick
from sentinel.engine.aggregator import BarAggregator, SessionVWAPTracker
from sentinel.engine.rvol import compute_tod_rvol
from sentinel.engine.context import MarketContext
from sentinel.engine.classifier import SignalClassifierEngine
from sentinel.engine.outcomes import evaluate_signal_outcomes
from sentinel.storage.db import (
    ensure_sentinel_schema,
    save_bar,
    save_signal,
    list_recent_bars,
    list_recent_signals,
    list_daily_cvd,
    record_daily_closing_cvd,
    ensure_historical_daily_cvd,
)

__all__ = [
    "Bar",
    "SentinelSignal",
    "SignalOutcome",
    "parse_dhan_binary_packet",
    "Tick",
    "BarAggregator",
    "SessionVWAPTracker",
    "compute_tod_rvol",
    "MarketContext",
    "SignalClassifierEngine",
    "evaluate_signal_outcomes",
    "ensure_sentinel_schema",
    "save_bar",
    "save_signal",
    "list_recent_bars",
    "list_recent_signals",
    "list_daily_cvd",
    "record_daily_closing_cvd",
    "ensure_historical_daily_cvd",
]
