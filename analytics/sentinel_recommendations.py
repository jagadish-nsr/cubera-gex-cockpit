"""analytics/sentinel_recommendations.py — OrderFlow Sentinel Trade Recommendations.

Reactive pipeline (live ticks only):
1. ATM option recommendation on a directional Sentinel signal.
2. Cash-index spot entry / invalidation / target from the signal (no invented levels).
3. Target Engine reprice using live DTE + ATM IV (refuse when IV is missing).
4. One active row per (symbol, strike, side); stack raises max target and protective spot inval.
5. Target hit on TOUCH (option or spot). Stop = SPOT_INVAL on completed 5-min cash close.
6. Paper trades open automatically by default; can be turned off or confirmed manually.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

import config
from data.expiry import next_monthly_expiry, next_weekly_expiry
from data.journal import recommendations_store as rec_store
from data.journal import store as journal_store
from sentinel.models import Bar, SentinelSignal

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

# Signal quality: micro-noise below this score does not open a setup.
# 14d prod tape: score≥70 CONTINUATION_BULL hit 72.5% / +108 pts at +5m;
# score<70 CONTINUATION (mostly old micro flood) hit 57% / +2 pts.
MIN_SIGNAL_SCORE = 70
_ALLOWED_TYPE_PREFIXES = (
    "CONTINUATION_",
    "ABSORPTION_",
    "EXHAUSTION_DOWN",
    "CVD_DIVERGENCE_",
)
# Empirically toxic on the live outcome tape — still journaled for research,
# but never mint a recommendation / paper from them.
_BLOCKED_SIGNAL_TYPES = frozenset({
    "EXHAUSTION_UP",
    "TRAPPED_BUYERS_EXHAUSTION",
    "TRAPPED_SELLERS_ABSORPTION",
})

_AUTO_PAPER = True  # default on: open paper when a setup is minted
_AUTO_PAPER_LOCK = threading.Lock()

_REFUSAL_COUNTS: dict[str, int] = {}
_REFUSAL_LOCK = threading.Lock()


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _today_ist() -> date:
    return datetime.now(IST).date()


def _note_refusal(reason: str) -> None:
    with _REFUSAL_LOCK:
        _REFUSAL_COUNTS[reason] = int(_REFUSAL_COUNTS.get(reason, 0)) + 1


def get_refusal_metrics() -> dict[str, int]:
    with _REFUSAL_LOCK:
        return dict(_REFUSAL_COUNTS)


def reset_refusal_metrics() -> None:
    with _REFUSAL_LOCK:
        _REFUSAL_COUNTS.clear()


def is_auto_paper_enabled() -> bool:
    with _AUTO_PAPER_LOCK:
        return bool(_AUTO_PAPER)


def set_auto_paper_enabled(enabled: bool) -> bool:
    """Paper trades open automatically when this is True (default True)."""
    global _AUTO_PAPER
    with _AUTO_PAPER_LOCK:
        _AUTO_PAPER = bool(enabled)
        return _AUTO_PAPER


def _resolve_symbol(instrument_id: str) -> str:
    """Only NIFTY / BANKNIFTY / SENSEX. Unknown symbols are refused."""
    clean = str(instrument_id or "").upper().strip()
    for suffix in ("-FUT", "_FUT", "-INDEX", "_INDEX"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    return clean if clean in config.INDICES else ""


def _get_strike_step(symbol: str) -> int:
    idx = config.get_index(symbol)
    step = int(getattr(idx, "strike_step", 0) or 0)
    if step <= 0:
        raise ValueError(f"No authentic strike step configured for index {symbol}")
    return step


def _get_lot_size(symbol: str) -> int:
    idx = config.get_index(symbol)
    lot = int(getattr(idx, "lot_size", 0) or 0)
    if lot <= 0:
        raise ValueError(f"No authentic lot size configured for index {symbol}")
    return lot


def _round_strike(price: float, step: int) -> float:
    if step <= 0:
        raise ValueError("strike step must be positive")
    return float(round(price / step) * step)


def _signal_passes_quality(signal: SentinelSignal) -> bool:
    score = int(getattr(signal, "score", 0) or 0)
    if score < MIN_SIGNAL_SCORE:
        return False
    sig_type = str(getattr(signal, "type", "") or "").upper()
    if sig_type in _BLOCKED_SIGNAL_TYPES:
        return False
    return any(sig_type.startswith(p) for p in _ALLOWED_TYPE_PREFIXES)


def _resolve_contract_context(symbol: str) -> dict[str, Any] | None:
    """Nearest listed expiry, live DTE, and ATM IV. Returns None when IV is missing."""
    idx = config.get_index(symbol)
    today = _today_ist()
    exchange = str(getattr(idx, "exchange", "NSE") or "NSE")
    if bool(getattr(idx, "has_weekly", True)):
        exp = next_weekly_expiry(today, exchange)
    else:
        exp = next_monthly_expiry(today, exchange)

    dte_days = float(max(0, (exp - today).days)) + 0.25
    atm_iv: float | None = None
    expiry_iso = exp.isoformat()

    try:
        from app.live_fetcher import latest as chain_latest

        pack = chain_latest(symbol, "current_week") or {}
        dash = pack.get("data")
        if dash is not None:
            levels = getattr(dash, "levels", None)
            raw_iv = getattr(levels, "atm_iv", None) if levels is not None else None
            if raw_iv is None:
                raw_iv = getattr(dash, "atm_iv", None)
            try:
                iv = float(raw_iv) if raw_iv is not None else 0.0
            except (TypeError, ValueError):
                iv = 0.0
            if iv > 0.05:
                atm_iv = iv
            raw_dte = getattr(dash, "dte_now", None)
            if raw_dte is not None:
                try:
                    dte_days = max(0.0, float(raw_dte))
                except (TypeError, ValueError):
                    pass
            nearest = getattr(dash, "nearest_expiry", None) or getattr(dash, "expiry", None)
            if nearest:
                expiry_iso = str(nearest)[:10]
    except Exception:
        pass

    if atm_iv is None or atm_iv <= 0.05:
        return None

    try:
        exp_tag = datetime.strptime(expiry_iso[:10], "%Y-%m-%d").strftime("%d%b%Y").upper()
    except Exception:
        exp_tag = expiry_iso

    return {
        "expiry": expiry_iso[:10],
        "expiry_tag": exp_tag,
        "dte_days": float(dte_days),
        "atm_iv": float(atm_iv),
    }


def _resolve_option_entry_ltp(
    symbol: str,
    strike: float,
    side: str,
    opt_sym: str = "",
    options_ltp_map: dict | None = None,
) -> float | None:
    """Live premium only. Never invent Black-Scholes."""
    book = options_ltp_map or {}
    for k in (opt_sym, f"{strike}_{side}", f"{int(strike)}_{side}", f"{symbol}_{int(strike)}_{side}"):
        if k in book:
            try:
                v = float(book[k] or 0.0)
                if v > 0:
                    return round(v, 2)
            except Exception:
                pass

    ltp = rec_store.resolve_live_option_ltp(symbol, strike, side, opt_sym, options_ltp_map)
    if ltp is not None and ltp > 0:
        return round(float(ltp), 2)
    return None


def _protective_spot_inval(side: str, active_sigs: list[dict]) -> float | None:
    """Most protective cash invalidation among active contributing signals."""
    vals = [float(s.get("spot_invalidation") or 0.0) for s in active_sigs if float(s.get("spot_invalidation") or 0.0) > 0]
    if not vals:
        return None
    # CE long: invalidation below spot — higher level is tighter/protective.
    # PE long put: invalidation above spot — lower level is tighter/protective.
    return max(vals) if side == "CE" else min(vals)


def _stack_option_sl(active_sigs: list[dict], fallback: float) -> float:
    """Higher premium floor is the tighter stop for a long option."""
    sls = [float(s.get("option_sl") or 0.0) for s in active_sigs if float(s.get("option_sl") or 0.0) > 0]
    return max(sls) if sls else float(fallback)


def _stable_rec_id(symbol: str, strike: float, side: str, session_day: str | None = None) -> str:
    day = session_day or _today_ist().strftime("%Y%m%d")
    return f"srec_{day}_{symbol}_{int(strike)}_{side.upper()}"


def _open_or_skip_paper(
    *,
    symbol: str,
    side: str,
    strike: float,
    entry_ltp: float,
    expiry_tag: str,
    spot: float,
    spot_target: float,
    spot_inval: float,
    opt_target: float,
    opt_sl: float,
    signal: SentinelSignal,
    lot_size: int,
    force: bool = False,
) -> str | None:
    if not force and not is_auto_paper_enabled():
        return None
    try:
        legs = [{
            "action": "BUY",
            "type": side,
            "strike": float(strike),
            "ltp": float(entry_ltp),
            "expiry": expiry_tag or "",
        }]
        pt = journal_store.open_trade(
            symbol=symbol,
            strategy_name=f"Sentinel {signal.type} {int(strike)}{side}",
            legs=legs,
            entry_spot=spot,
            entry_levels={
                "spot": spot,
                "spot_target": spot_target,
                "spot_invalidation": spot_inval,
                "option_target": opt_target,
                "option_sl": opt_sl,
            },
            entry_regime={
                "signal_type": signal.type,
                "bias": signal.bias,
                "score": getattr(signal, "score", 0),
                "rvol": getattr(signal, "rvol_tod", 1.0),
            },
            entry_net_premium=-entry_ltp,
            lot_size=lot_size,
            notes=(
                f"Sentinel {signal.event_id}. Tgt: {opt_target}, SpotInval: {spot_inval}, "
                f"OptSL(display): {opt_sl}"
            ),
            exit_by="SENTINEL_ENGINE",
        )
        return pt.trade_id
    except Exception as err:
        log.warning("Could not open paper trade for sentinel setup: %s", err)
        return None


def _sync_paper_notes(pt_id: str | None, note: str) -> None:
    if not pt_id:
        return
    try:
        journal_store.append_trade_note(pt_id, note)
    except Exception:
        pass


def on_sentinel_signal(
    signal: SentinelSignal,
    current_spot: float | None = None,
    options_ltp_map: dict | None = None,
    expiry_tag: str = "",
    open_paper: bool | None = None,
) -> dict | None:
    """Generate or stack a Sentinel recommendation. Paper opens only when allowed."""
    bias = str(signal.bias or "").upper()
    if bias not in ("LONG", "SHORT"):
        _note_refusal("non_directional")
        return None

    if not _signal_passes_quality(signal):
        _note_refusal("low_score_or_type")
        log.info(
            "Sentinel recommendation refused: score/type gate (%s score=%s)",
            getattr(signal, "type", ""), getattr(signal, "score", 0),
        )
        return None

    symbol = _resolve_symbol(signal.instrument_id)
    if not symbol:
        _note_refusal("unsupported_symbol")
        log.warning("Unsupported index symbol %r for Sentinel recommendation", signal.instrument_id)
        return None

    step = _get_strike_step(symbol)
    side = "CE" if bias == "LONG" else "PE"

    spot = float(current_spot if current_spot and current_spot > 0 else (signal.entry_price or 0.0))
    if spot <= 0:
        try:
            from app.spot_ticker import latest_quote
            q = latest_quote(symbol) or {}
            spot = float(q.get("spot") or 0.0)
        except Exception:
            pass

    if spot <= 0 or config.chain_spot_is_placeholder(symbol, spot):
        _note_refusal("placeholder_or_missing_spot")
        log.warning("Sentinel recommendation refused: invalid/placeholder spot %.2f for %s", spot, symbol)
        return None

    atm_strike = _round_strike(spot, step)

    inval_level = float(signal.invalidation_level or 0.0)
    if inval_level <= 0 or (bias == "LONG" and inval_level >= spot) or (bias == "SHORT" and inval_level <= spot):
        _note_refusal("bad_invalidation")
        log.warning("Sentinel recommendation refused: invalid signal invalidation %.2f for %s", inval_level, symbol)
        return None
    spot_inval = round(inval_level, 2)

    target_pts = float(signal.target_1_pts or 0.0)
    if target_pts <= 0:
        _note_refusal("missing_target_pts")
        log.warning("Sentinel recommendation refused: missing target_1_pts for %s", signal.event_id)
        return None

    spot_target = round(spot + target_pts if bias == "LONG" else spot - target_pts, 2)
    spot_target_2 = round(spot + (target_pts * 1.6) if bias == "LONG" else spot - (target_pts * 1.6), 2)

    ctx = _resolve_contract_context(symbol)
    if ctx is None:
        _note_refusal("missing_atm_iv")
        log.warning("Sentinel recommendation refused: no live ATM IV for %s", symbol)
        return None

    resolved_expiry = expiry_tag or str(ctx["expiry_tag"])
    opt_sym = f"{symbol} {resolved_expiry} {int(atm_strike)} {side}"
    entry_ltp = _resolve_option_entry_ltp(symbol, atm_strike, side, opt_sym, options_ltp_map)
    if entry_ltp is None or entry_ltp <= 0:
        _note_refusal("missing_option_ltp")
        log.warning(
            "Sentinel recommendation refused: no live option quote for %s %d %s",
            symbol, int(atm_strike), side,
        )
        return None

    from analytics.trade_ideas import _reprice_trade_idea_targets
    sl_opt, tgt1_opt, tgt2_opt, p_t1, p_sl, net_ev, rr_str, _ = _reprice_trade_idea_targets(
        side=side,
        strike=atm_strike,
        current_ltp=entry_ltp,
        spot=spot,
        inval_spot=spot_inval,
        tgt1_spot=spot_target,
        tgt2_spot=spot_target_2,
        dte_days=float(ctx["dte_days"]),
        atm_iv=float(ctx["atm_iv"]),
        horizon_minutes=45,
        step=step,
    )

    trig_ts = (
        signal.bar_start_ts.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(signal.bar_start_ts, "strftime")
        else str(signal.bar_start_ts)
    )
    session_day = trig_ts[:10].replace("-", "") if len(trig_ts) >= 10 else _today_ist().strftime("%Y%m%d")

    sig_summary = {
        "signal_id": signal.event_id,
        "signal_type": signal.type,
        "bias": bias,
        "score": getattr(signal, "score", 0),
        "triggered_at": trig_ts,
        "spot_entry": round(spot, 2),
        "spot_target": round(spot_target, 2),
        "spot_invalidation": round(spot_inval, 2),
        "option_entry": round(entry_ltp, 2),
        "option_target": round(tgt1_opt, 2),
        "option_sl": round(sl_opt, 2),
        "status": "ACTIVE",
    }

    existing = rec_store.get_active_sentinel_recommendation(symbol, atm_strike, side)
    if existing:
        contributing = existing.get("contributing_signals") or []
        if any(s.get("signal_id") == signal.event_id for s in contributing):
            return existing

        contributing.append(sig_summary)
        active_sigs = [s for s in contributing if s.get("status") == "ACTIVE"]
        max_opt_target = max(float(s["option_target"]) for s in active_sigs)
        if bias == "LONG":
            max_spot_target = max(float(s["spot_target"]) for s in active_sigs)
        else:
            max_spot_target = min(float(s["spot_target"]) for s in active_sigs)

        eff_sl = _stack_option_sl(active_sigs, sl_opt)
        eff_spot_inval = _protective_spot_inval(side, active_sigs) or spot_inval

        existing["spot_target"] = max_spot_target
        existing["spot_invalidation"] = eff_spot_inval
        existing["option_target"] = max_opt_target
        existing["option_sl"] = eff_sl
        existing["contributing_signals"] = contributing
        existing["contributing_signals_json"] = json.dumps(contributing)
        existing["current_spot"] = spot
        existing["current_ltp"] = entry_ltp
        existing["latest_signal_at"] = trig_ts
        existing["latest_signal_type"] = signal.type
        existing["expiry"] = resolved_expiry
        existing["dte_days"] = float(ctx["dte_days"])
        existing["atm_iv"] = float(ctx["atm_iv"])

        rec_store.save_or_update_sentinel_recommendation(existing)
        _sync_paper_notes(
            existing.get("paper_trade_id"),
            f"Stacked {signal.event_id}: tgt={max_opt_target}, spot_inval={eff_spot_inval}, opt_sl={eff_sl}",
        )
        log.info(
            "Linked signal %s to %s %s. Max tgt %.1f spot_inval %.1f",
            signal.event_id, atm_strike, side, max_opt_target, eff_spot_inval,
        )
        return existing

    rec_id = _stable_rec_id(symbol, atm_strike, side, session_day)
    lot_size = _get_lot_size(symbol)
    force_paper = bool(open_paper) if open_paper is not None else False
    pt_id = _open_or_skip_paper(
        symbol=symbol,
        side=side,
        strike=atm_strike,
        entry_ltp=entry_ltp,
        expiry_tag=resolved_expiry,
        spot=spot,
        spot_target=spot_target,
        spot_inval=spot_inval,
        opt_target=tgt1_opt,
        opt_sl=sl_opt,
        signal=signal,
        lot_size=lot_size,
        force=force_paper,
    )

    rec_data = {
        "rec_id": rec_id,
        "symbol": symbol,
        "strike": atm_strike,
        "side": side,
        "option_symbol": opt_sym,
        "triggered_at": trig_ts,
        "status": "ACTIVE",
        "spot_entry": spot,
        "spot_target": spot_target,
        "spot_invalidation": spot_inval,
        "option_entry": entry_ltp,
        "option_target": tgt1_opt,
        "option_sl": sl_opt,
        "current_ltp": entry_ltp,
        "current_spot": spot,
        "pnl_pts": 0.0,
        "pnl_pct": 0.0,
        "paper_trade_id": pt_id,
        "contributing_signals_json": json.dumps([sig_summary]),
        "outcome_ts": None,
        "exit_price": None,
        "exit_reason": None,
        "latest_signal_at": trig_ts,
        "latest_signal_type": signal.type,
        "expiry": resolved_expiry,
        "dte_days": float(ctx["dte_days"]),
        "atm_iv": float(ctx["atm_iv"]),
        "p_touch_target_1": float(p_t1),
        "p_touch_sl": float(p_sl),
        "net_ev_pts": float(net_ev),
        "risk_reward_ratio": str(rr_str),
    }

    rec_store.save_or_update_sentinel_recommendation(rec_data)
    log.info(
        "Opened Sentinel recommendation %s: %s %d %s spot=%.1f tgt=%.1f inval=%.1f entry=%.1f paper=%s",
        rec_id, symbol, int(atm_strike), side, spot, spot_target, spot_inval, entry_ltp, pt_id or "pending",
    )
    return rec_data


def confirm_paper_trade(rec_id: str) -> dict | None:
    """Explicit user confirm: open a paper trade for an ACTIVE recommendation without one."""
    ensure = rec_store.list_sentinel_recommendations(status="ACTIVE", limit=500)
    rec = next((r for r in ensure if r.get("rec_id") == rec_id), None)
    if rec is None:
        rows = rec_store.list_sentinel_recommendations(status="ALL", limit=500)
        rec = next((r for r in rows if r.get("rec_id") == rec_id), None)
    if rec is None:
        return None
    if rec.get("paper_trade_id"):
        return rec
    if str(rec.get("status") or "").upper() != "ACTIVE":
        return None

    class _Sig:
        event_id = f"confirm_{rec_id}"
        type = rec.get("latest_signal_type") or "CONTINUATION"
        bias = "LONG" if rec.get("side") == "CE" else "SHORT"
        score = 0
        rvol_tod = 1.0

    pt_id = _open_or_skip_paper(
        symbol=str(rec["symbol"]),
        side=str(rec["side"]),
        strike=float(rec["strike"]),
        entry_ltp=float(rec["option_entry"]),
        expiry_tag=str(rec.get("expiry") or ""),
        spot=float(rec["spot_entry"]),
        spot_target=float(rec["spot_target"]),
        spot_inval=float(rec["spot_invalidation"]),
        opt_target=float(rec["option_target"]),
        opt_sl=float(rec["option_sl"]),
        signal=_Sig(),  # type: ignore[arg-type]
        lot_size=_get_lot_size(str(rec["symbol"])),
        force=True,
    )
    if not pt_id:
        return None
    rec["paper_trade_id"] = pt_id
    rec_store.save_or_update_sentinel_recommendation(rec)
    return rec


def _get_closed_5m_candles(
    bars_5m: Sequence[Bar] | None = None,
    bars_1m: Sequence[Bar] | None = None,
    after_ts_str: str = "",
) -> list[Bar]:
    """Completed 5-minute candles strictly closed after after_ts_str."""
    closed: list[Bar] = []
    after_dt = None
    if after_ts_str:
        try:
            after_dt = datetime.fromisoformat(after_ts_str)
            if after_dt.tzinfo is None:
                after_dt = after_dt.replace(tzinfo=IST)
        except Exception:
            after_dt = None

    if bars_5m:
        for b5 in sorted(bars_5m, key=lambda b: b.start_ts):
            end_ts = b5.start_ts + timedelta(minutes=5)
            if after_dt and end_ts <= after_dt:
                continue
            if not getattr(b5, "incomplete", False):
                closed.append(b5)
        return closed

    if not bars_1m:
        return []

    buckets: dict[datetime, list[Bar]] = {}
    for b in bars_1m:
        ts = b.start_ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        floored = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
        buckets.setdefault(floored, []).append(b)

    now_ist = datetime.now(IST)
    for start in sorted(buckets):
        end = start + timedelta(minutes=5)
        if end > now_ist:
            continue
        if after_dt and end <= after_dt:
            continue
        group = sorted(buckets[start], key=lambda x: x.start_ts)
        if len(group) < 3:
            continue
        o = float(group[0].open)
        h = max(float(g.high) for g in group)
        l = min(float(g.low) for g in group)
        c = float(group[-1].close)
        closed.append(
            Bar(
                instrument_id=group[0].instrument_id,
                timeframe="5m",
                start_ts=start,
                open=o,
                high=h,
                low=l,
                close=c,
                volume_units=sum(float(g.volume_units or 0) for g in group),
                volume_lots=sum(float(getattr(g, "volume_lots", 0) or 0) for g in group),
                buy_vol_est=0.0,
                sell_vol_est=0.0,
                delta=sum(float(g.delta or 0) for g in group),
                delta_ratio=0.0,
                delta_quality=1.0,
                cvd=float(group[-1].cvd or 0),
                vwap=float(group[-1].vwap or c),
                vwap_sigma_1=0.0,
                vwap_sigma_2=0.0,
                vwap_lower_1=0.0,
                vwap_lower_2=0.0,
                oi=0,
                delta_oi=0,
                oi_fresh=False,
                incomplete=False,
                depth_imbalance=0.0,
            )
        )
    return closed


def evaluate_sentinel_recommendations_lifecycle(
    symbol: str,
    current_spot: float,
    options_ltp_map: dict | None = None,
    bars_5m: Sequence[Bar] | None = None,
    bars_1m: Sequence[Bar] | None = None,
) -> list[dict]:
    """Lifecycle: target on touch; spot invalidation stop on completed 5m cash close."""
    if current_spot <= 0:
        return []

    symbol = _resolve_symbol(symbol)
    if not symbol:
        return []

    active_recs = rec_store.list_active_sentinel_recommendations(symbol)
    if not active_recs:
        return []

    events_fired = []
    now_ts = _now_ist_str()

    for rec in active_recs:
        rec_id = rec["rec_id"]
        strike = float(rec["strike"])
        side = rec["side"]
        opt_sym = rec.get("option_symbol", "")
        opt_entry = float(rec["option_entry"])
        opt_target = float(rec["option_target"])
        opt_sl = float(rec["option_sl"])
        spot_target = float(rec["spot_target"])
        spot_inval = float(rec["spot_invalidation"])
        pt_id = rec.get("paper_trade_id")

        curr_ltp = _resolve_option_entry_ltp(symbol, strike, side, opt_sym, options_ltp_map)
        has_live_ltp = curr_ltp is not None and curr_ltp > 0
        disp_ltp = curr_ltp if has_live_ltp else opt_entry
        pnl_pts = round(disp_ltp - opt_entry, 2)
        pnl_pct = round((pnl_pts / opt_entry) * 100.0, 1) if opt_entry > 0 else 0.0

        rec["current_ltp"] = disp_ltp
        rec["current_spot"] = current_spot
        rec["pnl_pts"] = pnl_pts
        rec["pnl_pct"] = pnl_pct

        target_hit = False
        if has_live_ltp and opt_target > 0 and curr_ltp >= opt_target:
            target_hit = True
        elif side == "CE" and spot_target > 0 and current_spot >= spot_target:
            target_hit = True
        elif side == "PE" and spot_target > 0 and current_spot <= spot_target:
            target_hit = True

        if target_hit:
            exit_px = max(curr_ltp, opt_target) if has_live_ltp else opt_target
            rec_store.update_sentinel_recommendation_status(
                rec_id=rec_id,
                status="TARGET_HIT",
                exit_price=exit_px,
                exit_reason="TARGET",
                outcome_ts=now_ts,
            )
            if pt_id:
                try:
                    journal_store.close_trade(pt_id, exit_net_premium=-exit_px, exit_reason="TARGET")
                except Exception as e_close:
                    log.warning("Could not close paper trade %s on TARGET_HIT: %s", pt_id, e_close)
            events_fired.append({
                "rec_id": rec_id,
                "status": "TARGET_HIT",
                "exit_price": exit_px,
                "message": f"Target hit at ₹{exit_px:.1f} (+{pnl_pct:+.1f}%)",
            })
            continue

        closed_5m = _get_closed_5m_candles(bars_5m, bars_1m, rec.get("triggered_at", ""))
        contributing = rec.get("contributing_signals") or []
        spot_inval_hit = False
        levels_adjusted = False

        for c5 in closed_5m:
            c_close = float(c5.close)
            for s in contributing:
                if s.get("status") != "ACTIVE":
                    continue
                s_inval = float(s.get("spot_invalidation", 0.0))
                is_inv = (side == "CE" and c_close < s_inval) or (side == "PE" and c_close > s_inval)
                if is_inv:
                    s["status"] = "INVALIDATED"
                    s["invalidated_at"] = (
                        c5.start_ts.strftime("%Y-%m-%d %H:%M:%S")
                        if hasattr(c5.start_ts, "strftime") else str(c5.start_ts)
                    )
                    levels_adjusted = True

            active_remaining = [s for s in contributing if s.get("status") == "ACTIVE"]
            # Full stop only when every contributing signal is gone, or the close
            # breaches the protective inval of *remaining* signals (after fallback).
            if contributing and not active_remaining:
                spot_inval_hit = True
                break
            if active_remaining:
                eff_inval = _protective_spot_inval(side, active_remaining)
                if eff_inval is None:
                    continue
                is_strike_inval = (
                    (side == "CE" and c_close < eff_inval)
                    or (side == "PE" and c_close > eff_inval)
                )
                if is_strike_inval:
                    for s in active_remaining:
                        s["status"] = "INVALIDATED"
                        s["invalidated_at"] = (
                            c5.start_ts.strftime("%Y-%m-%d %H:%M:%S")
                            if hasattr(c5.start_ts, "strftime") else str(c5.start_ts)
                        )
                    spot_inval_hit = True
                    break

        if levels_adjusted and not spot_inval_hit:
            active_remaining = [s for s in contributing if s.get("status") == "ACTIVE"]
            if active_remaining:
                new_opt_target = max(float(s["option_target"]) for s in active_remaining)
                if side == "CE":
                    new_spot_target = max(float(s["spot_target"]) for s in active_remaining)
                else:
                    new_spot_target = min(float(s["spot_target"]) for s in active_remaining)
                new_spot_inval = _protective_spot_inval(side, active_remaining) or spot_inval
                new_opt_sl = _stack_option_sl(active_remaining, opt_sl)

                rec["option_target"] = new_opt_target
                rec["spot_target"] = new_spot_target
                rec["spot_invalidation"] = new_spot_inval
                rec["option_sl"] = new_opt_sl
                rec["contributing_signals"] = contributing
                rec["contributing_signals_json"] = json.dumps(contributing)
                rec_store.save_or_update_sentinel_recommendation(rec)
                _sync_paper_notes(
                    pt_id,
                    f"Fallback after signal inval: tgt={new_opt_target}, spot_inval={new_spot_inval}",
                )
                spot_inval = new_spot_inval
                log.info(
                    "Signal invalidated for %s. Fallback tgt=%.1f spot_inval=%.1f",
                    rec_id, new_opt_target, new_spot_inval,
                )

        if spot_inval_hit:
            exit_px = curr_ltp if has_live_ltp else (opt_sl if opt_sl > 0 else opt_entry)
            rec_store.update_sentinel_recommendation_status(
                rec_id=rec_id,
                status="SPOT_INVAL",
                exit_price=exit_px,
                exit_reason="SPOT_INVAL",
                outcome_ts=now_ts,
            )
            if pt_id:
                try:
                    journal_store.close_trade(pt_id, exit_net_premium=-exit_px, exit_reason="SPOT_INVAL")
                except Exception as e_close:
                    log.warning("Could not close paper trade %s on SPOT_INVAL: %s", pt_id, e_close)
            events_fired.append({
                "rec_id": rec_id,
                "status": "SPOT_INVAL",
                "exit_price": exit_px,
                "message": f"Spot invalidation on 5m close at ₹{exit_px:.1f} ({pnl_pct:+.1f}%)",
            })
            continue

        rec_store.save_or_update_sentinel_recommendation(rec)

    return events_fired


def get_sentinel_daily_metrics(symbol: str | None = None, session_date: str = "today") -> dict:
    """Daily success metrics for the Sentinel recommendation book."""
    recs = rec_store.list_sentinel_recommendations(symbol=symbol, session_date=session_date, status="ALL", limit=500)
    total_trades = len(recs)
    active_count = sum(1 for r in recs if r.get("status") == "ACTIVE")
    tgt_hits = sum(1 for r in recs if r.get("status") == "TARGET_HIT")
    # SPOT_INVAL is the stop; SL_HIT kept for legacy rows.
    stop_hits = sum(1 for r in recs if r.get("status") in ("SPOT_INVAL", "SL_HIT"))
    resolved = tgt_hits + stop_hits
    win_rate = round((tgt_hits / resolved) * 100.0, 1) if resolved > 0 else 0.0

    realized_inr = 0.0
    wins_inr = 0.0
    losses_inr = 0.0
    win_pts_list: list[float] = []
    loss_pts_list: list[float] = []

    for r in recs:
        status = r.get("status")
        if status not in ("TARGET_HIT", "SPOT_INVAL", "SL_HIT"):
            continue

        entry_px = float(r.get("option_entry") or 0.0)
        exit_px = float(r.get("exit_price") or entry_px)
        lot = _get_lot_size(r.get("symbol", "NIFTY"))
        pt_id = r.get("paper_trade_id")
        pt = journal_store.get_trade(pt_id) if pt_id else None

        if pt and pt.net_realized_pnl is not None:
            pnl_val = float(pt.net_realized_pnl)
        else:
            gross = (exit_px - entry_px) * lot
            costs = 40.0 + (1.0 * lot)
            pnl_val = gross - costs

        realized_inr += pnl_val
        pts_diff = exit_px - entry_px
        if status == "TARGET_HIT":
            wins_inr += max(0.0, pnl_val)
            win_pts_list.append(max(0.0, pts_diff))
        else:
            losses_inr += abs(min(0.0, pnl_val))
            loss_pts_list.append(abs(min(0.0, pts_diff)))

    avg_win_pts = round(sum(win_pts_list) / len(win_pts_list), 1) if win_pts_list else 0.0
    avg_loss_pts = round(sum(loss_pts_list) / len(loss_pts_list), 1) if loss_pts_list else 0.0
    realized_rr = round(avg_win_pts / avg_loss_pts, 2) if avg_loss_pts > 0 else (avg_win_pts if avg_win_pts > 0 else 0.0)
    profit_factor = round(wins_inr / losses_inr, 2) if losses_inr > 0 else (round(wins_inr / 1.0, 2) if wins_inr > 0 else 0.0)

    return {
        "symbol": symbol or "ALL",
        "session_date": session_date,
        "total_trades": total_trades,
        "active_trades": active_count,
        "resolved_trades": resolved,
        "target_hits": tgt_hits,
        "sl_hits": stop_hits,
        "spot_inval_hits": stop_hits,
        "win_rate_pct": win_rate,
        "realized_pnl_inr": round(realized_inr, 2),
        "realized_rr": realized_rr,
        "profit_factor": profit_factor,
        "avg_win_pts": avg_win_pts,
        "avg_loss_pts": avg_loss_pts,
        "auto_paper": is_auto_paper_enabled(),
        "refusals": get_refusal_metrics(),
        "contract": {
            "target": "touch (option LTP or cash spot target)",
            "stop": "SPOT_INVAL on completed 5m cash close",
            "paper": "auto-paper on by default (confirm if toggled off)",
        },
    }
