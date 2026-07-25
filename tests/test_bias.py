"""Tests for analytics/bias.py — validates the positioning-divergence formula.

Primary contract: the §7 worked example from the implementation prompt must be
reproduced EXACTLY using real data from the 24 Jul 2026 participant OI CSV.

Raw values from data/journal/participant_oi/2026-07-24.csv:
  Client: fut_long=239763 fut_short=63265  call_long=3506533 put_long=2630865 call_short=3461767 put_short=3333756
  DII:    fut_long=80806  fut_short=15678  call_long=7815    put_long=41274   call_short=275     put_short=225
  FII:    fut_long=28237  fut_short=299084 call_long=681831  put_long=1125560 call_short=940641  put_short=590382
  Pro:    fut_long=64445  fut_short=35224  call_long=1325852 put_long=1056485 call_short=1119349 put_short=929820

Expected bias scores (from prompt §7 and verified by local computation):
  Client:     +924,155
  DII:         +31,619
  FII:      -1,064,835
  Pro:        +109,059
  Divergence: +1,988,990  (Client - FII)
"""

import pytest
from analytics.bias import (
    LARGE_DIVERGENCE_THRESHOLD,
    bias_score,
    compute_category_biases,
    divergence_trend,
    interpret_divergence,
    rolling_divergence,
)

# ---------------------------------------------------------------------------
# Ground-truth fixture from 24 Jul 2026 CSV
# ---------------------------------------------------------------------------

_24JUL_PARSED = {
    "Client": {
        "fut_long": 239763, "fut_short": 63265,
        "opt_call_long": 3506533, "opt_put_long": 2630865,
        "opt_call_short": 3461767, "opt_put_short": 3333756,
    },
    "DII": {
        "fut_long": 80806, "fut_short": 15678,
        "opt_call_long": 7815, "opt_put_long": 41274,
        "opt_call_short": 275, "opt_put_short": 225,
    },
    "FII": {
        "fut_long": 28237, "fut_short": 299084,
        "opt_call_long": 681831, "opt_put_long": 1125560,
        "opt_call_short": 940641, "opt_put_short": 590382,
    },
    "Pro": {
        "fut_long": 64445, "fut_short": 35224,
        "opt_call_long": 1325852, "opt_put_long": 1056485,
        "opt_call_short": 1119349, "opt_put_short": 929820,
    },
    "TOTAL": {  # included so compute_category_biases ignores it gracefully
        "fut_long": 413251, "fut_short": 413251,
        "opt_call_long": 5522031, "opt_put_long": 4854184,
        "opt_call_short": 5522031, "opt_put_short": 4854184,
    },
}


# ---------------------------------------------------------------------------
# §7 Worked-example tests — these MUST pass on any refactoring
# ---------------------------------------------------------------------------

class TestWorkedExample:
    """Exact reproduction of the §7 worked example from the prompt."""

    def test_client_bias_score(self):
        assert bias_score(_24JUL_PARSED["Client"]) == +924_155

    def test_dii_bias_score(self):
        assert bias_score(_24JUL_PARSED["DII"]) == +31_619

    def test_fii_bias_score(self):
        assert bias_score(_24JUL_PARSED["FII"]) == -1_064_835

    def test_pro_bias_score(self):
        assert bias_score(_24JUL_PARSED["Pro"]) == +109_059

    def test_divergence_client_minus_fii(self):
        biases = compute_category_biases(_24JUL_PARSED)
        assert biases["divergence"] == +1_988_990

    def test_compute_category_biases_all_fields(self):
        biases = compute_category_biases(_24JUL_PARSED)
        assert biases["retail_bias"]        == +924_155
        assert biases["institutional_bias"] == -1_064_835
        assert biases["divergence"]         == +1_988_990
        assert biases["DII"]                == +31_619
        assert biases["Pro"]                == +109_059

    def test_breakdown_components(self):
        biases = compute_category_biases(_24JUL_PARSED)
        bd = biases["breakdown"]
        assert bd["Client"]["net_fut"]  == +176_498
        assert bd["Client"]["net_call"] == +44_766
        assert bd["Client"]["net_put"]  == -702_891
        assert bd["FII"]["net_fut"]     == -270_847
        assert bd["FII"]["net_call"]    == -258_810
        assert bd["FII"]["net_put"]     == +535_178


# ---------------------------------------------------------------------------
# bias_score unit tests
# ---------------------------------------------------------------------------

class TestBiasScore:
    def test_all_zeros(self):
        v = {k: 0 for k in ("fut_long", "fut_short",
                             "opt_call_long", "opt_call_short",
                             "opt_put_long", "opt_put_short")}
        assert bias_score(v) == 0

    def test_net_put_writer_bullish(self):
        """Net put-writer (short puts > long puts) should boost bias positively."""
        v = {"fut_long": 0, "fut_short": 0,
             "opt_call_long": 0, "opt_call_short": 0,
             "opt_put_long": 100, "opt_put_short": 500}  # net_put = -400 → −(−400) = +400
        assert bias_score(v) == 400

    def test_net_long_puts_bearish(self):
        """Net long puts should subtract from bias score."""
        v = {"fut_long": 0, "fut_short": 0,
             "opt_call_long": 0, "opt_call_short": 0,
             "opt_put_long": 500, "opt_put_short": 100}  # net_put = 400 → subtract
        assert bias_score(v) == -400

    def test_missing_keys_treated_as_zero(self):
        assert bias_score({}) == 0
        assert bias_score({"fut_long": 1000}) == 1000

    def test_none_values_treated_as_zero(self):
        v = {"fut_long": 1000, "fut_short": None,
             "opt_call_long": None, "opt_call_short": 0,
             "opt_put_long": 0, "opt_put_short": 0}
        assert bias_score(v) == 1000


# ---------------------------------------------------------------------------
# compute_category_biases
# ---------------------------------------------------------------------------

class TestComputeCategoryBiases:
    def test_empty_returns_empty(self):
        assert compute_category_biases({}) == {}
        assert compute_category_biases(None) == {}  # type: ignore

    def test_missing_category_treated_as_zero(self):
        """If FII is absent, institutional_bias should be 0, not a KeyError."""
        parsed = {"Client": _24JUL_PARSED["Client"]}
        biases = compute_category_biases(parsed)
        assert biases["institutional_bias"] == 0
        assert biases["retail_bias"] == +924_155
        assert biases["divergence"]  == +924_155  # Client - 0

    def test_symmetric_divergence(self):
        """divergence = retail_bias - institutional_bias always."""
        biases = compute_category_biases(_24JUL_PARSED)
        assert biases["divergence"] == biases["retail_bias"] - biases["institutional_bias"]


# ---------------------------------------------------------------------------
# divergence_trend
# ---------------------------------------------------------------------------

class TestDivergenceTrend:
    def _make_series(self, divergences: list[int]) -> list[dict]:
        return [{"date": f"2026-07-{i+1:02d}", "divergence": d,
                 "retail_bias": 0, "institutional_bias": 0,
                 "Client": 0, "DII": 0, "FII": 0, "Pro": 0}
                for i, d in enumerate(divergences)]

    def test_rising_trend(self):
        s = self._make_series([100_000, 300_000, 500_000, 700_000, 900_000])
        t = divergence_trend(s, window=5)
        assert t["trend"] == "rising"

    def test_falling_trend(self):
        s = self._make_series([900_000, 700_000, 500_000, 300_000, 100_000])
        t = divergence_trend(s, window=5)
        assert t["trend"] == "falling"

    def test_flat_trend(self):
        s = self._make_series([500_000, 500_000, 500_000, 500_000])
        t = divergence_trend(s, window=4)
        assert t["trend"] == "flat"

    def test_empty_series(self):
        t = divergence_trend([], window=5)
        assert t["trend"] == "unknown"

    def test_window_larger_than_series(self):
        s = self._make_series([100_000, 200_000])
        t = divergence_trend(s, window=20)
        assert t["n"] == 2


# ---------------------------------------------------------------------------
# interpret_divergence narrative
# ---------------------------------------------------------------------------

class TestInterpretDivergence:
    def _flat_trend(self) -> dict:
        return {"trend": "flat", "drift": 0.0, "n": 5}

    def test_large_positive_divergence_retail_long(self):
        text = interpret_divergence(
            divergence=+2_000_000,
            retail_bias=+900_000,
            institutional_bias=-1_100_000,
            trend_5d=self._flat_trend(),
        )
        assert "crowded long" in text.lower() or "retail" in text.lower()
        assert "fii" in text.lower() or "institutional" in text.lower()

    def test_large_negative_divergence_short_squeeze(self):
        text = interpret_divergence(
            divergence=-2_000_000,
            retail_bias=-900_000,
            institutional_bias=+1_100_000,
            trend_5d=self._flat_trend(),
        )
        assert "short" in text.lower()

    def test_no_divergence_range_bound(self):
        text = interpret_divergence(
            divergence=+50_000,  # below LARGE_DIVERGENCE_THRESHOLD
            retail_bias=+50_000,
            institutional_bias=0,
            trend_5d=self._flat_trend(),
        )
        assert "no strong divergence" in text.lower() or "range" in text.lower()

    def test_widening_trend_mentioned(self):
        widening = {"trend": "rising", "drift": 300_000.0, "n": 5}
        text = interpret_divergence(
            divergence=+1_000_000,
            retail_bias=+900_000,
            institutional_bias=-100_000,
            trend_5d=widening,
        )
        assert "widen" in text.lower() or "widening" in text.lower()
