"""analytics/market_intelligence.py — Sectorial Performance & News Intelligence Synthesis.

Synthesizes:
1. Real-time Sector Breadth (Advancers/Decliners, Weighted Index Contribution, Rotation Regimes)
2. News Sentiment & Catalyst Intensity (Impact scoring, Exogenous risk detection, Headline Categorization)
3. Plain-English Executive Macro Verdict for unified options trading context.

Separation guarantee:
- Pure analytical logic, testable offline without live networks or Streamlit imports.
- Gracefully handles missing/None data with fail-soft defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass
class SectorItem:
    label: str
    pct: float | None
    last: float | None
    group: str = "sector"         # "sector" or "broad"
    weight: float | None = None   # estimated % weight in Nifty 50


@dataclass
class SectorIntelligence:
    advancers: int
    decliners: int
    unchanged: int
    total_sectors: int
    breadth_pct: float                 # 0.0 to 100.0
    breadth_ratio_str: str             # e.g. "8/11 Green"
    breadth_state: str                 # "BROAD_ADVANCE", "LEANING_BULLISH", "NEUTRAL_MIXED", "LEANING_BEARISH", "BROAD_DECLINE"
    weighted_contribution: float       # estimated net contribution pts/percent to index
    top_leaders: list[SectorItem] = field(default_factory=list)
    top_laggards: list[SectorItem] = field(default_factory=list)
    rotation_regime: str = "BALANCED_CHOP"
    rotation_description: str = ""
    all_sectors: list[SectorItem] = field(default_factory=list)
    broad_indices: list[SectorItem] = field(default_factory=list)
    source: str = "nse"
    weights_as_of: str = "2025-07"


@dataclass
class NewsItem:
    title: str
    pub_time_ist: str = ""
    date_ist: str = ""
    age_min: int = 0
    impact: int = 0
    category: str = "Markets & Economy"
    sentiment: str = "NEUTRAL"
    is_fresh: bool = False


@dataclass
class NewsIntelligence:
    count: int = 0
    impact: int = 0
    top_headline: str = ""
    is_news_dominated: bool = False
    sentiment_label: str = "NEUTRAL"       # "BULLISH", "BEARISH", "NEUTRAL"
    sentiment_score: float = 0.0            # -1.0 (extreme bearish) to +1.0 (extreme bullish)
    sentiment_breakdown: dict[str, int] = field(default_factory=dict)
    top_themes: list[str] = field(default_factory=list)
    headlines: list[NewsItem] = field(default_factory=list)
    catalyst_risk_level: str = "LOW"       # "LOW", "MODERATE", "HIGH_SHOCK"


@dataclass
class MarketIntelligence:
    sector: SectorIntelligence
    news: NewsIntelligence
    macro_verdict: str = ""
    executive_narrative: str = ""
    alignment: str = "NEUTRAL"             # "CONFIRMING_BULLISH", "CONFIRMING_BEARISH", "DIVERGENT_ROTATION", "CHOP_UNCERTAIN"
    actionable_points: list[str] = field(default_factory=list)


# Sector grouping definitions for rotation analysis
CYCLICAL_SECTORS = {"Bank", "Pvt Bank", "Energy", "IT", "Oil & Gas", "Auto", "Metal"}
DEFENSIVE_SECTORS = {"FMCG", "Pharma", "Healthcare", "Cons Durables"}


def analyze_sectors(sectors_data: dict | None) -> SectorIntelligence:
    """Computes sector breadth, weighted index contribution, and rotation regime."""
    if not sectors_data or not isinstance(sectors_data, dict):
        return SectorIntelligence(
            advancers=0,
            decliners=0,
            unchanged=0,
            total_sectors=0,
            breadth_pct=50.0,
            breadth_ratio_str="No Data",
            breadth_state="NEUTRAL_MIXED",
            weighted_contribution=0.0,
            rotation_regime="BALANCED_CHOP",
            rotation_description="Sector quote feed unavailable.",
            source="none",
        )

    rows = sectors_data.get("rows") or []
    source = str(sectors_data.get("source") or "nse")
    weights_as_of = str(sectors_data.get("weights_as_of") or "2025-07")

    sector_items: list[SectorItem] = []
    broad_items: list[SectorItem] = []

    for r in rows:
        item = SectorItem(
            label=str(r.get("label", "")),
            pct=float(r["pct"]) if r.get("pct") is not None else None,
            last=float(r["last"]) if r.get("last") is not None else None,
            group=str(r.get("group", "sector")),
            weight=float(r["weight"]) if r.get("weight") is not None else None,
        )
        if item.group == "broad":
            broad_items.append(item)
        else:
            sector_items.append(item)

    # Validated sectors with valid pct changes
    valid_sectors = [s for s in sector_items if s.pct is not None]
    total_valid = len(valid_sectors)

    if total_valid == 0:
        return SectorIntelligence(
            advancers=0,
            decliners=0,
            unchanged=0,
            total_sectors=len(sector_items),
            breadth_pct=50.0,
            breadth_ratio_str="0/0 Green",
            breadth_state="NEUTRAL_MIXED",
            weighted_contribution=0.0,
            top_leaders=[],
            top_laggards=[],
            rotation_regime="BALANCED_CHOP",
            rotation_description="Sector percent changes currently unestablished (pre-market/after hours).",
            all_sectors=sector_items,
            broad_indices=broad_items,
            source=source,
            weights_as_of=weights_as_of,
        )

    advancers = sum(1 for s in valid_sectors if (s.pct or 0.0) > 0.05)
    decliners = sum(1 for s in valid_sectors if (s.pct or 0.0) < -0.05)
    unchanged = total_valid - advancers - decliners
    breadth_pct = round((advancers / total_valid) * 100.0, 1)

    if breadth_pct >= 70.0:
        breadth_state = "BROAD_ADVANCE"
    elif breadth_pct >= 55.0:
        breadth_state = "LEANING_BULLISH"
    elif breadth_pct <= 30.0:
        breadth_state = "BROAD_DECLINE"
    elif breadth_pct <= 45.0:
        breadth_state = "LEANING_BEARISH"
    else:
        breadth_state = "NEUTRAL_MIXED"

    # Weighted contribution estimate:
    # Use distinct core non-overlapping weights to avoid double counting Pvt Bank ⊂ Bank, Oil & Gas ⊂ Energy
    # Independent core basket: Bank(30), Energy(12), IT(12), FMCG(8), Auto(7), Healthcare(4.5), Metal(4.0), Cons Durables(2.0)
    core_weights = {
        "Bank": 30.0, "Energy": 12.0, "IT": 12.0, "FMCG": 8.0,
        "Auto": 7.0, "Healthcare": 4.5, "Metal": 4.0, "Cons Durables": 2.0,
    }
    net_contrib = 0.0
    sum_core_weights = sum(core_weights.values())
    for s in valid_sectors:
        if s.label in core_weights and s.pct is not None:
            net_contrib += (s.pct * core_weights[s.label]) / sum_core_weights
    weighted_contribution = round(net_contrib, 2)

    # Sort leaders and laggards
    sorted_sectors = sorted(valid_sectors, key=lambda s: s.pct or 0.0, reverse=True)
    top_leaders = sorted_sectors[:3]
    top_laggards = sorted(valid_sectors, key=lambda s: s.pct or 0.0)[:3]

    # Rotation regime analysis
    cyclical_moves = [s.pct for s in valid_sectors if s.label in CYCLICAL_SECTORS and s.pct is not None]
    defensive_moves = [s.pct for s in valid_sectors if s.label in DEFENSIVE_SECTORS and s.pct is not None]

    avg_cyc = sum(cyclical_moves) / len(cyclical_moves) if cyclical_moves else 0.0
    avg_def = sum(defensive_moves) / len(defensive_moves) if defensive_moves else 0.0

    if avg_def > 0.4 and avg_cyc < -0.1:
        rotation_regime = "DEFENSIVE_ROTATION"
        rotation_description = (
            f"Defensive outperformance: FMCG & Pharma up (+{avg_def:.2f}%) while Cyclicals lag ({avg_cyc:.2f}%). "
            "Institutional risk-off safe haven positioning."
        )
    elif avg_cyc > 0.4 and avg_cyc > avg_def:
        rotation_regime = "CYCLICAL_LEADERSHIP"
        rotation_description = (
            f"Risk-on cyclical leadership: High-beta BFSI, IT & Auto leading (+{avg_cyc:.2f}%). "
            "Broad economic expansion participation."
        )
    elif breadth_pct >= 70.0:
        rotation_regime = "BROAD_EXPANSION"
        rotation_description = f"Broad market expansion: {advancers}/{total_valid} sectors advancing across both cyclicals and defensives."
    elif breadth_pct <= 30.0:
        rotation_regime = "BROAD_LIQUIDATION"
        rotation_description = f"Broad market drag: {decliners}/{total_valid} sectors declining under widespread institutional distribution."
    else:
        rotation_regime = "BALANCED_CHOP"
        rotation_description = (
            f"Selective rotation / Sector dispersion: {advancers} green, {decliners} red. "
            "Index lacks unified directional consensus."
        )

    return SectorIntelligence(
        advancers=advancers,
        decliners=decliners,
        unchanged=unchanged,
        total_sectors=total_valid,
        breadth_pct=breadth_pct,
        breadth_ratio_str=f"{advancers}/{total_valid} Green",
        breadth_state=breadth_state,
        weighted_contribution=weighted_contribution,
        top_leaders=top_leaders,
        top_laggards=top_laggards,
        rotation_regime=rotation_regime,
        rotation_description=rotation_description,
        all_sectors=sector_items,
        broad_indices=broad_items,
        source=source,
        weights_as_of=weights_as_of,
    )


def analyze_news(news_data: dict | None) -> NewsIntelligence:
    """Analyzes news intensity, sentiment bias, top catalyst themes, and exogenous shock risks."""
    if not news_data or not isinstance(news_data, dict):
        return NewsIntelligence(
            count=0,
            impact=0,
            top_headline="",
            is_news_dominated=False,
            sentiment_label="NEUTRAL",
            sentiment_score=0.0,
            sentiment_breakdown={"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0},
            top_themes=[],
            headlines=[],
            catalyst_risk_level="LOW",
        )

    count = int(news_data.get("count", 0))
    impact = int(news_data.get("impact", 0))
    top_headline = str(news_data.get("top", "")).strip()

    raw_hls = news_data.get("headlines") or []
    news_items: list[NewsItem] = []
    sent_counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    theme_counts: dict[str, int] = {}

    for h in raw_hls:
        if isinstance(h, dict):
            item = NewsItem(
                title=str(h.get("title", "")),
                pub_time_ist=str(h.get("pub_time_ist", "")),
                date_ist=str(h.get("date_ist", "")),
                age_min=int(h.get("age_min", 0)),
                impact=int(h.get("impact", 0)),
                category=str(h.get("category", "Markets & Economy")),
                sentiment=str(h.get("sentiment", "NEUTRAL")),
                is_fresh=bool(h.get("is_fresh", False)),
            )
            news_items.append(item)
            sent = item.sentiment if item.sentiment in sent_counts else "NEUTRAL"
            sent_counts[sent] += 1
            theme_counts[item.category] = theme_counts.get(item.category, 0) + 1

    total_scored = sent_counts["BULLISH"] + sent_counts["BEARISH"] + sent_counts["NEUTRAL"]
    if total_scored > 0:
        net_diff = sent_counts["BULLISH"] - sent_counts["BEARISH"]
        sentiment_score = round(net_diff / total_scored, 2)
        if sentiment_score >= 0.20:
            sentiment_label = "BULLISH"
        elif sentiment_score <= -0.20:
            sentiment_label = "BEARISH"
        else:
            sentiment_label = "NEUTRAL"
    else:
        sentiment_score = 0.0
        sentiment_label = "NEUTRAL"

    # Sorted top themes
    top_themes = [k for k, _ in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:3]]

    # News-dominated day threshold (Phase B Confound Control)
    # Impact >= 6 or fresh geopolitical/macro shock headline
    shock_keywords = ("war", "missile", "airstrike", "nuclear", "circuit", "crash", "ceasefire", "rate cut", "rate hike")
    is_shock_top = any(k in top_headline.lower() for k in shock_keywords) if top_headline else False
    is_news_dominated = (impact >= 6) or (count >= 5 and impact >= 4) or is_shock_top

    if impact >= 8 or is_shock_top:
        catalyst_risk_level = "HIGH_SHOCK"
    elif impact >= 4:
        catalyst_risk_level = "MODERATE"
    else:
        catalyst_risk_level = "LOW"

    return NewsIntelligence(
        count=count,
        impact=impact,
        top_headline=top_headline,
        is_news_dominated=is_news_dominated,
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        sentiment_breakdown=sent_counts,
        top_themes=top_themes,
        headlines=news_items,
        catalyst_risk_level=catalyst_risk_level,
    )


def compute_market_intelligence(
    sectors_data: dict | None,
    news_data: dict | None,
    spot_chg_pct: float | None = None,
) -> MarketIntelligence:
    """Synthesizes sectorial breadth and news catalysts into a cohesive macro intelligence verdict."""
    sec_intel = analyze_sectors(sectors_data)
    news_intel = analyze_news(news_data)

    # 1. Macro alignment between sector breadth and index price move
    if spot_chg_pct is not None:
        if spot_chg_pct > 0.15 and sec_intel.breadth_pct >= 60.0:
            alignment = "CONFIRMING_BULLISH"
        elif spot_chg_pct < -0.15 and sec_intel.breadth_pct <= 40.0:
            alignment = "CONFIRMING_BEARISH"
        elif spot_chg_pct > 0.15 and sec_intel.breadth_pct < 45.0:
            alignment = "DIVERGENT_ROTATION"
        elif spot_chg_pct < -0.15 and sec_intel.breadth_pct > 55.0:
            alignment = "DIVERGENT_ROTATION"
        else:
            alignment = "CHOP_UNCERTAIN"
    else:
        if sec_intel.breadth_pct >= 65.0:
            alignment = "CONFIRMING_BULLISH"
        elif sec_intel.breadth_pct <= 35.0:
            alignment = "CONFIRMING_BEARISH"
        else:
            alignment = "CHOP_UNCERTAIN"

    # 2. Executive Headline Verdict
    if news_intel.is_news_dominated and news_intel.catalyst_risk_level == "HIGH_SHOCK":
        macro_verdict = f"⚡ Exogenous Catalyst Active: High-Impact News Overriding Dealer Flows ({news_intel.top_headline[:55]}...)"
    elif sec_intel.rotation_regime == "DEFENSIVE_ROTATION":
        macro_verdict = f"🛡️ Defensive Rotation: FMCG/Pharma Leading while Cyclicals Face Selling Pressure"
    elif sec_intel.rotation_regime == "CYCLICAL_LEADERSHIP":
        macro_verdict = f"🚀 Risk-On Cyclical Surge: High-Beta Banking & Growth Sectors Driving Tape ({sec_intel.breadth_ratio_str})"
    elif sec_intel.rotation_regime == "BROAD_EXPANSION":
        macro_verdict = f"🟢 Broad Market Expansion: {sec_intel.breadth_ratio_str} Supported by {news_intel.sentiment_label.title()} News Flow"
    elif sec_intel.rotation_regime == "BROAD_LIQUIDATION":
        macro_verdict = f"🔴 Broad Sector Liquidation: {sec_intel.decliners}/{sec_intel.total_sectors} Sectors Slumping"
    else:
        macro_verdict = f"⚖️ Balanced Sector Dispersion: {sec_intel.breadth_ratio_str} Amid {news_intel.sentiment_label.title()} Catalysts"

    # 3. Plain-English narrative synthesis
    top_lead_str = ", ".join(f"{s.label} ({s.pct:+.2f}%)" for s in sec_intel.top_leaders[:2]) if sec_intel.top_leaders else "None"
    top_lag_str = ", ".join(f"{s.label} ({s.pct:+.2f}%)" for s in sec_intel.top_laggards[:2]) if sec_intel.top_laggards else "None"

    lines = []
    lines.append(f"Market Breadth: {sec_intel.breadth_ratio_str} ({sec_intel.breadth_pct:.0f}% positive). Net weighted contribution: {sec_intel.weighted_contribution:+.2f}%.")
    lines.append(f"Sector Rotation: {sec_intel.rotation_description}")
    if sec_intel.top_leaders:
        lines.append(f"Key Leaders: {top_lead_str} | Key Laggards: {top_lag_str}.")

    if news_intel.top_headline:
        fresh_tag = "Fresh intraday burst" if news_intel.count > 0 else "Recent backdrop"
        lines.append(f"News Catalyst ({fresh_tag}, Impact: {news_intel.impact}): \"{news_intel.top_headline}\" with {news_intel.sentiment_label.lower()} sentiment undertone.")
    else:
        lines.append("News Catalyst: Quiet tape with no high-impact exogenous headlines detected.")

    executive_narrative = " ".join(lines)

    # 4. Actionable trader points
    points = []
    if news_intel.is_news_dominated:
        points.append("⚠️ Exogenous News Dominated: Dealer GEX walls may experience pin slippage or break under headline pressure.")
    elif sec_intel.breadth_pct >= 70.0 and alignment == "CONFIRMING_BULLISH":
        points.append("🟢 Strong Breadth Confirmation: High probability of call wall tests and supportive gamma flips holding pullbacks.")
    elif sec_intel.breadth_pct <= 30.0 and alignment == "CONFIRMING_BEARISH":
        points.append("🔴 Broad Distribution Confirmation: Elevated put buying likely to accelerate negative gamma cascades below put walls.")
    elif alignment == "DIVERGENT_ROTATION":
        points.append("⚠️ Sector Divergence: Index move is concentrated in isolated names; watch for shallow momentum fades at structural boundaries.")
    else:
        points.append("⚖️ Mixed Dispersion: Range-bound mean-reversion tactics favored over breakout extensions.")

    if sec_intel.top_leaders:
        points.append(f"📊 Primary sector driver today is {sec_intel.top_leaders[0].label} ({sec_intel.top_leaders[0].pct:+.2f}%).")

    if news_intel.top_themes:
        points.append(f"🗞️ Dominant news themes: {', '.join(news_intel.top_themes)}.")

    return MarketIntelligence(
        sector=sec_intel,
        news=news_intel,
        macro_verdict=macro_verdict,
        executive_narrative=executive_narrative,
        alignment=alignment,
        actionable_points=points,
    )
