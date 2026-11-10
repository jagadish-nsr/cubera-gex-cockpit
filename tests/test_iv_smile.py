"""Hand-checkable IV smile beyond 25Δ."""

from __future__ import annotations

import pandas as pd

from analytics.iv_skew import smile_by_delta


def test_smile_by_delta_10_25_atm_hand_check():
    # Spot 25400. Put 10Δ / 25Δ and call 25Δ / 10Δ with known IVs.
    rows = []
    # Puts: deeper OTM = lower delta magnitude approx via assigned delta
    for strike, delta, iv in [
        (24800, -0.10, 0.18),
        (25000, -0.25, 0.16),
        (25400, -0.50, 0.14),
        (25400, 0.50, 0.14),
        (25800, 0.25, 0.13),
        (26000, 0.10, 0.12),
    ]:
        rows.append({
            "expiry": "2026-11-11",
            "type": "P" if delta < 0 else "C",
            "strike": strike,
            "delta": delta,
            "iv": iv,
        })
    # Extra next expiry so skew_25delta term path stays quiet
    rows.append({
        "expiry": "2026-11-18", "type": "C", "strike": 25400, "delta": 0.50, "iv": 0.135,
    })
    rows.append({
        "expiry": "2026-11-18", "type": "P", "strike": 25400, "delta": -0.50, "iv": 0.135,
    })
    df = pd.DataFrame(rows)
    smile = smile_by_delta(df, spot=25400.0)
    assert smile["atm_iv_pct"] == 14.0
    put10 = next(p for p in smile["points"] if p["side"] == "PE" and p["delta"] == 0.10)
    call10 = next(p for p in smile["points"] if p["side"] == "CE" and p["delta"] == 0.10)
    assert put10["iv_pct"] == 18.0
    assert call10["iv_pct"] == 12.0
    # 10Δ skew = put − call = 6.0 pct points
    assert smile["skew_10d_pct"] == 6.0
    assert smile["skew_25d_pct"] == 3.0  # 16 − 13
