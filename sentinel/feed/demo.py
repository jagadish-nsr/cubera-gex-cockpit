"""sentinel/feed/demo.py — Demo Data Generator for OrderFlow Sentinel Tape.

Populates realistic intraday 1m/5m bars, CVD, VWAP bands, and OrderFlow signals
anchored to current live spot and GEX levels when live WebSocket stream is offline.
"""

from __future__ import annotations

import math
import random
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from sentinel.models import Bar, SentinelSignal, SignalOutcome
from sentinel.storage.db import (
    save_bar,
    save_signal,
    save_outcome,
    list_recent_bars,
    list_recent_signals,
    list_recent_outcomes,
    ensure_sentinel_schema,
    clear_sentinel_data,
    _WRITE_LOCK,
    _get_write_conn,
)
from sentinel.engine.aggregator import SessionVWAPTracker, aggregate_1m_to_5m, aggregate_bars
from sentinel.engine.outcomes import evaluate_signal_outcomes
from sentinel.engine.classifier import SignalClassifierEngine
from sentinel.engine.context import MarketContext

IST = timezone(timedelta(hours=5, minutes=30))


def _update_signal_walls_in_place(instrument_id: str, call_wall: float, put_wall: float) -> None:
    """Non-destructive update of GEX wall references in existing signals when walls shift."""
    try:
        existing_sigs = list_recent_signals(instrument_id, limit=30)
        for sig in existing_sigs:
            upd = False
            txt = sig.insight_text
            if put_wall and "Put Wall " in txt:
                m = re.search(r"Put Wall ([\d,]+)", txt)
                if m and abs(float(m.group(1).replace(",", "")) - put_wall) >= 25.0:
                    txt = re.sub(r"Put Wall [\d,]+", f"Put Wall {put_wall:,.0f}", txt)
                    upd = True
            if call_wall and "Call Wall " in txt:
                m = re.search(r"Call Wall ([\d,]+)", txt)
                if m and abs(float(m.group(1).replace(",", "")) - call_wall) >= 25.0:
                    txt = re.sub(r"Call Wall [\d,]+", f"Call Wall {call_wall:,.0f}", txt)
                    upd = True
            if upd:
                sig.insight_text = txt
                save_signal(sig)
    except Exception:
        pass


def seed_sentinel_demo_if_needed(
    instrument_id: str = "NIFTY-FUT",
    spot: float = 22680.0,
    call_wall: float = 22800.0,
    put_wall: float = 22500.0,
    force: bool = False,
) -> None:
    """Ensure completed intraday bars and authentic signals exist for today's session."""
    ensure_sentinel_schema()
    now_ist = datetime.now(IST)
    try:
        from data.holidays import is_trading_day, roll_back_to_trading_day
        is_live_trading_day = is_trading_day(now_ist.date())
    except Exception:
        is_live_trading_day = now_ist.weekday() < 5
        def roll_back_to_trading_day(d):
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d

    m_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    m_close = now_ist.replace(hour=15, minute=40, second=0, microsecond=0)

    existing_1m = list_recent_bars(instrument_id, timeframe="1m", limit=10)
    existing_5m = list_recent_bars(instrument_id, timeframe="5m", limit=10)
    if existing_1m and not force:
        last_b = existing_1m[-1]
        is_today = (last_b.start_ts.date() == now_ist.date())

        if is_live_trading_day and (m_open <= now_ist <= m_close):
            if not is_today:
                # Prior day's bars found during active market hours: start fresh for today
                force = True
            else:
                # Today's live session is already in progress: DO NOT wipe mid-day!
                # Update any wall references in-place if walls shifted
                _update_signal_walls_in_place(instrument_id, call_wall, put_wall)
        elif not is_trading_day(last_b.start_ts.date()):
            # Bars in DB are from an exchange holiday or weekend: purge and re-anchor
            force = True
        elif not is_live_trading_day or now_ist < m_open or now_ist > m_close:
            # Market closed: anchor to prior trading day
            ref_day = roll_back_to_trading_day(now_ist.date() - timedelta(days=1)) if (now_ist < m_open or not is_live_trading_day) else now_ist.date()
            if last_b.start_ts.date() != ref_day:
                force = True

    if len(existing_1m) >= 20 and len(existing_5m) >= 5 and not force:
        return

    if force:
        clear_sentinel_data(instrument_id)
    elif len(existing_1m) >= 20 and len(existing_5m) < 5:
        # 1m bars already exist: synthesize and save 5m bars without clearing 1m history
        bars_1m_all = list_recent_bars(instrument_id, timeframe="1m", limit=180)
        bars_5m = aggregate_1m_to_5m(bars_1m_all)
        for b5 in bars_5m:
            save_bar(b5)
        return

    if is_live_trading_day and (m_open <= now_ist <= m_close):
        # Market open: generate bars from market open (09:15) up to current minute
        end_time = now_ist.replace(second=0, microsecond=0)
        minutes_since_open = max(60, min(375, int((end_time - m_open).total_seconds() / 60) + 1))
        num_bars = minutes_since_open
        start_time = end_time - timedelta(minutes=num_bars - 1)
    else:
        # Market closed (after hours, weekend, or holiday):
        # Anchor bars to the closing hours of latest completed trading session
        if now_ist < m_open or not is_live_trading_day:
            ref_day = roll_back_to_trading_day(now_ist.date() - timedelta(days=1))
        else:
            ref_day = now_ist.date()
        end_time = datetime(ref_day.year, ref_day.month, ref_day.day, 15, 30, tzinfo=IST)
        num_bars = 120
        start_time = end_time - timedelta(minutes=num_bars - 1)

    vwap_tracker = SessionVWAPTracker()
    cur_price = spot - 25.0
    cur_cvd = 0.0
    base_vol = 12000
    lot_size = 10 if "SENSEX" in instrument_id else (15 if "BANKNIFTY" in instrument_id else 65)

    bars_to_save: list[Bar] = []

    for i in range(num_bars):
        bar_ts = start_time + timedelta(minutes=i)
        prog = i / max(1.0, float(num_bars - 1))
        # Smooth intraday wave towards call wall then smoothly converging to current spot
        drift = math.sin(i / 10.0) * 14.0 * (1.0 - prog)
        open_px = round(cur_price, 1)
        close_px = round((spot - 30.0) * (1.0 - prog) + spot * prog + drift, 1) if i < (num_bars - 1) else round(spot, 1)
        high_px = round(max(open_px, close_px) + abs(math.sin(i * 1.3) * 4.0) + 0.8, 1)
        low_px = round(min(open_px, close_px) - abs(math.cos(i * 1.1) * 4.0) - 0.8, 1)
        cur_price = close_px

        # Volume & Delta
        vol = int(base_vol * (1.0 + 0.5 * math.sin(i / 6.0) ** 2))
        is_up = close_px >= open_px
        delta_pct = 0.25 if is_up else -0.22

        delta = round(vol * delta_pct, 1)
        cur_cvd += delta

        vwap, up1, up2, dn1, dn2 = vwap_tracker.update(close_px, float(vol))

        bar = Bar(
            instrument_id=instrument_id,
            timeframe="1m",
            start_ts=bar_ts,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume_units=vol,
            volume_lots=round(vol / lot_size, 1),
            buy_vol_est=round((vol + delta) / 2.0, 1),
            sell_vol_est=round((vol - delta) / 2.0, 1),
            delta=delta,
            delta_ratio=round(delta / vol, 3),
            delta_quality=1.0,
            cvd=round(cur_cvd, 1),
            vwap=round(vwap, 1),
            vwap_sigma_1=round(up1, 1),
            vwap_sigma_2=round(up2, 1),
            vwap_lower_1=round(dn1, 1),
            vwap_lower_2=round(dn2, 1),
            oi=int(14500000 + i * 15000),
            delta_oi=int(15000 * (1.2 if is_up else -0.8)),
        )
        bars_to_save.append(bar)
        save_bar(bar)

    # Aggregate into 3m, 5m, 15m candles and save
    bars_5m = aggregate_1m_to_5m(bars_to_save)
    for b5 in bars_5m:
        save_bar(b5)
    for tf_min in (3, 15):
        agg = aggregate_bars(bars_to_save, target_minutes=tf_min)
        for b_agg in agg:
            save_bar(b_agg)

    # Seed rich, realistic sequence of signals spanning the session
    signal_templates = [
        (
            0.10, "CONTINUATION_BULL", "BULLISH", "LONG", 76, "HIGH", 2.8,
            ["ABOVE_VWAP", "ABOVE_VWAP_1SIGMA", "ORB_HIGH"],
            "Volume 2.8x norm with rising OI and aggressive buyer delta; candle closes near high above VWAP.",
            -4.0, "CONFIRMED_AT_WALL"
        ),
        (
            0.20, "EXHAUSTION_UP", "CAUTION", "NEUTRAL", 68, "MEDIUM", 2.0,
            ["ABOVE_VWAP_1SIGMA"],
            "Buying volume exhausted on narrow range candle; momentum stalling near VWAP +1σ band.",
            2.5, "NEUTRAL"
        ),
        (
            0.32, "ABSORPTION_BEAR", "BEARISH", "SHORT", 82, "HIGH", 3.4,
            ["NEAR_CALL_WALL", "ABOVE_VWAP_2SIGMA"],
            f"Heavy aggressive buying absorbed at Call Wall {call_wall:,.0f}; upper wick formed with delta divergence.",
            3.0, "CONFIRMED_AT_WALL"
        ),
        (
            0.44, "CONTINUATION_BEAR", "BEARISH", "SHORT", 77, "HIGH", 2.6,
            ["BELOW_VWAP", "BELOW_VWAP_1SIGMA"],
            "Aggressive seller delta accelerating below VWAP with rising volume; downward momentum expanding.",
            3.5, "CONFIRMED_AT_WALL"
        ),
        (
            0.55, "CVD_DIVERGENCE_BULL", "BULLISH", "LONG", 74, "HIGH", 2.3,
            ["BELOW_VWAP_1SIGMA", "CVD_DIV_BULL"],
            "Price prints lower low while session Cumulative Volume Delta prints higher low; institutional accumulation.",
            -4.0, "NEUTRAL"
        ),
        (
            0.68, "ABSORPTION_BULL", "BULLISH", "LONG", 84, "HIGH", 3.2,
            ["NEAR_PUT_WALL", "BELOW_VWAP_2SIGMA"],
            f"Aggressive market selling absorbed at Put Wall {put_wall:,.0f}; responsive buyers defending structural support.",
            -4.0, "CONFIRMED_AT_WALL"
        ),
        (
            0.78, "CONTINUATION_BULL", "BULLISH", "LONG", 79, "HIGH", 2.7,
            ["ABOVE_VWAP", "ABOVE_VWAP_1SIGMA"],
            "Demand responsive bounce lifting offer back above VWAP with rising buyer delta ratio and OI build.",
            -3.5, "CONFIRMED_AT_WALL"
        ),
        (
            0.88, "EXHAUSTION_UP", "CAUTION", "NEUTRAL", 66, "MEDIUM", 1.9,
            ["ABOVE_VWAP"],
            "Volume exhaustion on narrow candle body; momentum slowing near session VWAP mean.",
            2.5, "NEUTRAL"
        ),
        (
            0.94, "CVD_DIVERGENCE_BEAR", "BEARISH", "SHORT", 73, "HIGH", 2.4,
            ["NEAR_CALL_WALL", "CVD_DIV_BEAR"],
            f"Price testing near Call Wall {call_wall:,.0f} with waning CVD momentum; institutional distribution capping upside.",
            3.0, "CONFIRMED_AT_WALL"
        ),
    ]

    for rel_pos, sig_type, cat, bias, score, conf, rvol, tags, text, inval_off, gex_align in signal_templates:
        idx = int(rel_pos * (len(bars_to_save) - 1))
        idx = max(3, min(len(bars_to_save) - 2, idx))
        trigger_bar = bars_to_save[idx]
        sig_ts = trigger_bar.start_ts
        inval_lvl = round(trigger_bar.low + inval_off, 1) if bias == "LONG" else round(trigger_bar.high + inval_off, 1)

        sig = SentinelSignal(
            event_id=f"sig_{sig_ts.strftime('%Y%m%d_%H%M%S')}_{instrument_id}_1m_{sig_type}",
            instrument_id=instrument_id,
            timeframe="1m",
            bar_start_ts=sig_ts,
            type=sig_type,
            category=cat,
            bias=bias,
            score=score,
            confidence=conf,
            rvol_tod=rvol,
            delta=trigger_bar.delta,
            delta_ratio=trigger_bar.delta_ratio,
            oi_change=trigger_bar.delta_oi,
            context_tags=tags,
            insight_text=text,
            invalidation_level=inval_lvl,
            rule_set_version="v2.0",
            status="CONFIRMED",
            gex_alignment=gex_align,
        )
        save_signal(sig)
        outcome = evaluate_signal_outcomes(sig, bars_to_save[idx + 1:])
        save_outcome(outcome)


class SentinelLiveFeedEngine:
    """Active live OrderFlow simulation generator.

    Advances the live intraday tape on 3-second live fragment ticks:
    - Increments the current candle's trade volume, delta, CVD, and session VWAP
    - Rolls over completed 1m candles into 3m, 5m, 15m aggregations
    - Evaluates SignalClassifierEngine to generate live signals dynamically into the stream
    - Updates MFE/MAE excursion outcomes on active signals
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._classifiers: dict[str, SignalClassifierEngine] = {}
        self._vwap_trackers: dict[str, SessionVWAPTracker] = {}
        self._last_tick_time: dict[str, float] = {}
        self._bars_since_signal: dict[str, int] = {}
        self._last_walls: dict[str, tuple[float, float]] = {}

    def step(
        self,
        instrument_id: str = "NIFTY-FUT",
        spot: float = 22680.0,
        call_wall: float = 22800.0,
        put_wall: float = 22500.0,
    ) -> None:
        """Advance simulated live order flow by one active incremental step."""
        with self._lock:
            # 1. Ensure initial demo data exists
            seed_sentinel_demo_if_needed(instrument_id, spot=spot, call_wall=call_wall, put_wall=put_wall)

            # 2. Throttle to at most once per 2 seconds
            now_epoch = time.time()
            last_epoch = self._last_tick_time.get(instrument_id, 0.0)
            if (now_epoch - last_epoch) < 2.0:
                return
            self._last_tick_time[instrument_id] = now_epoch

            # 3. Fetch latest 1m bar
            recent_1m = list_recent_bars(instrument_id, timeframe="1m", limit=3)
            if not recent_1m:
                return
            latest = recent_1m[-1]

            # Track GEX walls intraday and update recent signals in-place if walls shifted
            prev_walls = self._last_walls.get(instrument_id)
            walls_shifted = prev_walls is not None and (
                abs(prev_walls[0] - call_wall) >= 25.0 or abs(prev_walls[1] - put_wall) >= 25.0
            )
            self._last_walls[instrument_id] = (call_wall, put_wall)
            if walls_shifted:
                _update_signal_walls_in_place(instrument_id, call_wall, put_wall)

            # 4. Initialize tracker/classifier if not present
            if instrument_id not in self._vwap_trackers:
                tracker = SessionVWAPTracker()
                tracker.cum_vol = float(latest.volume_units * 30)
                tracker.cum_pv = float(latest.vwap * tracker.cum_vol)
                tracker.cum_p2v = float((latest.vwap ** 2 + 100.0) * tracker.cum_vol)
                self._vwap_trackers[instrument_id] = tracker

            if instrument_id not in self._classifiers:
                self._classifiers[instrument_id] = SignalClassifierEngine()

            vwap_tracker = self._vwap_trackers[instrument_id]
            classifier = self._classifiers[instrument_id]

            # 5. Check if we should rollover to next minute candle
            now_ist = datetime.now(IST)
            bar_elapsed = (now_ist - latest.start_ts).total_seconds()
            should_rollover = bar_elapsed >= 60.0 or (latest.incomplete is False and bar_elapsed >= 45.0)

            # Micro-tick dynamics
            is_near_cw = (latest.close >= (call_wall - 25.0)) if call_wall else False
            is_near_pw = (latest.close <= (put_wall + 25.0)) if put_wall else False

            if is_near_cw:
                delta_sign = -1 if random.random() < 0.65 else 1
                price_drift = random.uniform(-1.5, 0.5)
            elif is_near_pw:
                delta_sign = 1 if random.random() < 0.65 else -1
                price_drift = random.uniform(-0.5, 1.5)
            else:
                delta_sign = random.choice([1, -1])
                price_drift = random.uniform(-1.0, 1.0)

            vol_tick = random.randint(350, 1200)
            delta_tick = int(vol_tick * delta_sign * random.uniform(0.18, 0.45))
            lot_size = 10 if "SENSEX" in instrument_id else (15 if "BANKNIFTY" in instrument_id else 65)

            if should_rollover:
                # --- ROLLOVER: Complete previous bar and start new 1m candle ---
                context = MarketContext(
                    day_high=max(latest.high, spot + 25.0),
                    day_low=min(latest.low, spot - 25.0),
                    call_wall=call_wall,
                    put_wall=put_wall,
                    orb_complete=True,
                    orb_high=spot + 20.0,
                    orb_low=spot - 20.0,
                )

                sig = classifier.evaluate_bar(latest, context)
                bars_since = self._bars_since_signal.get(instrument_id, 0) + 1
                self._bars_since_signal[instrument_id] = bars_since

                if not sig and bars_since >= 4:
                    self._bars_since_signal[instrument_id] = 0
                    sig_ts = latest.start_ts
                    if is_near_cw:
                        sig = SentinelSignal(
                            event_id=f"sig_{sig_ts.strftime('%Y%m%d_%H%M%S')}_{instrument_id}_1m_ABSORPTION_BEAR",
                            instrument_id=instrument_id,
                            timeframe="1m",
                            bar_start_ts=sig_ts,
                            type="ABSORPTION_BEAR",
                            category="BEARISH",
                            bias="SHORT",
                            score=random.randint(74, 86),
                            confidence="HIGH",
                            rvol_tod=round(random.uniform(2.1, 3.2), 1),
                            delta=latest.delta,
                            delta_ratio=latest.delta_ratio,
                            oi_change=int(random.randint(8000, 24000)),
                            context_tags=["NEAR_CALL_WALL", "ABOVE_VWAP_1SIGMA"],
                            insight_text=f"Aggressive buyer delta absorbed at Call Wall {call_wall:,.0f}; institutional supply defending resistance.",
                            invalidation_level=round(latest.high + 4.0, 1),
                            rule_set_version="v2.0",
                            status="CONFIRMED",
                            gex_alignment="CONFIRMED_AT_WALL",
                        )
                    elif is_near_pw:
                        sig = SentinelSignal(
                            event_id=f"sig_{sig_ts.strftime('%Y%m%d_%H%M%S')}_{instrument_id}_1m_ABSORPTION_BULL",
                            instrument_id=instrument_id,
                            timeframe="1m",
                            bar_start_ts=sig_ts,
                            type="ABSORPTION_BULL",
                            category="BULLISH",
                            bias="LONG",
                            score=random.randint(74, 88),
                            confidence="HIGH",
                            rvol_tod=round(random.uniform(2.2, 3.4), 1),
                            delta=latest.delta,
                            delta_ratio=latest.delta_ratio,
                            oi_change=int(random.randint(9000, 26000)),
                            context_tags=["NEAR_PUT_WALL", "BELOW_VWAP_1SIGMA"],
                            insight_text=f"Aggressive market selling absorbed at Put Wall {put_wall:,.0f}; responsive buyers stepping in.",
                            invalidation_level=round(latest.low - 4.0, 1),
                            rule_set_version="v2.0",
                            status="CONFIRMED",
                            gex_alignment="CONFIRMED_AT_WALL",
                        )
                    elif delta_sign > 0 and latest.close > latest.vwap:
                        sig = SentinelSignal(
                            event_id=f"sig_{sig_ts.strftime('%Y%m%d_%H%M%S')}_{instrument_id}_1m_CONT_BULL",
                            instrument_id=instrument_id,
                            timeframe="1m",
                            bar_start_ts=sig_ts,
                            type="CONTINUATION_BULL",
                            category="BULLISH",
                            bias="LONG",
                            score=random.randint(72, 82),
                            confidence="HIGH",
                            rvol_tod=round(random.uniform(1.8, 2.7), 1),
                            delta=latest.delta,
                            delta_ratio=latest.delta_ratio,
                            oi_change=int(random.randint(12000, 32000)),
                            context_tags=["ABOVE_VWAP", "ABOVE_VWAP_1SIGMA"],
                            insight_text="Rising buyer volume & positive delta push with OI accumulation above session VWAP.",
                            invalidation_level=round(latest.low - 3.5, 1),
                            rule_set_version="v2.0",
                            status="CONFIRMED",
                            gex_alignment="NEUTRAL",
                        )
                    else:
                        sig = SentinelSignal(
                            event_id=f"sig_{sig_ts.strftime('%Y%m%d_%H%M%S')}_{instrument_id}_1m_EXHAUSTION_UP",
                            instrument_id=instrument_id,
                            timeframe="1m",
                            bar_start_ts=sig_ts,
                            type="EXHAUSTION_UP",
                            category="CAUTION",
                            bias="NEUTRAL",
                            score=random.randint(64, 72),
                            confidence="MEDIUM",
                            rvol_tod=round(random.uniform(1.6, 2.2), 1),
                            delta=latest.delta,
                            delta_ratio=latest.delta_ratio,
                            oi_change=int(random.randint(5000, 15000)),
                            context_tags=["ABOVE_VWAP"],
                            insight_text="Volume exhaustion on narrow candle body; momentum slowing near session extremes.",
                            invalidation_level=round(latest.high + 3.0, 1),
                            rule_set_version="v2.0",
                            status="NEW",
                            gex_alignment="NEUTRAL",
                        )

                if sig:
                    save_signal(sig)
                    outcome = evaluate_signal_outcomes(sig, [latest])
                    save_outcome(outcome)

                new_start_ts = latest.start_ts + timedelta(minutes=1)
                new_open = latest.close
                new_close = round(spot, 1)
                new_high = round(max(new_open, new_close) + abs(price_drift) * 0.5, 1)
                new_low = round(min(new_open, new_close) - abs(price_drift) * 0.5, 1)
                new_cvd = round(latest.cvd + delta_tick, 1)

                vwap, up1, up2, dn1, dn2 = vwap_tracker.update(new_close, float(vol_tick))

                new_bar = Bar(
                    instrument_id=instrument_id,
                    timeframe="1m",
                    start_ts=new_start_ts,
                    open=new_open,
                    high=new_high,
                    low=new_low,
                    close=new_close,
                    volume_units=vol_tick,
                    volume_lots=round(vol_tick / lot_size, 1),
                    buy_vol_est=round((vol_tick + delta_tick) / 2.0, 1),
                    sell_vol_est=round((vol_tick - delta_tick) / 2.0, 1),
                    delta=float(delta_tick),
                    delta_ratio=round(delta_tick / vol_tick, 3),
                    delta_quality=1.0,
                    cvd=new_cvd,
                    vwap=round(vwap, 1),
                    vwap_sigma_1=round(up1, 1),
                    vwap_sigma_2=round(up2, 1),
                    vwap_lower_1=round(dn1, 1),
                    vwap_lower_2=round(dn2, 1),
                    oi=int(latest.oi + random.randint(1000, 5000)),
                    delta_oi=int(random.randint(-1500, 3500)),
                    incomplete=True,
                )
                save_bar(new_bar)

                # Resample and save higher timeframes
                recent_all_1m = list_recent_bars(instrument_id, timeframe="1m", limit=120)
                for tf_m in (3, 5, 15):
                    agg = aggregate_bars(recent_all_1m, target_minutes=tf_m)
                    if agg:
                        save_bar(agg[-1])

                # Dynamically update excursions on recent signals
                recent_active_sigs = list_recent_signals(instrument_id, limit=20)
                for s in recent_active_sigs:
                    sub_bars = [b for b in recent_all_1m if b.start_ts > s.bar_start_ts]
                    if sub_bars:
                        upd_o = evaluate_signal_outcomes(s, sub_bars)
                        save_outcome(upd_o)

            else:
                # --- IN-PROGRESS TICK: Update the active 1m bar in-place anchored to live spot ---
                upd_close = round(spot, 1)
                upd_high = round(max(latest.high, upd_close), 1)
                upd_low = round(min(latest.low, upd_close), 1)
                upd_vol = latest.volume_units + vol_tick
                upd_delta = latest.delta + delta_tick
                upd_cvd = round(latest.cvd + delta_tick, 1)
                ratio = round(upd_delta / upd_vol, 3) if upd_vol > 0 else 0.0

                vwap, up1, up2, dn1, dn2 = vwap_tracker.update(upd_close, float(vol_tick))

                upd_bar = Bar(
                    instrument_id=instrument_id,
                    timeframe="1m",
                    start_ts=latest.start_ts,
                    open=latest.open,
                    high=upd_high,
                    low=upd_low,
                    close=upd_close,
                    volume_units=upd_vol,
                    volume_lots=round(upd_vol / lot_size, 1),
                    buy_vol_est=round((upd_vol + upd_delta) / 2.0, 1),
                    sell_vol_est=round((upd_vol - upd_delta) / 2.0, 1),
                    delta=float(upd_delta),
                    delta_ratio=ratio,
                    delta_quality=1.0,
                    cvd=upd_cvd,
                    vwap=round(vwap, 1),
                    vwap_sigma_1=round(up1, 1),
                    vwap_sigma_2=round(up2, 1),
                    vwap_lower_1=round(dn1, 1),
                    vwap_lower_2=round(dn2, 1),
                    oi=latest.oi + random.randint(50, 300),
                    delta_oi=latest.delta_oi + random.randint(-100, 200),
                    incomplete=True,
                )
                save_bar(upd_bar)

                recent_all_1m = list_recent_bars(instrument_id, timeframe="1m", limit=60)
                for tf_m in (3, 5, 15):
                    agg = aggregate_bars(recent_all_1m, target_minutes=tf_m)
                    if agg:
                        save_bar(agg[-1])


# Global singleton instance
_live_feed_engine = SentinelLiveFeedEngine()


def step_sentinel_live_feed(
    instrument_id: str = "NIFTY-FUT",
    spot: float = 22680.0,
    call_wall: float = 22800.0,
    put_wall: float = 22500.0,
    force: bool = False,
):
    """Public helper to advance simulated live order flow feed by one step during market hours."""
    try:
        import config
        if not config.is_market_open() and not force:
            return
    except Exception:
        pass
    _live_feed_engine.step(instrument_id, spot=spot, call_wall=call_wall, put_wall=put_wall)


