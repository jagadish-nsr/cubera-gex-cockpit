"""Expiry-structure regime eras for India index options (for regime-split analysis).

Two SEBI/NSE structural breaks split the ~5-year sample into three eras — pooling a
gamma-blast / expiry-day backtest across them is contaminating, because both the set of
live weekly venues AND the weekend-theta profile of expiry day shift mid-sample:

  • 2024-11-20  SEBI single-weekly-per-exchange: NSE kept the **Nifty** weekly; Bank Nifty,
                FinNifty and Midcap weeklies were discontinued (monthly-only afterwards).
  • 2025-09-01  NSE moved the **Nifty** weekly expiry from **Thursday → Tuesday** (BSE Sensex
                stays Thursday) — a different weekend-decay profile going into expiry.

The dates are the effective boundaries; era membership is by calendar date (robust to the
weekday shift, so studies that flag expiry via dte≤1.5 just tag each day's era). Verified
against config.py / data/expiry.py. Single source of truth — never hard-code these dates in
the analysis scripts. Decision-support only. See docs/gamma-blast-research.md.
"""

from __future__ import annotations

from datetime import date, datetime

# Effective structural-break dates (inclusive lower bound of the new era).
SEBI_SINGLE_WEEKLY = date(2024, 11, 20)      # BankNifty/FinNifty/Midcap weeklies discontinued
NIFTY_WEEKLY_TO_TUESDAY = date(2025, 9, 1)   # Nifty weekly Thursday -> Tuesday

ERAS = ("pre-Nov24", "Nov24-Aug25", "Sep25+")   # chronological
ERA_LABELS = {
    "pre-Nov24":   "pre-20Nov24 · multi-weekly, Nifty Thu",
    "Nov24-Aug25": "20Nov24-Aug25 · single-weekly, Nifty Thu",
    "Sep25+":      "1Sep25-> · single-weekly, Nifty Tue",
}


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def expiry_era(d) -> str:
    """Era key for a calendar date (accepts 'YYYY-MM-DD' str, date, or datetime)."""
    dd = _as_date(d)
    if dd < SEBI_SINGLE_WEEKLY:
        return "pre-Nov24"
    if dd < NIFTY_WEEKLY_TO_TUESDAY:
        return "Nov24-Aug25"
    return "Sep25+"


def era_order(keys=None) -> list[str]:
    """Known eras (optionally restricted to those present in ``keys``), chronological."""
    ks = set(keys) if keys is not None else set(ERAS)
    return [e for e in ERAS if e in ks]
