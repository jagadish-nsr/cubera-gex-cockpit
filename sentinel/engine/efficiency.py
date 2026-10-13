"""sentinel/engine/efficiency.py — OrderFlow Sentinel Signal Efficiency Analytics.

Calculates descriptive performance analytics for Sentinel microstructure signals on a
Daily and Weekly basis:
  - Hit Rate / Win Rate (% reaching profit target or positive excursion)
  - Stop-Out Rate (% breaching invalidation level)
  - Edge Ratio (Average MFE / Average MAE)
  - Forward Return Expectancy (+1m, +3m, +5m, +15m, +30m)
  - Performance Breakdown by Signal Type, Bias, and GEX Wall Alignment
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sentinel.storage.db import _get_read_conn, _DB_PATH


@dataclass
class PeriodEfficiency:
    period: str                     # 'YYYY-MM-DD' (daily) or 'YYYY-Www' (weekly)
    instrument_id: str
    total_signals: int
    target_hits: int
    stopped_outs: int
    hit_rate_pct: float
    stop_rate_pct: float
    avg_mfe_pts: float
    avg_mae_pts: float
    edge_ratio: float
    avg_ret_1m: float
    avg_ret_3m: float
    avg_ret_5m: float
    avg_ret_15m: float
    avg_ret_30m: float


@dataclass
class CategoryBreakdown:
    label: str                      # Signal Type or GEX Alignment
    total_signals: int
    target_hits: int
    hit_rate_pct: float
    avg_mfe_pts: float
    avg_mae_pts: float
    edge_ratio: float


@dataclass
class SignalEfficiencyReport:
    instrument_id: str
    period_type: str                # "daily" | "weekly"
    total_signals: int
    overall_hit_rate: float
    overall_stop_rate: float
    overall_avg_mfe: float
    overall_avg_mae: float
    overall_edge_ratio: float
    expectancy_curve: dict[str, float]  # {"+1m": x, "+3m": y, "+5m": z, "+15m": w, "+30m": v}
    timeline: list[PeriodEfficiency] = field(default_factory=list)
    by_type: list[CategoryBreakdown] = field(default_factory=list)
    by_alignment: list[CategoryBreakdown] = field(default_factory=list)
    by_category: list[CategoryBreakdown] = field(default_factory=list)


def compute_signal_efficiency(
    instrument_id: str | None = None,
    period_type: str = "daily",
    limit_periods: int = 30,
) -> SignalEfficiencyReport:
    """Compute daily or weekly signal efficiency metrics from sentinel_signals and signal_outcomes.

    Args:
        instrument_id: Optional symbol filter (e.g. 'SENSEX-FUT', 'NIFTY-FUT', or None for ALL).
        period_type: 'daily' (YYYY-MM-DD) or 'weekly' (YYYY-Www).
        limit_periods: Number of recent periods to return in the timeline.

    Returns:
        SignalEfficiencyReport dataclass with aggregated KPIs, timeline, and category breakdowns.
    """
    conn = _get_read_conn()
    try:
        # 1. Base query joining signals with outcomes
        where_clause = "WHERE o.event_id IS NOT NULL"
        params: list[Any] = []
        if instrument_id and instrument_id.upper() not in ("ALL", ""):
            where_clause += " AND s.instrument_id = ?"
            params.append(instrument_id)

        period_expr = (
            "substr(s.bar_start_ts, 1, 10)"
            if period_type == "daily"
            else "strftime('%Y-W%W', s.bar_start_ts)"
        )

        query = f"""
            SELECT
                {period_expr} AS period_label,
                s.instrument_id,
                COUNT(*) AS total_count,
                SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS target_hits,
                SUM(CASE WHEN o.stopped_out = 1 THEN 1 ELSE 0 END) AS stopped_outs,
                AVG(o.mfe_pts) AS avg_mfe,
                AVG(o.mae_pts) AS avg_mae,
                AVG(COALESCE(o.ret_1m_pts, 0)) AS avg_ret_1m,
                AVG(COALESCE(o.ret_3m_pts, 0)) AS avg_ret_3m,
                AVG(COALESCE(o.ret_5m_pts, 0)) AS avg_ret_5m,
                AVG(COALESCE(o.ret_15m_pts, 0)) AS avg_ret_15m,
                AVG(COALESCE(o.ret_30m_pts, 0)) AS avg_ret_30m
            FROM sentinel_signals s
            JOIN signal_outcomes o ON s.event_id = o.event_id
            {where_clause}
            GROUP BY period_label
            ORDER BY period_label DESC
            LIMIT ?
        """
        timeline_rows = conn.execute(query, params + [limit_periods]).fetchall()

        timeline: list[PeriodEfficiency] = []
        for r in timeline_rows:
            n = r["total_count"]
            hits = r["target_hits"] or 0
            stops = r["stopped_outs"] or 0
            mfe = round(float(r["avg_mfe"] or 0.0), 2)
            mae = round(float(r["avg_mae"] or 0.0), 2)
            edge = round(mfe / mae, 2) if mae > 0.001 else round(mfe, 2)
            timeline.append(
                PeriodEfficiency(
                    period=str(r["period_label"]),
                    instrument_id=instrument_id or "ALL",
                    total_signals=n,
                    target_hits=hits,
                    stopped_outs=stops,
                    hit_rate_pct=round((hits / n) * 100.0, 1) if n > 0 else 0.0,
                    stop_rate_pct=round((stops / n) * 100.0, 1) if n > 0 else 0.0,
                    avg_mfe_pts=mfe,
                    avg_mae_pts=mae,
                    edge_ratio=edge,
                    avg_ret_1m=round(float(r["avg_ret_1m"] or 0.0), 2),
                    avg_ret_3m=round(float(r["avg_ret_3m"] or 0.0), 2),
                    avg_ret_5m=round(float(r["avg_ret_5m"] or 0.0), 2),
                    avg_ret_15m=round(float(r["avg_ret_15m"] or 0.0), 2),
                    avg_ret_30m=round(float(r["avg_ret_30m"] or 0.0), 2),
                )
            )

        # 2. Overall Summary KPIs
        tot_query = f"""
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS target_hits,
                SUM(CASE WHEN o.stopped_out = 1 THEN 1 ELSE 0 END) AS stopped_outs,
                AVG(o.mfe_pts) AS avg_mfe,
                AVG(o.mae_pts) AS avg_mae,
                AVG(COALESCE(o.ret_1m_pts, 0)) AS avg_ret_1m,
                AVG(COALESCE(o.ret_3m_pts, 0)) AS avg_ret_3m,
                AVG(COALESCE(o.ret_5m_pts, 0)) AS avg_ret_5m,
                AVG(COALESCE(o.ret_15m_pts, 0)) AS avg_ret_15m,
                AVG(COALESCE(o.ret_30m_pts, 0)) AS avg_ret_30m
            FROM sentinel_signals s
            JOIN signal_outcomes o ON s.event_id = o.event_id
            {where_clause}
        """
        tot_r = conn.execute(tot_query, params).fetchone()
        total_n = tot_r["total_count"] if tot_r else 0
        tot_hits = (tot_r["target_hits"] if tot_r else 0) or 0
        tot_stops = (tot_r["stopped_outs"] if tot_r else 0) or 0
        overall_mfe = round(float(tot_r["avg_mfe"] or 0.0), 2) if tot_r else 0.0
        overall_mae = round(float(tot_r["avg_mae"] or 0.0), 2) if tot_r else 0.0
        overall_edge = round(overall_mfe / overall_mae, 2) if overall_mae > 0.001 else overall_mfe

        expectancy = {
            "+1m": round(float(tot_r["avg_ret_1m"] or 0.0), 2) if tot_r else 0.0,
            "+3m": round(float(tot_r["avg_ret_3m"] or 0.0), 2) if tot_r else 0.0,
            "+5m": round(float(tot_r["avg_ret_5m"] or 0.0), 2) if tot_r else 0.0,
            "+15m": round(float(tot_r["avg_ret_15m"] or 0.0), 2) if tot_r else 0.0,
            "+30m": round(float(tot_r["avg_ret_30m"] or 0.0), 2) if tot_r else 0.0,
        }

        # 3. Breakdown by Signal Type
        type_query = f"""
            SELECT
                s.type AS group_label,
                COUNT(*) AS count,
                SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS hits,
                AVG(o.mfe_pts) AS avg_mfe,
                AVG(o.mae_pts) AS avg_mae
            FROM sentinel_signals s
            JOIN signal_outcomes o ON s.event_id = o.event_id
            {where_clause}
            GROUP BY s.type
            ORDER BY count DESC
        """
        by_type = [
            CategoryBreakdown(
                label=r["group_label"],
                total_signals=r["count"],
                target_hits=r["hits"] or 0,
                hit_rate_pct=round(((r["hits"] or 0) / r["count"]) * 100.0, 1),
                avg_mfe_pts=round(float(r["avg_mfe"] or 0.0), 2),
                avg_mae_pts=round(float(r["avg_mae"] or 0.0), 2),
                edge_ratio=round(float(r["avg_mfe"] or 0.0) / float(r["avg_mae"] or 1.0), 2),
            )
            for r in conn.execute(type_query, params).fetchall()
        ]

        # 4. Breakdown by GEX Wall Alignment
        align_query = f"""
            SELECT
                s.gex_alignment AS group_label,
                COUNT(*) AS count,
                SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS hits,
                AVG(o.mfe_pts) AS avg_mfe,
                AVG(o.mae_pts) AS avg_mae
            FROM sentinel_signals s
            JOIN signal_outcomes o ON s.event_id = o.event_id
            {where_clause}
            GROUP BY s.gex_alignment
            ORDER BY count DESC
        """
        by_alignment = [
            CategoryBreakdown(
                label=r["group_label"],
                total_signals=r["count"],
                target_hits=r["hits"] or 0,
                hit_rate_pct=round(((r["hits"] or 0) / r["count"]) * 100.0, 1),
                avg_mfe_pts=round(float(r["avg_mfe"] or 0.0), 2),
                avg_mae_pts=round(float(r["avg_mae"] or 0.0), 2),
                edge_ratio=round(float(r["avg_mfe"] or 0.0) / float(r["avg_mae"] or 1.0), 2),
            )
            for r in conn.execute(align_query, params).fetchall()
        ]

        # 5. Breakdown by Category (BULLISH / BEARISH / CAUTION)
        cat_query = f"""
            SELECT
                s.category AS group_label,
                COUNT(*) AS count,
                SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS hits,
                AVG(o.mfe_pts) AS avg_mfe,
                AVG(o.mae_pts) AS avg_mae
            FROM sentinel_signals s
            JOIN signal_outcomes o ON s.event_id = o.event_id
            {where_clause}
            GROUP BY s.category
            ORDER BY count DESC
        """
        by_category = [
            CategoryBreakdown(
                label=r["group_label"],
                total_signals=r["count"],
                target_hits=r["hits"] or 0,
                hit_rate_pct=round(((r["hits"] or 0) / r["count"]) * 100.0, 1),
                avg_mfe_pts=round(float(r["avg_mfe"] or 0.0), 2),
                avg_mae_pts=round(float(r["avg_mae"] or 0.0), 2),
                edge_ratio=round(float(r["avg_mfe"] or 0.0) / float(r["avg_mae"] or 1.0), 2),
            )
            for r in conn.execute(cat_query, params).fetchall()
        ]

        return SignalEfficiencyReport(
            instrument_id=instrument_id or "ALL",
            period_type=period_type,
            total_signals=total_n,
            overall_hit_rate=round((tot_hits / total_n) * 100.0, 1) if total_n > 0 else 0.0,
            overall_stop_rate=round((tot_stops / total_n) * 100.0, 1) if total_n > 0 else 0.0,
            overall_avg_mfe=overall_mfe,
            overall_avg_mae=overall_mae,
            overall_edge_ratio=overall_edge,
            expectancy_curve=expectancy,
            timeline=timeline,
            by_type=by_type,
            by_alignment=by_alignment,
            by_category=by_category,
        )

    finally:
        conn.close()


def calculate_model_calibration_and_expectancy(
    eff_report: SignalEfficiencyReport,
    spot: float = 24000.0,
    atm_iv: float = 0.14,
    dte_days: float = 5.0,
    horizon_minutes: int = 30,
) -> dict[str, Any]:
    """Calculate theoretical calibration alpha and Black-76 options expectancy curve.

    Bridges OrderFlow Sentinel empirical results with Target Engine quantitative models:
      1. Compares empirical win/hit rates against theoretical reflection-principle P(touch).
         Calibration Alpha = Empirical Hit Rate % - Theoretical P(touch) %.
      2. Reprices forward returns into ATM options price/percentage returns and Greeks attribution.
    """
    import math
    from target_engine.levels import compute_touch_probability
    from target_engine.pricer import price, reprice
    from target_engine.time_utils import get_horizon_annual_duration

    eff_iv = max(atm_iv, 0.05) if atm_iv > 0 else 0.14
    h_years = get_horizon_annual_duration(horizon_minutes)
    sigma_h = max(spot * eff_iv * math.sqrt(h_years), 15.0)

    # 1. Overall model calibration
    target_dist = max(eff_report.overall_avg_mfe, 25.0)
    theo_p_touch = compute_touch_probability(target_dist, sigma_h) * 100.0
    calib_alpha = round(eff_report.overall_hit_rate - theo_p_touch, 1)

    # 2. Breakdowns calibration
    by_type_calib = []
    for item in eff_report.by_type:
        d = max(item.avg_mfe_pts, 15.0)
        p = compute_touch_probability(d, sigma_h) * 100.0
        by_type_calib.append({
            "label": item.label,
            "count": item.total_signals,
            "empirical_hit": item.hit_rate_pct,
            "theoretical_p": round(p, 1),
            "alpha": round(item.hit_rate_pct - p, 1),
        })

    by_align_calib = []
    for item in eff_report.by_alignment:
        d = max(item.avg_mfe_pts, 15.0)
        p = compute_touch_probability(d, sigma_h) * 100.0
        by_align_calib.append({
            "label": item.label,
            "count": item.total_signals,
            "empirical_hit": item.hit_rate_pct,
            "theoretical_p": round(p, 1),
            "alpha": round(item.hit_rate_pct - p, 1),
        })

    # 3. Black-76 Options Expectancy Curve
    step = 50.0 if "NIFTY" in eff_report.instrument_id else 100.0
    atm_strike = round(spot / step) * step
    t0 = max(dte_days, 0.1) / 365.0
    p0_ce = price(F=spot, K=atm_strike, T=t0, sigma=eff_iv, r=0.065, option_type="CE")
    p0_pe = price(F=spot, K=atm_strike, T=t0, sigma=eff_iv, r=0.065, option_type="PE")

    options_curve = []
    for h_label, idx_pts in eff_report.expectancy_curve.items():
        try:
            m_min = int(h_label.replace("+", "").replace("m", ""))
        except ValueError:
            m_min = 5

        t_h = max(1e-4, t0 - (m_min / (375.0 * 252.0)))
        f_h = spot + idx_pts

        ph_ce = reprice(F_new=f_h, K=atm_strike, T_new=t_h, sigma_new=eff_iv, r=0.065, option_type="CE")
        pnl_pts = ph_ce - p0_ce
        pnl_pct = (pnl_pts / p0_ce * 100.0) if p0_ce > 0 else 0.0
        delta_contrib = 0.50 * idx_pts
        decay_contrib = pnl_pts - delta_contrib

        options_curve.append({
            "horizon": h_label,
            "index_ret_pts": round(idx_pts, 1),
            "opt_entry": round(p0_ce, 1),
            "opt_target": round(ph_ce, 1),
            "opt_ret_pts": round(pnl_pts, 1),
            "opt_ret_pct": round(pnl_pct, 1),
            "delta_pnl": round(delta_contrib, 1),
            "theta_pnl": round(decay_contrib, 1),
        })

    return {
        "sigma_h": round(sigma_h, 1),
        "target_dist": round(target_dist, 1),
        "theoretical_p_touch": round(theo_p_touch, 1),
        "empirical_hit_rate": round(eff_report.overall_hit_rate, 1),
        "calibration_alpha": calib_alpha,
        "atm_strike": int(atm_strike),
        "atm_call_entry": round(p0_ce, 1),
        "atm_put_entry": round(p0_pe, 1),
        "by_type_calibration": by_type_calib,
        "by_alignment_calibration": by_align_calib,
        "options_expectancy_curve": options_curve,
    }

