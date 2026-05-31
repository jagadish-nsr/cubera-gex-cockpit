"""Parity recovers a KNOWN (r, q, F) from synthetic prices built to satisfy
put-call parity exactly: C - P = e^(-rT) * (F - K)."""

import math

import pandas as pd
import pytest

from data.parity import imply_forward_and_rate, attach_parity_rates

SPOT = 100.0
T = 0.5
R = 0.04
Q = 0.015


def _synthetic_expiry():
    DF = math.exp(-R * T)
    F = SPOT * math.exp((R - Q) * T)
    strikes = [90, 95, 100, 105, 110]
    rows = []
    for k in strikes:
        cmp_ = DF * (F - k)          # C - P required by parity
        put = 5.0                    # arbitrary baseline put price
        call = put + cmp_            # call consistent with parity
        rows.append((k, "C", call, T))
        rows.append((k, "P", put, T))
    return pd.DataFrame(rows, columns=["strike", "type", "mid", "T"]), F


def test_imply_recovers_known_rates():
    df, F = _synthetic_expiry()
    fit = imply_forward_and_rate(df, SPOT, expiry="2026-06-19")
    assert not fit.flagged, fit.note
    assert fit.r == pytest.approx(R, abs=1e-6)
    assert fit.q == pytest.approx(Q, abs=1e-6)
    assert fit.forward == pytest.approx(F, abs=1e-6)


def test_attach_marks_unfittable_expiry():
    # One good expiry + one with a single strike (cannot fit) -> NaN r/q + fit flagged.
    good, _ = _synthetic_expiry()
    good["expiry"] = "2026-06-19"
    bad = pd.DataFrame(
        [(100, "C", 3.0, 0.25, "2026-07-17")],
        columns=["strike", "type", "mid", "T", "expiry"],
    )
    contracts = pd.concat([good, bad], ignore_index=True)
    out, fits = attach_parity_rates(contracts, SPOT)
    assert fits["2026-07-17"].flagged
    assert out[out["expiry"] == "2026-07-17"]["r"].isna().all()
    assert out[out["expiry"] == "2026-06-19"]["r"].notna().all()
