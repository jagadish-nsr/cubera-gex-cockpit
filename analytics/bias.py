"""analytics/bias.py — Core positioning-divergence analytics.

Implements the bias_score formula from the positioning-divergence spec (§3.1–3.2):

    net_fut_index(c)  = FutureIndexLong(c)  - FutureIndexShort(c)
    net_call_index(c) = OptionIndexCallLong(c) - OptionIndexCallShort(c)
    net_put_index(c)  = OptionIndexPutLong(c)  - OptionIndexPutShort(c)
    bias_score(c)     = net_fut_index + net_call_index - net_put_index

Net long puts is bearish so it's subtracted; net put-writing (negative net_put)
adds to a bullish score — the sign resolves automatically.

Separation guarantee: this module imports nothing from the network or Dhan/NSE
layers. All inputs are plain dicts, making every function unit-testable with
fixed sample data and no mocking required.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Configurable thresholds (single place — not scattered constants)
# ---------------------------------------------------------------------------

# Divergence score at which Client vs FII positioning is considered "large"
# (roughly 1–2σ of the historical distribution; tune after 30+ days of data).
LARGE_DIVERGENCE_THRESHOLD: int = 500_000

# Rolling-window trend is "widening" when the linear drift exceeds this per session
WIDENING_DRIFT_THRESHOLD: float = 100_000.0

# Default rolling windows for trend computation
WINDOWS = (5, 10, 20)


# ---------------------------------------------------------------------------
# §3.1 Category-level bias
# ---------------------------------------------------------------------------

def bias_score(v: dict) -> int:
    """Return bias_score for one participant row.

    v must contain the six index F&O fields as stored by parse_participant_oi:
      fut_long, fut_short, opt_call_long, opt_call_short, opt_put_long, opt_put_short.

    Formula (from the spec):
        net_fut   = fut_long      - fut_short
        net_call  = opt_call_long - opt_call_short
        net_put   = opt_put_long  - opt_put_short
        bias      = net_fut + net_call - net_put

    Returns 0 for any missing field (fail-soft).
    """
    def _i(key: str) -> int:
        val = v.get(key)
        try:
            return int(val) if val is not None else 0
        except (ValueError, TypeError):
            return 0

    net_fut  = _i("fut_long")      - _i("fut_short")
    net_call = _i("opt_call_long") - _i("opt_call_short")
    net_put  = _i("opt_put_long")  - _i("opt_put_short")
    return net_fut + net_call - net_put


def compute_category_biases(parsed: dict) -> dict:
    """Compute bias_score for all four categories and the headline divergence.

    parsed: the dict returned by parse_participant_oi (keys: Client, DII, FII, Pro, TOTAL).

    Returns:
    {
      "Client": int,  "DII": int,  "FII": int,  "Pro": int,
      "retail_bias": int,            # == Client bias
      "institutional_bias": int,     # == FII bias (smart-money proxy per spec)
      "divergence": int,             # == retail_bias - institutional_bias
      # per-component breakdown for display
      "breakdown": {
          "Client": {"net_fut": int, "net_call": int, "net_put": int, "bias": int},
          ...
      }
    }
    """
    if not parsed:
        return {}

    breakdown: dict = {}
    scores: dict = {}

    for who in ("Client", "DII", "FII", "Pro"):
        v = parsed.get(who) or {}

        def _i(key: str) -> int:
            val = v.get(key)
            try:
                return int(val) if val is not None else 0
            except (ValueError, TypeError):
                return 0

        net_fut  = _i("fut_long")      - _i("fut_short")
        net_call = _i("opt_call_long") - _i("opt_call_short")
        net_put  = _i("opt_put_long")  - _i("opt_put_short")
        bs = net_fut + net_call - net_put
        scores[who] = bs
        breakdown[who] = {"net_fut": net_fut, "net_call": net_call,
                          "net_put": net_put, "bias": bs}

    retail = scores.get("Client", 0)
    inst   = scores.get("FII",    0)
    return {
        **scores,
        "retail_bias":        retail,
        "institutional_bias": inst,
        "divergence":         retail - inst,
        "breakdown":          breakdown,
    }


# ---------------------------------------------------------------------------
# §3.2 Trend: rolling divergence history
# ---------------------------------------------------------------------------

def rolling_divergence(n: int = 20) -> list[dict]:
    """Load the last ``n`` captured participant-OI days and compute per-day biases.

    Returns a chronological list of dicts:
        {"date": "YYYY-MM-DD", "retail_bias": int, "institutional_bias": int,
         "divergence": int, "Client": int, "DII": int, "FII": int, "Pro": int}

    Reads from the flat journal (data/journal/participant_oi/) via the existing
    app.participant_oi helpers — no network calls.
    """
    from app.participant_oi import available_days, load_participant_day

    days = available_days()
    if not days:
        return []

    out: list[dict] = []
    for d in days[-n:]:
        parsed = load_participant_day(d)
        if not parsed:
            continue
        biases = compute_category_biases(parsed)
        if not biases:
            continue
        out.append({
            "date":                d,
            "retail_bias":        biases["retail_bias"],
            "institutional_bias": biases["institutional_bias"],
            "divergence":         biases["divergence"],
            "Client":             biases.get("Client", 0),
            "DII":                biases.get("DII",    0),
            "FII":                biases.get("FII",    0),
            "Pro":                biases.get("Pro",    0),
        })
    return out


def divergence_trend(series: list[dict], window: int) -> dict:
    """Compute the linear-drift trend of the divergence series over ``window`` sessions.

    Delegates to app.participant_oi.series_drift (which handles zero-crossing,
    short inputs, and NaNs). Returns the same drift dict:
        {"trend": "rising"|"falling"|"flat"|"unknown", "drift": float,
         "first": float, "last": float, "n": int}
    """
    from app.participant_oi import series_drift

    values = [r["divergence"] for r in series[-window:] if r.get("divergence") is not None]
    return series_drift(values)


# ---------------------------------------------------------------------------
# §3.4 Narrative: interpreted read
# ---------------------------------------------------------------------------

def interpret_divergence(
    divergence: int,
    retail_bias: int,
    institutional_bias: int,
    trend_5d: dict,
    trend_10d: dict | None = None,
) -> str:
    """Return a plain-language narrative for the positioning read.

    Examples from the spec:
    - "Retail crowded long, FII net short/hedged — caution on chasing longs, watch downside."
    - "Retail short, FII long — short-squeeze setup."
    - "No strong divergence — range-bound positioning read."
    """
    lines: list[str] = []

    # --- Direction read ---
    if divergence > LARGE_DIVERGENCE_THRESHOLD:
        retail_desc = "crowded long" if retail_bias > 0 else "building longs"
        fii_desc    = "net short / hedged" if institutional_bias < 0 else "reducing longs"
        lines.append(
            f"**Retail {retail_desc}, FII {fii_desc}** — "
            "caution on chasing longs; watch for downside squeeze or reversal."
        )
    elif divergence < -LARGE_DIVERGENCE_THRESHOLD:
        retail_desc = "crowded short" if retail_bias < 0 else "building shorts"
        fii_desc    = "net long" if institutional_bias > 0 else "covering shorts"
        lines.append(
            f"**Retail {retail_desc}, FII {fii_desc}** — "
            "short-squeeze setup; institutional longs could drive a sharp rally."
        )
    else:
        lines.append(
            "**No strong divergence** between retail and institutional positioning — "
            "range-bound read; no clear trapped-retail setup today."
        )

    # --- Trend read ---
    trend = trend_5d.get("trend", "unknown")
    drift = trend_5d.get("drift", 0.0)
    n5    = trend_5d.get("n", 0)
    if trend == "rising" and drift > WIDENING_DRIFT_THRESHOLD:
        lines.append(
            f"Divergence has been **widening** over the last {n5} sessions "
            f"(+{drift:,.0f} per session) — conviction is strengthening; "
            "a single-session reversal is less likely."
        )
    elif trend == "falling" and drift < -WIDENING_DRIFT_THRESHOLD:
        lines.append(
            f"Divergence is **narrowing** over the last {n5} sessions "
            f"({drift:+,.0f} per session) — positioning is converging; "
            "the setup is weakening."
        )

    if trend_10d:
        t10 = trend_10d.get("trend", "unknown")
        if t10 != trend and t10 != "unknown":
            lines.append(
                f"Note: 10-day trend is **{t10}** while 5-day is **{trend}** — "
                "short and medium-term signals diverge; treat with extra caution."
            )

    return "  \n".join(lines)
