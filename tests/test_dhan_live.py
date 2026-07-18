"""Dhan live-adapter hand-checks: the Dhan JSON -> NSE-shaped payload mapping must feed the
existing NSE parsers identically, must fail loud on a bad shape, and the token store/status must
work without touching the real secret file."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from data import dhan_live
from data.dhan import _jwt_exp, token_status, write_token
from data.nse import (parse_nse_option_chain, per_strike_nearest_changes, per_strike_oi)

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "dhan_optionchain_sample.json"


def _sample_chain():
    """Documented Dhan /v2/optionchain shape: data.last_price + data.oc{strike:{ce,pe}}."""
    def side(ltp, oi, poi, iv, pclose, vol):
        return {"last_price": ltp, "oi": oi, "previous_oi": poi,
                "implied_volatility": iv, "previous_close_price": pclose, "volume": vol}
    return {"data": {"last_price": 24360.0, "oc": {
        "24300.000000": {"ce": side(120.0, 1000, 800, 12.5, 100.0, 50),
                         "pe": side(60.0, 2000, 2200, 13.0, 70.0, 40)},
        "24400.000000": {"ce": side(70.0, 1500, 1500, 12.0, 80.0, 30),
                         "pe": side(110.0, 900, 700, 13.5, 90.0, 20)},
    }}}


def test_to_nse_rows_maps_fields_and_spot():
    rows, spot = dhan_live._to_nse_rows(_sample_chain(), "2026-07-23")
    assert spot == 24360.0
    ce = next(r["CE"] for r in rows if r["strikePrice"] == 24300.0 and "CE" in r)
    assert ce["openInterest"] == 1000
    assert ce["changeinOpenInterest"] == 200          # 1000 - 800 (vs prev close)
    assert ce["impliedVolatility"] == 12.5            # PERCENT, matching NSE's convention
    assert ce["lastPrice"] == 120.0
    assert ce["change"] == 20.0                       # 120 - 100 (LTP - prev close)
    assert ce["expiryDate"] == "23-07-2026"           # ISO -> DD-MM-YYYY (parse_nse_date-friendly)


def test_payload_feeds_existing_nse_parsers_identically():
    rows, spot = dhan_live._to_nse_rows(_sample_chain(), "2026-07-23")
    payload = {"records": {"data": rows, "underlyingValue": spot}}
    # 1) the normalizer the engine consumes
    norm, sp = parse_nse_option_chain(payload)
    assert sp == 24360.0
    ce = next(r for r in norm if r["strike"] == 24300.0 and r["type"] == "C")
    assert ce["iv"] == pytest.approx(0.125)           # percent -> decimal in the parser
    assert ce["oi"] == 1000
    # 2) per-strike OI / dOI
    ps = {r["strike"]: r for r in per_strike_oi(payload)}
    assert ps[24300.0]["call_oi"] == 1000 and ps[24300.0]["call_doi"] == 200
    assert ps[24300.0]["put_doi"] == -200             # 2000 - 2200
    # 3) nearest-expiry premium change (the exact build-up tag input)
    nc = per_strike_nearest_changes(payload)
    assert nc[24300.0]["call_dprem"] == 20.0
    assert nc[24300.0]["put_dprem"] == -10.0          # 60 - 70


def test_to_nse_rows_raises_on_bad_shape():
    with pytest.raises(ValueError):
        dhan_live._to_nse_rows({"data": {"foo": 1}}, "2026-07-23")   # no oc / no spot


def test_fetch_chain_falls_through_to_source_selection(monkeypatch):
    """Dhan-success path returns source 'dhan' without touching NSE."""
    import app.data_access as da

    class _Idx:
        symbol = "NIFTY"
        has_weekly = True
    monkeypatch.setattr(dhan_live, "fetch_chain",
                        lambda sym, **k: {"records": {"data": [], "underlyingValue": 1.0}})
    monkeypatch.setattr(dhan_live, "fetch_index_ohlc", lambda sym, **k: {"high": 2, "low": 1})
    monkeypatch.setattr("data.dhan.token_status", lambda: {"present": True})
    payload, source, ohlc, note = da._fetch_live_chain(_Idx(), "dhan")
    assert source == "dhan" and note is None and ohlc == {"high": 2, "low": 1}


def test_jwt_exp_and_token_status(monkeypatch):
    exp = int(datetime.now(timezone.utc).timestamp()) + 3600
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    tok = f"aGRy.{payload}.sig"
    assert abs((_jwt_exp(tok) - datetime.now(timezone.utc)).total_seconds() - 3600) < 5
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", tok)
    stt = token_status()
    assert stt["present"] and stt["expired"] is False and 0.9 < stt["hours_left"] < 1.1


def test_write_token_writes_secret(monkeypatch, tmp_path):
    f = tmp_path / ".dhan_token"
    monkeypatch.setattr("data.dhan.TOKEN_FILE", f)
    write_token("  abc.def.ghi  ")
    assert f.read_text(encoding="utf-8") == "abc.def.ghi"
    with pytest.raises(ValueError):
        write_token("   ")


@pytest.mark.skipif(not FIXTURE.exists(), reason="run scripts/dhan_optionchain_smoke.py first")
def test_real_fixture_parses():
    """Locks the field map against a REAL captured response once the smoke test has saved one."""
    j = json.loads(FIXTURE.read_text(encoding="utf-8"))
    exp = "2026-01-01"  # expiry string only affects the label here
    rows, spot = dhan_live._to_nse_rows(j, exp)
    assert spot and spot > 0 and rows
    norm, sp = parse_nse_option_chain({"records": {"data": rows, "underlyingValue": spot}})
    assert len(norm) > 0
