"""Named, explicit configuration for the Nifty GEX engine.

Same discipline as the SPX project: anything that changes a number the user might
trade on lives here as a named constant and is echoed onto every output. India-
specific values are marked VERIFY — they must be checked live, never assumed from
memory (NSE changes lot sizes and expiry rules by circular).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Tradable indices (the per-index landmines) -----------------------------
# Each index carries ONLY what differs between indices: the NSE option-chain
# symbol, the contract multiplier (lot size), whether the index still has WEEKLY
# expiries, and the per-strike OI band half-width. The pure GEX engine is
# index-agnostic and reused unchanged; only these knobs change.
#
# ⚠️ lot_size + has_weekly are "VERIFY LIVE, never guess" landmines (CLAUDE.md):
# NSE revises lot sizes and expiry rules by circular. The dealer-sign model is
# the SAME unvalidated landmine for BOTH indices (India retail-SELLING flow);
# Bank Nifty is, if anything, more retail-heavy — no extra trust there.
@dataclass(frozen=True)
class Index:
    symbol: str        # NSE option-chain symbol ("NIFTY" / "BANKNIFTY")
    label: str         # display name
    lot_size: int      # contract multiplier — VERIFY against the current NSE circular
    has_weekly: bool   # are weekly expiries served? (drives the expiry window/fetch)
    band_pts: float    # per-strike OI band: keep strikes within ± this of spot


# CONFIRMED 2026-07 (user-verified against broker):
#  - NIFTY: lot 65 (SEBI revision effective Jan-2026 onward, was 75); WEEKLY +
#    monthly, expiry Tuesday.
#  - BANKNIFTY: lot 30 (NSE circular NSE/FAOP/70616, eff. Jan-2026 cycle, was 35);
#    MONTHLY ONLY (last Tuesday) — NSE discontinued Bank Nifty weeklies on
#    20-Nov-2024 under SEBI's single-weekly-per-exchange rule. Bigger index
#    (~3000-pt strikes are 100 apart), so a wider OI band than Nifty.
INDICES: dict[str, Index] = {
    "NIFTY":     Index("NIFTY", "Nifty 50", 65, True, 2000),       # lot 65 (Jan-2026, was 75)
    "BANKNIFTY": Index("BANKNIFTY", "Bank Nifty", 30, False, 4000),  # lot 30, monthly-only
}
DEFAULT_INDEX = "NIFTY"


def get_index(symbol: str | None) -> Index:
    """Resolve a symbol to its Index (None -> the default). Fails loud on an
    unknown symbol rather than silently trading the wrong contract."""
    sym = (symbol or DEFAULT_INDEX).upper()
    try:
        return INDICES[sym]
    except KeyError:
        raise ValueError(
            f"unknown index {sym!r}; known: {sorted(INDICES)}"
        ) from None


# --- Dealer gamma sign convention -------------------------------------------
# ⚠️ UNVERIFIED FOR INDIA. The long-call/short-put-gamma convention is the US
# index-dealer read. India's retail-option-SELLING microstructure may invert or
# distort it. This default is a PLACEHOLDER to be re-validated empirically before
# any Nifty output is trusted (see docs/nifty-nse-adapter-scope.md §6).
DEALER_SIGN_MODEL = "long_call_short_put"  # VERIFY for Nifty before trusting

# --- Contract multiplier (default index lot size) ---------------------------
# ⚠️ VERIFY LIVE. NSE revises lot sizes by circular. This scales EVERY GEX number.
# Back-compat aliases for the default index; per-index code reads Index.lot_size
# off the INDICES registry above (the single source of truth).
NIFTY_LOT_SIZE = INDICES[DEFAULT_INDEX].lot_size  # VERIFY against the current NSE circular
CONTRACT_SIZE = NIFTY_LOT_SIZE

# --- Expiry window ----------------------------------------------------------
# Allowed: "0dte" | "weekly" | "monthly" | "all" | "0dte_to_next_monthly"
EXPIRY_WINDOW_DEFAULT = "0dte_to_next_monthly"

# --- Gamma-flip profile grid ------------------------------------------------
FLIP_GRID_RANGE = 0.10   # +/- 10% around spot
FLIP_GRID_STEP = 0.0025  # 0.25% steps

# --- Expiry weighting -------------------------------------------------------
# Near-dated gamma drives intraday action, so weight each contract's GEX by its
# days-to-expiry. Explicit + logged + tunable (gex/weighting.py). 'equal' = off.
#   'exp' with a 5-day half-life: a 5-DTE contract counts half a 0-DTE one.
EXPIRY_WEIGHTING = "exp"           # equal | exp | inverse | nearest
EXPIRY_WEIGHT_HALFLIFE_DAYS = 5.0  # only used by 'exp'

# --- OI staleness -----------------------------------------------------------
OI_STALE_HOURS = 30  # NSE OI is end-of-day lagging; flag beyond ~1 trading day

# --- Per-strike OI/ΔOI capture ----------------------------------------------
# Phase A also logs per-strike OI / ΔOI (to test whether price respects the
# strike where OI builds — the question aggregate ΔOI can't answer). To bound
# the file, only strikes within this many points of spot are kept (the walls
# always sit well inside this band; far-OTM strikes carry negligible OI).
# Back-compat alias for the default index (per-index code reads Index.band_pts).
OI_STRIKE_BAND_PTS = INDICES[DEFAULT_INDEX].band_pts

# --- Market / timezone ------------------------------------------------------
MARKET_TZ = "Asia/Kolkata"   # NSE session 09:15–15:30 IST
UNDERLYING = INDICES[DEFAULT_INDEX].symbol   # back-compat alias for the default index

# --- Risk-free / dividend sourcing ------------------------------------------
# r and q are IMPLIED per-expiry from put-call parity off the same chain — no
# Indian rate feed (MIBOR/T-bill) or dividend assumption needed.
RISK_FREE_FALLBACK = None
