"""commentary/engine.py — Real-time Session Commentary Engine Orchestrator."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from commentary.models import (
    PointInTimeView,
    CommentaryEntry,
    SuggestionBite,
    to_ist,
)
from commentary.detectors.gap import OpeningGapDetector
from commentary.detectors.walls import WallGexShiftDetector
from commentary.detectors.session_structure import SessionStructureDetector
from commentary.detectors.divergences import DivergenceDetector
from commentary.detectors.impulse import ImpulsiveMoveDetector
from commentary.detectors.volatility import VolatilityRegimeDetector
from commentary.detectors.expiry import ExpiryDynamicsDetector
from commentary.detectors.footprint import InstitutionalFootprintDetector
from commentary.detectors.news import NewsCatalystDetector
from commentary.detectors.pinned import PinnedPositionDetector
from commentary.detectors.secondary_walls import SecondaryWallDetector
from commentary.detectors.pcr_flow import PcrFlowDetector
from commentary.detectors.vwap_dynamics import VwapDynamicsDetector
from commentary.detectors.sector_breadth import SectorBreadthDetector
from commentary.detectors.straddle_decay import StraddleDecayDetector
from commentary.scoring import SignificanceScorer
from commentary.story_state import StoryStateManager
from commentary.narrator.client import NarratorClient
from commentary.bites.engine import BiteEngine
from commentary.bites.lifecycle import BiteLifecycleEvaluator
from commentary.storage.db import save_commentary_entry, get_recent_commentary_entries, clear_commentary_cache

logger = logging.getLogger(__name__)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class SessionCommentaryEngine:
    """Live commentary coordinator that evaluates market ticks, detects events, and streams entries."""

    def __init__(self, symbol: str = "NIFTY") -> None:
        self.symbol = symbol.upper()
        self.gap_det = OpeningGapDetector()
        self.walls_det = WallGexShiftDetector()
        self.struct_det = SessionStructureDetector()
        self.div_det = DivergenceDetector()
        self.imp_det = ImpulsiveMoveDetector()
        self.vol_det = VolatilityRegimeDetector()
        self.exp_det = ExpiryDynamicsDetector()
        self.foot_det = InstitutionalFootprintDetector()
        self.news_det = NewsCatalystDetector()
        self.pin_det = PinnedPositionDetector()
        self.sec_walls_det = SecondaryWallDetector()
        self.pcr_det = PcrFlowDetector()
        self.vwap_det = VwapDynamicsDetector()
        self.sec_breadth_det = SectorBreadthDetector()
        self.straddle_det = StraddleDecayDetector()

        self.scorer = SignificanceScorer()
        self.story_mgr = StoryStateManager()
        self.narrator = NarratorClient()
        self.bites_engine = BiteEngine(session_count=0, is_calibrated=False)

        self._active_bites: list[SuggestionBite] = []
        self._last_eval_ts: dt.datetime | None = None

    def evaluate_live(
        self,
        symbol: str,
        data: Any,
        candles_1m: list[dict[str, Any]] | None = None,
        force_publish_top: bool = False,
    ) -> list[CommentaryEntry]:
        """Evaluate current live dashboard data and publish new commentary entries."""
        if data is None or not getattr(data, "levels", None):
            return []

        # Anchor timestamp to the actual market snapshot time if available, otherwise current IST time
        data_ts = getattr(data, "fetched_at", None) or getattr(data.levels, "as_of", None)
        now_ist = to_ist(data_ts) if data_ts is not None else dt.datetime.now(IST)

        # 1. Extract true market levels
        levels = data.levels
        spot = float(getattr(levels, "spot", None) or 22600.0)
        prev_close = float(getattr(levels, "prev_close", None) or spot)
        atm_iv = float(getattr(levels, "atm_iv", None) or 0.14)
        call_wall = float(getattr(levels, "call_wall", None) or (spot + 100))
        put_wall = float(getattr(levels, "put_wall", None) or (spot - 100))
        flip = float(getattr(levels, "flip", None) or spot)
        max_pain = float(getattr(levels, "max_pain", None) or spot)
        net_gex_sign = int(getattr(levels, "net_gex_sign", 1 if spot >= flip else -1))

        # Get pinned recommendations
        pinned_records = []
        try:
            from data.journal.recommendations_store import list_bookmarked_records
            pinned_records = list_bookmarked_records(symbol)
        except Exception:
            pass

        # Fetch active Sentinel microstructure signals
        sentinel_signals = []
        try:
            from sentinel.storage.db import list_recent_signals
            raw_sigs = list_recent_signals(f"{symbol.upper()}-FUT", limit=10)
            for s in reversed(raw_sigs):
                sentinel_signals.append({
                    "signal_type": s.type,
                    "score": s.score,
                    "category": s.category,
                    "bias": s.bias,
                    "rvol": getattr(s, "rvol_tod", 1.5),
                    "delta_ratio": getattr(s, "delta_ratio", 0.0),
                    "invalidation_level": getattr(s, "invalidation_level", 0.0),
                })
        except Exception:
            pass

        # Fetch 1-minute candles from sentinel.db if not explicitly provided
        if not candles_1m:
            try:
                import sqlite3
                con = sqlite3.connect("data/sentinel.db")
                cur = con.cursor()
                inst_id = f"{symbol.upper()}-FUT"
                cur.execute(
                    "SELECT start_ts, open, high, low, close, volume_units, vwap FROM sentinel_bars "
                    "WHERE instrument_id=? AND start_ts <= ? ORDER BY start_ts DESC LIMIT 60",
                    (inst_id, now_ist.isoformat()),
                )
                rows = cur.fetchall()
                con.close()
                if rows:
                    candles_1m = [
                        {"start_ts": r[0], "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "vwap": float(r[6])}
                        for r in reversed(rows)
                    ]
            except Exception as e:
                logger.debug("Could not fetch sentinel bars: %s", e)

        # Build extra facts for level-down detectors
        extra_facts: dict[str, Any] = {}
        if candles_1m:
            extra_facts["session_vwap"] = float(candles_1m[-1].get("vwap", spot))
            extra_facts["vwap"] = extra_facts["session_vwap"]

        pcr_val = getattr(levels, "pcr", None) or getattr(levels, "pcr_oi", None)
        if pcr_val is not None:
            extra_facts["pcr"] = float(pcr_val)

        sec_val = getattr(data, "sectors", None)
        if sec_val:
            extra_facts["sectors"] = sec_val

        oi_strikes = getattr(data, "oi_strikes", None)
        if oi_strikes and isinstance(oi_strikes, list):
            extra_facts["oi_strikes"] = oi_strikes
            try:
                if len(oi_strikes) >= 2:
                    calls_sorted = sorted(oi_strikes, key=lambda x: float(x.get("call_oi", 0.0)), reverse=True)
                    puts_sorted = sorted(oi_strikes, key=lambda x: float(x.get("put_oi", 0.0)), reverse=True)
                    if len(calls_sorted) >= 2:
                        extra_facts["call_wall_2"] = float(calls_sorted[1].get("strike", 0.0))
                    if len(puts_sorted) >= 2:
                        extra_facts["put_wall_2"] = float(puts_sorted[1].get("strike", 0.0))
            except Exception:
                pass

        # Build strict PointInTimeView
        pit_view = PointInTimeView(
            as_of=now_ist,
            symbol=symbol.upper(),
            spot=spot,
            prior_close=prev_close,
            atm_iv=atm_iv,
            expected_move_1d=float(getattr(levels, "expected_move", 100.0) or 100.0),
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_flip=flip,
            max_pain=max_pain,
            net_gex_sign=net_gex_sign,
            candles_1m=candles_1m or [],
            pinned_recommendations=pinned_records,
            sentinel_signals=sentinel_signals,
            extra_facts=extra_facts,
        )

        # 2. Update phase
        self.story_mgr.update_phase(now_ist)
        state = self.story_mgr.state

        # 3. Run detectors (Macro, Microstructure, and Level-Down Flow)
        events = []
        events.extend(self.gap_det.detect(pit_view, state))
        events.extend(self.walls_det.detect(pit_view, state))
        events.extend(self.struct_det.detect(pit_view, state))
        events.extend(self.div_det.detect(pit_view, state))
        events.extend(self.imp_det.detect(pit_view, state))
        events.extend(self.vol_det.detect(pit_view, state))
        events.extend(self.exp_det.detect(pit_view, state))
        events.extend(self.foot_det.detect(pit_view, state))
        events.extend(self.news_det.detect(pit_view, state))
        events.extend(self.pin_det.detect(pit_view, state))
        events.extend(self.sec_walls_det.detect(pit_view, state))
        events.extend(self.pcr_det.detect(pit_view, state))
        events.extend(self.vwap_det.detect(pit_view, state))
        events.extend(self.sec_breadth_det.detect(pit_view, state))
        events.extend(self.straddle_det.detect(pit_view, state))

        if not events:
            return []

        # 4. Score and merge
        scored_raw = [self.scorer.score_event(ev, state) for ev in events]
        scored = self.scorer.merge_confluent_events(scored_raw)
        publishable = [s for s in scored if self.scorer.should_publish(s)[0]]

        if not publishable and force_publish_top and scored:
            publishable = [max(scored, key=lambda s: s.significance)]

        new_entries: list[CommentaryEntry] = []
        if publishable:
            # Publish up to 2 distinct event types
            published_types: set[str] = set()
            for pub_scored in publishable:
                top_event = pub_scored.event
                if top_event.type in published_types:
                    continue
                published_types.add(top_event.type)

                self.story_mgr.update_thesis(top_event)
                headline, body = self.narrator.narrate(top_event)
                entry_id = f"entry-{int(now_ist.timestamp())}-{top_event.type.lower()}"

                entry = CommentaryEntry(
                    id=entry_id,
                    ts=now_ist,
                    phase=state.phase,
                    event_ids=[pub_scored.event.id],
                    headline=headline,
                    body=body,
                    verified=True,
                    tags=[top_event.type] + list(top_event.quality_flags),
                    facts={
                        "spot": spot,
                        "call_wall": call_wall,
                        "put_wall": put_wall,
                        "gamma_flip": flip,
                        "expected_move": float(getattr(levels, "expected_move", 100.0) or 100.0),
                        "event_type": top_event.type,
                        "event_facts": top_event.facts,
                    },
                )

                # Generate Bites
                bites = self.bites_engine.generate_bites(pit_view, [pub_scored])
                entry.bites.extend(bites)

                # Persist
                save_commentary_entry(entry, symbol=symbol)
                self.story_mgr.record_entry(entry)
                new_entries.append(entry)

                if len(new_entries) >= 2:
                    break

        return new_entries

    def synthesize_session_narrative(self, symbol: str, session_date: str) -> list[CommentaryEntry]:
        """Synthesize and populate comprehensive chronological commentary for the session date from timeseries & sentinel feeds."""
        import os
        import sqlite3
        import pandas as pd

        sym = symbol.upper()
        ts_file = f"data/journal/timeseries/{sym}/{session_date}.csv"
        if not os.path.exists(ts_file):
            logger.info("No timeseries file found for %s on %s", sym, session_date)
            return []

        try:
            df = pd.read_csv(ts_file)
        except Exception as e:
            logger.warning("Failed to read timeseries %s: %s", ts_file, e)
            return []

        if df.empty:
            return []

        # Load 1-minute bars from sentinel.db
        all_bars: list[dict[str, Any]] = []
        sig_rows: list[tuple] = []
        try:
            con = sqlite3.connect("data/sentinel.db")
            cur = con.cursor()
            cur.execute(
                "SELECT start_ts, open, high, low, close, volume_units, vwap FROM sentinel_bars "
                "WHERE instrument_id=? AND start_ts LIKE ? ORDER BY start_ts ASC",
                (f"{sym}-FUT", f"{session_date}%"),
            )
            all_bars = [
                {"start_ts": r[0], "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "vwap": float(r[6])}
                for r in cur.fetchall()
            ]
            cur.execute(
                "SELECT bar_start_ts, type, category, bias, score, insight_text FROM sentinel_signals "
                "WHERE instrument_id=? AND bar_start_ts LIKE ? ORDER BY bar_start_ts ASC",
                (f"{sym}-FUT", f"{session_date}%"),
            )
            sig_rows = cur.fetchall()
            con.close()
        except Exception as e:
            logger.debug("Sentinel DB read error: %s", e)

        # Fresh temporary engine to evaluate clean chronological session state
        temp_engine = SessionCommentaryEngine(sym)

        # Look up authenticated auction discovery record for this session date
        auc_open = None
        auc_prev_close = None
        try:
            from data.pre_post_market import get_persisted_auction_record
            auc_rec = get_persisted_auction_record(sym, session_date)
            if auc_rec and auc_rec.get("is_discovered"):
                auc_open = float(auc_rec.get("discovered_spot") or 0.0)
                auc_prev_close = float(auc_rec.get("prev_close") or 0.0)
        except Exception:
            pass

        if auc_open and auc_prev_close:
            gap_pts = round(auc_open - auc_prev_close, 2)
            temp_engine.gap_det._open_price = auc_open
            temp_engine.gap_det._prior_close = auc_prev_close
            temp_engine.gap_det._initial_gap_pts = gap_pts
            temp_engine.gap_det._initial_gap_fired = True

        created_entries: list[CommentaryEntry] = []
        prev_spot = auc_prev_close if (auc_prev_close and auc_prev_close > 0) else float(df.iloc[0]["spot"])

        for idx, row in df.iterrows():
            t_str = str(row.get("time_ist", "09:15"))
            try:
                parts = t_str.split(":")
                h, m = int(parts[0]), int(parts[1])
                y, mon, d = map(int, session_date.split("-"))
                as_of_dt = dt.datetime(y, mon, d, h, m, 0, tzinfo=IST)
            except Exception:
                continue

            cur_bars = [b for b in all_bars if b["start_ts"] <= as_of_dt.isoformat()]
            cur_sigs = [
                {"signal_type": r[1], "category": r[2], "bias": r[3], "score": r[4], "insight_text": r[5]}
                for r in sig_rows if r[0] <= as_of_dt.isoformat()
            ]

            spot = float(row["spot"])

            if cur_bars:
                fut_close = cur_bars[-1]["close"]
                fut_vwap = cur_bars[-1]["vwap"]
                # Translate futures VWAP to spot space using contemporaneous basis
                basis = fut_close - spot
                vwap_val = round(fut_vwap - basis, 1)
            else:
                vwap_val = spot

            pcr_val = float(row.get("pcr_oi", 1.0) or 1.0)

            extra_facts: dict[str, Any] = {
                "session_vwap": vwap_val,
                "vwap": vwap_val,
                "pcr": pcr_val,
                "pcr_oi": pcr_val,
            }

            cw = float(row["call_wall"])
            pw = float(row["put_wall"])
            flip = float(row.get("flip", spot))
            max_pain = float(row.get("max_pain", 22950.0) or 22950.0)

            pit_view = PointInTimeView(
                as_of=as_of_dt,
                symbol=sym,
                spot=spot,
                prior_close=prev_spot if idx > 0 else (auc_prev_close or spot),
                atm_iv=0.14,
                expected_move_1d=120.0,
                call_wall=cw,
                put_wall=pw,
                gamma_flip=flip,
                max_pain=max_pain,
                net_gex_sign=1 if spot >= flip else -1,
                candles_1m=cur_bars,
                sentinel_signals=cur_sigs,
                extra_facts=extra_facts,
            )
            prev_spot = spot

            temp_engine.story_mgr.update_phase(as_of_dt)
            state = temp_engine.story_mgr.state

            events: list[Event] = []
            events.extend(temp_engine.gap_det.detect(pit_view, state))
            events.extend(temp_engine.walls_det.detect(pit_view, state))
            events.extend(temp_engine.struct_det.detect(pit_view, state))
            events.extend(temp_engine.div_det.detect(pit_view, state))
            events.extend(temp_engine.imp_det.detect(pit_view, state))
            events.extend(temp_engine.vol_det.detect(pit_view, state))
            events.extend(temp_engine.exp_det.detect(pit_view, state))
            events.extend(temp_engine.foot_det.detect(pit_view, state))
            events.extend(temp_engine.sec_walls_det.detect(pit_view, state))
            events.extend(temp_engine.pcr_det.detect(pit_view, state))
            events.extend(temp_engine.vwap_det.detect(pit_view, state))
            events.extend(temp_engine.straddle_det.detect(pit_view, state))

            if not events:
                continue

            scored_raw = [temp_engine.scorer.score_event(ev, state) for ev in events]
            scored = temp_engine.scorer.merge_confluent_events(scored_raw)
            publishable = [s for s in scored if temp_engine.scorer.should_publish(s)[0]]

            # Ensure baseline morning entry at 09:15 or 09:30 if empty
            if not publishable and (idx <= 1 or not created_entries) and scored:
                publishable = [max(scored, key=lambda s: s.significance)]

            if publishable:
                for pub_s in publishable[:2]:
                    ev = pub_s.event
                    temp_engine.story_mgr.update_thesis(ev)
                    headline, body = temp_engine.narrator.narrate(ev)
                    entry_id = f"entry-{int(as_of_dt.timestamp())}-{ev.type.lower()}"

                    entry = CommentaryEntry(
                        id=entry_id,
                        ts=as_of_dt,
                        phase=state.phase,
                        event_ids=[ev.id],
                        headline=headline,
                        body=body,
                        verified=True,
                        tags=[ev.type] + list(ev.quality_flags),
                        facts={
                            "spot": spot,
                            "call_wall": cw,
                            "put_wall": pw,
                            "gamma_flip": flip,
                            "expected_move": 120.0,
                            "event_type": ev.type,
                            "event_facts": ev.facts,
                        },
                    )
                    bites = temp_engine.bites_engine.generate_bites(pit_view, [pub_s])
                    entry.bites.extend(bites)

                    save_commentary_entry(entry, symbol=sym)
                    temp_engine.story_mgr.record_entry(entry)
                    created_entries.append(entry)

        clear_commentary_cache()
        return created_entries


_ENGINES: dict[str, SessionCommentaryEngine] = {}


def get_session_commentary_engine(symbol: str = "NIFTY") -> SessionCommentaryEngine:
    """Return persistent singleton commentary engine for the given symbol."""
    sym = symbol.upper()
    if sym not in _ENGINES:
        _ENGINES[sym] = SessionCommentaryEngine(sym)
    return _ENGINES[sym]
