"""Path-shape meter for an Indian cash session.

Two windows, both visible:

- Dreiss chop on 14 five-minute bars. Buckets follow the cash clock, which
  already lands on 09:15 because that open is a multiple of five minutes.
- Path efficiency on the last 30 one-minute closes: net move divided by the
  path. VWAP crosses in that same window are counted and shown.

The book-balance sign is not an input. A high reading means the path is boxed.
It does not recommend a fade, a condor, or a breakout.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

CHOP_BARS = 14
EFF_BARS = 30
EFF_MIN_BARS = 15
CHOP_HIGH = 61.8
CHOP_LOW = 38.2
EFF_CHOPPY = 0.30
EFF_DIRECTIONAL = 0.60

IST = timezone(timedelta(hours=5, minutes=30))
_OPEN_MIN = 9 * 60 + 15
_CLOSE_MIN = 15 * 60 + 30


def _get(bar: Any, key: str):
    if isinstance(bar, dict):
        return bar.get(key)
    return getattr(bar, key, None)


def _incomplete(bar: Any) -> bool:
    return bool(_get(bar, "incomplete"))


def _f(bar: Any, key: str) -> float | None:
    raw = _get(bar, key)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _stamp(bar: Any) -> datetime | None:
    raw = _get(bar, "start_ts")
    if raw is None:
        raw = _get(bar, "time")
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _in_cash_session(dt: datetime) -> bool:
    mins = dt.hour * 60 + dt.minute
    return _OPEN_MIN <= mins <= _CLOSE_MIN


def _usable(bars: list[Any] | None) -> list[Any]:
    out = []
    for bar in bars or []:
        if _incomplete(bar):
            continue
        if _f(bar, "high") is None or _f(bar, "low") is None or _f(bar, "close") is None:
            continue
        out.append(bar)
    return out


def resample_session_5m(bars_1m: list[Any] | None) -> list[dict]:
    """Five-minute OHLC from one-minute bars, cash session only.

    A bar with no clock is dropped. Buckets are minute//5, the same grid the
    sentinel aggregator uses, which matches the 09:15 open.
    """
    groups: dict[datetime, list[Any]] = {}
    for bar in _usable(bars_1m):
        dt = _stamp(bar)
        if dt is None or not _in_cash_session(dt):
            continue
        bucket_min = (dt.minute // 5) * 5
        key = dt.replace(minute=bucket_min, second=0, microsecond=0)
        groups.setdefault(key, []).append(bar)
    out = []
    for key in sorted(groups):
        chunk = groups[key]
        out.append({
            "start_ts": key,
            "open": _f(chunk[0], "open") if _f(chunk[0], "open") is not None else _f(chunk[0], "close"),
            "high": max(_f(b, "high") for b in chunk),
            "low": min(_f(b, "low") for b in chunk),
            "close": _f(chunk[-1], "close"),
            "vwap": _f(chunk[-1], "vwap"),
            "incomplete": False,
        })
    return out


def dreiss_chop(bars: list[Any] | None, n: int = CHOP_BARS) -> float | None:
    """Dreiss chop. Needs n true ranges, so n+1 bars. A zero range is 100."""
    usable = _usable(bars)
    if len(usable) < n + 1:
        return None
    window = usable[-(n + 1):]
    prev_close = _f(window[0], "close")
    chosen = window[1:]
    trs = []
    highs = []
    lows = []
    for bar in chosen:
        high = _f(bar, "high")
        low = _f(bar, "low")
        close = _f(bar, "close")
        if high is None or low is None or close is None or prev_close is None:
            return None
        if high < low:
            return None
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        highs.append(high)
        lows.append(low)
        prev_close = close
    span = max(highs) - min(lows)
    total = sum(trs)
    if span <= 0:
        return 100.0 if total == 0 else None
    if total <= 0 or n <= 1:
        return None
    return 100.0 * math.log10(total / span) / math.log10(n)


def path_efficiency(bars: list[Any] | None, n: int = EFF_BARS, min_bars: int = EFF_MIN_BARS) -> tuple[float | None, int]:
    """Net close-to-close move divided by the sum of absolute steps."""
    closes = [_f(b, "close") for b in _usable(bars)]
    closes = [c for c in closes if c is not None]
    if len(closes) < min_bars:
        return None, len(closes)
    use = closes[-n:]
    path = sum(abs(use[i] - use[i - 1]) for i in range(1, len(use)))
    net = abs(use[-1] - use[0])
    if path == 0:
        return (0.0 if net == 0 else None), len(use)
    return net / path, len(use)


def vwap_crosses(bars: list[Any] | None, n: int = EFF_BARS, min_bars: int = EFF_MIN_BARS) -> int | None:
    """How often the close changed sides of the bar's VWAP."""
    rows = []
    for bar in _usable(bars):
        close = _f(bar, "close")
        vwap = _f(bar, "vwap")
        if close is None or vwap is None or vwap <= 0:
            continue
        rows.append((close, vwap))
    if len(rows) < min_bars:
        return None
    use = rows[-n:]
    sides = []
    for close, vwap in use:
        gap = close - vwap
        if gap > 0:
            sides.append(1)
        elif gap < 0:
            sides.append(-1)
    crosses = 0
    for prev, cur in zip(sides, sides[1:]):
        if prev != cur:
            crosses += 1
    return crosses


def _state(chop: float | None, efficiency: float | None) -> str:
    if chop is None and efficiency is None:
        return "not_ready"
    chop_hi = chop is not None and chop > CHOP_HIGH
    chop_lo = chop is not None and chop < CHOP_LOW
    eff_lo = efficiency is not None and efficiency < EFF_CHOPPY
    eff_hi = efficiency is not None and efficiency > EFF_DIRECTIONAL
    if (chop_hi and eff_hi) or (chop_lo and eff_lo):
        return "mixed"
    if eff_lo and (chop is None or chop_hi):
        return "choppy"
    if chop_hi and efficiency is None:
        return "choppy"
    if eff_hi and (chop is None or not chop_hi):
        return "directional"
    if chop_lo and (efficiency is None or not eff_lo):
        return "directional"
    return "mixed"


def _headline(state: str) -> str:
    return {
        "choppy": "Choppy",
        "directional": "Directional",
        "mixed": "Mixed",
        "not_ready": "Not ready",
    }.get(state, "Not ready")


def _line(state: str, chop: float | None, efficiency: float | None, crosses: int | None,
          n_5m: int, n_1m: int) -> str:
    parts = []
    if chop is None:
        parts.append(f"Five-minute chop needs {CHOP_BARS + 1} bars and has {n_5m}.")
    else:
        parts.append(f"Five-minute chop {chop:.1f} over {CHOP_BARS} bars.")
    if efficiency is None:
        parts.append(f"One-minute path efficiency needs {EFF_MIN_BARS} bars and has {n_1m}.")
    else:
        parts.append(f"Path efficiency {efficiency:.2f} over {n_1m} one-minute bars.")
    if crosses is None:
        parts.append("VWAP crosses unavailable.")
    else:
        parts.append(f"{crosses} VWAP crosses.")
    if state == "choppy":
        parts.append("The path is boxed.")
    elif state == "directional":
        parts.append("The path is a push.")
    elif state == "mixed" and chop is not None and efficiency is not None:
        parts.append("The windows do not share one shape.")
    return " ".join(parts)


def compute_choppiness_meter(bars_1m: list[Any] | None, bars_5m: list[Any] | None = None) -> dict:
    """Chop, path efficiency, and VWAP crosses. No gamma sign, no trade score."""
    five = _usable(bars_5m) if bars_5m else resample_session_5m(bars_1m)
    one = _usable(bars_1m)
    chop = dreiss_chop(five, CHOP_BARS)
    efficiency, n_1m = path_efficiency(one, EFF_BARS, EFF_MIN_BARS)
    crosses = vwap_crosses(one, EFF_BARS, EFF_MIN_BARS)
    state = _state(chop, efficiency)
    return {
        "state": state,
        "headline": _headline(state),
        "line": _line(state, chop, efficiency, crosses, len(five), n_1m),
        "chop": None if chop is None else round(chop, 1),
        "efficiency": None if efficiency is None else round(efficiency, 2),
        "vwap_crosses": crosses,
        "n_5m": len(five),
        "n_1m": n_1m,
    }
