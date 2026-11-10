"""15-minute signal digest for the Decisive GO strip.

Rolls Sentinel signals, GO emits, and commentary bites into one summary so the
raw flood can stay collapsed. Descriptive only — not a second GO engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


_IST = timezone(timedelta(hours=5, minutes=30))


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # epoch seconds
        try:
            return datetime.fromtimestamp(float(raw), tz=_IST)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=_IST)
        return raw.astimezone(_IST)
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=_IST)
        return dt.astimezone(_IST)
    except ValueError:
        return None


def build_signal_digest(
    *,
    go_events: list[dict[str, Any]] | None = None,
    sentinel_signals: list[dict[str, Any]] | None = None,
    commentary: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    window_minutes: int = 15,
) -> dict[str, Any]:
    """Roll recent streams into a single 15m digest.

    Returns headline counts, bias lean, top bites, and a one-line summary.
    """
    now_ist = now or datetime.now(_IST)
    if now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=_IST)
    else:
        now_ist = now_ist.astimezone(_IST)
    cutoff = now_ist - timedelta(minutes=max(1, int(window_minutes)))

    fires = scouts = holds = stages = skips = 0
    go_bites: list[str] = []
    for ev in go_events or []:
        ts = _parse_ts(ev.get("ts") or ev.get("time"))
        if ts is None or ts < cutoff:
            continue
        et = str(ev.get("event_type") or "").upper()
        st = str(ev.get("state") or "").upper()
        if et == "EMIT":
            if st == "FIRE":
                fires += 1
            elif st == "SCOUT":
                scouts += 1
            else:
                holds += 1
            sk = ev.get("strategy_key") or "GO"
            go_bites.append(f"{st} {sk}")
        elif et == "STAGE":
            stages += 1
        elif et == "SKIP":
            skips += 1

    bull = bear = 0
    sig_bites: list[str] = []
    for s in sentinel_signals or []:
        ts = _parse_ts(s.get("ts") or s.get("time") or s.get("bar_start_ts"))
        if ts is None or ts < cutoff:
            continue
        bias = str(s.get("bias") or s.get("category") or "").upper()
        if "LONG" in bias or "BULL" in bias:
            bull += 1
        elif "SHORT" in bias or "BEAR" in bias:
            bear += 1
        label = s.get("type") or s.get("insight_text") or "signal"
        sig_bites.append(str(label)[:48])

    commentary_n = 0
    com_bites: list[str] = []
    for c in commentary or []:
        ts = _parse_ts(c.get("ts") or c.get("created_at") or c.get("time"))
        if ts is None or ts < cutoff:
            continue
        commentary_n += 1
        text = c.get("headline") or c.get("text") or c.get("topic") or c.get("body")
        if text:
            com_bites.append(str(text)[:64])

    if bull > bear:
        lean = "tape lean LONG"
    elif bear > bull:
        lean = "tape lean SHORT"
    elif bull or bear:
        lean = "tape mixed"
    else:
        lean = "tape quiet"

    parts = []
    if fires or scouts or holds:
        parts.append(f"GO {fires}F/{scouts}S/{holds}H")
    if stages or skips:
        parts.append(f"stage {stages} · skip {skips}")
    if bull or bear:
        parts.append(f"sentinel {bull}↑/{bear}↓")
    if commentary_n:
        parts.append(f"commentary {commentary_n}")
    active = bool(parts)
    if active:
        parts.append(lean)
        summary = " · ".join(parts)
    else:
        summary = f"Quiet last {window_minutes}m"

    bites = (go_bites[:2] + sig_bites[:2] + com_bites[:1])[:5]
    return {
        "window_minutes": int(window_minutes),
        "as_of": now_ist.isoformat(),
        "summary": summary,
        "counts": {
            "go_fire": fires,
            "go_scout": scouts,
            "go_hold": holds,
            "go_stage": stages,
            "go_skip": skips,
            "sentinel_bull": bull,
            "sentinel_bear": bear,
            "commentary": commentary_n,
        },
        "lean": lean,
        "bites": bites,
        "raw_collapsed_hint": True,
    }
