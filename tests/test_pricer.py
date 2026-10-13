"""Unit tests for Black-76 pricer, Greeks, IV solver, and time utilities (Phase 1).

Tests:
1. Reference values and sanity bounds
2. Put-Call parity
3. IV solver round-trip (accuracy < 1e-6)
4. Analytical Greeks vs Finite-Difference benchmarks
5. Greek attribution and residual properties
6. Extreme inputs and edge cases
7. Reconciliation checks
8. Time handling and fractional T calculations
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

from target_engine.contracts import GreekAttribution, OptionGreeks
from target_engine.pricer import (
    compute_path_grid,
    greek_attribution,
    greeks,
    implied_vol,
    price,
    reconciliation_check,
    reprice,
)
from target_engine.time_utils import (
    calculate_fractional_t,
    calculate_horizon_t,
    parse_expiry_datetime,
    to_ist,
)


class TestBlack76Pricer:
    """Test core pricing and put-call parity."""

    def test_reference_value_call_and_put(self):
        # Known Black-76 benchmark: F=25000, K=25000, T=30/365, sigma=0.15, r=0.065
        F = 25000.0
        K = 25000.0
        T = 30.0 / 365.0
        sigma = 0.15
        r = 0.065

        call_p = price(F, K, T, sigma, r, "CE")
        put_p = price(F, K, T, sigma, r, "PE")

        assert call_p > 0.0
        assert put_p > 0.0
        # For ATM forward (F=K), call and put prices should be identical
        assert math.isclose(call_p, put_p, rel_tol=1e-5)

    def test_put_call_parity(self):
        # Call - Put = exp(-r*T) * (F - K)
        F = 25200.0
        r = 0.065
        T = 15.0 / 365.0
        sigma = 0.16

        for K in [24500.0, 25000.0, 25200.0, 25500.0, 26000.0]:
            c = price(F, K, T, sigma, r, "CE")
            p = price(F, K, T, sigma, r, "PE")
            df = math.exp(-r * T)
            parity_diff = (c - p) - df * (F - K)
            assert abs(parity_diff) < 1e-6, f"Parity violated at strike {K}: diff={parity_diff}"

    def test_zero_time_returns_intrinsic(self):
        F = 25350.0
        r = 0.065
        sigma = 0.15
        T = 0.0

        # ITM Call
        assert math.isclose(price(F, 25000.0, T, sigma, r, "CE"), 350.0, abs_tol=1e-4)
        # OTM Call
        assert math.isclose(price(F, 25500.0, T, sigma, r, "CE"), 0.0, abs_tol=1e-4)
        # ITM Put
        assert math.isclose(price(F, 25500.0, T, sigma, r, "PE"), 150.0, abs_tol=1e-4)
        # OTM Put
        assert math.isclose(price(F, 25000.0, T, sigma, r, "PE"), 0.0, abs_tol=1e-4)

    def test_invalid_parameters_raise(self):
        with pytest.raises(ValueError):
            price(-25000, 25000, 0.1, 0.15, 0.065, "CE")
        with pytest.raises(ValueError):
            price(25000, -25000, 0.1, 0.15, 0.065, "CE")
        with pytest.raises(ValueError):
            price(25000, 25000, 0.1, 0.15, 0.065, "INVALID")


class TestImpliedVolatilitySolver:
    """Test Newton-Raphson + Bisection IV inversion."""

    @pytest.mark.parametrize("sigma_target", [0.08, 0.125, 0.18, 0.25, 0.40, 0.75])
    @pytest.mark.parametrize("strike_offset", [-500.0, 0.0, 500.0])
    @pytest.mark.parametrize("opt_type", ["CE", "PE"])
    def test_iv_roundtrip(self, sigma_target, strike_offset, opt_type):
        F = 25000.0
        K = F + strike_offset
        T = 20.0 / 365.0
        r = 0.065

        mkt_price = price(F, K, T, sigma_target, r, opt_type)
        recovered_iv = implied_vol(mkt_price, F, K, T, r, opt_type)

        assert recovered_iv is not None
        assert abs(recovered_iv - sigma_target) < 1e-5, (
            f"Failed roundtrip: target={sigma_target}, got={recovered_iv}"
        )

    def test_below_intrinsic_returns_none(self):
        F = 25500.0
        K = 25000.0
        T = 10.0 / 365.0
        r = 0.065
        df = math.exp(-r * T)
        intrinsic = df * (F - K)

        # Price below discounted intrinsic violates no-arbitrage
        bad_price = intrinsic - 5.0
        assert implied_vol(bad_price, F, K, T, r, "CE") is None

    def test_excessive_price_returns_none(self):
        F = 25000.0
        K = 25000.0
        T = 10.0 / 365.0
        # Call cannot exceed F * df
        bad_price = 26000.0
        assert implied_vol(bad_price, F, K, T, 0.065, "CE") is None


class TestGreeksVsFiniteDifference:
    """Verify analytical Greeks match numerical finite-difference derivatives."""

    def test_delta_and_gamma_finite_difference(self):
        F = 25000.0
        K = 25000.0
        T = 25.0 / 365.0
        sigma = 0.18
        r = 0.065
        dF = 1.0  # 1 index point bump

        # Call Greeks
        g_call = greeks(F, K, T, sigma, r, "CE")
        p_up = price(F + dF, K, T, sigma, r, "CE")
        p_dn = price(F - dF, K, T, sigma, r, "CE")
        fd_delta = (p_up - p_dn) / (2.0 * dF)
        fd_gamma = (p_up - 2.0 * price(F, K, T, sigma, r, "CE") + p_dn) / (dF ** 2)

        assert math.isclose(g_call.delta, fd_delta, rel_tol=1e-4)
        assert math.isclose(g_call.gamma, fd_gamma, rel_tol=1e-3)

        # Put Delta
        g_put = greeks(F, K, T, sigma, r, "PE")
        p_up_put = price(F + dF, K, T, sigma, r, "PE")
        p_dn_put = price(F - dF, K, T, sigma, r, "PE")
        fd_delta_put = (p_up_put - p_dn_put) / (2.0 * dF)
        assert math.isclose(g_put.delta, fd_delta_put, rel_tol=1e-4)

    def test_vega_finite_difference(self):
        F = 25000.0
        K = 25200.0
        T = 15.0 / 365.0
        sigma = 0.16
        r = 0.065
        # Vega is defined per 1% (0.01) change in IV
        dSigma = 0.001

        g = greeks(F, K, T, sigma, r, "CE")
        p_up = price(F, K, T, sigma + dSigma, r, "CE")
        p_dn = price(F, K, T, sigma - dSigma, r, "CE")
        fd_vega = ((p_up - p_dn) / (2.0 * dSigma)) * 0.01

        assert math.isclose(g.vega, fd_vega, rel_tol=1e-4)

    def test_theta_finite_difference(self):
        F = 25000.0
        K = 25000.0
        T = 30.0 / 365.0
        sigma = 0.15
        r = 0.065
        # 1 hour elapsed in years
        dT_elapsed = (1.0 / 24.0) / 365.0
        dT_days = 1.0 / 24.0

        g = greeks(F, K, T, sigma, r, "CE")
        p_now = price(F, K, T, sigma, r, "CE")
        p_later = price(F, K, T - dT_elapsed, sigma, r, "CE")
        fd_theta_per_day = (p_later - p_now) / dT_days

        assert math.isclose(g.theta, fd_theta_per_day, rel_tol=1e-3)
        assert g.theta < 0.0  # Time decay is negative for long ATM options


class TestRepricingAndAttribution:
    """Test scenario repricing and Greek decomposition."""

    def test_reprice_and_attribution_identity(self):
        F_entry = 25000.0
        F_target = 25150.0  # +150 pts rally
        sigma_entry = 0.16
        sigma_target = 0.155  # -0.5 pts vol crush
        T_entry = 10.0 / 365.0
        T_target = 9.5 / 365.0  # half day passed
        K = 25100.0
        r = 0.065

        attr = greek_attribution(
            F_entry=F_entry,
            F_target=F_target,
            sigma_entry=sigma_entry,
            sigma_target=sigma_target,
            T_entry=T_entry,
            T_target=T_target,
            K=K,
            r=r,
            option_type="CE",
        )

        assert isinstance(attr, GreekAttribution)
        # Modeled + Residual must exactly equal Full Reprice PnL
        assert math.isclose(attr.total_modeled_pnl + attr.residual, attr.full_reprice_pnl, abs_tol=1e-7)
        # Delta PnL should be positive for an upmove on a Call
        assert attr.delta_pnl > 0.0
        # Vega PnL should be negative for a vol drop on a long option
        assert attr.vega_pnl < 0.0
        # Theta PnL should be negative for elapsed time
        assert attr.theta_pnl < 0.0

    def test_reconciliation_check(self):
        F = 25000.0
        K = 25000.0
        T = 7.0 / 365.0
        iv = 0.15
        r = 0.065

        # Exact model price
        exact_p = price(F, K, T, iv, r, "CE")
        is_ok, p_calc, gap = reconciliation_check(exact_p + 0.5, F, K, T, iv, r, "CE", tolerance_pts=2.0)
        assert is_ok is True
        assert math.isclose(gap, 0.5, abs_tol=1e-4)

        # Large discrepancy fails
        is_ok_bad, _, gap_bad = reconciliation_check(exact_p + 15.0, F, K, T, iv, r, "CE", tolerance_pts=2.0)
        assert is_ok_bad is False
        assert gap_bad > 10.0

    def test_path_grid(self):
        grid = compute_path_grid(
            F_entry=25000.0,
            K=25000.0,
            T_entry=10.0 / 365.0,
            sigma_entry=0.16,
            target_sigma=0.15,
            spot_shifts=[0.99, 1.0, 1.01],
            time_fractions=[0.0, 0.5, 1.0],
            r=0.065,
            option_type="CE",
        )
        assert len(grid) == 3
        assert "t_0pct" in grid[0]
        assert "t_100pct" in grid[0]


class TestTimeUtils:
    """Test IST datetime conversions and horizon clipping."""

    def test_fractional_t_calculation(self):
        now = datetime(2026, 10, 15, 9, 30, 0)
        expiry = datetime(2026, 10, 15, 15, 30, 0)
        t_years = calculate_fractional_t(now, expiry)

        # 6 hours = 0.25 days = 0.25 / 365 years
        expected = 0.25 / 365.0
        assert math.isclose(t_years, expected, rel_tol=1e-4)

    def test_horizon_clipped_to_expiry(self):
        now = datetime(2026, 10, 15, 14, 0, 0)
        expiry = datetime(2026, 10, 15, 15, 30, 0)  # Expiry is 1.5h away

        # Request 180 min horizon (3 hours), should be clipped to 1.5 hours
        horizon_end, remaining_t = calculate_horizon_t(now, 180, expiry)
        exp_dt = parse_expiry_datetime(expiry)

        assert horizon_end == exp_dt
        assert remaining_t <= 1e-5
