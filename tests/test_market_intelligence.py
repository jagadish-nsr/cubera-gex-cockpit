"""tests/test_market_intelligence.py — Unit tests for analytics/market_intelligence.py.
"""

from analytics.market_intelligence import (
    analyze_news,
    analyze_sectors,
    compute_market_intelligence,
)


def test_analyze_sectors_empty_or_none():
    res = analyze_sectors(None)
    assert res.total_sectors == 0
    assert res.breadth_state == "NEUTRAL_MIXED"
    assert res.rotation_regime == "BALANCED_CHOP"

    res_empty = analyze_sectors({"rows": []})
    assert res_empty.total_sectors == 0
    assert res_empty.breadth_ratio_str == "0/0 Green"


def test_analyze_sectors_broad_expansion():
    rows = [
        {"label": "Bank", "pct": 1.4, "last": 54000.0, "group": "sector", "weight": 30.0},
        {"label": "IT", "pct": 0.8, "last": 28000.0, "group": "sector", "weight": 12.0},
        {"label": "Energy", "pct": 1.1, "last": 36000.0, "group": "sector", "weight": 12.0},
        {"label": "FMCG", "pct": 0.5, "last": 44000.0, "group": "sector", "weight": 8.0},
        {"label": "Auto", "pct": 0.9, "last": 25000.0, "group": "sector", "weight": 7.0},
        {"label": "Metal", "pct": 1.5, "last": 12000.0, "group": "sector", "weight": 4.0},
        {"label": "Pharma", "pct": 0.3, "last": 26000.0, "group": "sector", "weight": 4.0},
        {"label": "Healthcare", "pct": -0.2, "last": 15000.0, "group": "sector", "weight": 4.5},
        {"label": "Nifty 50", "pct": 0.95, "last": 22500.0, "group": "broad", "weight": None},
    ]
    data = {"rows": rows, "source": "nse", "weights_as_of": "2025-07"}
    res = analyze_sectors(data)

    assert res.total_sectors == 8
    assert res.advancers == 7
    assert res.decliners == 1
    assert res.breadth_pct > 80.0
    assert res.breadth_state == "BROAD_ADVANCE"
    assert res.weighted_contribution > 0.5
    assert len(res.top_leaders) == 3
    assert res.top_leaders[0].label in ("Metal", "Bank")
    assert len(res.broad_indices) == 1
    assert res.broad_indices[0].label == "Nifty 50"


def test_analyze_sectors_defensive_rotation():
    rows = [
        {"label": "Bank", "pct": -0.8, "last": 54000.0, "group": "sector", "weight": 30.0},
        {"label": "IT", "pct": -0.5, "last": 28000.0, "group": "sector", "weight": 12.0},
        {"label": "Auto", "pct": -0.4, "last": 25000.0, "group": "sector", "weight": 7.0},
        {"label": "FMCG", "pct": 1.6, "last": 44000.0, "group": "sector", "weight": 8.0},
        {"label": "Pharma", "pct": 1.2, "last": 26000.0, "group": "sector", "weight": 4.0},
        {"label": "Healthcare", "pct": 0.9, "last": 15000.0, "group": "sector", "weight": 4.5},
    ]
    data = {"rows": rows, "source": "nse", "weights_as_of": "2025-07"}
    res = analyze_sectors(data)

    assert res.rotation_regime == "DEFENSIVE_ROTATION"
    assert "FMCG" in res.rotation_description


def test_analyze_news_sentiment_and_shock():
    news_data = {
        "count": 4,
        "impact": 9,
        "top": "Middle East airstrike escalates regional war fears, oil surges",
        "headlines": [
            {
                "title": "Middle East airstrike escalates regional war fears, oil surges",
                "pub_time_ist": "08:30 IST",
                "date_ist": "06-Oct",
                "age_min": 10,
                "impact": 6,
                "category": "Geopolitics",
                "sentiment": "BEARISH",
                "is_fresh": True,
            },
            {
                "title": "Crude oil jumps 3% as tanker supply disrupted",
                "pub_time_ist": "08:15 IST",
                "date_ist": "06-Oct",
                "age_min": 25,
                "impact": 3,
                "category": "Energy & Crude",
                "sentiment": "BEARISH",
                "is_fresh": True,
            },
        ],
    }
    res = analyze_news(news_data)

    assert res.count == 4
    assert res.impact == 9
    assert res.is_news_dominated is True
    assert res.catalyst_risk_level == "HIGH_SHOCK"
    assert res.sentiment_label == "BEARISH"
    assert "Geopolitics" in res.top_themes


def test_compute_market_intelligence_full_synthesis():
    sec_data = {
        "rows": [
            {"label": "Bank", "pct": 1.2, "last": 54000.0, "group": "sector", "weight": 30.0},
            {"label": "IT", "pct": 0.9, "last": 28000.0, "group": "sector", "weight": 12.0},
            {"label": "Energy", "pct": 0.7, "last": 36000.0, "group": "sector", "weight": 12.0},
            {"label": "Auto", "pct": 0.6, "last": 25000.0, "group": "sector", "weight": 7.0},
            {"label": "FMCG", "pct": 0.4, "last": 44000.0, "group": "sector", "weight": 8.0},
        ],
        "source": "nse",
    }
    news_data = {
        "count": 3,
        "impact": 2,
        "top": "Indices snap losing streak as crude oil prices ease",
        "headlines": [
            {
                "title": "Indices snap losing streak as crude oil prices ease",
                "pub_time_ist": "08:45 IST",
                "date_ist": "06-Oct",
                "age_min": 12,
                "impact": 2,
                "category": "Energy & Crude",
                "sentiment": "BULLISH",
                "is_fresh": True,
            }
        ],
    }

    intel = compute_market_intelligence(sec_data, news_data, spot_chg_pct=+0.65)

    assert intel.alignment == "CONFIRMING_BULLISH"
    assert intel.sector.breadth_pct == 100.0
    assert "Broad Market Expansion" in intel.macro_verdict or "Risk-On" in intel.macro_verdict
    assert len(intel.actionable_points) >= 2
    assert "Bank" in intel.executive_narrative
