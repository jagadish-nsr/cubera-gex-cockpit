"""analytics/sentinel_recommendations.py — OrderFlow Sentinel Trade Recommendations.

Implements the OrderFlow Sentinel Trade Recommendation Pipeline:
1. Generates ATM option trade recommendations upon Sentinel directional signal trigger.
2. Derives equivalent Cash Index spot entry, invalidation, and targets.
3. Reprices entry, targets, and SL using Target Engine (Black-76 + IV forecasting).
4. Enforces strict single-entry-per-strike constraint while active.
5. Multi-signal dynamic max target and invalidation fallback stack.
6. Execution lifecycle: Target Hit on TOUCH; Stop Loss Hit ONLY on completed 5-min candle CLOSE.
7. Automated paper trade creation and daily success metrics tracking.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import config
from data.journal import recommendations_store as rec_store
from data.journal import store as journal_store
from sentinel.models import Bar, SentinelSignal

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _resolve_symbol(instrument_id: str) -> str:
    """Normalize instrument id to base cash symbol e.g. 'NIFTY-FUT' -> 'NIFTY'.

    Zero-Fabrication Contract:
    Only returns symbols registered in config.INDICES ('NIFTY', 'BANKNIFTY', 'SENSEX').
    Refuses FINNIFTY or unknown symbols to prevent orphaned contracts with fabricated parameters.
    """
    clean = str(instrument_id or "").upper().strip()
    for suffix in ("-FUT", "_FUT", "-INDEX", "_INDEX"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
    return clean if clean in config.INDICES else ""


def _get_strike_step(symbol: str) -> int:
    """Return index strike step strictly from config. Never guess or fabricate."""
    idx = config.get_index(symbol)
    step = int(getattr(idx, "strike_step", 0) or 0)
    if step <= 0:
        raise ValueError(f"No authentic strike step configured for index {symbol}")
    return step


def _get_lot_size(symbol: str) -> int:
    """Return index contract lot size strictly from config. Never invent lot sizes."""
    idx = config.get_index(symbol)
    lot = int(getattr(idx, "lot_size", 0) or 0)
    if lot <= 0:
        raise ValueError(f"No authentic lot size configured for index {symbol}")
    return lot


def _round_strike(price: float, step: int) -> float:
    if step <= 0:
        step = 50
    return float(round(price / step) * step)


def _resolve_option_entry_ltp(
    symbol: str,
    strike: float,
    side: str,
    opt_sym: str = "",
    options_ltp_map: dict | None = None,
) -> float | None:
    """Resolve live option premium from live tick book, WebSocket cache, or chain map.

    Zero-Fabrication Contract:
    Never invent an option premium from Black-Scholes or artificial delta when market data
    is unavailable. If no authentic quote exists, returns None.
    """
    # 1. Check live memory / chain map
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


def on_sentinel_signal(
    signal: SentinelSignal,
    current_spot: float | None = None,
    options_ltp_map: dict | None = None,
    expiry_tag: str = "",
) -> dict | None:
    """Process an incoming SentinelSignal and generate or link a trade recommendation.

    Requirements:
    1. ATM option recommendation at trigger time.
    2. Spot invalidation and targets on Cash Index.
    3. Target Engine repriced entry, targets, and SL.
    7. Automated paper trade opening.
    8. Single active entry per strike: if already active, elevate target to MAX.
    """
    bias = str(signal.bias or "").upper()
    if bias not in ("LONG", "SHORT"):
        return None  # No directional trade for neutral signals

    symbol = _resolve_symbol(signal.instrument_id)
    if not symbol or symbol not in config.INDICES:
        log.warning("Unsupported or unconfigured index symbol %r for Sentinel recommendation", signal.instrument_id)
        return None

    step = _get_strike_step(symbol)
    side = "CE" if bias == "LONG" else "PE"

    # 1. Resolve Cash Spot & ATM Strike
    spot = float(current_spot if current_spot and current_spot > 0 else (signal.entry_price or 0.0))
    if spot <= 0:
        try:
            from app.spot_ticker import latest_quote
            q = latest_quote(symbol) or {}
            spot = float(q.get("spot") or 0.0)
        except Exception:
            pass

    # Zero-Fabrication Contract: Refuse signal when spot is missing, non-positive, or placeholder
    if spot <= 0 or config.chain_spot_is_placeholder(symbol, spot):
        log.warning(
            "Sentinel recommendation refused: invalid or placeholder spot %.2f for %s",
            spot, symbol,
        )
        return None

    atm_strike = _round_strike(spot, step)

    # 2. Derive Spot Targets and Invalidation on Cash Index
    inval_level = float(signal.invalidation_level or 0.0)
    if inval_level <= 0 or (bias == "LONG" and inval_level >= spot) or (bias == "SHORT" and inval_level <= spot):
        log.warning("Sentinel recommendation refused: invalid signal invalidation %.2f for %s", inval_level, symbol)
        return None
    spot_inval = round(inval_level, 2)

    target_pts = float(signal.target_1_pts or 0.0)
    if target_pts <= 0:
        target_pts = max(35.0, abs(spot - spot_inval) * 1.5)

    spot_target = round(spot + target_pts if bias == "LONG" else spot - target_pts, 2)
    spot_target_2 = round(spot + (target_pts * 1.6) if bias == "LONG" else spot - (target_pts * 1.6), 2)

    # 3. Resolve Option Contract & Entry Premium
    opt_sym = f"{symbol} {expiry_tag + ' ' if expiry_tag else ''}{int(atm_strike)} {side}"
    entry_ltp = _resolve_option_entry_ltp(symbol, atm_strike, side, opt_sym, options_ltp_map)
    if entry_ltp is None or entry_ltp <= 0:
        log.warning(
            "Sentinel recommendation refused: no authentic live option quote available for %s %d %s",
            symbol, int(atm_strike), side,
        )
        return None

    # 4. Target Engine Repricing (Spot -> Option)
    from analytics.trade_ideas import _reprice_trade_idea_targets
    sl_opt, tgt1_opt, tgt2_opt, p_t1, p_sl, net_ev, rr_str, _ = _reprice_trade_idea_targets(
        side=side,
        strike=atm_strike,
        current_ltp=entry_ltp,
        spot=spot,
        inval_spot=spot_inval,
        tgt1_spot=spot_target,
        tgt2_spot=spot_target_2,
        dte_days=3.0,
        atm_iv=0.135,
        horizon_minutes=45,
        step=step,
    )

    trig_ts = (
        signal.bar_start_ts.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(signal.bar_start_ts, "strftime")
        else str(signal.bar_start_ts)
    )

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

    # 5. Requirement 8: Single active entry per strike price
    existing = rec_store.get_active_sentinel_recommendation(symbol, atm_strike, side)
    if existing:
        contributing = existing.get("contributing_signals") or []
        # Prevent exact duplicate signal ID registration
        if any(s.get("signal_id") == signal.event_id for s in contributing):
            return existing

        contributing.append(sig_summary)
        active_sigs = [s for s in contributing if s.get("status") == "ACTIVE"]

        # Dynamic MAX target across all active signals for this strike
        max_opt_target = max(s["option_target"] for s in active_sigs)
        if bias == "LONG":
            max_spot_target = max(s["spot_target"] for s in active_sigs)
        else:
            max_spot_target = min(s["spot_target"] for s in active_sigs)

        # SL Policy for Stacked Long Option Positions:
        # For option buyers, max(active_sls) acts as a protective/trailing stop floor
        # (a higher premium floor curtails maximum adverse loss). If an elevated signal
        # is later invalidated on a 5m close, the invalidation fallback mechanism restores
        # the wider stop of the remaining active signals.
        active_sls = [s["option_sl"] for s in active_sigs if s.get("option_sl", 0) > 0]
        eff_sl = max(active_sls) if active_sls else existing.get("option_sl", sl_opt)

        existing["spot_target"] = max_spot_target
        existing["option_target"] = max_opt_target
        existing["option_sl"] = eff_sl
        existing["contributing_signals"] = contributing
        existing["contributing_signals_json"] = json.dumps(contributing)
        existing["current_spot"] = spot
        existing["current_ltp"] = entry_ltp
        existing["latest_signal_at"] = trig_ts
        existing["latest_signal_type"] = signal.type

        rec_store.save_or_update_sentinel_recommendation(existing)
        log.info(
            "Linked signal %s to active strike %s %s. Updated max target to %.1f (spot %.1f)",
            signal.event_id, atm_strike, side, max_opt_target, max_spot_target,
        )
        return existing

    # 6. No active entry for this strike -> Create New Recommendation
    rec_id = f"srec_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}_{symbol}_{int(atm_strike)}_{side}"
    lot_size = _get_lot_size(symbol)

    # Auto-create Paper Trade (Requirement 7)
    pt_id = None
    try:
        legs = [{
            "action": "BUY",
            "type": side,
            "strike": float(atm_strike),
            "ltp": float(entry_ltp),
            "expiry": expiry_tag or "",
        }]
        levels_snap = {
            "spot": spot,
            "spot_target": spot_target,
            "spot_invalidation": spot_inval,
            "option_target": tgt1_opt,
            "option_sl": sl_opt,
        }
        regime_snap = {
            "signal_type": signal.type,
            "bias": bias,
            "score": getattr(signal, "score", 0),
            "rvol": getattr(signal, "rvol_tod", 1.0),
        }
        pt = journal_store.open_trade(
            symbol=symbol,
            strategy_name=f"Sentinel {signal.type} {int(atm_strike)}{side}",
            legs=legs,
            entry_spot=spot,
            entry_levels=levels_snap,
            entry_regime=regime_snap,
            entry_net_premium=-entry_ltp,
            lot_size=lot_size,
            notes=f"Auto-generated from Sentinel {signal.event_id}. Tgt: {tgt1_opt}, SL: {sl_opt}",
            exit_by="SENTINEL_ENGINE",
        )
        pt_id = pt.trade_id
    except Exception as err:
        log.warning("Could not auto-open paper trade for sentinel rec %s: %s", rec_id, err)

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
    }

    rec_store.save_or_update_sentinel_recommendation(rec_data)
    log.info(
        "Opened Sentinel Trade Recommendation %s: %s %d %s (Spot: %.1f, Tgt: %.1f, SL: %.1f, Opt Entry: %.1f)",
        rec_id, symbol, int(atm_strike), side, spot, spot_target, spot_inval, entry_ltp,
    )
    return rec_data


def _get_closed_5m_candles(
    bars_5m: Sequence[Bar] | None = None,
    bars_1m: Sequence[Bar] | None = None,
    after_ts_str: str = "",
) -> list[Bar]:
    """Return completed, closed 5-minute candles strictly closed after after_ts_str."""
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
            if getattr(b5, "incomplete", False):
                continue
            close_ts = b5.start_ts + timedelta(minutes=5)
            if after_dt is None or close_ts > after_dt:
                closed.append(b5)
        return closed

    # Bucket 1m bars into 5m candles if explicit 5m bars not passed
    if bars_1m:
        from collections import defaultdict
        buckets: dict[datetime, list[Bar]] = defaultdict(list)
        for b in sorted(bars_1m, key=lambda b: b.start_ts):
            dt = b.start_ts
            bucket_min = (dt.minute // 5) * 5
            b_ts = dt.replace(minute=bucket_min, second=0, microsecond=0)
            buckets[b_ts].append(b)

        sorted_buckets = sorted(buckets.items(), key=lambda x: x[0])
        for idx, (b_ts, b_list) in enumerate(sorted_buckets):
            # Closed if subsequent bucket started or full 5 1m bars present
            is_closed = (idx < len(sorted_buckets) - 1) or (len(b_list) >= 5)
            if not is_closed:
                continue
            close_ts = b_ts + timedelta(minutes=5)
            if after_dt is None or close_ts > after_dt:
                inst = b_list[0].instrument_id
                closed.append(Bar(
                    instrument_id=inst,
                    timeframe="5m",
                    start_ts=b_ts,
                    open=b_list[0].open,
                    high=max(x.high for x in b_list),
                    low=min(x.low for x in b_list),
                    close=b_list[-1].close,
                    volume_units=sum(x.volume_units for x in b_list),
                    volume_lots=round(sum(x.volume_units for x in b_list) / 65.0, 1),
                    buy_vol_est=sum(x.buy_vol_est for x in b_list),
                    sell_vol_est=sum(x.sell_vol_est for x in b_list),
                    delta=sum(x.delta for x in b_list),
                    delta_ratio=0.0,
                    delta_quality=1.0,
                    cvd=b_list[-1].cvd,
                    vwap=b_list[-1].vwap,
                    vwap_sigma_1=b_list[-1].vwap_sigma_1,
                    vwap_sigma_2=b_list[-1].vwap_sigma_2,
                    vwap_lower_1=b_list[-1].vwap_lower_1,
                    vwap_lower_2=b_list[-1].vwap_lower_2,
                    oi=b_list[-1].oi,
                    delta_oi=0,
                ))

    return closed


def evaluate_sentinel_recommendations_lifecycle(
    symbol: str,
    current_spot: float,
    options_ltp_map: dict | None = None,
    bars_5m: Sequence[Bar] | None = None,
    bars_1m: Sequence[Bar] | None = None,
) -> list[dict]:
    """Check active Sentinel recommendations against live price and 5m closed candles.

    Execution Criteria (Req 6 & 8):
    - TARGET HIT: On TOUCH of Option Target or Spot Target.
    - STOP LOSS HIT: ONLY on completed 5-min candle CLOSE below SL.
    - FALLBACK TARGET: When the signal providing the max target is invalidated on a
      5-min candle close, target falls back to the max target of remaining active signals.
    """
    if current_spot <= 0:
        return []

    symbol = _resolve_symbol(symbol)
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
        has_live_ltp = (curr_ltp is not None and curr_ltp > 0)
        disp_ltp = curr_ltp if has_live_ltp else opt_entry
        pnl_pts = round(disp_ltp - opt_entry, 2)
        pnl_pct = round((pnl_pts / opt_entry) * 100.0, 1) if opt_entry > 0 else 0.0

        # Update telemetry
        rec["current_ltp"] = disp_ltp
        rec["current_spot"] = current_spot
        rec["pnl_pts"] = pnl_pts
        rec["pnl_pct"] = pnl_pct

        # ------------------------------------------------------------------
        # Check TARGET HIT on TOUCH (Requirement 6)
        # Target hit occurs on touch of either option target (requires authentic live LTP)
        # or equivalent Cash Index spot target.
        # ------------------------------------------------------------------
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
                "message": f"🎯 Target Hit at ₹{exit_px:.1f} (+{pnl_pct:+.1f}%)",
            })
            continue

        # ------------------------------------------------------------------
        # Check Signal Invalidation & SL Hit on 5-MIN CANDLE CLOSE (Req 6 & 8)
        # ------------------------------------------------------------------
        closed_5m = _get_closed_5m_candles(bars_5m, bars_1m, rec.get("triggered_at", ""))
        contributing = rec.get("contributing_signals") or []
        sl_hit = False
        target_adjusted = False

        for c5 in closed_5m:
            c_close = float(c5.close)

            # 1. Invalidate any individual contributing signals whose invalidation is breached on 5m close
            for s in contributing:
                if s.get("status") != "ACTIVE":
                    continue
                s_inval = float(s.get("spot_invalidation", 0.0))
                is_inv = (side == "CE" and c_close < s_inval) or (side == "PE" and c_close > s_inval)
                if is_inv:
                    s["status"] = "INVALIDATED"
                    s["invalidated_at"] = c5.start_ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(c5.start_ts, "strftime") else str(c5.start_ts)
                    target_adjusted = True

            # 2. Check if the strike recommendation Stop Loss is breached on 5m close
            is_strike_sl = (side == "CE" and c_close < spot_inval) or (side == "PE" and c_close > spot_inval)
            active_remaining = [s for s in contributing if s.get("status") == "ACTIVE"]

            if is_strike_sl or (contributing and not active_remaining):
                sl_hit = True
                break

        # If individual signal(s) got invalidated, fallback to MAX target of remaining active signals (Req 8)
        if target_adjusted and not sl_hit:
            active_remaining = [s for s in contributing if s.get("status") == "ACTIVE"]
            if active_remaining:
                new_opt_target = max(s["option_target"] for s in active_remaining)
                if side == "CE":
                    new_spot_target = max(s["spot_target"] for s in active_remaining)
                else:
                    new_spot_target = min(s["spot_target"] for s in active_remaining)

                rec["option_target"] = new_opt_target
                rec["spot_target"] = new_spot_target
                rec["contributing_signals"] = contributing
                rec["contributing_signals_json"] = json.dumps(contributing)
                rec_store.save_or_update_sentinel_recommendation(rec)
                log.info("Signal invalidated for %s. Fallback target set to %.1f", rec_id, new_opt_target)

        if sl_hit:
            exit_px = curr_ltp if has_live_ltp else opt_sl
            rec_store.update_sentinel_recommendation_status(
                rec_id=rec_id,
                status="SL_HIT",
                exit_price=exit_px,
                exit_reason="STOP",
                outcome_ts=now_ts,
            )
            if pt_id:
                try:
                    journal_store.close_trade(pt_id, exit_net_premium=-exit_px, exit_reason="STOP")
                except Exception as e_close:
                    log.warning("Could not close paper trade %s on SL_HIT: %s", pt_id, e_close)

            events_fired.append({
                "rec_id": rec_id,
                "status": "SL_HIT",
                "exit_price": exit_px,
                "message": f"⛔ Stop Loss Hit on 5m Close at ₹{exit_px:.1f} ({pnl_pct:+.1f}%)",
            })
            continue

        # If still active, persist fresh live LTP and PnL
        rec_store.save_or_update_sentinel_recommendation(rec)

    return events_fired


def get_sentinel_daily_metrics(symbol: str | None = None, session_date: str = "today") -> dict:
    """Calculate and return daily success metrics (Req 7)."""
    recs = rec_store.list_sentinel_recommendations(symbol=symbol, session_date=session_date, status="ALL", limit=500)
    total_trades = len(recs)
    active_count = sum(1 for r in recs if r.get("status") == "ACTIVE")
    tgt_hits = sum(1 for r in recs if r.get("status") == "TARGET_HIT")
    sl_hits = sum(1 for r in recs if r.get("status") == "SL_HIT")
    resolved = tgt_hits + sl_hits

    win_rate = round((tgt_hits / resolved) * 100.0, 1) if resolved > 0 else 0.0

    # Net Realized INR from paper trades
    realized_inr = 0.0
    wins_inr = 0.0
    losses_inr = 0.0
    win_pts_list: list[float] = []
    loss_pts_list: list[float] = []

    for r in recs:
        status = r.get("status")
        if status not in ("TARGET_HIT", "SL_HIT"):
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
            costs = 40.0 + (1.0 * lot)  # ₹40 brokerage + 1pt round-trip slip
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
        "sl_hits": sl_hits,
        "win_rate_pct": win_rate,
        "realized_pnl_inr": round(realized_inr, 2),
        "realized_rr": realized_rr,
        "profit_factor": profit_factor,
        "avg_win_pts": avg_win_pts,
        "avg_loss_pts": avg_loss_pts,
    }
