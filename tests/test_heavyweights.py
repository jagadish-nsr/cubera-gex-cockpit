"""Hand-checkable tests for Nifty-50 heavyweight board (no live Dhan required)."""

from __future__ import annotations

from data.heavyweights import (
    HEAVYWEIGHTS,
    filter_heavyweight_news,
    heavyweight_symbols,
    valid_heavyweight_rows,
)


def test_curated_list_has_unique_symbols_and_ids():
    syms = [s for s, _l, _i, _w in HEAVYWEIGHTS]
    ids = [i for _s, _l, i, _w in HEAVYWEIGHTS]
    assert len(syms) == len(set(syms)) >= 10
    assert len(ids) == len(set(ids))
    assert "RELIANCE" in syms and "HDFCBANK" in syms
    # Never use substring lot/expiry logic — exact symbols only.
    assert "NIFTY" not in syms


def test_valid_rows_rejects_poison_stubs():
    assert valid_heavyweight_rows([{"symbol": "A", "last": 1}]) is False
    good = [
        {"symbol": sym, "label": lab, "pct": 0.5, "last": 1000.0 + i, "weight": wt}
        for i, (sym, lab, _sid, wt) in enumerate(HEAVYWEIGHTS[:8])
    ]
    assert valid_heavyweight_rows(good) is True


def test_filter_heavyweight_news_keeps_tagged_only():
    headlines = [
        {
            "title": "Reliance Q2 beats estimates",
            "related_stocks": ["RELIANCE"],
            "category": "Corporate & Earnings",
            "sentiment": "BULLISH",
        },
        {
            "title": "Fed holds rates",
            "related_stocks": [],
            "category": "Macro & Policy",
            "sentiment": "NEUTRAL",
        },
        {
            "title": "Infosys deal win",
            "related_stocks": ["INFY", "TCS"],
            "category": "Corporate & Earnings",
            "sentiment": "BULLISH",
        },
    ]
    got = filter_heavyweight_news(headlines)
    assert len(got) == 2
    assert got[0]["matched_heavyweights"] == ["RELIANCE"]
    assert "INFY" in got[1]["matched_heavyweights"]
    assert set(heavyweight_symbols()) >= {"RELIANCE", "INFY"}
