"""tests/test_pre_post_market.py — Unit tests for pre-open and post-market microstructure data pipeline."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from data.pre_post_market import (
    IST,
    acknowledge_pre_market_chime,
    calculate_pre_market_option_shocks,
    classify_opening_gap,
    estimate_pre_bell_option_ltps,
    get_current_session_phase,
    get_persisted_auction_record,
    pre_market_poller_asleep,
    get_post_market_state,
    get_pre_market_state,
    init_pre_post_db,
    is_contract_expired,
    save_option_shocks,
    save_post_market_state,
    save_pre_market_state,
    update_gap_fill_status,
)


class TestPrePostMarket(unittest.TestCase):
    def test_session_phase_classification(self):
        # Pick a Tuesday (e.g. 2026-10-13)
        dt_base = datetime(2026, 10, 13, 0, 0, 0, tzinfo=IST)

        # 06:29 IST -> OFF_HOURS, one minute until the morning briefing
        p_code, _, mins_left = get_current_session_phase(dt_base.replace(hour=6, minute=29))
        self.assertEqual(p_code, "OFF_HOURS")
        self.assertEqual(mins_left, 1)

        # 06:30 IST -> PRE_MARKET_SETUP
        p_code, _, mins_left = get_current_session_phase(dt_base.replace(hour=6, minute=30))
        self.assertEqual(p_code, "PRE_MARKET_SETUP")
        self.assertEqual(mins_left, 150)

        # 08:30 IST -> PRE_MARKET_SETUP, thirty minutes until the cash auction
        p_code, _, mins_left = get_current_session_phase(dt_base.replace(hour=8, minute=30))
        self.assertEqual(p_code, "PRE_MARKET_SETUP")
        self.assertEqual(mins_left, 30)

        # Holiday (Dussehra 2026-10-20) stays closed through the briefing window
        dt_holiday = datetime(2026, 10, 20, 7, 0, 0, tzinfo=IST)
        p_code, _, _ = get_current_session_phase(dt_holiday)
        self.assertEqual(p_code, "OFF_HOURS")

        # 09:00:00 IST -> PRE_MARKET_ENTRY (auction copy, not the morning briefing)
        p_code, _, _ = get_current_session_phase(dt_base.replace(hour=9, minute=0))
        self.assertEqual(p_code, "PRE_MARKET_ENTRY")

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

    def test_poller_asleep_before_0850(self):
        self.assertTrue(pre_market_poller_asleep(datetime(2026, 10, 13, 6, 30, tzinfo=IST)))
        self.assertTrue(pre_market_poller_asleep(datetime(2026, 10, 13, 8, 49, tzinfo=IST)))
        self.assertFalse(pre_market_poller_asleep(datetime(2026, 10, 13, 8, 50, tzinfo=IST)))
        self.assertFalse(pre_market_poller_asleep(datetime(2026, 10, 13, 9, 8, tzinfo=IST)))
        self.assertTrue(pre_market_poller_asleep(datetime(2026, 10, 13, 9, 22, tzinfo=IST)))
        self.assertTrue(pre_market_poller_asleep(datetime(2026, 10, 11, 9, 0, tzinfo=IST)))

    @patch("data.dhan_live.fetch_index_ohlc")
    @patch("data.market_snapshot.resolve_offmarket_snapshot")
    def test_morning_setup_reads_snapshot_not_dhan(self, mock_snap, mock_fetch):
        from data import pre_post_market as ppm
        ppm._PRE_MARKET_CACHE.clear()
        mock_snap.return_value = {
            "spot": 22600.0,
            "prev_close": 22600.0,
            "call_wall": 22800.0,
            "put_wall": 22400.0,
            "gamma_flip": 22550.0,
            "net_gex": -1.0,
            "as_of": "2026-10-12T15:35:00+05:30",
        }
        dt = datetime(2026, 10, 13, 7, 0, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=True, now=dt)
        mock_fetch.assert_not_called()
        self.assertEqual(state["phase_code"], "PRE_MARKET_SETUP")
        self.assertFalse(state["is_discovered"])
        self.assertFalse(state.get("discovered_spot"))
        self.assertEqual(state["call_wall"], 22800.0)
        self.assertEqual(state["put_wall"], 22400.0)
        self.assertEqual(state["gamma_flip"], 22550.0)
        self.assertEqual(state["oi_as_of"], "2026-10-12T15:35:00+05:30")
        self.assertEqual(state["oi_as_of_display"], "12-10-2026 15:35:00 IST")
        self.assertFalse(state["snapshot_stale"])
        self.assertIn("12-10-2026 close", state["status_text"])
        self.assertIn("call-gamma", state["status_text"])
        self.assertIn("put-gamma", state["status_text"])
        self.assertIn("flip", state["status_text"])
        self.assertIn("OI as of 12-10-2026 15:35:00 IST", state["status_text"])

    @patch("data.dhan_live.fetch_index_ohlc")
    @patch("data.market_snapshot.resolve_offmarket_snapshot")
    def test_morning_close_is_the_session_last_not_the_prior_reference(self, mock_snap, mock_fetch):
        """26 Oct book: spot 22753.2 is that close. prev_close 22600 is 23 Oct."""
        from data import pre_post_market as ppm
        ppm._PRE_MARKET_CACHE.clear()
        mock_snap.return_value = {
            "spot": 22753.2,
            "prev_close": 22600.0,
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "gamma_flip": 22750.551157338163,
            "as_of": "2026-10-26T10:10:20.010027+00:00",
        }
        dt = datetime(2026, 10, 27, 6, 39, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=True, now=dt)
        mock_fetch.assert_not_called()
        self.assertEqual(state["prev_close"], 22753.2)
        self.assertEqual(state["oi_as_of_display"], "26-10-2026 15:40:20 IST")
        self.assertIn("26-10-2026 close ₹22,753", state["status_text"])
        self.assertNotIn("22,600", state["status_text"])
        # 22753.2 − 22750.551 = 2.65 → 3 pts above. 22753.2 − 23000 = −246.8 → −247.
        # 22753.2 − 22700 = 53.2 → +53.
        self.assertIn("3 pts above the flip ₹22,751", state["status_text"])
        self.assertIn("-247 pts versus the call-gamma strike ₹23,000", state["status_text"])
        self.assertIn("+53 pts versus the put-gamma strike ₹22,700", state["status_text"])

    @patch("data.dhan_live.fetch_index_ohlc")
    @patch("data.market_snapshot.resolve_offmarket_snapshot")
    def test_morning_withholds_a_close_from_another_session(self, mock_snap, mock_fetch):
        from data import pre_post_market as ppm
        ppm._PRE_MARKET_CACHE.clear()
        mock_snap.return_value = {
            "spot": 22600.0,
            "prev_close": 22500.0,
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "gamma_flip": 22750.0,
            "as_of": "2026-10-21T10:27:04+05:30",
        }
        dt = datetime(2026, 10, 27, 6, 39, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=True, now=dt)
        mock_fetch.assert_not_called()
        self.assertTrue(state["snapshot_stale"])
        self.assertEqual(state["prev_close"], 0.0)
        self.assertIn("21-10-2026 10:27:04 IST", state["status_text"])
        self.assertIn("26-10-2026 close", state["status_text"])
        self.assertIn("not shown", state["status_text"])
        self.assertNotIn("22,600", state["status_text"])

    @patch("data.dhan_live.fetch_index_ohlc")
    @patch("data.market_snapshot.resolve_offmarket_snapshot")
    def test_morning_withholds_a_close_when_the_timestamp_is_missing(self, mock_snap, mock_fetch):
        from data import pre_post_market as ppm
        ppm._PRE_MARKET_CACHE.clear()
        mock_snap.return_value = {
            "spot": 22753.2,
            "prev_close": 22600.0,
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "as_of": "",
        }
        dt = datetime(2026, 10, 27, 6, 39, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=True, now=dt)
        mock_fetch.assert_not_called()
        self.assertTrue(state["snapshot_stale"])
        self.assertEqual(state["prev_close"], 0.0)
        self.assertIn("no timestamp", state["status_text"])
        self.assertNotIn("22,753", state["status_text"])

    @patch("sentinel.storage.db.record_daily_closing_cvd", side_effect=RuntimeError("skip"))
    @patch("data.pre_post_market.save_post_market_state")
    @patch("data.dhan_live.fetch_index_ohlc", return_value=None)
    @patch("data.pre_post_market.get_persisted_auction_record", return_value=None)
    @patch("data.market_snapshot.get_snapshot")
    def test_post_market_net_change_uses_the_same_session_reference(self, mock_snap, _persist, _fetch, _save, _cvd):
        """Morning cache prev_close is the session last. Net change uses the row's prior reference."""
        from data import pre_post_market as ppm
        ppm._POST_MARKET_CACHE.clear()
        ppm._PRE_MARKET_CACHE["NIFTY"] = {"date": "2026-10-26", "prev_close": 22753.2}
        mock_snap.return_value = {
            "spot": 22753.2,
            "prev_close": 22600.0,
            "high": 22753.2,
            "low": 22387.3,
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "as_of": "2026-10-26T15:40:00+05:30",
        }
        state = get_post_market_state("NIFTY", force_refresh=True, now=datetime(2026, 10, 26, 15, 45, tzinfo=IST))
        self.assertEqual(state["prev_close"], 22600.0)
        self.assertEqual(state["net_change_pts"], 153.2)
        self.assertIn("Session Concluded", state["status_text"])

    @patch("sentinel.storage.db.record_daily_closing_cvd", side_effect=RuntimeError("skip"))
    @patch("data.pre_post_market.save_post_market_state")
    @patch("data.dhan_live.fetch_index_ohlc", return_value=None)
    @patch("data.pre_post_market.get_persisted_auction_record", return_value=None)
    @patch("data.market_snapshot.get_snapshot")
    def test_post_market_refuses_a_different_session_book(self, mock_snap, _persist, _fetch, _save, _cvd):
        from data import pre_post_market as ppm
        ppm._POST_MARKET_CACHE.clear()
        mock_snap.return_value = {
            "spot": 22600.0,
            "prev_close": 22500.0,
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "as_of": "2026-10-23T15:40:00+05:30",
        }
        state = get_post_market_state("NIFTY", force_refresh=True, now=datetime(2026, 10, 26, 15, 45, tzinfo=IST))
        self.assertEqual(state["settlement_spot"], 0.0)
        self.assertEqual(state["net_change_pts"], 0.0)
        self.assertFalse(state["post_market_settled"])
        self.assertIn("23-10-2026", state["status_text"])
        self.assertIn("26-10-2026", state["status_text"])
        self.assertIn("not shown", state["status_text"])
        _save.assert_not_called()

    def test_measured_settle_ignores_another_sessions_walls(self):
        from data.pre_post_market import _with_measured_settle
        with patch("data.market_snapshot.get_snapshot", return_value={
            "call_wall": 23000.0,
            "put_wall": 22700.0,
            "as_of": "2026-10-21T15:40:00+05:30",
        }):
            state = _with_measured_settle({
                "date": "2026-10-26",
                "pin_efficiency_pct": 100.0,
                "pin_target_wall": "Call Wall",
                "settlement_spot": 22753.2,
                "next_key_levels": "",
                "settle_note": "placeholder",
            }, "NIFTY")
        self.assertIn("21-10-2026", state["settle_note"])
        self.assertIn("26-10-2026", state["settle_note"])
        self.assertNotIn("23,000", state["settle_note"])
        self.assertEqual(state["pin_target_wall"], "Call Wall")

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

    @patch("data.dhan_live.fetch_index_ohlc")
    @patch("data.pre_post_market.get_persisted_auction_record", return_value=None)
    @patch("data.pre_post_market.ensure_pre_market_poller")
    def test_http_reader_does_not_hit_dhan_during_discovery(self, mock_ensure, _persist, mock_fetch):
        from data import pre_post_market as ppm
        ppm._PRE_MARKET_CACHE.clear()
        dt = datetime(2026, 10, 13, 9, 8, 20, tzinfo=IST)
        state = get_pre_market_state("NIFTY", force_refresh=False, now=dt)
        mock_fetch.assert_not_called()
        mock_ensure.assert_called()
        self.assertFalse(state.get("is_discovered"))

    def test_classify_opening_gap_regimes(self):
        # 1. Call Wall test
        cw_res = classify_opening_gap(gap_pts=50.0, gap_pct=0.22, spot=22700.0, call_wall=22700.0, put_wall=22400.0)
        self.assertEqual(cw_res["regime"], "SHORT_GAMMA_WALL_TEST")
        self.assertIn("CALL-GAMMA", cw_res["badge"])

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

        # 6. Call Wall Breach Squeeze (> CW + 10)
        cws_res = classify_opening_gap(gap_pts=150.0, gap_pct=0.66, spot=22720.0, call_wall=22700.0, put_wall=22400.0)
        self.assertEqual(cws_res["regime"], "CALL_WALL_BREACH_SQUEEZE")
        self.assertIn("CALL STRIKE", cws_res["favored_play"])
        self.assertNotIn("SQUEEZE", cws_res["favored_play"])

        # 7. Put Wall Breakdown Cascade (< PW - 10)
        pwc_res = classify_opening_gap(gap_pts=-150.0, gap_pct=-0.67, spot=22380.0, call_wall=22700.0, put_wall=22400.0)
        self.assertEqual(pwc_res["regime"], "PUT_WALL_BREAKDOWN_CASCADE")
        self.assertIn("PUT STRIKE", pwc_res["favored_play"])
        self.assertNotIn("CASCADE", pwc_res["favored_play"])

        # 8. Gap up above the balance, still under the call strike. Not a fade.
        gup_res = classify_opening_gap(gap_pts=100.0, gap_pct=0.45, spot=22550.0, call_wall=22700.0, put_wall=22300.0, flip=22450.0)
        self.assertEqual(gup_res["regime"], "GAP_UP_INTO_POS_GAMMA")
        self.assertIn("NO FADE", gup_res["favored_play"])

        # 9. Gap down while still above the balance. Not a bounce.
        gdp_res = classify_opening_gap(gap_pts=-100.0, gap_pct=-0.44, spot=22400.0, call_wall=22700.0, put_wall=22300.0, flip=22350.0)
        self.assertEqual(gdp_res["regime"], "GAP_DOWN_INTO_POS_GAMMA")
        self.assertIn("NO BOUNCE", gdp_res["favored_play"])
        self.assertNotIn("Call gamma is larger", gup_res["briefing"])
        self.assertNotIn("Call gamma is larger", gdp_res["briefing"])

        heavy = classify_opening_gap(
            gap_pts=100.0, gap_pct=0.45, spot=22550.0, call_wall=22700.0, put_wall=22300.0,
            flip=22450.0, net_gex=1.0,
        )
        self.assertIn("Call gamma is larger", heavy["briefing"])
        light = classify_opening_gap(
            gap_pts=100.0, gap_pct=0.45, spot=22550.0, call_wall=22700.0, put_wall=22300.0,
            flip=22450.0, net_gex=-1.0,
        )
        self.assertEqual(light["regime"], "GAP_UP_INTO_POS_GAMMA")
        self.assertIn("Put gamma is larger", light["briefing"])
        self.assertNotIn("Call gamma is larger", light["briefing"])

    def test_settle_distance_is_not_a_stamped_pin(self):
        from data.pre_post_market import settle_vs_gamma_strikes
        near = settle_vs_gamma_strikes(22520.0, 22700.0, 22500.0, 50.0)
        self.assertEqual(near["pin_target_wall"], "22,500 put-gamma")
        self.assertLess(near["pin_efficiency_pct"], 100.0)
        self.assertIn("not a pin", near["settle_note"])
        missing = settle_vs_gamma_strikes(22600.0, 0.0, 0.0, 50.0)
        self.assertIsNone(missing["pin_efficiency_pct"])

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

    def test_calculate_pre_market_option_shocks(self):
        # Gap up +100 pts: 22500 -> 22600
        res = calculate_pre_market_option_shocks(
            discovered_spot=22600.0,
            prev_close=22500.0,
            symbol="NIFTY",
            dte_days=3.0,
            atm_iv=0.14,
        )
        self.assertEqual(res["gap_pts"], 100.0)
        self.assertEqual(res["atm_strike"], 22600.0)
        self.assertGreater(len(res["projected_strikes"]), 6)
        self.assertGreater(res["atm_straddle_projected"], 0.0)
        self.assertIn("expansion", res["narrative"].lower())

        # Check ATM CE gained value and ATM PE lost value
        atm_ce = [s for s in res["projected_strikes"] if s["strike"] == 22600.0 and s["side"] == "CE"][0]
        atm_pe = [s for s in res["projected_strikes"] if s["strike"] == 22600.0 and s["side"] == "PE"][0]
        self.assertGreater(atm_ce["shock_pts"], 0.0)
        self.assertLess(atm_pe["shock_pts"], 0.0)

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

    def test_pre_post_market_sqlite_persistence_and_hydration(self):
        test_sym = "TEST_NIFTY"
        test_date = "2026-10-15"

        init_pre_post_db()

        # 1. Save Pre-Market discovery
        sample_state = {
            "symbol": test_sym,
            "date": test_date,
            "phase_code": "REGULAR_SESSION",
            "is_discovered": True,
            "discovered_spot": 25150.0,
            "prev_close": 25000.0,
            "gap_pts": 150.0,
            "gap_pct": 0.60,
            "gap_regime": "GAP_UP_RUNAWAY",
            "regime_badge": "🚀 GAP & GO (BULLISH)",
            "regime_color": "var(--go)",
            "regime_briefing": "Strong gap up test.",
            "favored_play": "CE MOMENTUM",
            "gap_fill_status": "UNFILLED",
            "discovered_at": "09:08:12",
            "status_text": "Equilibrium Discovered: ₹25,150.0",
        }
        sample_shocks = {
            "straddle_shock_pct": 8.5,
            "atm_straddle_prev": 320.0,
            "atm_straddle_projected": 347.2,
            "projected_strikes": [
                {"strike": 25150.0, "side": "CE", "prev_ltp": 160.0, "projected_open_ltp": 210.0, "shock_pts": 50.0, "shock_pct": 31.2},
                {"strike": 25150.0, "side": "PE", "prev_ltp": 160.0, "projected_open_ltp": 137.2, "shock_pts": -22.8, "shock_pct": -14.2},
            ],
        }

        save_pre_market_state(test_sym, sample_state, sample_shocks)

        # 2. Retrieve persisted record
        rec = get_persisted_auction_record(test_sym, test_date)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["symbol"], test_sym)
        self.assertEqual(rec["discovered_spot"], 25150.0)
        self.assertEqual(rec["gap_pts"], 150.0)
        self.assertEqual(rec["gap_regime"], "GAP_UP_RUNAWAY")
        self.assertEqual(rec["gap_fill_status"], "UNFILLED")
        self.assertEqual(rec["straddle_shock_pct"], 8.5)
        self.assertEqual(len(rec["projected_strikes"]), 2)

        # 3. Dynamic Gap Fill Update
        update_gap_fill_status(test_sym, "FILLED", test_date)
        rec2 = get_persisted_auction_record(test_sym, test_date)
        self.assertEqual(rec2["gap_fill_status"], "FILLED")

        # 4. Save Post-Market Settlement
        post_state = {
            "symbol": test_sym,
            "date": test_date,
            "settlement_spot": 25180.0,
            "last_continuous_spot": 25175.0,
            "settlement_divergence_pts": 5.0,
            "pin_efficiency_pct": 98.0,
            "pin_target_wall": "25200 CW",
            "settled_at": "15:40:00",
            "closing_cvd": 45000.0,
            "orderflow_bias": "Institutional Net Buyers (+)",
            "status_text": "Session Concluded",
        }
        save_post_market_state(test_sym, post_state)

        rec3 = get_persisted_auction_record(test_sym, test_date)
        self.assertEqual(rec3["post_market_settled"], 1)
        self.assertEqual(rec3["settlement_spot"], 25180.0)
        self.assertEqual(rec3["closing_cvd"], 45000.0)
        self.assertEqual(rec3["pin_efficiency_pct"], 98.0)
        # Pre-market data must still be intact
        self.assertEqual(rec3["discovered_spot"], 25150.0)
        self.assertEqual(rec3["gap_fill_status"], "FILLED")


if __name__ == "__main__":
    unittest.main()

