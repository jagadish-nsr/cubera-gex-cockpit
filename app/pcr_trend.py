"""PCR Trend Analytics — data loading, resampling, and chart building.

NO Streamlit import here — keeps the module fully unit-testable offline.
The dashboard calls these helpers and handles UI rendering.

Data sources used:
  - data/journal/timeseries/<SYMBOL>/YYYY-MM-DD.csv
      → pcr_oi (full-chain put/call OI ratio) already captured per snapshot
  - data/journal/oi_strikes/<date>.csv  (via app/timeseries.load_strikes_day)
      → call_oi / put_oi per strike → near-ATM PCR proxy derived by summing
        the 20 closest-to-ATM strikes around spot per snapshot.

India-specific interpretation (surfaced in the dashboard):
  High PCR (> 1.2) = heavy put WRITING = bullish structural support.
  Low  PCR (< 0.8) = heavy call WRITING = bearish structural resistance.
  OPPOSITE of the US fear-gauge convention.
  Source: NSE literature (MDPI Economies, 2001-2013); sign-free, exploratory.

Decision-support only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

try:
    import altair as alt
    _HAS_ALTAIR = True
except ImportError:
    alt = None  # type: ignore[assignment]
    _HAS_ALTAIR = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTERVALS = [5, 15, 45]
_BULLISH_PCR = 1.2
_BEARISH_PCR = 0.8

_INTERVAL_COLOURS = {
    5:  "#60a5fa",   # blue-400
    15: "#a78bfa",   # violet-400
    45: "#f472b6",   # pink-400
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ist_today_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_pcr_today(
    symbol: str,
    live_points: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """Load today's PCR time-series for *symbol*.

    Merges on-disk captured rows with any live points from this session.
    Returns DataFrame with: timestamp_utc, time_ist, date_ist, spot,
    pcr_oi, pcr_near.  Empty DataFrame (with those columns) if no data.
    """
    from app.timeseries import load_day

    _COLS = ["timestamp_utc", "time_ist", "date_ist", "spot", "pcr_oi", "pcr_near"]
    ist_today = _ist_today_str()

    disk = load_day(ist_today, symbol)

    sess_rows: list[dict] = []
    if live_points:
        for r in live_points.values():
            if r.get("date_ist") == ist_today:
                sess_rows.append(r)
    sess = pd.DataFrame(sess_rows) if sess_rows else pd.DataFrame()

    frames = [f for f in (disk, sess) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=_COLS)

    df = (pd.concat(frames, ignore_index=True)
          .drop_duplicates(subset="timestamp_utc")
          .sort_values("timestamp_utc")
          .reset_index(drop=True))

    if "pcr_oi" not in df.columns:
        df["pcr_oi"] = float("nan")
    df["pcr_oi"] = pd.to_numeric(df["pcr_oi"], errors="coerce")

    df["pcr_near"] = _derive_near_atm_pcr(df, ist_today, symbol)

    for c in ("spot", "time_ist", "date_ist"):
        if c not in df.columns:
            df[c] = None

    # Return all columns but ensure required ones are first
    extra = [c for c in df.columns if c not in _COLS]
    return df[_COLS + extra]


def _derive_near_atm_pcr(ts_df: pd.DataFrame, date_ist: str, symbol: str) -> pd.Series:
    """Derive per-snapshot near-ATM PCR from oi_strikes CSV.

    For each snapshot time_ist, sums call_oi + put_oi for the 20 strikes
    closest to spot. Returns Series aligned to ts_df.index (all NaN on failure).
    """
    try:
        from app.timeseries import load_strikes_day

        sk = load_strikes_day(date_ist, symbol=symbol)
        if sk.empty or "call_oi" not in sk.columns or "put_oi" not in sk.columns:
            return pd.Series(float("nan"), index=ts_df.index)

        sk["call_oi"] = pd.to_numeric(sk["call_oi"], errors="coerce").fillna(0.0)
        sk["put_oi"]  = pd.to_numeric(sk["put_oi"],  errors="coerce").fillna(0.0)
        sk["strike"]  = pd.to_numeric(sk["strike"],  errors="coerce")

        result = pd.Series(float("nan"), index=ts_df.index)

        for idx, row in ts_df.iterrows():
            t    = row.get("time_ist", "")
            spot = float(row.get("spot", 0) or 0)
            if spot <= 0 or not t:
                continue
            snap_sk = sk[sk["time_ist"] == t].dropna(subset=["strike"])
            if snap_sk.empty:
                continue
            near  = snap_sk.iloc[(snap_sk["strike"] - spot).abs().argsort()[:20]]
            c_oi  = near["call_oi"].sum()
            p_oi  = near["put_oi"].sum()
            if c_oi > 0:
                result.at[idx] = p_oi / c_oi

        return result

    except Exception:  # noqa: BLE001
        return pd.Series(float("nan"), index=ts_df.index)


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def resample_pcr(df: pd.DataFrame, interval_min: int) -> pd.DataFrame:
    """Resample snapshot-level PCR to ``interval_min``-minute buckets (last value).

    Returns DataFrame with: bucket_label (HH:MM str), pcr_oi, pcr_near,
    spot, time_ist, date_ist.
    """
    if df.empty:
        return pd.DataFrame(columns=["bucket_label", "pcr_oi", "pcr_near",
                                     "spot", "time_ist", "date_ist"])

    work = df.copy()
    work["_ts"] = pd.to_datetime(work["timestamp_utc"], utc=True, errors="coerce")
    work = work.dropna(subset=["_ts"]).sort_values("_ts").reset_index(drop=True)

    if interval_min <= 5:
        work["bucket_label"] = work["time_ist"].fillna(
            work["_ts"].dt.strftime("%H:%M"))
    else:
        work["bucket_label"] = work["_ts"].dt.floor(f"{interval_min}min").dt.strftime("%H:%M")

    agg = (work.groupby("bucket_label", sort=False)
           .agg(
               pcr_oi   =("pcr_oi",   "last"),
               pcr_near =("pcr_near", "last"),
               spot     =("spot",     "last"),
               time_ist =("time_ist", "last"),
               date_ist =("date_ist", "last"),
           )
           .reset_index()
           .sort_values("bucket_label")
           .reset_index(drop=True))
    return agg


# ---------------------------------------------------------------------------
# Chart building
# ---------------------------------------------------------------------------

def build_pcr_chart(
    df_raw: pd.DataFrame,
    intervals: list[int] | None = None,
    show_full_chain: bool = True,
    show_near_atm: bool = True,
    show_bands: bool = True,
    chart_height: int = 340,
) -> Any:
    """Build an Altair layered PCR trend chart.

    Solid lines = full-chain PCR  (pcr_oi)
    Dashed lines = near-ATM proxy  (pcr_near)
    Colour = interval bucket (blue=5m, violet=15m, pink=45m)
    """
    if not _HAS_ALTAIR:
        return None

    if df_raw.empty:
        return None

    intervals = intervals or INTERVALS
    layers: list[Any] = []

    # --- Reference bands ---------------------------------------------------
    if show_bands:
        for val, label, colour in [
            (_BULLISH_PCR, f"Put-writing support (PCR {_BULLISH_PCR})", "#22c55e"),
            (_BEARISH_PCR, f"Call-writing resistance (PCR {_BEARISH_PCR})", "#ef4444"),
        ]:
            band_df = pd.DataFrame({"pcr": [val], "label": [label]})
            layers.append(
                alt.Chart(band_df)
                .mark_rule(strokeDash=[6, 3], color=colour, opacity=0.50, strokeWidth=1.5)
                .encode(y=alt.Y("pcr:Q"), tooltip=alt.value(label))
            )

    # --- Detect near-ATM availability -------------------------------------
    has_near = (
        show_near_atm
        and "pcr_near" in df_raw.columns
        and df_raw["pcr_near"].notna().sum() > max(2, len(df_raw) * 0.3)
    )

    # --- Lines per interval -----------------------------------------------
    for i, iv in enumerate(intervals):
        rs = resample_pcr(df_raw, iv)
        if rs.empty:
            continue
        colour = _INTERVAL_COLOURS.get(iv, "#94a3b8")
        sw = 1.5 + i * 0.8   # stroke width increases with interval length

        # Full-chain (solid)
        if show_full_chain:
            fc = rs.dropna(subset=["pcr_oi"])
            if not fc.empty:
                layers.append(
                    alt.Chart(fc)
                    .mark_line(point=alt.OverlayMarkDef(size=30),
                               color=colour, strokeWidth=sw, opacity=0.90)
                    .encode(
                        x=alt.X("bucket_label:N", title="Time (IST)", sort="ascending"),
                        y=alt.Y("pcr_oi:Q", title="PCR (OI)",
                                scale=alt.Scale(zero=False)),
                        tooltip=[
                            alt.Tooltip("bucket_label:N", title="Time IST"),
                            alt.Tooltip("pcr_oi:Q", title=f"PCR {iv}m full-chain", format=".3f"),
                            alt.Tooltip("spot:Q", title="Spot", format=",.0f"),
                        ],
                    )
                )

        # Near-ATM proxy (dashed)
        if has_near:
            nr = rs.dropna(subset=["pcr_near"])
            if not nr.empty:
                layers.append(
                    alt.Chart(nr)
                    .mark_line(color=colour, strokeWidth=sw,
                               strokeDash=[5, 3], opacity=0.60)
                    .encode(
                        x=alt.X("bucket_label:N", sort="ascending"),
                        y=alt.Y("pcr_near:Q", scale=alt.Scale(zero=False)),
                        tooltip=[
                            alt.Tooltip("bucket_label:N", title="Time IST"),
                            alt.Tooltip("pcr_near:Q",
                                        title=f"PCR {iv}m near ATM", format=".3f"),
                        ],
                    )
                )

    if not layers:
        return None

    return (
        alt.layer(*layers)
        .properties(height=chart_height)
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True, gridColor="#1e293b", gridOpacity=0.5,
            labelColor="#94a3b8", titleColor="#94a3b8",
        )
    )


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def pcr_summary(df_raw: pd.DataFrame) -> dict:
    """Compute session summary stats from the raw PCR DataFrame."""
    out: dict = {
        "current_pcr": None, "session_high": None, "session_low": None,
        "session_open": None, "delta_from_open": None,
        "has_near_data": False, "n_points": 0, "last_time": None,
    }
    if df_raw.empty or "pcr_oi" not in df_raw.columns:
        return out

    pcr = pd.to_numeric(df_raw["pcr_oi"], errors="coerce").dropna()
    if pcr.empty:
        return out

    out["n_points"]       = int(len(pcr))
    out["current_pcr"]    = float(pcr.iloc[-1])
    out["session_high"]   = float(pcr.max())
    out["session_low"]    = float(pcr.min())
    out["session_open"]   = float(pcr.iloc[0])
    out["delta_from_open"] = out["current_pcr"] - out["session_open"]

    if "time_ist" in df_raw.columns and not df_raw.empty:
        out["last_time"] = str(df_raw["time_ist"].iloc[-1])

    if "pcr_near" in df_raw.columns:
        near = pd.to_numeric(df_raw["pcr_near"], errors="coerce").dropna()
        out["has_near_data"] = int(len(near)) > max(2, int(len(pcr)) * 0.3)

    return out


def pcr_bias_label(pcr: float | None) -> tuple[str, str]:
    """(label, hex-colour) for the current PCR reading.

    India convention: high PCR = put-writing = bullish support.
    """
    if pcr is None:
        return "—", "#64748b"
    if pcr >= _BULLISH_PCR:
        return f"PUT-WRITING SUPPORT ({pcr:.2f})", "#22c55e"
    if pcr <= _BEARISH_PCR:
        return f"CALL-WRITING RESISTANCE ({pcr:.2f})", "#ef4444"
    return f"NEUTRAL ({pcr:.2f})", "#f59e0b"
