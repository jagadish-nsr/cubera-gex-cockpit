"""sentinel/feed/live.py — Real-time Dhan WebSocket & Intraday Ingestion for OrderFlow Sentinel.

Replaces synthetic demo generation with 100% genuine market data:
1. Backfills/syncs true intraday 1-minute futures candles from Dhan charts/intraday API for today.
2. Streams live futures binary/quote ticks directly from the Dhan WebSocket (via ws_spot_feed).
3. Computes real Bar orderflow delta, CVD, session VWAP bands, and true algorithmic signals.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, date, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

from sentinel.models import Bar, SentinelSignal
from sentinel.engine.aggregator import BarAggregator, SessionVWAPTracker, aggregate_bars
from sentinel.engine.classifier import SignalClassifierEngine
from sentinel.engine.context import MarketContext
from sentinel.engine.outcomes import evaluate_signal_outcomes
from sentinel.storage.db import (
    save_bar,
    save_signal,
    save_outcome,
    list_recent_bars,
    list_recent_signals,
    list_recent_outcomes,
    purge_old_sentinel_data,
)

log = logging.getLogger(__name__)

_LOCK = threading.Lock()

# Tape completeness: a hole ≥ this many minutes forces a broker resync (rate-limited).
_GAP_RESYNC_MINUTES = 3
_GAP_RESYNC_COOLDOWN_SEC = 180.0
# Tick silence: if no futures tick arrives for this long during session, resync
# even when no bar completed (Sensex 9-Nov dropped 106 minutes while Nifty lived —
# gap detection only ran on completed bars, so a silent feed never self-healed).
_TICK_SILENCE_SEC = 90.0
_SILENCE_POLL_SEC = 30.0
# Process-wide silence watchdog (one thread). Instance managers still poll via
# the singleton + any registered managers so unit tests do not spawn N loops.
_SILENCE_WATCHDOG_STARTED = False
_SILENCE_WATCHDOG_LOCK = threading.Lock()
_SILENCE_WATCHDOG_STOP = threading.Event()
_SILENCE_WATCH_MANAGERS: list = []
_OI_FLAT_BARS = 5


def resolve_gex_levels_for_symbol(symbol: str) -> dict[str, float | None]:
    """Best-effort walls/flip/magnet from the latest published book.

    Prefers ``latest_snap_{SYM}.pkl`` (has magnet). Falls back to the session
    snapshot SQLite row for walls/flip. Never invents levels.
    """
    try:
        import config
        sym = config.index_key(symbol)
    except Exception:
        sym = (symbol or "NIFTY").upper().replace("-FUT", "")

    out: dict[str, float | None] = {
        "call_wall": None,
        "put_wall": None,
        "flip": None,
        "magnet": None,
    }

    def _pos(v: Any) -> float | None:
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    try:
        from pathlib import Path
        import pickle

        path = Path(f"data/journal/latest_snap_{sym}.pkl")
        if path.exists():
            with open(path, "rb") as fh:
                cached = pickle.load(fh)
            data = cached.get("data") if isinstance(cached, dict) else cached
            lv = getattr(data, "levels", None)
            if lv is not None:
                out["call_wall"] = _pos(getattr(lv, "call_wall", None))
                out["put_wall"] = _pos(getattr(lv, "put_wall", None))
                out["flip"] = _pos(getattr(lv, "flip", None))
                out["magnet"] = _pos(getattr(lv, "magnet", None))
    except Exception:
        log.debug("resolve_gex_levels: latest_snap read failed for %s", sym, exc_info=True)

    try:
        from data.market_snapshot import get_snapshot

        snap = get_snapshot(sym) or {}
        if out["call_wall"] is None:
            out["call_wall"] = _pos(snap.get("call_wall"))
        if out["put_wall"] is None:
            out["put_wall"] = _pos(snap.get("put_wall"))
        if out["flip"] is None:
            out["flip"] = _pos(snap.get("gamma_flip"))
    except Exception:
        log.debug("resolve_gex_levels: snapshot read failed for %s", sym, exc_info=True)

    return out


def _as_ist(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def _session_day(ts: datetime) -> str:
    return _as_ist(ts).strftime("%Y-%m-%d")


def _minute_floor(ts: datetime) -> datetime:
    return _as_ist(ts).replace(second=0, microsecond=0)


def largest_1m_gap_minutes(bars: list[Bar], now_ist: datetime | None = None) -> int:
    """Largest hole between consecutive session 1m bars (and trailing hole to now).

    Returns 0 when fewer than two bars. Used by diagnostics; live resync prefers
    ``trailing_1m_gap_minutes`` so historical holes do not spam broker sync.
    """
    if not bars:
        return 0
    ordered = sorted((_minute_floor(b.start_ts) for b in bars))
    largest = 0
    for a, b in zip(ordered, ordered[1:]):
        gap = int((b - a).total_seconds() // 60) - 1
        if gap > largest:
            largest = gap
    if now_ist is not None:
        trailing = int((_minute_floor(now_ist) - ordered[-1]).total_seconds() // 60) - 1
        if trailing > largest:
            largest = trailing
    return max(0, largest)


def trailing_1m_gap_minutes(bars: list[Bar], now_ist: datetime | None = None) -> int:
    """Minutes since the latest 1m bar floor (how far the tape lags wall clock).

    11 Nov: silence poll used largest mid-window hole while ticks were fresh
    (tick_age≈0, gap=3) → 39 Sensex resyncs. Heal only when the *latest* bar
    is stale, or when ticks themselves go quiet.
    """
    if not bars:
        return 0
    now = _as_ist(now_ist or datetime.now(IST))
    last = max(_minute_floor(b.start_ts) for b in bars)
    return max(0, int((_minute_floor(now) - last).total_seconds() // 60) - 1)


# Always poll these even if an aggregator was dropped mid-session (prod 11 Nov
# lost all three -FUT near 10:25; an empty aggregator map must not skip heal).
_SILENCE_WATCH_INSTRUMENTS: tuple[str, ...] = (
    "NIFTY-FUT",
    "BANKNIFTY-FUT",
    "SENSEX-FUT",
)


class LiveSentinelManager:
    """Manages real-time intraday synchronization and live WebSocket tick aggregation."""

    def __init__(self) -> None:
        self._aggregators: dict[str, BarAggregator] = {}
        self._classifiers: dict[str, SignalClassifierEngine] = {}
        self._contexts: dict[str, MarketContext] = {}
        self._last_synced_time: dict[str, float] = {}
        self._sync_requested_at: dict[str, float] = {}
        self._prev_cum_vol: dict[str, int] = {}
        self._last_tick_time: dict[str, float] = {}
        # Armed only on the first live tick — never from __init__ or session sync.
        self._recommendations_armed = False
        # Last known GEX levels per symbol (survives until MarketContext exists).
        self._pending_gex: dict[str, dict[str, float | None]] = {}
        self._gap_resync_at: dict[str, float] = {}
        self._instrument_symbol: dict[str, str] = {}
        with _SILENCE_WATCHDOG_LOCK:
            if self not in _SILENCE_WATCH_MANAGERS:
                _SILENCE_WATCH_MANAGERS.append(self)

    def _arm_recommendations_if_needed(self) -> None:
        """Enable reactive recommendations only after a live futures tick arrives."""
        if self._recommendations_armed:
            return
        from sentinel.storage.db import enable_recommendation_listener
        enable_recommendation_listener()
        self._recommendations_armed = True

    @staticmethod
    def _normalize_tick_time(ltt: int | None, now_ist: datetime) -> datetime:
        """Normalize Dhan LTT timestamp by subtracting the 19,800s IST offset if present."""
        if not ltt or ltt <= 0:
            return now_ist
        now_epoch = time.time()
        # Dhan encodes LTT with +5h30m (19,800s) added relative to true UTC epoch
        if abs(ltt - (now_epoch + 19800)) < 7200:
            true_utc_epoch = ltt - 19800
        elif abs(ltt - now_epoch) < 7200:
            true_utc_epoch = ltt
        else:
            return now_ist
        dt = datetime.fromtimestamp(true_utc_epoch, tz=timezone.utc).astimezone(IST)
        # Never allow tick time to be in the future beyond 60s
        if dt > now_ist + timedelta(seconds=60):
            return now_ist
        return dt

    def update_gex_levels(
        self,
        symbol: str,
        call_wall: float | None = None,
        put_wall: float | None = None,
        flip: float | None = None,
        magnet: float | None = None,
    ) -> None:
        """Push current GEX levels into the live MarketContext (and pending stash)."""
        try:
            import config
            sym = config.index_key(symbol)
        except Exception:
            sym = (symbol or "NIFTY").upper().replace("-FUT", "")
        instrument_id = f"{sym}-FUT"
        payload = {
            "call_wall": call_wall,
            "put_wall": put_wall,
            "flip": flip,
            "magnet": magnet,
            "symbol": sym,
        }
        with _LOCK:
            prev = dict(self._pending_gex.get(sym) or {})
            for k, v in payload.items():
                if v is not None:
                    prev[k] = v
            self._pending_gex[sym] = prev
            ctx = self._contexts.get(instrument_id)
            if ctx is not None:
                ctx.symbol = sym
                ctx.set_gex_levels(
                    call_wall=prev.get("call_wall"),
                    put_wall=prev.get("put_wall"),
                    flip=prev.get("flip"),
                    magnet=prev.get("magnet"),
                    symbol=sym,
                )

    def sync_live_session(
        self,
        instrument_id: str,
        symbol: str,
        call_wall: float | None = None,
        put_wall: float | None = None,
        flip: float | None = None,
        magnet: float | None = None,
        force: bool = False,
    ) -> bool:
        """Sync real 1-minute futures bars from Dhan API for today's session.

        Fetches real historical 1m bars from 09:15 up to now, computes true VWAP and CVD,
        and runs the SignalClassifierEngine to generate real signals.
        """
        now_epoch = time.time()

        # Fill missing walls from the last published book / pending push.
        try:
            import config
            sym_key = config.index_key(symbol)
        except Exception:
            sym_key = (symbol or "NIFTY").upper().replace("-FUT", "")
        pending = dict(self._pending_gex.get(sym_key) or {})
        resolved = resolve_gex_levels_for_symbol(sym_key)
        if call_wall is None:
            call_wall = pending.get("call_wall") or resolved.get("call_wall")
        if put_wall is None:
            put_wall = pending.get("put_wall") or resolved.get("put_wall")
        if flip is None:
            flip = pending.get("flip") or resolved.get("flip")
        if magnet is None:
            magnet = pending.get("magnet") or resolved.get("magnet")

        # Target today once the session briefing window has begun (09:00 regular /
        # 13:00 Muhurat); otherwise the latest completed regular trading day.
        now_ist = datetime.now(IST)
        try:
            from data.holidays import roll_back_to_trading_day
            from data.session_calendar import clock_for, is_live_session_day
            clock = clock_for(now_ist.date())
            briefing = now_ist.replace(
                hour=clock.briefing_min // 60,
                minute=clock.briefing_min % 60,
                second=0, microsecond=0,
            )
            if is_live_session_day(now_ist.date()) and now_ist >= briefing:
                target_date = now_ist.date()
            else:
                target_date = roll_back_to_trading_day(
                    now_ist.date() - timedelta(days=1) if now_ist < briefing else now_ist.date()
                )
        except Exception:
            target_date = now_ist.date()
        target_date_str = target_date.strftime("%Y-%m-%d")

        existing_sample = list_recent_bars(instrument_id, limit=5, session_only=False, _auto_populate=False)
        needs_day_rollover = bool(
            existing_sample and _session_day(existing_sample[-1].start_ts) != target_date_str
        )

        # Claim the slot, then release the lock before the broker call.
        # The event loop also waits on this lock, and Lock is not re-entrant.
        with _LOCK:
            last_sync = self._last_synced_time.get(instrument_id, 0.0)
            if not force and not needs_day_rollover and existing_sample and (now_epoch - last_sync) < 60.0:
                return False
            self._last_synced_time[instrument_id] = now_epoch

        try:
            from data.dhan import fetch_futures_intraday, nearest_futures

            futs = nearest_futures(symbol)
            if not futs:
                log.warning("No futures resolved for %s", symbol)
                return False

            front_fut = futs[0]
            sec_id = front_fut["security_id"]
            seg = "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO"

            df = fetch_futures_intraday(sec_id, target_date_str, target_date_str, interval="1", segment=seg)
            if df is None or df.empty:
                log.info("No intraday bars returned for %s (date %s)", instrument_id, target_date_str)
                return False
        except Exception as exc:
            log.error("Failed to fetch live session bars for %s: %s", instrument_id, exc, exc_info=True)
            return False

        with _LOCK:
            try:
                # A reload mid-session must not delete live sweeps. Those rows
                # are intra-bar prints; a 1m Dhan candle cannot recreate them.
                # Day rollover must not wipe prior sessions either — audits need
                # them. Age-based purge keeps the ledger bounded; session_only
                # reads keep today's tape isolated from yesterday.
                stored = list_recent_bars(
                    instrument_id, timeframe="1m", limit=800,
                    session_only=False, _auto_populate=False,
                )
                stored_day = _session_day(stored[-1].start_ts) if stored else None
                rollover = bool(needs_day_rollover and stored_day and stored_day != target_date_str)
                kept_by_minute: dict[datetime, Bar] = {}
                if rollover:
                    pruned = purge_old_sentinel_data()
                    log.info(
                        "Sentinel day rollover for %s: retained session %s, "
                        "syncing %s (age-purge %s)",
                        instrument_id, stored_day, target_date_str, pruned,
                    )
                for existing in stored:
                    if _session_day(existing.start_ts) == target_date_str:
                        kept_by_minute[_minute_floor(existing.start_ts)] = existing

                # Sort by timestamp
                df = df.sort_values("timestamp").reset_index(drop=True)

                lot_size = front_fut.get("lot") or (20 if "SENSEX" in symbol else (30 if "BANKNIFTY" in symbol else 65))

                # Track session metrics
                vwap_tracker = SessionVWAPTracker()
                cum_cvd = 0.0
                prev_oi = 0
                oi_flat_bars = 0
                bars_to_save: list[Bar] = []
                signals_generated: list[SentinelSignal] = []
                n_kept = 0
                n_written = 0

                # Context — walls/flip/magnet from args, pending push, or published book
                day_high = float(df["high"].max())
                day_low = float(df["low"].min())
                ctx = MarketContext(
                    day_high=day_high,
                    day_low=day_low,
                    call_wall=call_wall,
                    put_wall=put_wall,
                    gamma_flip=flip,
                    magnet=magnet,
                    symbol=sym_key,
                )
                self._contexts[instrument_id] = ctx
                self._pending_gex[sym_key] = {
                    "call_wall": call_wall,
                    "put_wall": put_wall,
                    "flip": flip,
                    "magnet": magnet,
                    "symbol": sym_key,
                }

                classifier = SignalClassifierEngine()
                self._classifiers[instrument_id] = classifier

                for i, row in df.iterrows():
                    o = float(row["open"])
                    h = float(row["high"])
                    l = float(row["low"])
                    c = float(row["close"])
                    vol = int(row.get("volume", 0) or 0)
                    oi = int(row.get("oi", 0) or 0)
                    ts_epoch = float(row["timestamp"])
                    bar_dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).astimezone(IST)
                    if bar_dt > datetime.now(IST) + timedelta(minutes=2):
                        continue

                    kept = kept_by_minute.get(_minute_floor(bar_dt))
                    if kept is not None:
                        # Keep the live minute. Replacing it with candle-estimated
                        # delta would erase the tape the sweeps were read from.
                        typical_kept = (kept.high + kept.low + kept.close) / 3.0
                        vwap_tracker.update(typical_kept, float(kept.volume_units))
                        cum_cvd = kept.cvd
                        if kept.oi > 0:
                            if prev_oi > 0 and kept.oi == prev_oi:
                                oi_flat_bars += 1
                            else:
                                oi_flat_bars = 0
                            prev_oi = kept.oi
                        bars_to_save.append(kept)
                        ctx.update_bar(kept)
                        classifier.evaluate_bar(kept, ctx)
                        n_kept += 1
                        continue

                    # Estimate buyer/seller volume from candle structure & price delta
                    typical_price = (h + l + c) / 3.0
                    vwap, up1, up2, dn1, dn2 = vwap_tracker.update(typical_price, float(vol))

                    # Enhanced Microstructure Delta: blend candle body thrust with wick location
                    rng = max(0.5, h - l)
                    body = c - o
                    close_pct = (c - l) / rng
                    body_factor = (body / rng) if rng > 0 else 0.0
                    location_factor = (close_pct - 0.5) * 2.0
                    combined_thrust = (0.65 * body_factor) + (0.35 * location_factor)

                    bar_delta = round(vol * combined_thrust * 0.50, 1) if vol > 0 else 0.0
                    cum_cvd += bar_delta
                    delta_ratio = round(bar_delta / vol, 4) if vol > 0 else 0.0

                    buy_vol = round((vol + bar_delta) / 2.0, 1)
                    sell_vol = round((vol - bar_delta) / 2.0, 1)

                    delta_oi = (oi - prev_oi) if (prev_oi > 0 and oi > 0) else 0
                    if oi > 0:
                        if prev_oi > 0 and oi == prev_oi:
                            oi_flat_bars += 1
                        else:
                            oi_flat_bars = 0
                        prev_oi = oi
                    # Same gate as BarAggregator: flat OI reprint is not a live tape.
                    oi_fresh = oi_flat_bars < _OI_FLAT_BARS

                    bar = Bar(
                        instrument_id=instrument_id,
                        timeframe="1m",
                        start_ts=bar_dt,
                        open=round(o, 2),
                        high=round(h, 2),
                        low=round(l, 2),
                        close=round(c, 2),
                        volume_units=vol,
                        volume_lots=round(vol / lot_size, 1),
                        buy_vol_est=buy_vol,
                        sell_vol_est=sell_vol,
                        delta=bar_delta,
                        delta_ratio=delta_ratio,
                        delta_quality=1.0,
                        cvd=round(cum_cvd, 1),
                        vwap=round(vwap, 2),
                        vwap_sigma_1=round(up1, 2),
                        vwap_sigma_2=round(up2, 2),
                        vwap_lower_1=round(dn1, 2),
                        vwap_lower_2=round(dn2, 2),
                        oi=oi,
                        delta_oi=delta_oi,
                        oi_fresh=oi_fresh,
                        incomplete=False,
                        depth_imbalance=0.0,
                    )
                    bars_to_save.append(bar)
                    save_bar(bar)
                    n_written += 1

                    ctx.update_bar(bar)
                    ctx.refresh_chop(bars_to_save)
                    sig = classifier.evaluate_bar(bar, ctx)
                    if sig:
                        # Sync/backfill must not mint recommendations or paper trades.
                        save_signal(sig, emit_recommendations=False)
                        signals_generated.append(sig)

                # Evaluate outcomes for signals generated across the session bars
                for sig in signals_generated:
                    sub_bars = [b for b in bars_to_save if b.start_ts > sig.bar_start_ts]
                    if sub_bars:
                        try:
                            outcome = evaluate_signal_outcomes(
                                sig, sub_bars, call_wall=call_wall or 0.0, put_wall=put_wall or 0.0
                            )
                            save_outcome(outcome)
                        except Exception as e_out:
                            log.debug("Failed evaluating outcome for %s: %s", sig.event_id, e_out)

                # Aggregate to 3m, 5m, 15m and save
                for tf_min in (3, 5, 15):
                    agg = aggregate_bars(bars_to_save, target_minutes=tf_min)
                    for b_agg in agg:
                        save_bar(b_agg)

                # Initialize aggregator for ongoing live ticks from this point
                latest_bar = bars_to_save[-1]
                agg = BarAggregator(instrument_id=instrument_id, timeframe="1m")
                agg.running_cvd = cum_cvd
                agg.last_tick_price = latest_bar.close
                agg.vwap_tracker = vwap_tracker
                agg.bar_oi = latest_bar.oi
                agg.prev_bar_oi = prev_oi if prev_oi > 0 else latest_bar.oi
                agg._oi_flat_bars = int(oi_flat_bars)
                self._aggregators[instrument_id] = agg
                self._instrument_symbol[instrument_id] = symbol
                self._last_tick_time[instrument_id] = time.time()
                self._ensure_silence_watchdog()

                log.info(
                    "Successfully synced %d real intraday bars for %s "
                    "(kept %d live minutes, wrote %d, latest close: %.1f, oi_flat=%d)",
                    len(bars_to_save),
                    instrument_id,
                    n_kept,
                    n_written,
                    latest_bar.close,
                    oi_flat_bars,
                )
                try:
                    from sentinel.storage.db import record_daily_closing_cvd
                    record_daily_closing_cvd(instrument_id, symbol=symbol, trade_date=target_date_str)
                except Exception:
                    pass
                return True

            except Exception as exc:
                log.error("Failed to sync live session bars for %s: %s", instrument_id, exc, exc_info=True)
                return False

    def on_tick(self, pkt: dict, symbol: str) -> None:
        """Process incoming WebSocket tick for futures contract in real time."""
        ltp = pkt.get("ltp")
        if not ltp or ltp <= 0:
            return

        instrument_id = f"{symbol}-FUT"
        self._instrument_symbol[instrument_id] = symbol
        self._last_tick_time[instrument_id] = time.time()
        self._ensure_silence_watchdog()

        agg = self._aggregators.get(instrument_id)
        if agg is None:
            # A restart drops the in-memory aggregator while today's bars remain
            # in sqlite. Ticks must start one session sync, off this thread.
            self._schedule_session_sync(instrument_id, symbol)
            return

        self._arm_recommendations_if_needed()

        now_ist = datetime.now(IST)
        ltt = pkt.get("ltt")
        tick_dt = self._normalize_tick_time(ltt, now_ist)

        cum_vol = pkt.get("volume")
        vol_delta = 1
        if cum_vol is not None and cum_vol > 0:
            prev = self._prev_cum_vol.get(instrument_id)
            if prev is not None and cum_vol >= prev:
                vol_delta = max(1, cum_vol - prev)
            self._prev_cum_vol[instrument_id] = cum_vol
        else:
            vol_delta = pkt.get("ltq") or 1

        oi = pkt.get("oi") or 0
        depth_imbalance = float(pkt.get("depth_imbalance", 0.0) or 0.0)

        with _LOCK:
            completed_bar = agg.add_tick(
                price=float(ltp),
                timestamp=tick_dt,
                volume_delta=int(vol_delta),
                oi=int(oi),
                depth_imbalance=depth_imbalance,
            )

            # High-speed intra-bar micro-burst detection (< 500ms latency)
            burst = agg.check_micro_burst(tick_dt)
            if burst is not None:
                ctx = self._contexts.get(instrument_id)
                classifier = self._classifiers.get(instrument_id)
                if ctx and classifier:
                    micro_sig = classifier.evaluate_micro_burst(burst, ctx)
                    if micro_sig:
                        micro_sig.entry_price = float(ltp)
                        save_signal(micro_sig)

            if completed_bar is not None:
                save_bar(completed_bar)

                # Update context
                ctx = self._contexts.get(instrument_id)
                bars_eval = list_recent_bars(
                    instrument_id, timeframe="1m", limit=120,
                    session_only=True, _auto_populate=False,
                )
                if ctx:
                    ctx.update_bar(completed_bar)
                    ctx.refresh_chop(bars_eval)
                    classifier = self._classifiers.get(instrument_id)
                    if classifier:
                        sig = classifier.evaluate_bar(completed_bar, ctx)
                        if sig:
                            sig.entry_price = float(ltp)
                            save_signal(sig)

                # Fail loud when the latest bar lags wall clock (trailing hole).
                # Mid-window historical holes are healed by sync; do not re-fire
                # on every tick while the tape tip is current (11 Nov Sensex spam).
                try:
                    gap = trailing_1m_gap_minutes(bars_eval, now_ist=tick_dt)
                    if gap >= _GAP_RESYNC_MINUTES:
                        log.warning(
                            "Sentinel 1m tape gap on %s: trailing hole %d min "
                            "(threshold %d) — scheduling broker resync",
                            instrument_id, gap, _GAP_RESYNC_MINUTES,
                        )
                        self._schedule_gap_resync(instrument_id, symbol)
                except Exception:
                    log.debug("gap watchdog failed for %s", instrument_id, exc_info=True)

                # Update outcomes of recent signals with subsequent bars
                try:
                    recent_sigs = list_recent_signals(instrument_id, limit=30)
                    cw = ctx.call_wall if ctx else 0.0
                    pw = ctx.put_wall if ctx else 0.0
                    for s in recent_sigs:
                        sub_bars = [b for b in bars_eval if b.start_ts > s.bar_start_ts]
                        if sub_bars:
                            upd_o = evaluate_signal_outcomes(s, sub_bars, call_wall=cw or 0.0, put_wall=pw or 0.0)
                            save_outcome(upd_o)

                    # Evaluate Sentinel trade recommendations lifecycle
                    from analytics.sentinel_recommendations import evaluate_sentinel_recommendations_lifecycle
                    evaluate_sentinel_recommendations_lifecycle(
                        symbol=symbol,
                        current_spot=float(ltp),
                        bars_1m=bars_eval,
                    )
                except Exception as err:
                    log.debug("Failed evaluating live tick outcomes for %s: %s", instrument_id, err)

                # Aggregate into higher timeframes on interval boundaries
                bars_1m = list_recent_bars(
                    instrument_id, timeframe="1m", limit=30, _auto_populate=False,
                )
                for tf in (3, 5, 15):
                    agg_list = aggregate_bars(bars_1m, target_minutes=tf)
                    if agg_list:
                        save_bar(agg_list[-1])

                try:
                    from sentinel.storage.db import record_daily_closing_cvd
                    record_daily_closing_cvd(instrument_id, symbol=symbol, trade_date=tick_dt.strftime("%Y-%m-%d"))
                except Exception:
                    pass

    def _ensure_silence_watchdog(self) -> None:
        """Start one process-wide poller that resyncs instruments with a silent feed.

        Must not take ``_LOCK`` — ``sync_live_session`` arms this while holding
        that lock (non-reentrant). One thread for the process; polls every
        registered ``LiveSentinelManager`` (singleton + test instances).
        """
        global _SILENCE_WATCHDOG_STARTED
        with _SILENCE_WATCHDOG_LOCK:
            if self not in _SILENCE_WATCH_MANAGERS:
                _SILENCE_WATCH_MANAGERS.append(self)
            if _SILENCE_WATCHDOG_STARTED:
                return
            _SILENCE_WATCHDOG_STARTED = True
            _SILENCE_WATCHDOG_STOP.clear()

        def _loop() -> None:
            while not _SILENCE_WATCHDOG_STOP.is_set():
                try:
                    # Interruptible wait so tests / shutdown are not stuck on sleep.
                    if _SILENCE_WATCHDOG_STOP.wait(_SILENCE_POLL_SEC):
                        break
                    with _SILENCE_WATCHDOG_LOCK:
                        managers = list(_SILENCE_WATCH_MANAGERS)
                    for mgr in managers:
                        try:
                            mgr._poll_silent_instruments()
                        except Exception:
                            log.debug("silence watchdog poll failed", exc_info=True)
                except Exception:
                    log.debug("silence watchdog iteration failed", exc_info=True)

        threading.Thread(
            target=_loop, name="sentinel-silence-watchdog", daemon=True,
        ).start()

    def _session_active_for_silence(self, now_ist: datetime | None = None) -> bool:
        """True during cash/FNO hours when a silent futures feed is a hole, not off-hours."""
        now = now_ist or datetime.now(IST)
        try:
            import config
            return bool(config.is_market_open(now, include_fno_extended=True))
        except Exception:
            t_min = now.hour * 60 + now.minute
            return (9 * 60 + 15) <= t_min < (15 * 60 + 40) and now.weekday() < 5

    def _poll_silent_instruments(self) -> None:
        """Resync instruments whose ticks went quiet or whose latest 1m bar lags.

        Triggers:
        - tick silence ≥ ``_TICK_SILENCE_SEC`` (prod 11 Nov death mode — no new
          bars means the completed-bar gap check never runs);
        - trailing gap ≥ ``_GAP_RESYNC_MINUTES`` (latest bar behind wall clock).

        Does **not** resync solely because an older hole sits inside the
        lookback window while ticks and the latest bar are fresh.
        """
        if not self._session_active_for_silence():
            return
        now = time.time()
        now_ist = datetime.now(IST)
        with _LOCK:
            instruments = set(self._aggregators.keys()) | set(self._last_tick_time.keys())
            instruments.update(_SILENCE_WATCH_INSTRUMENTS)
            for iid in _SILENCE_WATCH_INSTRUMENTS:
                self._instrument_symbol.setdefault(iid, iid.replace("-FUT", ""))
        for instrument_id in sorted(instruments):
            symbol = self._instrument_symbol.get(
                instrument_id, instrument_id.replace("-FUT", ""),
            )
            last_tick = self._last_tick_time.get(instrument_id, 0.0)
            silent_sec = (now - last_tick) if last_tick > 0 else 0.0
            bars = list_recent_bars(
                instrument_id, timeframe="1m", limit=30,
                session_only=True, _auto_populate=False,
            )
            trail_gap = trailing_1m_gap_minutes(bars, now_ist=now_ist) if bars else 0
            # Boot with today's bars already on disk but no tick yet this process:
            # trailing_gap heals the prod-style hard stop. Cold start (no bars)
            # waits for the normal session sync — do not spam.
            if silent_sec >= _TICK_SILENCE_SEC or trail_gap >= _GAP_RESYNC_MINUTES:
                log.warning(
                    "Sentinel silence/gap on %s: tick_age=%.0fs trailing_gap=%dm — scheduling resync",
                    instrument_id, silent_sec, trail_gap,
                )
                self._schedule_gap_resync(instrument_id, symbol)

    def _schedule_gap_resync(self, instrument_id: str, symbol: str) -> None:
        """Force a live session sync after a detected 1m tape hole (rate-limited)."""
        now = time.time()
        with _LOCK:
            last = self._gap_resync_at.get(instrument_id, 0.0)
            if (now - last) < _GAP_RESYNC_COOLDOWN_SEC:
                return
            self._gap_resync_at[instrument_id] = now

        def _run() -> None:
            try:
                levels = resolve_gex_levels_for_symbol(symbol)
                ok = self.sync_live_session(
                    instrument_id,
                    symbol,
                    call_wall=levels.get("call_wall"),
                    put_wall=levels.get("put_wall"),
                    flip=levels.get("flip"),
                    magnet=levels.get("magnet"),
                    force=True,
                )
                log.warning(
                    "Sentinel gap resync for %s finished ok=%s", instrument_id, ok,
                )
            except Exception:
                log.exception("Sentinel gap resync failed for %s", instrument_id)

        threading.Thread(
            target=_run, name=f"sentinel-gap-resync-{symbol}", daemon=True,
        ).start()

    def _schedule_session_sync(self, instrument_id: str, symbol: str) -> None:
        """Start one intraday sync when the live aggregator is missing.

        The caller is the futures tick thread. The broker fetch stays on its
        own thread, and a second tick inside 60s does not start another one.
        """
        now = time.time()
        with _LOCK:
            if self._aggregators.get(instrument_id) is not None:
                return
            last = self._sync_requested_at.get(instrument_id, 0.0)
            if (now - last) < 60.0:
                return
            self._sync_requested_at[instrument_id] = now

        def _run() -> None:
            try:
                levels = resolve_gex_levels_for_symbol(symbol)
                self.sync_live_session(
                    instrument_id,
                    symbol,
                    call_wall=levels.get("call_wall"),
                    put_wall=levels.get("put_wall"),
                    flip=levels.get("flip"),
                    magnet=levels.get("magnet"),
                )
            except Exception:
                log.exception("Background sentinel sync failed for %s", instrument_id)

        threading.Thread(
            target=_run, name=f"sentinel-sync-{symbol}", daemon=True,
        ).start()


# Global singleton manager
_live_manager = LiveSentinelManager()


def update_sentinel_gex_levels(
    symbol: str,
    call_wall: float | None = None,
    put_wall: float | None = None,
    flip: float | None = None,
    magnet: float | None = None,
) -> None:
    """Public updater: keep live OrderFlow context on the current GEX book."""
    _live_manager.update_gex_levels(
        symbol,
        call_wall=call_wall,
        put_wall=put_wall,
        flip=flip,
        magnet=magnet,
    )


def push_gex_from_published_book(symbol: str) -> None:
    """Resolve walls from the just-published book and push into Sentinel."""
    levels = resolve_gex_levels_for_symbol(symbol)
    update_sentinel_gex_levels(
        symbol,
        call_wall=levels.get("call_wall"),
        put_wall=levels.get("put_wall"),
        flip=levels.get("flip"),
        magnet=levels.get("magnet"),
    )


def evaluate_active_sentinel_outcomes(
    instrument_id: str = "NIFTY-FUT",
    call_wall: float = 0.0,
    put_wall: float = 0.0,
) -> int:
    """Evaluate and persist forward outcomes for all active session signals."""
    bars = list_recent_bars(instrument_id, timeframe="1m", limit=500, session_only=True)
    sigs = list_recent_signals(instrument_id, limit=250)
    count = 0
    for s in sigs:
        sub_bars = [b for b in bars if b.start_ts > s.bar_start_ts]
        if sub_bars:
            try:
                outcome = evaluate_signal_outcomes(s, sub_bars, call_wall=call_wall, put_wall=put_wall)
                save_outcome(outcome)
                count += 1
            except Exception:
                pass
    return count


def sync_live_sentinel_if_needed(
    instrument_id: str = "NIFTY-FUT",
    symbol: str = "NIFTY",
    call_wall: float | None = None,
    put_wall: float | None = None,
    flip: float | None = None,
    magnet: float | None = None,
    force: bool = False,
) -> bool:
    """Public helper to ensure real intraday bars are synced for Sentinel."""
    return _live_manager.sync_live_session(
        instrument_id=instrument_id,
        symbol=symbol,
        call_wall=call_wall,
        put_wall=put_wall,
        flip=flip,
        magnet=magnet,
        force=force,
    )


def push_live_tick_to_sentinel(pkt: dict, symbol: str) -> None:
    """Public helper called by ws_spot_feed to deliver real futures ticks to Sentinel."""
    _live_manager.on_tick(pkt, symbol)
