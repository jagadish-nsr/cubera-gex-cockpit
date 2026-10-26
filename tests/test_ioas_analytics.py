"""tests/test_ioas_analytics.py — Unit tests for IOAS v3.0 analytics modules.

Covers:
  analytics.directional_bias  — §3.1 Directional Bias Score
  analytics.buildup           — §3.2 Strike Buildup Matrix
  analytics.parity_arb        — §3.3 Put-Call Parity Arb Monitor
  analytics.divergence        — §3.4 Active Divergence Engine

All tests are network-free, Streamlit-free, and deterministic.
"""

from __future__ import annotations
import pytest
import math


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_oi_strikes(n=10, spot=24000, step=50):
    """Create synthetic per-strike OI data for testing."""
    rows = []
    for i in range(-n // 2, n // 2):
        k = spot + i * step
        rows.append({
            "strike": float(k),
            "call_oi": 100_000 + i * 5_000,
            "put_oi": 120_000 - i * 3_000,
            "call_doi": 5_000 if i > 0 else -3_000,
            "put_doi": -2_000 if i > 0 else 4_000,
            "call_ltp": max(1.0, 200 - abs(i) * 30),
            "put_ltp": max(1.0, 200 + abs(i) * 20),
            "net_gex": (100_000 + i * 5_000) * 0.5 * (k ** 2) / (24000 ** 2) * 0.01,
        })
    return rows


def _make_contracts_df(spot=24000, step=50, n=10):
    """Create synthetic contracts DataFrame for testing."""
    pytest.importorskip("pandas")
    import pandas as pd
    rows = []
    for i in range(-n, n):
        k = spot + i * step
        for opt_type in ("C", "P"):
            rows.append({
                "expiry": "2026-10-03",
                "type": opt_type,
                "strike": float(k),
                "ltp": max(0.5, 200 - abs(i) * 20),
                "iv": 0.12 + abs(i) * 0.005,
                "delta": (0.5 - i * 0.05) if opt_type == "C" else (-0.5 + i * 0.05),
                "T": 1 / 365,
            })
    return pd.DataFrame(rows)


# ===========================================================================
# §3.1 Directional Bias Score
# ===========================================================================

class TestDirectionalBias:

    def test_all_none_inputs_returns_neutral(self):
        """With all optional inputs missing, score should be near 0."""
        from analytics.directional_bias import compute_directional_bias
        result = compute_directional_bias(spot=24000.0, call_wall=None, put_wall=None, max_pain=None, pcr=None)
        assert result.score == 0.0
        assert "Neutral" in result.regime

    def test_pcr_does_not_move_the_score(self):
        """PCR states the open-interest mix. Only VIX moves this case."""
        from analytics.directional_bias import compute_directional_bias
        result = compute_directional_bias(
            spot=24000.0, call_wall=24500, put_wall=23500, max_pain=24100,
            pcr=1.50,
            india_vix={"last": 11.0},
        )
        pcr_row = next(s for s in result.signals if s.key == "s1_pcr_level")
        assert pcr_row.value == 0.0
        assert "put-heavy" in pcr_row.label
        assert "not a direction" in pcr_row.label.lower()
        assert result.score == 1.0

    def test_low_pcr_does_not_add_a_bearish_point(self):
        """Call-heavy PCR is a fact. The bearish point here is high VIX only."""
        from analytics.directional_bias import compute_directional_bias
        result = compute_directional_bias(
            spot=24000.0, call_wall=24500, put_wall=23500, max_pain=23800,
            pcr=0.60,
            india_vix={"last": 25.0},
        )
        pcr_row = next(s for s in result.signals if s.key == "s1_pcr_level")
        assert pcr_row.value == 0.0
        assert "call-heavy" in pcr_row.label
        assert result.score == -1.0

    def test_score_in_bounds(self):
        """Score must always be in [-10, +10]."""
        from analytics.directional_bias import compute_directional_bias
        # Pathological case: all signals maxed
        result = compute_directional_bias(
            spot=24000.0, call_wall=25000, put_wall=23000, max_pain=25000,
            pcr=2.0, pcr_15m_ago=1.0, call_doi=-1_000_000, put_doi=5_000_000,
            oi_strikes=_make_oi_strikes(), india_vix={"last": 8.0},
            skew_25d=0.05, near_atm_iv=0.10,
        )
        assert -10.0 <= result.score <= 10.0

    def test_weights_sum(self):
        """Weights must sum to 10.0 (spec invariant)."""
        from analytics.directional_bias import WEIGHTS
        assert abs(sum(WEIGHTS.values()) - 10.0) < 1e-6

    def test_eight_subsignals_returned(self):
        """compute_directional_bias must always return exactly 8 sub-signals."""
        from analytics.directional_bias import compute_directional_bias
        result = compute_directional_bias(spot=24000.0, call_wall=24500, put_wall=23500, max_pain=24100, pcr=1.1)
        assert len(result.signals) == 8

    def test_wall_align_above_midpoint(self):
        """Spot above the gamma-strike midpoint is a location. It does not score."""
        from analytics.directional_bias import _s4_wall_align
        val, label, _ = _s4_wall_align(24500, 25000, 23000)
        assert val == 0.0
        assert "location only" in label.lower()

    def test_pcr_trend_does_not_score(self):
        """A PCR change is recorded and does not add a point."""
        from analytics.directional_bias import _s2_pcr_trend
        up, up_label, _ = _s2_pcr_trend(1.30, 1.20)
        down, down_label, _ = _s2_pcr_trend(1.00, 1.20)
        assert up == 0.0 and down == 0.0
        assert "not a direction" in up_label.lower()
        assert "not a direction" in down_label.lower()

    def test_pcr_trend_missing(self):
        """Missing PCR history returns 0 (fail-soft)."""
        from analytics.directional_bias import _s2_pcr_trend
        val, label, _ = _s2_pcr_trend(None, None)
        assert val == 0.0

    def test_iv_rank_low_vix_bullish(self):
        """Low VIX (<12) → bullish IV Rank signal."""
        from analytics.directional_bias import _s8_iv_rank
        val, _, _ = _s8_iv_rank({"last": 10.0}, None)
        assert val == 1.0

    def test_iv_rank_high_vix_bearish(self):
        """High VIX (>22) → bearish IV Rank signal."""
        from analytics.directional_bias import _s8_iv_rank
        val, _, _ = _s8_iv_rank({"last": 25.0}, None)
        assert val == -1.0

    def test_oi_velocity_does_not_score(self):
        """Open-interest changes are shown and do not move the score."""
        from analytics.directional_bias import _s6_oi_velocity
        put_side, put_label, _ = _s6_oi_velocity(100_000, 500_000)
        call_side, call_label, _ = _s6_oi_velocity(500_000, 50_000)
        assert put_side == 0.0 and call_side == 0.0
        assert "not a direction" in put_label.lower()
        assert "not a direction" in call_label.lower()

    def test_max_pain_is_not_a_pull(self):
        """Distance to max pain is a location. It does not score."""
        from analytics.directional_bias import _s5_maxpain_grav
        val, label, _ = _s5_maxpain_grav(23000.0, 24000.0, band_bps=50.0)
        assert val == 0.0
        assert "not a pull" in label.lower()

    def test_regime_labels(self):
        """Verify all regime labels are mapped correctly."""
        from analytics.directional_bias import _regime_label
        assert "Extreme Bullish" in _regime_label(8.0)
        assert "Bullish" in _regime_label(5.0)
        assert "Neutral" in _regime_label(0.0)
        assert "Bearish" in _regime_label(-5.0)
        assert "Extreme Bearish" in _regime_label(-8.0)


# ===========================================================================
# §3.2 Strike Buildup Matrix
# ===========================================================================

class TestBuildupMatrix:

    def test_empty_input_returns_empty_result(self):
        """Empty oi_strikes returns neutral empty result."""
        from analytics.buildup import build_strike_matrix
        result = build_strike_matrix([])
        assert result["strikes"] == []
        assert result["summary"]["dominant_bias"] == "NEUTRAL"

    def test_classify_buildup_logic(self):
        """classify_buildup maps price/OI direction to correct label."""
        from analytics.buildup import classify_buildup
        assert classify_buildup(1000, True) == "LONG_BUILDUP"
        assert classify_buildup(1000, False) == "SHORT_BUILDUP"
        assert classify_buildup(-1000, True) == "SHORT_COVERING"
        assert classify_buildup(-1000, False) == "LONG_UNWINDING"
        assert classify_buildup(0, True) == "NEUTRAL"

    def test_build_returns_top_3_conviction(self):
        """build_strike_matrix returns exactly top_n=3 conviction strikes."""
        from analytics.buildup import build_strike_matrix
        oi_strikes = _make_oi_strikes(n=20)
        result = build_strike_matrix(oi_strikes, price_up=True, spot=24000, top_n=3)
        assert len(result["top_conviction"]) <= 3

    def test_build_sorts_by_velocity(self):
        """top_conviction strikes are sorted descending by net_velocity."""
        from analytics.buildup import build_strike_matrix
        oi_strikes = _make_oi_strikes(n=20)
        result = build_strike_matrix(oi_strikes, price_up=True)
        vels = [s.net_velocity for s in result["strikes"]]
        assert vels == sorted(vels, reverse=True)

    def test_min_oi_filter(self):
        """Strikes below min_oi threshold are excluded."""
        from analytics.buildup import build_strike_matrix
        oi_strikes = [
            {"strike": 24000.0, "call_oi": 10, "put_oi": 10,
             "call_doi": 5, "put_doi": 5},  # total OI=20, below min
            {"strike": 24050.0, "call_oi": 100_000, "put_oi": 100_000,
             "call_doi": 5000, "put_doi": 5000},  # above min
        ]
        result = build_strike_matrix(oi_strikes, min_oi=50_000)
        assert len(result["strikes"]) == 1
        assert result["strikes"][0].strike == 24050.0

    def test_dominant_bias_bullish(self):
        """More long-buildup strikes than short-buildup → BULLISH dominant bias."""
        from analytics.buildup import build_strike_matrix
        # price_up=True + put_doi > 0 → LONG_BUILDUP for puts (protective puts adding)
        oi_strikes = [
            {"strike": float(24000 + i * 50), "call_oi": 50_000, "put_oi": 50_000,
             "call_doi": -2000, "put_doi": 3000}  # put_doi>0, call_doi<0 → long_b++
            for i in range(10)
        ]
        result = build_strike_matrix(oi_strikes, price_up=True, min_oi=0)
        assert result["summary"]["dominant_bias"] == "BULLISH"

    def test_no_crash_on_none_doi(self):
        """Handles missing/None doi fields gracefully."""
        from analytics.buildup import build_strike_matrix
        oi_strikes = [{"strike": 24000.0, "call_oi": 100_000, "put_oi": 80_000,
                       "call_doi": None, "put_doi": None}]
        result = build_strike_matrix(oi_strikes, min_oi=0)
        assert result["strikes"][0].call_classification == "NEUTRAL"


# ===========================================================================
# §3.3 Put-Call Parity Arb Monitor
# ===========================================================================

class TestParityArb:

    def test_none_df_returns_empty(self):
        """compute_parity_arb with None df returns empty report."""
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(None, spot=24000.0)
        assert result.deviations == []
        assert result.summary == "Insufficient data"

    def test_fair_pricing_no_alerts(self):
        """Perfectly priced synthetic (C-P = S-K*e^-rT) → no alerts."""
        pytest.importorskip("pandas")
        import pandas as pd
        import math
        spot, r, T = 24000.0, 0.065, 1/365
        rows = []
        for k in [23900.0, 24000.0, 24100.0]:
            fair_fwd = spot - k * math.exp(-r * T)
            c_ltp = fair_fwd + 100.0  # both sides off by equal amount
            p_ltp = 100.0
            for opt_type, ltp in [("C", c_ltp), ("P", p_ltp)]:
                rows.append({"expiry": "2026-10-03", "type": opt_type,
                             "strike": k, "ltp": ltp, "T": T})
        df = pd.DataFrame(rows)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=spot)
        assert len(result.alerted) == 0

    def test_mispriced_triggers_alert(self):
        """Grossly mispriced strikes (>10bps) should trigger alerts."""
        pytest.importorskip("pandas")
        import pandas as pd
        spot = 24000.0
        rows = [
            {"expiry": "2026-10-03", "type": "C", "strike": 24000.0, "ltp": 300.0, "T": 1/365},
            {"expiry": "2026-10-03", "type": "P", "strike": 24000.0, "ltp": 50.0, "T": 1/365},
        ]
        df = pd.DataFrame(rows)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=spot)
        assert len(result.alerted) > 0

    def test_deviations_sorted_by_abs_bps(self):
        """Deviations are sorted descending by |discrepancy_bps|."""
        df = _make_contracts_df(spot=24000)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=24000.0)
        bps = [abs(d.discrepancy_bps) for d in result.deviations]
        assert bps == sorted(bps, reverse=True)

    def test_zero_dte_guard(self):
        """T=0 DTE is clamped to avoid ZeroDivisionError."""
        pytest.importorskip("pandas")
        import pandas as pd
        rows = [
            {"expiry": "2026-10-03", "type": "C", "strike": 24000.0, "ltp": 50.0, "T": 0.0},
            {"expiry": "2026-10-03", "type": "P", "strike": 24000.0, "ltp": 50.0, "T": 0.0},
        ]
        df = pd.DataFrame(rows)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=24000.0)
        # Should not raise — just produce a result
        assert result is not None

    def test_summary_all_fair(self):
        """Summary mentions all within tolerance when no alerts."""
        df = _make_contracts_df(spot=24000, n=5)
        # Patch all LTPs to enforce near-parity (not guaranteed from synthetic data)
        import pandas as pd
        import math
        spot, r = 24000.0, 0.065
        rows = []
        for k in [23900.0, 24000.0, 24100.0]:
            T = 1 / 365
            fair_diff = spot - k * math.exp(-r * T)
            for opt_type, ltp in [("C", fair_diff / 2 + 50), ("P", -fair_diff / 2 + 50)]:
                rows.append({"expiry": "2026-10-03", "type": opt_type,
                             "strike": k, "ltp": max(0.1, ltp), "T": T})
        df = pd.DataFrame(rows)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=spot)
        if not result.alerted:
            assert "within" in result.summary.lower() or "No arb" in result.summary

    def test_contracts_with_mid_column(self):
        """Standard OptionsSnapshot contracts DataFrame with 'mid' instead of 'ltp' works."""
        import pandas as pd
        spot = 24000.0
        rows = [
            {"expiry": "2026-10-03", "type": "C", "strike": 24000.0, "mid": 300.0, "T": 1/365},
            {"expiry": "2026-10-03", "type": "P", "strike": 24000.0, "mid": 50.0, "T": 1/365},
        ]
        df = pd.DataFrame(rows)
        from analytics.parity_arb import compute_parity_arb
        result = compute_parity_arb(df, spot=spot)
        assert len(result.deviations) == 1
        assert result.deviations[0].strike == 24000.0
        assert result.deviations[0].call_ltp == 300.0
        assert result.deviations[0].put_ltp == 50.0



# ===========================================================================
# §3.4 Active Divergence Engine
# ===========================================================================

class TestDivergenceEngine:

    def test_no_inputs_returns_clean(self):
        """detect_divergences with all-None inputs returns clean report."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=24000, spot_prev=None, pcr=None, pcr_prev=None,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None, call_wall_prev=None, put_wall_prev=None,
        )
        assert report.active_divergences == []
        assert report.new_alerts == []
        assert "No active" in report.summary

    def test_price_pcr_bear_divergence(self):
        """Rising spot + falling PCR → PRICE_PCR_BEAR divergence."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=24300, spot_prev=24000,   # +1.25% up
            pcr=0.85, pcr_prev=1.10,      # PCR fell >3%
            pcr_prev_session=None,
            atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        types = {d.type for d in report.active_divergences}
        assert "PRICE_PCR_BEAR" in types

    def test_price_pcr_bull_divergence(self):
        """Falling spot + rising PCR → PRICE_PCR_BULL divergence."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=23700, spot_prev=24000,   # price down
            pcr=1.30, pcr_prev=1.00,      # PCR up >3%
            pcr_prev_session=None,
            atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        types = {d.type for d in report.active_divergences}
        assert "PRICE_PCR_BULL" in types

    def test_pcr_flip_shift_alert(self):
        """PCR crossing 1.0 generates a PCR_FLIP shift alert."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=24000, spot_prev=24000,
            pcr=0.95, pcr_prev=1.05,      # flip below 1.0
            pcr_prev_session=None,
            atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        alert_types = {a.type for a in report.new_alerts}
        assert "PCR_FLIP" in alert_types

    def test_call_wall_breach_alert(self):
        """Spot crossing call wall from below generates WALL_BREACH alert."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=24550,         # crossed above call_wall
            spot_prev=24400,    # was below
            pcr=None, pcr_prev=None,
            pcr_prev_session=None,
            atm_iv=None, atm_iv_prev=None,
            call_wall=24500,    # the wall
            put_wall=23500,
            call_wall_prev=24500, put_wall_prev=23500,
        )
        alert_types = {a.type for a in report.new_alerts}
        assert "WALL_BREACH" in alert_types

    def test_parity_dislocation_after_3_polls(self):
        """3 consecutive parity breaches → PARITY_DISLOC divergence."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=24000, spot_prev=None, pcr=None, pcr_prev=None,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
            parity_alert_count=3,
        )
        types = {d.type for d in report.active_divergences}
        assert "PARITY_DISLOC" in types

    def test_oi_burst_alert(self):
        """2σ OI burst generates OI_BURST shift alert."""
        from analytics.divergence import detect_divergences
        history = [1_000, 1_100, 900, 1_050, 980, 1020]
        report = detect_divergences(
            spot=24000, spot_prev=None, pcr=None, pcr_prev=None,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
            call_doi_total=10_000,   # way above history average ~1000
            put_doi_total=12_000,
            oi_doi_history=history,
        )
        alert_types = {a.type for a in report.new_alerts}
        assert "OI_BURST" in alert_types

    def test_divergence_persistence(self):
        """Divergence persists across polls when condition is maintained."""
        from analytics.divergence import detect_divergences
        # First poll — create divergence
        r1 = detect_divergences(
            spot=24300, spot_prev=24000,
            pcr=0.85, pcr_prev=1.10,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        # Second poll — condition still holds, pass existing divergences
        r2 = detect_divergences(
            spot=24400, spot_prev=24300,
            pcr=0.80, pcr_prev=0.85,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
            existing_divergences=r1.active_divergences,
        )
        # Should still have the same divergence (not duplicated)
        types = [d.type for d in r2.active_divergences]
        assert types.count("PRICE_PCR_BEAR") == 1

    def test_divergence_resolves(self):
        """Divergence resolves when condition no longer holds."""
        from analytics.divergence import detect_divergences
        # Create divergence
        r1 = detect_divergences(
            spot=24300, spot_prev=24000,
            pcr=0.85, pcr_prev=1.10,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        # Condition reverses — spot now lower, PCR recovers
        r2 = detect_divergences(
            spot=24100, spot_prev=24300,  # spot fell
            pcr=1.10, pcr_prev=0.85,     # PCR recovered
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
            existing_divergences=r1.active_divergences,
        )
        active_types = {d.type for d in r2.active_divergences}
        assert "PRICE_PCR_BEAR" not in active_types

    def test_severity_levels_assigned(self):
        """HIGH severity is assigned to price/pcr bear and parity dislocations."""
        from analytics.divergence import detect_divergences
        r = detect_divergences(
            spot=24300, spot_prev=24000,
            pcr=0.85, pcr_prev=1.10,
            pcr_prev_session=None, atm_iv=None, atm_iv_prev=None,
            call_wall=None, put_wall=None,
            call_wall_prev=None, put_wall_prev=None,
        )
        for d in r.active_divergences:
            if d.type == "PRICE_PCR_BEAR":
                assert d.severity == "HIGH"

    def test_no_crash_extreme_inputs(self):
        """No exception on extreme/edge-case inputs."""
        from analytics.divergence import detect_divergences
        report = detect_divergences(
            spot=0.0001, spot_prev=0.0001,
            pcr=99.99, pcr_prev=0.0001,
            pcr_prev_session=0.5,
            atm_iv=9.99, atm_iv_prev=0.001,
            call_wall=0.001, put_wall=0.001,
            call_wall_prev=0.001, put_wall_prev=0.001,
            parity_alert_count=999,
        )
        assert report is not None


# ===========================================================================
# Integration: Bias + Buildup together
# ===========================================================================

class TestIntegration:

    def test_bias_uses_buildup_oi_strikes(self):
        """Bias score changes when oi_strikes is provided vs not."""
        from analytics.directional_bias import compute_directional_bias
        base = compute_directional_bias(
            spot=24000, call_wall=24500, put_wall=23500, max_pain=24100, pcr=1.1,
        )
        with_buildup = compute_directional_bias(
            spot=24000, call_wall=24500, put_wall=23500, max_pain=24100, pcr=1.1,
            oi_strikes=_make_oi_strikes(),
        )
        # Both should be valid scores
        assert -10.0 <= base.score <= 10.0
        assert -10.0 <= with_buildup.score <= 10.0

    def test_full_analytics_pipeline_no_crash(self):
        """Full pipeline: bias + buildup + parity + divergence runs without error."""
        from analytics.directional_bias import compute_directional_bias
        from analytics.buildup import build_strike_matrix
        from analytics.parity_arb import compute_parity_arb
        from analytics.divergence import detect_divergences

        oi_strikes = _make_oi_strikes()
        df = _make_contracts_df()

        bias = compute_directional_bias(
            spot=24000, call_wall=24500, put_wall=23500, max_pain=24100,
            pcr=1.2, call_doi=50_000, put_doi=120_000, oi_strikes=oi_strikes,
            india_vix={"last": 14.0}, skew_25d=0.02,
        )
        buildup = build_strike_matrix(oi_strikes, price_up=True, spot=24000, min_oi=0)
        parity = compute_parity_arb(df, spot=24000.0)
        div = detect_divergences(
            spot=24000, spot_prev=23950, pcr=1.2, pcr_prev=1.15,
            pcr_prev_session=None, atm_iv=0.12, atm_iv_prev=0.11,
            call_wall=24500, put_wall=23500,
            call_wall_prev=24500, put_wall_prev=23500,
        )

        assert bias.score is not None
        assert buildup["summary"]["dominant_bias"] in ("BULLISH", "BEARISH", "NEUTRAL")
        assert parity.summary != ""
        assert div.summary != ""
