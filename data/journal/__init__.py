"""Paper-trade journal package.

Modules:
  store     — SQLite ledger (paper_trades table, WAL mode, single-writer).
  pnl       — Mark-to-market and realised P&L net of costs.
  scorecard — Aggregate descriptive stats (never auto-tuning).
"""
