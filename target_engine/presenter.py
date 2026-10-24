"""Serialize Target Engine output for the cockpit: dial, edge label, ranked T1/SL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def compose_edge_label(scenario_set) -> tuple[str, bool]:
    """Honest range vs directional edge. Range-led is not a confirmed directional edge."""
    p_bull = float(scenario_set.get_prob("bull"))
    p_base = float(scenario_set.get_prob("base"))
    p_bear = float(scenario_set.get_prob("bear"))
    range_led = p_base >= max(p_bull, p_bear)
    no_edge = bool(scenario_set.no_edge) or range_led
    if no_edge:
        if range_led:
            return "RANGE LED — NO DIRECTIONAL EDGE", True
        return "NO EDGE — RANGE DOMINANT", True
    return "CONFIRMED EDGE", False


def compose_confluence_dial(scenario_set, feature_vector=None) -> Dict[str, Any]:
    """0–100 confluence strength from T2 conviction; verdict from dominant scenario.

    rank_score is tanh ~[-1, 1] and must not be used as the dial numerator.
    """
    feats = {}
    if feature_vector is not None and getattr(feature_vector, "features", None):
        feats = feature_vector.features

    conviction = float(getattr(scenario_set, "conviction", 0.0) or 0.0)
    score = int(round(max(0.0, min(1.0, conviction)) * 100.0))

    _, no_edge = compose_edge_label(scenario_set)
    dominant = str(getattr(scenario_set, "dominant_scenario", "base") or "base").lower()

    if no_edge or dominant == "base":
        verdict = "CONFLUENCE RANGE"
        color = "var(--spark)"
    elif dominant == "bull":
        verdict = "CONFLUENCE BULLISH"
        color = "var(--go)"
    else:
        verdict = "CONFLUENCE BEARISH"
        color = "var(--stop)"

    if score >= 65:
        conviction_label = "HIGH CONVICTION"
    elif score >= 40:
        conviction_label = "MEDIUM CONVICTION"
    else:
        conviction_label = "LOW CONVICTION"

    cw = float(feats.get("F1_call_wall_prox", 0.0) or 0.0)
    pw = float(feats.get("F2_put_wall_prox", 0.0) or 0.0)
    pcr = float(feats.get("F5_pcr_level", 0.0) or 0.0)
    atm_iv = float(feats.get("F13_atm_iv", 0.14) or 0.14)
    trend = float(feats.get("F16_intraday_trend", 0.0) or 0.0)
    skew = float(feats.get("F14_skew_change", 0.0) or 0.0)

    def _pct(x: float) -> int:
        return int(max(0, min(100, round(50.0 + 50.0 * max(-1.0, min(1.0, x))))))

    return {
        "score": score,
        "verdict": verdict,
        "color": color,
        "conviction_label": conviction_label,
        "components": {
            "GEX Alignment": _pct(pw - cw),
            "Sentinel CVD": _pct(trend),
            "OI Shift Flow": _pct(pcr),
            "IV Skew Context": int(max(0, min(100, round(100.0 - atm_iv * 250.0 + skew * 20.0)))),
        },
    }


def preferred_scenario_for_structure(
    structure_type: str,
    option_type: str,
    dominant: str,
) -> str:
    """Map a structure to the scenario whose underlying target it is betting on."""
    st = (structure_type or "").upper()
    ot = (option_type or "").upper()
    if any(k in st for k in ("IRON", "STRANGLE", "STRADDLE", "BUTTERFLY", "CONDOR")):
        return "base"
    if "PUT" in st or ot == "PE":
        return "base" if dominant == "base" else "bear"
    if "CALL" in st or ot == "CE":
        return "base" if dominant == "base" else "bull"
    return dominant or "base"


def _stop_scenario(pref: str, option_type: str) -> str:
    ot = (option_type or "CE").upper()
    if pref == "bull":
        return "bear"
    if pref == "bear":
        return "bull"
    return "bear" if ot == "CE" else "bull"


def _opt_target(scenario_targets: dict, name: str) -> Optional[Any]:
    if not scenario_targets:
        return None
    return scenario_targets.get(name)


def serialize_ranked_candidates(candidates, dominant: str = "base") -> List[Dict[str, Any]]:
    """T1/SL from the structure's matching Black-76 scenario, not a 1.35× LTP guess."""
    rows: List[Dict[str, Any]] = []
    dom = (dominant or "base").lower()
    for c in candidates or []:
        leg = c.legs[0] if getattr(c, "legs", None) else None
        stk = float(leg.strike) if leg else 0.0
        ot = (leg.option_type if leg else "CE") or "CE"
        st = getattr(c, "structure_type", "") or ""
        pref = preferred_scenario_for_structure(st, ot, dom)
        sc_tgt = _opt_target(getattr(c, "scenario_targets", {}) or {}, pref)
        sl_tgt = _opt_target(getattr(c, "scenario_targets", {}) or {}, _stop_scenario(pref, ot))

        if sc_tgt is not None:
            ent = float(getattr(sc_tgt, "entry_premium", 0.0) or 0.0)
            t1 = float(getattr(sc_tgt, "target_premium", 0.0) or 0.0)
            pt = float(getattr(sc_tgt, "p_touch_target", 0.0) or 0.0) if hasattr(sc_tgt, "p_touch_target") else 0.0
        else:
            ent = float(leg.entry_premium) if leg else 0.0
            t1 = ent
            pt = 0.0

        if sl_tgt is not None:
            sl = float(getattr(sl_tgt, "target_premium", 0.0) or 0.0)
        else:
            max_l = float(getattr(c, "max_loss_pts", 0.0) or 0.0)
            sl = max(0.05, ent - max_l) if ent > 0 else ent

        # Debit longs: stop cannot sit through the entry.
        if ent > 0 and sl >= ent:
            max_l = float(getattr(c, "max_loss_pts", 0.0) or 0.0)
            sl = max(0.05, ent - max_l) if max_l > 0 else max(0.05, ent * 0.70)

        rr_val = float(getattr(c, "risk_reward", 0.0) or 0.0)
        rank = float(getattr(c, "rank_score", 0.0) or 0.0)
        score_100 = int(round(50.0 + 50.0 * max(-1.0, min(1.0, rank))))

        rows.append({
            "strike": stk,
            "option_type": ot,
            "structure_type": st,
            "scenario": pref,
            "score": score_100,
            "entry_ltp": round(ent, 1),
            "target_1": round(t1, 1),
            "stop_loss": round(sl, 1),
            "net_ev": round(float(getattr(c, "expected_value", 0.0) or 0.0), 1),
            "risk_reward": f"1 : {rr_val:.1f}",
            "p_touch": round(float(pt), 2),
        })
    return rows
