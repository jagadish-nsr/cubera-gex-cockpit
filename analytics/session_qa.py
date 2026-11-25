"""Grounded Session Desk Q&A — FAQ briefs + optional local-LLM free-form ask.

Deterministic FAQ answers are the best path (exact stamped numbers). LLM may
polish FAQ prose or answer free-form questions from the same fact pack. Trader
confirms; no order staging from this module.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
SIGN_CONVENTION = "short_call_short_put"
CONFIRM_LINE = "Decision support only — you confirm; chat never stages orders."

# Three different refusals so the trader can tell what actually happened.
_FREEFORM_OFF = (
    "Open ask is off: CUBERA_LLM is not enabled on this server. "
    "Try an FAQ chip for a stamped brief from the session book. "
    + CONFIRM_LINE
)
_FREEFORM_NO_ANSWER = (
    "Open ask got no answer from the local model (timeout or model not loaded). "
    "Try an FAQ chip for a stamped brief from the session book. "
    + CONFIRM_LINE
)
_FREEFORM_UNGROUNDED = (
    "Open ask answered, but it cited a number that is not in the session pack, "
    "so the answer was withheld. Rephrase without new levels, or use an FAQ chip. "
    + CONFIRM_LINE
)

_QA_FORBIDDEN = [
    "sure shot",
    "guaranteed",
    "can't lose",
    "cant lose",
    "buy now",
    "sell now",
    "risk-free",
    "risk free",
    "easy money",
    "hurry",
    "last chance",
    "to the moon",
    "free money",
    "blind buy",
    "place order",
    "market order",
    "auto-exec",
    "auto exec",
    "auto-execution",
    "enter long",
    "enter short",
]

SESSION_FAQS: list[dict[str, Any]] = [
    {
        "id": "futures_positioning",
        "label": "Futures positioning?",
        "focus_keys": ["positioning", "futures_state"],
        "question": "What is index futures positioning saying — overnight and live price/OI?",
        "genai": True,
    },
    {
        "id": "price_move",
        "label": "How did price move?",
        "focus_keys": ["price_move", "spot", "call_wall", "put_wall", "gamma_flip"],
        "question": "How has price moved this session versus prior close and the walls?",
        "genai": True,
    },
    {
        "id": "alert_tape",
        "label": "Alert tape meaning?",
        "focus_keys": ["market_alert", "spot", "call_wall", "put_wall"],
        "question": "What does the cockpit alert tape mean for a trader right now?",
        "genai": True,
    },
    {
        "id": "book_balance",
        "label": "What's the book saying?",
        "focus_keys": ["regime", "net_gex", "sign_convention", "oi_timestamp"],
        "question": "What is the call−put book saying right now?",
    },
    {
        "id": "walls_flip",
        "label": "Where are walls and flip?",
        "focus_keys": ["call_wall", "put_wall", "magnet", "gamma_flip", "spot"],
        "question": "Where are the call wall, put wall, magnet, and gamma flip versus spot?",
    },
    {
        "id": "why_stance",
        "label": "Why this stance?",
        "focus_keys": ["market_alert"],
        "question": "Why does the desk show this stance, and what are the drivers?",
    },
    {
        "id": "what_to_watch",
        "label": "What would you watch?",
        "focus_keys": ["market_alert", "call_wall", "put_wall", "spot"],
        "question": "What would you watch from here — trigger, kill, and walls vs spot?",
    },
    {
        "id": "go_now",
        "label": "What's the GO card?",
        "focus_keys": ["go"],
        "question": "What does the current GO decision say, including legs if any?",
    },
    {
        "id": "risk_kill",
        "label": "Where's the kill?",
        "focus_keys": ["market_alert", "call_wall", "put_wall"],
        "question": "Where is the kill / invalidation relative to the gamma walls?",
    },
    {
        "id": "session_phase",
        "label": "What phase are we in?",
        "focus_keys": ["session_phase", "stale_session", "as_of", "oi_timestamp"],
        "question": "What session phase are we in, and how fresh is the book?",
    },
    {
        "id": "overnight",
        "label": "Overnight / catalysts?",
        "focus_keys": ["digest"],
        "question": "What did the overnight / pre-open digest say about catalysts?",
    },
    {
        "id": "commentary",
        "label": "What did commentary note?",
        "focus_keys": ["commentary"],
        "question": "What have recent session commentary headlines noted?",
    },
    {
        "id": "lot_expiry",
        "label": "Lot and expiry rules?",
        "focus_keys": ["lot_size", "expiry_rule", "symbol"],
        "question": "What are the lot size and expiry weekday rules for this index?",
    },
]

_FAQ_BY_ID = {f["id"]: f for f in SESSION_FAQS}

# Light in-process rate limit: one in-flight + short cooldown per symbol.
_qa_lock = threading.Lock()
_qa_inflight: set[str] = set()
_qa_last_done: dict[str, float] = {}
_QA_COOLDOWN_S = 1.5


def list_session_faqs() -> list[dict[str, Any]]:
    """Chip payload for the UI."""
    return [
        {
            "id": f["id"],
            "label": f["label"],
            "question": f["question"],
            "genai": bool(f.get("genai")),
        }
        for f in SESSION_FAQS
    ]


def _fmt_level(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if f != f or f == 0:  # NaN or zero often means missing wall
        return "n/a"
    return f"{f:,.0f}"


def _fmt_gex(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    abs_f = abs(f)
    if abs_f >= 1e12:
        return f"{f / 1e12:+.2f}T"
    if abs_f >= 1e9:
        return f"{f / 1e9:+.2f}B"
    if abs_f >= 1e6:
        return f"{f / 1e6:+.2f}M"
    return f"{f:+.0f}"


def _oi_stamp(raw: Any) -> str:
    if raw is None:
        return ""
    if hasattr(raw, "isoformat"):
        try:
            return raw.isoformat()
        except Exception:  # noqa: BLE001
            pass
    return str(raw).strip()


def _expiry_rule_for(symbol: str) -> str:
    from app.verify_exchange import EXPECTED_EXPIRY
    from config import index_key

    spec = EXPECTED_EXPIRY.get(index_key(symbol)) or {}
    return str(spec.get("label") or "see exchange calendar")


def _slim_positioning(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {
        "alert": bool(raw.get("alert")),
        "headline": str(raw.get("headline") or "")[:300],
        "overnight_line": str(raw.get("overnight_line") or "")[:400],
        "live_line": str(raw.get("live_line") or "")[:400],
        "overnight_state": raw.get("overnight_state"),
        "live_label": raw.get("live_label"),
        "file_date": raw.get("file_date"),
        "file_is_previous_session": raw.get("file_is_previous_session"),
    }


def _slim_futures_state(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {
        "state": raw.get("state"),
        "dprice": raw.get("dprice"),
        "doi": raw.get("doi"),
        "price": raw.get("price"),
        "as_of": raw.get("as_of"),
    }


def _slim_price_move(
    *,
    spot: Any,
    prev_close: Any,
    day_open: Any = None,
    day_high: Any = None,
    day_low: Any = None,
) -> dict[str, Any] | None:
    try:
        sp = float(spot or 0)
    except (TypeError, ValueError):
        sp = 0.0
    try:
        prev = float(prev_close or 0)
    except (TypeError, ValueError):
        prev = 0.0
    if sp <= 0 and prev <= 0:
        return None
    change_pts = None
    change_pct = None
    if sp > 0 and prev > 0:
        change_pts = round(sp - prev, 1)
        change_pct = round(100.0 * (sp - prev) / prev, 2)
    out: dict[str, Any] = {
        "spot": sp if sp > 0 else None,
        "prev_close": prev if prev > 0 else None,
        "change_pts": change_pts,
        "change_pct": change_pct,
    }
    for key, raw in (("day_open", day_open), ("day_high", day_high), ("day_low", day_low)):
        try:
            v = float(raw or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            out[key] = v
    return out


def build_session_context(
    symbol: str,
    *,
    levels: dict[str, Any] | None = None,
    alert: dict[str, Any] | None = None,
    go: dict[str, Any] | None = None,
    commentary: list[dict[str, Any]] | None = None,
    digest: dict[str, Any] | None = None,
    session_meta: dict[str, Any] | None = None,
    positioning: dict[str, Any] | None = None,
    futures_state: dict[str, Any] | None = None,
    price_move: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a slim, stamped fact pack. Missing pieces stay null/empty."""
    from config import INDICES, index_key

    sym = index_key(symbol)
    idx = INDICES[sym]
    lv = dict(levels or {})
    meta = dict(session_meta or {})
    alert_slim = None
    if isinstance(alert, dict) and alert:
        alert_slim = {
            "stance": alert.get("stance"),
            "confidence": alert.get("confidence"),
            "zone": alert.get("zone"),
            "headline": alert.get("headline"),
            "drivers": alert.get("drivers") or alert.get("diagnostics"),
            "trigger": alert.get("trigger"),
            "kill": alert.get("kill"),
            "outcome": alert.get("outcome"),
        }
    go_slim = None
    if isinstance(go, dict) and go:
        legs = go.get("contracts") or go.get("legs") or []
        go_slim = {
            "state": go.get("state"),
            "mode": go.get("mode"),
            "strategy_key": go.get("strategy_key"),
            "thesis": go.get("thesis"),
            "size_tier": go.get("size_tier"),
            "contracts": legs[:6] if isinstance(legs, list) else [],
            "invalidation": go.get("invalidation") or {},
            "hard_blocks": go.get("hard_blocks") or [],
            "emitted_at": go.get("emitted_at"),
        }
    commentary_slim: list[dict[str, str]] = []
    for e in (commentary or [])[:8]:
        if not isinstance(e, dict):
            continue
        commentary_slim.append({
            "headline": str(e.get("headline") or e.get("title") or "")[:200],
            "phase": str(e.get("phase") or ""),
            "ts": str(e.get("ts") or e.get("timestamp") or "")[:40],
        })
    digest_slim = None
    if isinstance(digest, dict) and digest:
        digest_slim = {
            "narrative": str(digest.get("narrative") or "")[:800],
            "catalyst_risk": digest.get("catalyst_risk"),
            "overnight_impact": digest.get("overnight_impact"),
            "status": digest.get("status"),
            "narrative_source": digest.get("narrative_source"),
        }

    oi_ts = _oi_stamp(lv.get("oi_timestamp") or meta.get("oi_timestamp"))
    as_of = str(meta.get("as_of") or datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"))

    pm = price_move if isinstance(price_move, dict) else None
    if pm is None:
        pm = _slim_price_move(
            spot=lv.get("spot"),
            prev_close=lv.get("prev_close") or meta.get("prev_close"),
            day_open=lv.get("day_open") or meta.get("day_open"),
            day_high=lv.get("day_high") or meta.get("day_high"),
            day_low=lv.get("day_low") or meta.get("day_low"),
        )

    return {
        "symbol": sym,
        "sign_convention": SIGN_CONVENTION,
        "lot_size": int(idx.lot_size),
        "strike_step": int(idx.strike_step),
        "expiry_rule": _expiry_rule_for(sym),
        "spot": lv.get("spot"),
        "call_wall": lv.get("call_wall"),
        "put_wall": lv.get("put_wall"),
        "magnet": lv.get("magnet"),
        "gamma_flip": lv.get("flip") if "flip" in lv else lv.get("gamma_flip"),
        "max_pain": lv.get("max_pain"),
        "regime": lv.get("regime"),
        "net_gex": lv.get("total_net_gex") if "total_net_gex" in lv else lv.get("net_gex"),
        "oi_timestamp": oi_ts,
        "oi_stale": bool(lv.get("oi_stale") or meta.get("oi_stale")),
        "session_phase": meta.get("session_phase"),
        "stale_session": bool(meta.get("stale_session")),
        "as_of": as_of,
        "market_alert": alert_slim,
        "go": go_slim,
        "commentary": commentary_slim,
        "digest": digest_slim,
        "positioning": _slim_positioning(positioning),
        "futures_state": _slim_futures_state(futures_state),
        "price_move": pm,
    }


def gather_session_context(symbol: str) -> dict[str, Any]:
    """Pull live in-memory session facts. Never scrapes NSE for a chat turn."""
    from config import index_key, is_market_open

    sym = index_key(symbol)
    levels: dict[str, Any] = {}
    meta: dict[str, Any] = {
        "as_of": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "session_phase": None,
        "stale_session": False,
        "oi_stale": False,
    }
    dash_obj = None
    try:
        from app import live_fetcher
        pub = live_fetcher.latest(sym) or {}
        dash_obj = pub.get("data")
        if dash_obj is not None and getattr(dash_obj, "levels", None) is not None:
            lv = dash_obj.levels
            levels = {
                "spot": float(getattr(lv, "spot", 0) or 0),
                "call_wall": float(getattr(lv, "call_wall", 0) or 0),
                "put_wall": float(getattr(lv, "put_wall", 0) or 0),
                "magnet": float(getattr(lv, "magnet", 0) or 0),
                "flip": getattr(lv, "flip", None),
                "max_pain": getattr(lv, "max_pain", None),
                "regime": str(getattr(lv, "regime", "") or ""),
                "total_net_gex": float(getattr(lv, "total_net_gex", 0) or 0),
                "oi_timestamp": getattr(lv, "oi_timestamp", None),
                "oi_stale": bool(getattr(lv, "oi_stale", False)),
            }
            meta["oi_stale"] = bool(levels.get("oi_stale"))
        fetched = pub.get("fetched")
        if fetched is not None:
            meta["as_of"] = _oi_stamp(fetched) or meta["as_of"]
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather levels: %s", ex)

    try:
        from data.pre_post_market import get_current_session_phase
        phase, _, _ = get_current_session_phase()
        meta["session_phase"] = phase
    except Exception:  # noqa: BLE001
        pass

    alert = None
    try:
        from analytics.market_alert import evaluate_market_alert
        from config import INDICES

        idx = INDICES[sym]
        spot = float(levels.get("spot") or 0)
        if spot > 0:
            flip = levels.get("flip")
            try:
                flip_f = float(flip) if flip is not None else 0.0
            except (TypeError, ValueError):
                flip_f = 0.0
            payload = {
                "spot": spot,
                "strike_step": int(idx.strike_step),
                "is_market_open": bool(is_market_open()),
                "market_state": "OPEN" if is_market_open() else "CLOSED",
                "as_of_time": meta.get("as_of"),
                "session_phase": meta.get("session_phase"),
                "kpis": {
                    "call_wall": {"val": levels.get("call_wall")},
                    "put_wall": {"val": levels.get("put_wall")},
                    "gamma_flip": {"val": flip_f or None, "raw": flip_f or None},
                    "regime": {"val": str(levels.get("regime") or "NEUTRAL").upper()},
                    "net_gex": {"val": levels.get("total_net_gex")},
                },
                "orderflow_alerts": [],
            }
            alert = evaluate_market_alert(payload)
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather alert: %s", ex)

    commentary: list[dict] = []
    try:
        from commentary.storage.db import get_recent_commentary_entries
        commentary = get_recent_commentary_entries(sym, limit=12) or []
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather commentary: %s", ex)

    digest = None
    try:
        from analytics.pre_open_digest import load_sealed_digest
        digest = load_sealed_digest(datetime.now(IST).date())
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather digest: %s", ex)

    go = None
    try:
        from data.journal import go_efficiency as go_eff
        opens = go_eff.list_open(sym, include_decision=True) or []
        if opens:
            row = opens[0]
            go = row.get("decision") if isinstance(row, dict) else None
            if go is None and isinstance(row, dict):
                go = {
                    "state": row.get("state"),
                    "strategy_key": row.get("strategy_key"),
                    "thesis": row.get("thesis"),
                    "contracts": row.get("contracts") or [],
                }
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather go: %s", ex)

    positioning = None
    try:
        from app.session_reads import build_session_reads
        reads = build_session_reads(sym)
        positioning = reads.get("positioning_flow")
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather positioning: %s", ex)

    futures_state = None
    try:
        from analytics.session_map import read_futures_state
        futures_state = read_futures_state(sym, datetime.now(IST).strftime("%Y-%m-%d"))
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather futures_state: %s", ex)

    # Price path: prefer day_ohlc / quote prev_close when present on the published dash.
    prev_close = None
    day_open = day_high = day_low = None
    try:
        if dash_obj is not None:
            ohlc = getattr(dash_obj, "day_ohlc", None) or {}
            if isinstance(ohlc, dict):
                prev_close = ohlc.get("prev_close") or ohlc.get("close")
                day_open = ohlc.get("open")
                day_high = ohlc.get("high")
                day_low = ohlc.get("low")
            lv_obj = getattr(dash_obj, "levels", None)
            if prev_close is None and lv_obj is not None:
                prev_close = getattr(lv_obj, "prev_close", None)
                day_open = day_open or getattr(lv_obj, "day_open", None)
        if prev_close is None:
            from app.spot_ticker import latest_quote
            q = latest_quote(sym) or {}
            prev_close = q.get("prev_close")
            if not levels.get("spot") and q.get("spot"):
                levels["spot"] = float(q["spot"])
    except Exception as ex:  # noqa: BLE001
        log.debug("session_qa gather price_move: %s", ex)

    price_move = _slim_price_move(
        spot=levels.get("spot"),
        prev_close=prev_close,
        day_open=day_open,
        day_high=day_high,
        day_low=day_low,
    )

    return build_session_context(
        sym,
        levels=levels,
        alert=alert,
        go=go if isinstance(go, dict) else None,
        commentary=commentary,
        digest=digest,
        session_meta=meta,
        positioning=positioning if isinstance(positioning, dict) else None,
        futures_state=futures_state if isinstance(futures_state, dict) else None,
        price_move=price_move,
    )


def build_narrative_session_context(
    symbol: str,
    *,
    overrides: dict[str, Any] | None = None,
    story_thesis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Slim desk pack for commentary LLM — book + alert + positioning + path.

    ``overrides`` win for live levels already computed in the commentary engine
    (spot/walls/flip) so the story stays on the same book as the detectors.
    """
    from config import index_key

    sym = index_key(symbol)
    try:
        full = gather_session_context(sym)
    except Exception as ex:  # noqa: BLE001
        log.debug("narrative context gather failed: %s", ex)
        full = {"symbol": sym}

    keep = (
        "symbol",
        "sign_convention",
        "spot",
        "call_wall",
        "put_wall",
        "magnet",
        "gamma_flip",
        "max_pain",
        "regime",
        "net_gex",
        "oi_timestamp",
        "oi_stale",
        "session_phase",
        "as_of",
        "lot_size",
        "strike_step",
        "market_alert",
        "positioning",
        "futures_state",
        "price_move",
        "digest",
    )
    slim: dict[str, Any] = {k: full[k] for k in keep if k in full and full[k] is not None}

    go = full.get("go")
    if isinstance(go, dict) and go:
        slim["go"] = {
            "state": go.get("state"),
            "strategy_key": go.get("strategy_key"),
            "thesis": str(go.get("thesis") or "")[:200],
            "contracts": (go.get("contracts") or [])[:2],
        }

    if isinstance(story_thesis, dict) and story_thesis:
        slim["running_thesis"] = {
            "bias": story_thesis.get("bias"),
            "confidence": story_thesis.get("confidence"),
        }

    if overrides:
        for key, val in overrides.items():
            if val is None:
                continue
            slim[key] = val
    slim.setdefault("symbol", sym)
    slim.setdefault("sign_convention", SIGN_CONVENTION)
    return slim


def render_faq_brief(faq_id: str, context: dict[str, Any]) -> str:
    """Deterministic answer from stamped facts. Always ends with CONFIRM_LINE."""
    ctx = context or {}
    fid = str(faq_id or "").strip()
    alert = ctx.get("market_alert") or {}
    go = ctx.get("go") or {}
    digest = ctx.get("digest") or {}
    sym = ctx.get("symbol") or "NIFTY"
    oi = ctx.get("oi_timestamp") or "unknown"
    as_of = ctx.get("as_of") or ""

    pos = ctx.get("positioning") or {}
    fut = ctx.get("futures_state") or {}
    pm = ctx.get("price_move") or {}

    if fid == "futures_positioning":
        bits = []
        if pos.get("headline"):
            bits.append(str(pos["headline"]))
        if pos.get("overnight_line"):
            bits.append(f"Overnight: {pos['overnight_line']}")
        if pos.get("live_line"):
            bits.append(f"Live: {pos['live_line']}")
        if fut.get("state"):
            bits.append(
                f"Futures state: {fut['state']}"
                + (f" (dprice {fut.get('dprice')}, dOI {fut.get('doi')})" if fut.get("dprice") is not None else "")
                + (f" as-of {fut.get('as_of')}" if fut.get("as_of") else "")
            )
        if not bits:
            body = (
                f"No futures positioning read in the pack for {sym}. "
                f"Sensex has no NSE participant file; Nifty/Bank Nifty need the futures journal."
            )
        else:
            body = " ".join(bits) + " Positioning is inventory context, not a direction call."
    elif fid == "price_move":
        if not pm or not pm.get("spot"):
            body = f"No spot/price-move path in the pack for {sym}."
        else:
            ch = pm.get("change_pts")
            pct = pm.get("change_pct")
            ch_s = "n/a" if ch is None else f"{ch:+.1f} pts"
            pct_s = "" if pct is None else f" ({pct:+.2f}%)"
            range_bits = []
            if pm.get("day_high") and pm.get("day_low"):
                range_bits.append(
                    f"session range {_fmt_level(pm['day_low'])}–{_fmt_level(pm['day_high'])}"
                )
            if pm.get("day_open"):
                range_bits.append(f"open {_fmt_level(pm['day_open'])}")
            range_s = ("; " + ", ".join(range_bits)) if range_bits else ""
            body = (
                f"{sym} spot {_fmt_level(pm.get('spot'))} vs prior close "
                f"{_fmt_level(pm.get('prev_close'))}: {ch_s}{pct_s}{range_s}. "
                f"Vs walls: call {_fmt_level(ctx.get('call_wall'))}, "
                f"put {_fmt_level(ctx.get('put_wall'))}, "
                f"flip {_fmt_level(ctx.get('gamma_flip'))}. "
                f"As-of {as_of}."
            )
    elif fid == "alert_tape":
        if not alert:
            body = f"No cockpit alert tape in the pack for {sym}."
        else:
            body = (
                f"Alert tape: {alert.get('stance') or 'n/a'} "
                f"({alert.get('confidence') or 'n/a'}) in zone {alert.get('zone') or 'n/a'}. "
                f"{alert.get('headline') or ''} "
                f"Drivers: {alert.get('drivers') or 'n/a'} "
                f"Trigger: {alert.get('trigger') or 'n/a'}. "
                f"Kill: {alert.get('kill') or 'n/a'}. "
                f"Spot {_fmt_level(ctx.get('spot'))} vs call "
                f"{_fmt_level(ctx.get('call_wall'))} / put {_fmt_level(ctx.get('put_wall'))}."
            ).strip()
    elif fid == "book_balance":
        regime = str(ctx.get("regime") or "n/a").upper()
        body = (
            f"{sym} book under {SIGN_CONVENTION}: regime {regime}, "
            f"net GEX {_fmt_gex(ctx.get('net_gex'))} (call magnitude minus put magnitude). "
            f"POSITIVE means call-heavy; NEGATIVE means put-heavy — not a fade/run forecast. "
            f"OI as-of {oi}."
        )
    elif fid == "walls_flip":
        flip = ctx.get("gamma_flip")
        flip_s = _fmt_level(flip) if flip not in (None, "", 0, 0.0) else "no zero-crossing in range"
        body = (
            f"{sym} spot {_fmt_level(ctx.get('spot'))}. "
            f"Call wall {_fmt_level(ctx.get('call_wall'))}, "
            f"put wall {_fmt_level(ctx.get('put_wall'))}, "
            f"magnet {_fmt_level(ctx.get('magnet'))}, "
            f"gamma flip {flip_s}. "
            f"Max pain {_fmt_level(ctx.get('max_pain'))}. OI as-of {oi}."
        )
    elif fid == "why_stance":
        if not alert:
            body = f"No market-alert stance is available for {sym} in this pack."
        else:
            body = (
                f"Stance {alert.get('stance') or 'n/a'} "
                f"({alert.get('confidence') or 'n/a'} confidence), "
                f"zone {alert.get('zone') or 'n/a'}. "
                f"{alert.get('headline') or ''} "
                f"Drivers: {alert.get('drivers') or 'n/a'} "
                f"OI as-of {oi}."
            ).strip()
    elif fid == "what_to_watch":
        if not alert:
            body = (
                f"Watch walls vs spot {_fmt_level(ctx.get('spot'))}: "
                f"call {_fmt_level(ctx.get('call_wall'))}, "
                f"put {_fmt_level(ctx.get('put_wall'))}. No alert trigger/kill in pack."
            )
        else:
            body = (
                f"Trigger: {alert.get('trigger') or 'n/a'}. "
                f"Kill: {alert.get('kill') or 'n/a'}. "
                f"Spot {_fmt_level(ctx.get('spot'))} vs call wall "
                f"{_fmt_level(ctx.get('call_wall'))} / put wall "
                f"{_fmt_level(ctx.get('put_wall'))}. "
                f"Stops belong beyond the gamma strikes, never on the magnet."
            )
    elif fid == "go_now":
        if not go:
            body = f"No open GO decision in the pack for {sym}."
        else:
            legs = go.get("contracts") or []
            leg_bits = []
            for leg in legs[:4]:
                if not isinstance(leg, dict):
                    continue
                leg_bits.append(
                    f"{leg.get('action') or ''} {leg.get('opt_type') or ''} "
                    f"{_fmt_level(leg.get('strike'))} {leg.get('expiry') or ''}".strip()
                )
            legs_s = "; ".join(leg_bits) if leg_bits else "no legs listed"
            blocks = go.get("hard_blocks") or []
            block_s = f" Hard blocks: {', '.join(str(b) for b in blocks[:3])}." if blocks else ""
            body = (
                f"GO state {go.get('state') or 'n/a'} · mode {go.get('mode') or 'n/a'} · "
                f"{go.get('strategy_key') or 'n/a'}. "
                f"Thesis: {go.get('thesis') or 'n/a'}. "
                f"Legs: {legs_s}.{block_s}"
            )
    elif fid == "risk_kill":
        kill = (alert or {}).get("kill") if alert else None
        if kill:
            body = (
                f"Kill / invalidation from the alert tape: {kill}. "
                f"Call wall {_fmt_level(ctx.get('call_wall'))}, "
                f"put wall {_fmt_level(ctx.get('put_wall'))}. "
                f"Place stops beyond the gamma concentrations, not at the magnet "
                f"{_fmt_level(ctx.get('magnet'))}."
            )
        else:
            inv = (go or {}).get("invalidation") if go else None
            if inv:
                body = f"GO invalidation in pack: {inv}. Walls call {_fmt_level(ctx.get('call_wall'))} / put {_fmt_level(ctx.get('put_wall'))}."
            else:
                body = (
                    f"No explicit kill string in the pack. "
                    f"Reference walls: call {_fmt_level(ctx.get('call_wall'))}, "
                    f"put {_fmt_level(ctx.get('put_wall'))}."
                )
    elif fid == "session_phase":
        stale = "yes — book may be prior session" if ctx.get("stale_session") else "no"
        oi_stale = "yes" if ctx.get("oi_stale") else "no"
        body = (
            f"Phase {ctx.get('session_phase') or 'n/a'}. "
            f"Context as-of {as_of}. OI timestamp {oi} (oi_stale={oi_stale}). "
            f"stale_session={stale}."
        )
    elif fid == "overnight":
        if not digest or not digest.get("narrative"):
            body = f"No sealed pre-open digest narrative in the pack for {sym}."
        else:
            body = (
                f"Catalyst risk {digest.get('catalyst_risk') or 'n/a'} "
                f"(overnight_impact {digest.get('overnight_impact')}). "
                f"{digest.get('narrative')}"
            )
    elif fid == "commentary":
        rows = ctx.get("commentary") or []
        if not rows:
            body = f"No recent commentary headlines in the pack for {sym}."
        else:
            bits = [f"- {r.get('headline')}" for r in rows[:5] if r.get("headline")]
            body = "Recent commentary:\n" + "\n".join(bits) if bits else f"No headlines for {sym}."
    elif fid == "lot_expiry":
        body = (
            f"{sym} lot size {ctx.get('lot_size')} (contract multiplier). "
            f"Strike step {ctx.get('strike_step')}. "
            f"Expiry rule: {ctx.get('expiry_rule')}. "
            f"Holiday moves expiry to the previous trading day. "
            f"Sign convention {SIGN_CONVENTION}."
        )
    else:
        body = f"Unknown FAQ id {fid!r}. Use a catalog chip."

    text = " ".join(str(body).split())
    if not text.endswith(CONFIRM_LINE):
        text = f"{text} {CONFIRM_LINE}"
    return text


def _fact_numbers_from_context(context: dict[str, Any]) -> list[float]:
    """Numbers the model may cite: typed fields plus digits inside string leaves.

    Commentary headlines (e.g. ``Put Wall migration 25,700 to 25,500``) live in the
    pack as strings; a typed-only harvest would false-reject a grounded cite.
    """
    from commentary.narrator.verifier import (
        _extract_all_numbers_from_dict,
        _extract_numbers_from_text,
    )

    facts = dict(context or {})
    fact_nums: list[float] = list(_extract_all_numbers_from_dict(facts))

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                _walk(item)
        elif isinstance(node, str) and node.strip():
            fact_nums.extend(_extract_numbers_from_text(node))

    _walk(facts)
    return fact_nums


def verify_qa_answer(text: str, context: dict[str, Any]) -> tuple[bool, list[str]]:
    """Reject hype / execution language and strike-like numbers absent from the pack.

    Small ints (time fragments, counts) and calendar years are ignored so as-of
    stamps and GEX abbreviations do not false-reject grounded prose.
    """
    from commentary.narrator.verifier import (
        _extract_numbers_from_text,
        _matches_any_fact,
        verify_commentary,
    )

    facts = dict(context or {})
    ok, violations = verify_commentary(
        text or "",
        facts,
        custom_forbidden=_QA_FORBIDDEN,
        strict_numeric=False,
    )
    fact_nums = _fact_numbers_from_context(facts)
    # GEX abbreviated forms in briefs (+2.02B) — allow the scaled mantissa.
    for raw in list(fact_nums):
        abs_r = abs(raw)
        if abs_r >= 1e6:
            fact_nums.append(abs_r / 1e6)
            fact_nums.append(abs_r / 1e9)
            fact_nums.append(abs_r / 1e12)
            fact_nums.append(raw / 1e6)
            fact_nums.append(raw / 1e9)
            fact_nums.append(raw / 1e12)
    fact_nums.extend([0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 65.0, 100.0])
    for num in _extract_numbers_from_text(text or ""):
        abs_n = abs(num)
        # Years / clock fragments / small counts — skip.
        if abs_n < 100.0 or 2020.0 <= abs_n <= 2035.0:
            continue
        if not _matches_any_fact(num, fact_nums):
            violations.append(f"Unverified number in commentary: {num}")
            ok = False
    return ok, violations


def _faq_id_or_none(faq_id: str | None) -> str | None:
    if not faq_id:
        return None
    fid = str(faq_id).strip()
    return fid if fid in _FAQ_BY_ID else None


def _try_llm_polish_or_answer(
    *,
    context: dict[str, Any],
    question: str,
    faq_id: str | None,
    history: list[dict[str, str]] | None,
    heuristic: str | None,
) -> tuple[str | None, str | None]:
    """Return (text, model) or (None, None)."""
    try:
        from analytics.llm_client import complete_chat, llm_enabled, model_name
        from analytics.llm_prompts import session_qa_messages

        if not llm_enabled("qa"):
            return None, None
        messages = session_qa_messages(
            context,
            question,
            history=history,
            faq_id=faq_id,
            heuristic_brief=heuristic,
        )
        raw = complete_chat(
            messages,
            purpose="session_qa",
            temperature=0.2,
            max_tokens=640,
            timeout_s=90.0,
        )
        if not raw or not str(raw).strip():
            return None, None
        text = str(raw).strip()
        if CONFIRM_LINE.lower() not in text.lower():
            text = f"{text} {CONFIRM_LINE}"
        return text, model_name()
    except Exception as ex:  # noqa: BLE001
        log.warning("session_qa llm failed: %s", ex)
        return None, None


def ask_session_qa(
    symbol: str,
    *,
    question: str | None = None,
    faq_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer an FAQ (deterministic-first) or a free-form question (LLM + verify)."""
    from config import index_key

    sym = index_key(symbol)
    ctx = context if isinstance(context, dict) and context else gather_session_context(sym)
    fid = _faq_id_or_none(faq_id)
    q = (question or "").strip()
    if fid and not q:
        q = str(_FAQ_BY_ID[fid]["question"])

    citations = {
        "oi_timestamp": ctx.get("oi_timestamp"),
        "as_of": ctx.get("as_of"),
        "symbol": ctx.get("symbol"),
        "sign_convention": ctx.get("sign_convention"),
    }

    if fid:
        brief = render_faq_brief(fid, ctx)
        polished, model = _try_llm_polish_or_answer(
            context=ctx,
            question=q,
            faq_id=fid,
            history=history,
            heuristic=brief,
        )
        if polished:
            ok, violations = verify_qa_answer(polished, ctx)
            if ok:
                return {
                    "answer": polished,
                    "source": "genai",
                    "faq_id": fid,
                    "model": model,
                    "context_as_of": ctx.get("as_of"),
                    "citations": citations,
                    "symbol": sym,
                }
            log.info("session_qa FAQ polish rejected: %s", violations[:3])
        return {
            "answer": brief,
            "source": "heuristic",
            "faq_id": fid,
            "model": None,
            "context_as_of": ctx.get("as_of"),
            "citations": citations,
            "symbol": sym,
        }

    # Free-form
    if not q:
        return {
            "answer": "Ask a question or pick an FAQ chip. " + CONFIRM_LINE,
            "source": "refuse",
            "faq_id": None,
            "model": None,
            "context_as_of": ctx.get("as_of"),
            "citations": citations,
            "symbol": sym,
        }

    from analytics.llm_client import llm_enabled

    if not llm_enabled("qa"):
        return {
            "answer": _FREEFORM_OFF,
            "source": "refuse",
            "faq_id": None,
            "model": None,
            "context_as_of": ctx.get("as_of"),
            "citations": citations,
            "symbol": sym,
            "reject_reason": "llm_off",
        }

    raw, model = _try_llm_polish_or_answer(
        context=ctx,
        question=q,
        faq_id=None,
        history=history,
        heuristic=None,
    )
    if not raw:
        return {
            "answer": _FREEFORM_NO_ANSWER,
            "source": "refuse",
            "faq_id": None,
            "model": None,
            "context_as_of": ctx.get("as_of"),
            "citations": citations,
            "symbol": sym,
            "reject_reason": "no_answer",
        }
    ok, violations = verify_qa_answer(raw, ctx)
    if ok:
        return {
            "answer": raw,
            "source": "genai",
            "faq_id": None,
            "model": model,
            "context_as_of": ctx.get("as_of"),
            "citations": citations,
            "symbol": sym,
        }
    log.info("session_qa freeform rejected: %s", violations[:3])
    return {
        "answer": _FREEFORM_UNGROUNDED,
        "source": "refuse",
        "faq_id": None,
        "model": model,
        "context_as_of": ctx.get("as_of"),
        "citations": citations,
        "symbol": sym,
        "reject_reason": "ungrounded",
        "violations": violations[:5],
    }


def acquire_qa_slot(symbol: str) -> tuple[bool, str]:
    """Rate-limit helper for the API. Returns (ok, reason)."""
    from config import index_key

    sym = index_key(symbol)
    now = time.time()
    with _qa_lock:
        if sym in _qa_inflight:
            return False, "A session Q&A request is already in flight for this symbol."
        last = _qa_last_done.get(sym, 0.0)
        if now - last < _QA_COOLDOWN_S:
            return False, "Slow down — wait a moment before the next ask."
        _qa_inflight.add(sym)
        return True, ""


def release_qa_slot(symbol: str) -> None:
    from config import index_key

    sym = index_key(symbol)
    with _qa_lock:
        _qa_inflight.discard(sym)
        _qa_last_done[sym] = time.time()
