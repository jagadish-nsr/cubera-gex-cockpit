"""commentary/narrator/templates.py — Deterministic Template Library for Zero-Latency Narration."""

from __future__ import annotations

from typing import Any
from commentary.models import Event, SuggestionBite


def render_event_template(event: Event, context: dict[str, Any] | None = None) -> tuple[str, str]:
    """Generate deterministic headline and body for any event without LLM dependencies.
    
    Returns:
        (headline, body)
    """
    evt_type = event.type
    f = event.facts

    if evt_type == "GAP_UP":
        gap_pts = f.get("gap_pts", 0.0)
        gap_pct = f.get("gap_pct", 0.0)
        open_p = f.get("open", 0.0)
        cw = f.get("call_wall", 0.0)
        headline = f"Gap-Up Open: +{gap_pts:,.1f} pts (+{gap_pct:.2f}%)"
        body = (
            f"Nifty opened higher at ₹{open_p:,.1f}, up {gap_pts:+.1f} pts ({gap_pct:+.2f}%) "
            f"versus prior close. Current spot is testing towards the ₹{cw:,.0f} Call Wall."
        )

    elif evt_type == "GAP_DOWN":
        gap_pts = f.get("gap_pts", 0.0)
        gap_pct = f.get("gap_pct", 0.0)
        open_p = f.get("open", 0.0)
        pw = f.get("put_wall", 0.0)
        headline = f"Gap-Down Open: {gap_pts:,.1f} pts ({gap_pct:.2f}%)"
        body = (
            f"Nifty opened lower at ₹{open_p:,.1f}, down {gap_pts:,.1f} pts ({gap_pct:.2f}%) "
            f"versus prior close. Downside pressure tests towards the ₹{pw:,.0f} Put Wall."
        )

    elif evt_type == "GAP_FILLED":
        retrace = f.get("retrace_pct", 100.0)
        spot = f.get("current_spot", 0.0)
        headline = "Opening Gap Fully Retraced"
        body = f"Morning opening gap has been {retrace:.0f}% filled as spot returns to ₹{spot:,.1f}."

    elif evt_type == "WALL_TEST":
        wtype = f.get("wall_type", "WALL").replace("_", " ").title()
        strike = f.get("strike", 0.0)
        spot = f.get("spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        headline = f"{wtype} Tested at ₹{strike:,.0f}"
        body = f"Spot ₹{spot:,.1f} is testing the key {wtype} at ₹{strike:,.0f} (within {dist:.1f} pts). Options defense active."

    elif evt_type == "WALL_BREAK":
        wtype = f.get("wall_type", "WALL").replace("_", " ").title()
        strike = f.get("strike", 0.0)
        spot = f.get("spot", 0.0)
        headline = f"{wtype} Broken at ₹{strike:,.0f}"
        body = f"Spot ₹{spot:,.1f} has closed beyond the {wtype} at ₹{strike:,.0f}, forcing dealer delta adjustments."

    elif evt_type == "WALL_MIGRATION":
        wtype = f.get("wall_type", "WALL").replace("_", " ").title()
        old_k = f.get("old_strike", 0.0)
        new_k = f.get("new_strike", 0.0)
        headline = f"{wtype} Migrated from ₹{old_k:,.0f} to ₹{new_k:,.0f}"
        body = f"Dealer open interest repositioned as the {wtype} shifted from ₹{old_k:,.0f} to ₹{new_k:,.0f}."

    elif evt_type == "FLIP_LEVEL_CROSS":
        spot = f.get("spot", 0.0)
        flip = f.get("flip_level", 0.0)
        regime = f.get("regime", "")
        headline = f"Gamma Flip Crossed at ₹{flip:,.0f}"
        body = f"Spot ₹{spot:,.1f} crossed the Gamma Flip level (₹{flip:,.0f}), shifting market dynamics into {regime}."

    elif evt_type == "REGIME_CHANGE":
        new_reg = f.get("new_regime", "")
        spot = f.get("spot", 0.0)
        headline = f"Regime Shift: {new_reg}"
        body = f"Dealer positioning transitioned to {new_reg} at spot ₹{spot:,.1f}. Volatility expectations adjusted."

    elif evt_type == "ORB_ESTABLISHED":
        h = f.get("orb_high", 0.0)
        l = f.get("orb_low", 0.0)
        rng = f.get("orb_range_pts", 0.0)
        headline = f"Opening 15m Range Set: {rng:,.1f} pts"
        body = f"The initial 15-minute range is established between ₹{l:,.1f} and ₹{h:,.1f} ({rng:.1f} pts range)."

    elif evt_type == "ORB_BREAK_UP":
        h = f.get("orb_high", 0.0)
        spot = f.get("spot", 0.0)
        ext = f.get("extension_pts", 0.0)
        headline = f"Opening Range Breakout (Upside): +{ext:.1f} pts"
        body = f"Spot ₹{spot:,.1f} pushed above the opening range high of ₹{h:,.1f} by {ext:.1f} pts."

    elif evt_type == "ORB_BREAK_DOWN":
        l = f.get("orb_low", 0.0)
        spot = f.get("spot", 0.0)
        ext = f.get("extension_pts", 0.0)
        headline = f"Opening Range Breakdown: -{ext:.1f} pts"
        body = f"Spot ₹{spot:,.1f} broke below the opening range low of ₹{l:,.1f} by {ext:.1f} pts."

    elif evt_type in ("VWAP_RECLAIM", "VWAP_LOSS"):
        direction = f.get("cross_direction", "")
        vwap = f.get("vwap", 0.0)
        spot = f.get("spot", 0.0)
        action = "reclaimed" if evt_type == "VWAP_RECLAIM" else "lost"
        headline = f"Session VWAP {action.title()} at ₹{vwap:,.1f}"
        body = f"Spot ₹{spot:,.1f} {action} Session VWAP (₹{vwap:,.1f}), trading {direction.lower()} intraday volume average."

    elif evt_type == "IMPULSIVE_MOVE":
        direction = f.get("direction", "")
        pts = f.get("move_pts", 0.0)
        vol_r = f.get("volume_ratio", 1.0)
        cls = f.get("classification", "")
        headline = f"Impulsive {direction.title()} Move ({pts:+,.1f} pts)"
        body = f"Displacement of {pts:+,.1f} pts on {vol_r:.1f}x volume burst over 5 minutes. Structure classifies as {cls}."

    elif evt_type == "IV_SPIKE":
        curr_iv = f.get("current_atm_iv", 0.0)
        shift = f.get("shift_pct", 0.0)
        headline = f"ATM IV Spike: +{shift:.1f}%"
        body = f"Implied volatility expanded sharply to {curr_iv:.1f}% (+{shift:.1f}% shift). Option demand rising."

    elif evt_type == "IV_CRUSH":
        curr_iv = f.get("current_atm_iv", 0.0)
        shift = f.get("shift_pct", 0.0)
        headline = f"ATM IV Crush: {shift:.1f}%"
        body = f"Implied volatility contracted to {curr_iv:.1f}% ({shift:.1f}% shift), reflecting premium compression."

    elif evt_type == "MAX_PAIN_PINNING":
        mp = f.get("max_pain", 0.0)
        spot = f.get("spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        headline = f"Expiry Pinning Active near Max Pain ₹{mp:,.0f}"
        body = f"Spot ₹{spot:,.1f} is pinned within {dist:.1f} pts of Max Pain (₹{mp:,.0f}). Late-cycle theta decay accelerating."

    elif evt_type == "INSTITUTIONAL_FOOTPRINT":
        lbl = f.get("footprint_label", "Institutional footprint")
        spot = f.get("spot", 0.0)
        headline = f"OrderFlow Footprint: {lbl.title()}"
        body = f"Microstructure detection identifies {lbl} around spot ₹{spot:,.1f} on elevated relative volume."

    elif evt_type == "NEWS_CATALYST":
        hd = f.get("headline", "")
        src = f.get("source", "")
        headline = "Macro Catalyst Coinciding with Tape"
        body = f"Possible catalyst: '{hd}' ({src}) coincided with session volatility."

    elif evt_type == "PINNED_TARGET_HIT":
        rec_id = f.get("rec_id", "")
        gain = f.get("gain_pts", 0.0)
        t_spot = f.get("target_spot", 0.0)
        headline = f"Pinned Position Target 1 Reached (+{gain:.1f} pts)"
        body = (
            f"Pinned trade {rec_id} reached Target 1 at spot ₹{t_spot:,.1f} (+{gain:.1f} pts gain). "
            f"Trailing stop to entry breakeven or partial profit booking is an option."
        )

    elif evt_type == "PINNED_TARGET_APPROACH":
        rec_id = f.get("rec_id", "")
        dist = f.get("dist_to_target", 0.0)
        t_spot = f.get("target_spot", 0.0)
        headline = f"Pinned Trade Approaching Target 1"
        body = f"Pinned recommendation {rec_id} is within {dist:.1f} pts of Target 1 (₹{t_spot:,.1f})."

    elif evt_type == "PINNED_INVALIDATION_WARNING":
        rec_id = f.get("rec_id", "")
        inval = f.get("invalidation_spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        headline = f"Pinned Trade Invalidation Warning"
        body = f"Spot is within {dist:.1f} pts of invalidation wall (₹{inval:,.1f}) for pinned trade {rec_id}. Thesis under pressure."

    else:
        headline = f"Market Development: {evt_type.replace('_', ' ').title()}"
        body = f"Event {evt_type} recorded at spot ₹{f.get('spot', 0.0):,.1f}."

    return headline, body


def render_bite_template(bite: SuggestionBite) -> str:
    """Generate clean, non-imperative phrasing for suggestion bites."""
    btype = bite.type
    trig = bite.levels.get("trigger", 0.0)
    inval = bite.levels.get("invalidation", 0.0)
    tgt = bite.levels.get("target", 0.0)
    rr = bite.risk.get("rr", 2.0)
    loss = bite.risk.get("max_loss_per_lot", 0.0)
    frame = bite.action_frame

    if btype == "WATCH":
        return f"Watch {trig:,.0f}: a sustained 5-min close would confirm directional follow-through."
    elif btype == "PREPARE":
        return f"If price reclaims {trig:,.0f}, {frame} fits. Invalidation below {inval:,.0f}; est. max loss ₹{loss:,.0f}/lot."
    elif btype == "ACT-IF":
        return f"If 5-min closes beyond {trig:,.0f}: {frame} fits. Target zone ₹{tgt:,.0f}; invalid on close beyond ₹{inval:,.0f}; R:R {rr}; est. max loss ₹{loss:,.0f}/lot."
    elif btype == "MANAGE":
        return f"Target approached near ₹{trig:,.0f}. Trailing stop to breakeven or partial profit booking are valid options; invalidation remains ₹{inval:,.0f}."
    elif btype == "STAND-ASIDE":
        return f"Elevated event risk and compressed edge. Waiting for clearer post-event structure is a reasonable stance."
    elif btype == "HEDGE":
        return f"Heightened gamma volatility into resistance. Defined-risk spreads recommended over unhedged short options."
    return f"{btype}: Monitor level ₹{trig:,.0f} with invalidation at ₹{inval:,.0f}."
