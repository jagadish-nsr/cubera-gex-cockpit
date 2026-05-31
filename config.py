"""Named, explicit configuration for the Nifty GEX engine.

Same discipline as the SPX project: anything that changes a number the user might
trade on lives here as a named constant and is echoed onto every output. India-
specific values are marked VERIFY — they must be checked live, never assumed from
memory (NSE changes lot sizes and expiry rules by circular).
"""

from __future__ import annotations

# --- Dealer gamma sign convention -------------------------------------------
# ⚠️ UNVERIFIED FOR INDIA. The long-call/short-put-gamma convention is the US
# index-dealer read. India's retail-option-SELLING microstructure may invert or
# distort it. This default is a PLACEHOLDER to be re-validated empirically before
# any Nifty output is trusted (see docs/nifty-nse-adapter-scope.md §6).
DEALER_SIGN_MODEL = "long_call_short_put"  # VERIFY for Nifty before trusting

# --- Contract multiplier (Nifty lot size) -----------------------------------
# ⚠️ VERIFY LIVE. NSE revises the Nifty lot size by circular (recently raised to
# keep contract value in a target band). This scales EVERY GEX number.
NIFTY_LOT_SIZE = 75  # VERIFY against the current NSE circular
CONTRACT_SIZE = NIFTY_LOT_SIZE

# --- Expiry window ----------------------------------------------------------
# Allowed: "0dte" | "weekly" | "monthly" | "all" | "0dte_to_next_monthly"
EXPIRY_WINDOW_DEFAULT = "0dte_to_next_monthly"

# --- Gamma-flip profile grid ------------------------------------------------
FLIP_GRID_RANGE = 0.10   # +/- 10% around spot
FLIP_GRID_STEP = 0.0025  # 0.25% steps

# --- OI staleness -----------------------------------------------------------
OI_STALE_HOURS = 30  # NSE OI is end-of-day lagging; flag beyond ~1 trading day

# --- Market / timezone ------------------------------------------------------
MARKET_TZ = "Asia/Kolkata"   # NSE session 09:15–15:30 IST
UNDERLYING = "NIFTY"

# --- Risk-free / dividend sourcing ------------------------------------------
# r and q are IMPLIED per-expiry from put-call parity off the same chain — no
# Indian rate feed (MIBOR/T-bill) or dividend assumption needed.
RISK_FREE_FALLBACK = None
