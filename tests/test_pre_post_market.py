"""tests/test_pre_post_market.py — Unit tests for pre-open and post-market microstructure data pipeline."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from data.pre_post_market import (
    IST,
    acknowledge_pre_market_chime,
    classify_opening_gap,
    estimate_pre_bell_option_ltps,
    get_current_session_phase,
    get_post_market_state,
    get_pre_market_state,
    is_contract_expired,
)


class TestPrePostMarket(unittest.TestCase):
    def test_session_phase_classification(self):
        # Pick a Tuesday (e.g. 2026-10-13)
        dt_base = datetime(2026, 10, 13, 0, 0, 0, tzinfo=IST)

        # 08:30 IST -> OFF_HOURS
        p_code, _, mins_left = get_current_session_phase(dt_base.replace(hour=8, minute=30))
        self.assertEqual(p_code, "OFF_HOURS")
        self.assertEqual(mins_left, 30)

        # 09:05:00 IST -> PRE_MARKET_ENTRY
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=9, minute=5))
        self.assertEqual(p_code, "PRE_MARKET_ENTRY")

        # 09:07:35 IST -> PRE_MARKET_DISCOVERY
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=9, minute=7, second=35))
        self.assertEqual(p_code, "PRE_MARKET_DISCOVERY")

        # 09:12:00 IST -> PRE_MARKET_DISCOVERY
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=9, minute=12))
        self.assertEqual(p_code, "PRE_MARKET_DISCOVERY")

        # 10:15:00 IST -> REGULAR_SESSION
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=10, minute=15))
        self.assertEqual(p_code, "REGULAR_SESSION")

        # 15:35:00 IST -> CAS_AND_FNO_EXTENDED
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=15, minute=35))
        self.assertEqual(p_code, "CAS_AND_FNO_EXTENDED")

        # 15:45:00 IST -> POST_MARKET_SETTLEMENT
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=15, minute=45))
        self.assertEqual(p_code, "POST_MARKET_SETTLEMENT")

        # 16:15:00 IST -> OFF_HOURS
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=16, minute=15))
        self.assertEqual(p_code, "OFF_HOURS")

        # Weekend test (Sunday 2026-10-11)
        dt_sun = datetime(2026, 10, 11, 10, 0, 0, tzinfo=IST)
        p_code, _, _ = get_current_session_phase(dt_sun)
        self.assertEqual(p_code, "OFF_HOURS")

    @patch("data.dhan_live.fetch_index_ohlc")
    def test_pre_market_discovery_and_chime(self, mock_fetch):
        # Mock discovery response during 09:08:15 IST
        mock_fetch.return_value = {
            "open": 22680.0,
            "prev_close": 22600.0,
            "ltp": 22680.0,
            "close": 22600.0,
            "as_of": "2026-10-13 09:08:15",
        }
        dt_disc = datetime(2026, 10, 13, 9, 8, 20, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=True, now=dt_disc)

        self.assertTrue(state["is_discovered"])
        self.assertEqual(state["discovered_spot"], 22680.0)
        self.assertEqual(state["prev_close"], 22600.0)
        self.assertEqual(state["gap_pts"], 80.0)
        self.assertAlmostEqual(state["gap_pct"], 0.35, places=2)
        self.assertTrue(state.get("should_play_chime"))

        # Acknowledge chime
        acknowledge_pre_market_chime("NIFTY")
        state_again = get_pre_market_state("NIFTY", force_refresh=False, now=dt_disc)
        self.assertFalse(state_again.get("should_play_chime", False))

    def test_classify_opening_gap_regimes(self):
        # 1. Call Wall test
        cw_res = classify_opening_gap(gap_pts=50.0, gap_pct=0.22, spot=22700.0, call_wall=22700.0, put_wall=22400.0)
        self.assertEqual(cw_res["regime"], "SHORT_GAMMA_WALL_TEST")
        self.assertIn("CALL WALL", cw_res["badge"])

        # 2. Put Wall test
        pw_res = classify_opening_gap(gap_pts=-80.0, gap_pct=-0.35, spot=22405.0, call_wall=22700.0, put_wall=22400.0)
        self.assertEqual(pw_res["regime"], "PUT_WALL_BREAKDOWN_TEST")

        # 3. Gap Up Runaway (> +0.40%)
        gu_res = classify_opening_gap(gap_pts=120.0, gap_pct=0.53, spot=22620.0, call_wall=23000.0, put_wall=22200.0)
        self.assertEqual(gu_res["regime"], "GAP_UP_RUNAWAY")

        # 4. Gap Down Trap (< -0.40%)
        gd_res = classify_opening_gap(gap_pts=-110.0, gap_pct=-0.48, spot=22390.0, call_wall=23000.0, put_wall=22000.0)
        self.assertEqual(gd_res["regime"], "GAP_DOWN_TRAP")

        # 5. Balanced Auction (<= 0.15%)
        ba_res = classify_opening_gap(gap_pts=15.0, gap_pct=0.07, spot=22515.0, call_wall=23000.0, put_wall=22000.0)
        self.assertEqual(ba_res["regime"], "AUCTION_BALANCED")

    def test_estimate_pre_bell_option_ltps(self):
        # Discovered gap +80 pts from 22600 to 22680
        candidates = [
            {"strike": 22700.0, "side": "CE", "prev_ltp": 45.0, "delta": 0.42},
            {"strike": 22600.0, "side": "PE", "prev_ltp": 50.0, "delta": -0.45},
        ]
        shocks = estimate_pre_bell_option_ltps(discovered_spot=22680.0, prev_spot=22600.0, candidate_strikes=candidates)
        self.assertEqual(len(shocks), 2)

        # CE should gain value
        ce_shock = shocks[0]
        self.assertGreater(ce_shock["est_open_ltp"], 45.0)
        self.assertGreater(ce_shock["chg_ltp"], 0)

        # PE should lose value
        pe_shock = shocks[1]
        self.assertLess(pe_shock["est_open_ltp"], 50.0)
        self.assertLess(pe_shock["chg_ltp"], 0)

    def test_is_contract_expired(self):
        ref_today = date(2026, 10, 11)

        # 1. Active contract expiring 2026-10-15
        rec_active = {
            "expiry": "2026-10-15",
            "option_symbol": "NIFTY 15 OCT 22700 CE",
            "payload_json": {"expiry": "2026-10-15"},
        }
        self.assertFalse(is_contract_expired(rec_active, ref_date=ref_today))

        # 2. Expired contract expiring 2026-10-08
        rec_expired = {
            "expiry": "2026-10-08",
            "option_symbol": "NIFTY 08 OCT 22700 CE",
            "payload_json": {"expiry": "2026-10-08"},
        }
        self.assertTrue(is_contract_expired(rec_expired, ref_date=ref_today))

        # 3. Contract expiring today (same day) -> not yet expired during trading day
        rec_today = {
            "expiry": "2026-10-11",
            "option_symbol": "NIFTY 11 OCT 22700 CE",
            "payload_json": {"expiry": "2026-10-11"},
        }
        self.assertFalse(is_contract_expired(rec_today, ref_date=ref_today))

    @patch("config.is_market_open")
    def test_score_history_market_hours_and_price_guards(self, mock_market_open):
        import uuid
        from data.journal.recommendations_store import append_score_history, get_score_history
        test_rec_id = f"TEST_REC_GUARD_{uuid.uuid4().hex[:8]}"

        # 1. Market closed: append should be completely suppressed
        mock_market_open.return_value = False
        append_score_history(
            rec_id=test_rec_id, score=85, current_spot=22600.0, current_ltp=45.0, reason="Initial"
        )
        self.assertEqual(len(get_score_history(test_rec_id)), 0)

        # 2. Market open: first entry writes
        mock_market_open.return_value = True
        append_score_history(
            rec_id=test_rec_id, score=85, current_spot=22600.0, current_ltp=45.0, reason="Initial"
        )
        hist = get_score_history(test_rec_id)
        self.assertEqual(len(hist), 1)

        # 3. Score changes but spot & LTP unchanged: should be suppressed
        append_score_history(
            rec_id=test_rec_id, score=90, current_spot=22600.0, current_ltp=45.0, reason="Shift"
        )
        self.assertEqual(len(get_score_history(test_rec_id)), 1)

        # 4. Score changes and price moves: should append new audit entry
        append_score_history(
            rec_id=test_rec_id, score=90, current_spot=22630.0, current_ltp=55.0, reason="Price moved"
        )
        hist2 = get_score_history(test_rec_id)
        self.assertEqual(len(hist2), 2)
        self.assertEqual(hist2[0]["score"], 90)

    def test_is_recently_triggered_10m(self):
        from data.pre_post_market import is_recently_triggered
        now_dt = datetime.now(timezone(timedelta(hours=5, minutes=30)))

        # 5 minutes ago (< 600s) -> True
        dt_5m = (now_dt - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertTrue(is_recently_triggered(dt_5m, max_age_seconds=600.0))

        # 9 minutes ago (< 600s) -> True
        dt_9m = (now_dt - timedelta(minutes=9)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertTrue(is_recently_triggered(dt_9m, max_age_seconds=600.0))

        # 12 minutes ago (> 600s) -> False
        dt_12m = (now_dt - timedelta(minutes=12)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertFalse(is_recently_triggered(dt_12m, max_age_seconds=600.0))


if __name__ == "__main__":
    unittest.main()

