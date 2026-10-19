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
        sym = f.get("symbol") or "Nifty"
        headline = f"Gap-Up Open: +{gap_pts:,.1f} pts (+{gap_pct:.2f}%)"
        body = (
            f"{sym} opened higher at ₹{open_p:,.1f}, up {gap_pts:+.1f} pts ({gap_pct:+.2f}%) "
            f"versus prior close. Current spot is testing towards the ₹{cw:,.0f} Call Wall."
        )

    elif evt_type == "GAP_DOWN":
        gap_pts = f.get("gap_pts", 0.0)
        gap_pct = f.get("gap_pct", 0.0)
        open_p = f.get("open", 0.0)
        pw = f.get("put_wall", 0.0)
        sym = f.get("symbol") or "Nifty"
        headline = f"Gap-Down Open: {gap_pts:,.1f} pts ({gap_pct:.2f}%)"
        body = (
            f"{sym} opened lower at ₹{open_p:,.1f}, down {gap_pts:,.1f} pts ({gap_pct:.2f}%) "
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

    elif evt_type in ("IV_SPIKE", "VOL_ATM_IV_SPIKE"):
        curr_iv = f.get("current_atm_iv", 0.0)
        shift = f.get("shift_pct", 0.0)
        headline = "IV Expansion"
        body = f"ATM implied volatility expanded sharply to {curr_iv:.1f}% (+{shift:.1f}% shift). Option demand rising."

    elif evt_type in ("IV_CRUSH", "VOL_ATM_IV_CRUSH"):
        curr_iv = f.get("current_atm_iv", 0.0)
        shift = f.get("shift_pct", 0.0)
        headline = "IV Crush"
        body = f"ATM implied volatility contracted to {curr_iv:.1f}% ({shift:.1f}% shift), reflecting premium compression."

    elif evt_type == "VOL_INDIA_VIX_SURGE":
        headline = "VIX Surge"
        body = "India VIX surged abruptly, signaling elevated intraday risk pricing across index options."

    elif evt_type in ("MAX_PAIN_PINNING", "EXPIRY_PINNING_MAX_PAIN"):
        mp = f.get("max_pain", 0.0)
        spot = f.get("spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        headline = "Expiry Pinning"
        body = f"Spot ₹{spot:,.1f} is pinned within {dist:.0f} pts of Max Pain (₹{mp:,.0f}). Late-cycle theta decay accelerating."

    elif evt_type == "EXPIRY_GAMMA_ACCELERATION":
        headline = "Expiry Gamma Acceleration"
        body = "High gamma concentration near at-the-money strikes is amplifying intraday delta sensitivity."

    elif evt_type == "DIVERGENCE_PRICE_OI":
        spot = f.get("spot", 0.0)
        headline = "Divergence"
        body = "A fresh high, but futures OI is falling and ATM IV is rising: the move looks like short covering rather than new buying."

    elif evt_type == "DIVERGENCE_PRICE_IV":
        headline = "Divergence"
        body = "Price is pushing higher while ATM IV rises: unusual move pointing to short-term hedging or event premium demand."

    elif evt_type == "DIVERGENCE_PRICE_PCR":
        headline = "Divergence"
        body = "Price is rising while Put-Call Ratio trends lower: put support may be unwinding on the advance."

    elif evt_type == "DIVERGENCE_SECTOR_BREADTH":
        headline = "Divergence"
        body = "Index fresh high is diverging from heavyweight breadth: participation remains selective."

    elif evt_type == "INSTITUTIONAL_FOOTPRINT":
        lbl = f.get("footprint_label", "Institutional footprint")
        spot = f.get("spot", 0.0)
        headline = f"Footprint: {lbl.title()}"
        body = f"Microstructure detection identifies {lbl} around spot ₹{spot:,.1f} on elevated relative volume."

    elif evt_type == "NEWS_CATALYST":
        hd = f.get("headline", "")
        src = f.get("source", "")
        headline = "Possible Catalyst"
        body = f"A headline '{hd}' ({src}) coincided with session volatility."

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
        headline = "Pinned Trade Approaching Target 1"
        body = f"Pinned recommendation {rec_id} is within {dist:.1f} pts of Target 1 (₹{t_spot:,.1f})."

    elif evt_type == "PINNED_INVALIDATION_WARNING":
        rec_id = f.get("rec_id", "")
        inval = f.get("invalidation_spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        headline = "Pinned Trade Invalidation Warning"
        body = f"Spot is within {dist:.1f} pts of invalidation wall (₹{inval:,.1f}) for pinned trade {rec_id}. Thesis under pressure."

    elif evt_type == "SECONDARY_WALL_TEST":
        wtype = f.get("wall_type", "SECONDARY_WALL").replace("_", " ").title()
        strike = f.get("strike", 0.0)
        spot = f.get("spot", 0.0)
        dist = f.get("dist_pts", 0.0)
        pw = f.get("primary_wall", 0.0)
        headline = f"{wtype} Tested at ₹{strike:,.0f}"
        body = f"Spot ₹{spot:,.1f} is testing {wtype} at ₹{strike:,.0f} (within {dist:.1f} pts), acting as intermediate layer before primary wall ₹{pw:,.0f}."

    elif evt_type == "STRIKE_OI_SURGE":
        strike = f.get("strike", 0.0)
        side = f.get("side", "CALL").upper()
        doi = f.get("doi", 0.0)
        spot = f.get("spot", 0.0)
        headline = f"Heavy {side.title()} Buildup at ₹{strike:,.0f}"
        body = f"Notable {side.lower()} open interest addition (+{doi:,.0f} contracts) at strike ₹{strike:,.0f} with spot at ₹{spot:,.1f}."

    elif evt_type == "PCR_SHIFT":
        direction = f.get("direction", "")
        old_pcr = f.get("old_pcr", 0.0)
        new_pcr = f.get("new_pcr", 0.0)
        delta_pcr = f.get("delta_pcr", 0.0)
        spot = f.get("spot", 0.0)
        if direction == "BULLISH":
            headline = f"PCR Surge: {old_pcr:.2f} → {new_pcr:.2f} (+{delta_pcr:+.2f})"
            body = f"Put-Call Ratio expanded to {new_pcr:.2f} as aggressive put writing reinforces downside support at spot ₹{spot:,.1f}."
        elif direction == "BEARISH":
            headline = f"PCR Contraction: {old_pcr:.2f} → {new_pcr:.2f} ({delta_pcr:+.2f})"
            body = f"Put-Call Ratio softened to {new_pcr:.2f}, reflecting call writing buildup and reduced put support at spot ₹{spot:,.1f}."
        else:
            pcr = f.get("pcr", new_pcr)
            headline = f"PCR Skew Extended: {pcr:.2f}"
            body = f"Put-Call Ratio reached extended sentiment reading of {pcr:.2f} at spot ₹{spot:,.1f}."

    elif evt_type == "VWAP_HOLD_BOUNCE":
        vwap = f.get("vwap", 0.0)
        spot = f.get("spot", 0.0)
        side = f.get("side", "SUPPORT")
        headline = f"Session VWAP Held as {side.title()} at ₹{vwap:,.1f}"
        body = f"Spot ₹{spot:,.1f} tested Session VWAP (₹{vwap:,.1f}) and successfully held, confirming intraday buyer defense."

    elif evt_type == "VWAP_BAND_EXTENSION":
        direction = f.get("direction", "OVERBOUGHT")
        ext = f.get("extension_pts", 0.0)
        vwap = f.get("vwap", 0.0)
        spot = f.get("spot", 0.0)
        headline = f"VWAP Stretch: {direction.title()} ({ext:+.1f} pts)"
        body = f"Spot ₹{spot:,.1f} is stretched {ext:+.1f} pts away from Session VWAP (₹{vwap:,.1f}), entering {direction.lower()} mean-reversion territory."

    elif evt_type == "SECTOR_LEADERSHIP":
        leader = f.get("leader", "")
        leader_pct = f.get("leader_pct", 0.0)
        laggard = f.get("laggard", "")
        g_cnt = f.get("green_count", 0)
        tot = f.get("total_count", 0)
        headline = f"Sector Leadership: {leader} (+{leader_pct:.2f}%)"
        body = f"{leader} is pacing the session rally (+{leader_pct:.2f}%), with {g_cnt}/{tot} sectors positive. Relative laggard: {laggard}."

    elif evt_type == "BROAD_BASED_ADVANCE":
        g_cnt = f.get("green_count", 0)
        tot = f.get("total_count", 0)
        lead = f.get("top_gainer", "")
        lead_pct = f.get("top_gainer_pct", 0.0)
        headline = f"Broad Market Advance ({g_cnt}/{tot} Sectors Green)"
        body = f"Broad market breadth provides strong underlying support with {g_cnt} of {tot} sectors in the green. Led by {lead} (+{lead_pct:.2f}%)."

    elif evt_type == "RANGE_COMPRESSION":
        rng = f.get("range_pts", 0.0)
        h = f.get("high", 0.0)
        l = f.get("low", 0.0)
        win = f.get("window_minutes", 30)
        headline = f"Midday Range Squeeze: {rng:.1f} pts"
        body = f"Spot is compressing in a tight {rng:.1f} pts range between ₹{l:,.1f} and ₹{h:,.1f} over the past {win} minutes. Breakout setup forming."

    elif evt_type == "ATM_STRADDLE_DECAY":
        curr = f.get("current_straddle", 0.0)
        dec_pts = f.get("decay_pts", 0.0)
        dec_pct = f.get("decay_pct", 0.0)
        headline = f"ATM Straddle Decay: -{dec_pts:.1f} pts (-{dec_pct:.1f}%)"
        body = f"ATM straddle premium compressed to ₹{curr:,.1f} (decay of -{dec_pts:.1f} pts / -{dec_pct:.1f}%), indicating strong theta capture."

    elif evt_type in ("CONTINUATION_BULL", "CONTINUATION_BEAR", "EXHAUSTION_UP", "ABSORPTION_BEAR", "ABSORPTION_BULL"):
        insight = f.get("insight_text", "")
        headline = f"Footprint: {evt_type.replace('_', ' ').title()}"
        body = insight or f"Institutional microstructure footprint detected {evt_type.replace('_', ' ').lower()} at spot ₹{f.get('spot', 0.0):,.1f}."

    else:
        headline = f"Market Development: {evt_type.replace('_', ' ').title()}"
        body = f"Event {evt_type} recorded at spot ₹{f.get('spot', 0.0):,.1f}."

    return headline, body


def render_bite_template(bite: SuggestionBite | dict[str, Any]) -> str:
    """Generate clean, non-imperative storytelling phrasing for suggestion bites."""
    if isinstance(bite, dict):
        btype = bite.get("type", "WATCH")
        levels = bite.get("levels", {})
        risk = bite.get("risk", {})
        frame = bite.get("action_frame", "")
        badge = bite.get("badge", {})
    else:
        btype = bite.type
        levels = bite.levels
        risk = bite.risk
        frame = bite.action_frame
        badge = bite.badge

    trig = levels.get("trigger", 0.0)
    inval = levels.get("invalidation", 0.0)
    tgt = levels.get("target", 0.0)
    rr = risk.get("rr", 2.0)
    loss = risk.get("max_loss_per_lot", 0.0)
    loss_str = f"₹{loss:,.0f}" if loss > 0 else "₹X"

    if btype == "WATCH":
        return f"WATCH {trig:,.0f}. A 5-min close above it would weaken the wall-defence read."
    elif btype == "PREPARE":
        return f"PREPARE. If price slips back under {trig:,.0f} (estimated gamma flip), a range-to-bearish read strengthens; {frame} would fit; est. max loss {loss_str}/lot; invalid on a close above {inval:,.0f}. Badge: heuristic, shadow-graded."
    elif btype == "ACT-IF":
        return f"ACT-IF. If 5-min closes beyond {trig:,.0f}: {frame} fits; target zone {tgt:,.0f}, invalid beyond {inval:,.0f}; R:R {rr:.1f}; est. max loss {loss_str}/lot. Badge: heuristic, shadow-graded."
    elif btype == "TRIGGERED":
        return f"TRIGGERED (the earlier {frame} idea is now live; target zone {tgt:,.0f}, invalid above {inval:,.0f})."
    elif btype == "MANAGE":
        return f"MANAGE (Target approached near {trig:,.0f}; trailing stop to breakeven or partial profit booking are valid options; invalidation {inval:,.0f})."
    elif btype == "STAND-ASIDE":
        return "STAND-ASIDE for tomorrow's open: event risk overnight."
    elif btype == "HEDGE":
        return "HEDGE. Short-gamma exposure into resistance; defined-risk spreads reduce tail risk."
    return f"{btype}: Monitor level {trig:,.0f} with invalidation at {inval:,.0f}."


def extract_topic_header(raw_headline: str, entry: Any | None = None) -> str:
    """Derive clean, bold topic header for narrative storytelling."""
    hl = str(raw_headline).strip()
    if not hl:
        return "Market Update"
    
    # If headline is like "Gap-Up Open: +150.0 pts", split on colon if the prefix is descriptive
    if ":" in hl:
        prefix = hl.split(":", 1)[0].strip()
        if 3 <= len(prefix) <= 40:
            return prefix
    return hl.rstrip(".!?")


def format_story_entry(entry: Any, include_date: bool = True, *args: Any, **kwargs: Any) -> str:
    """Format commentary entry in clean freeflowing storytelling style with Date & Time and Bold Topic Header.

    Example output:
    14 Oct 2026, 09:15 IST | **Gap-Up Open**: Nifty opened 0.7% (about 1.0σ) above the prior close, straight into the 23,000 call wall (24% of nearby call OI). **Bite: WATCH 23,000** — A 5-min close above it would weaken the wall-defence read.
    """
    from commentary.models import to_ist
    ts_val = getattr(entry, "ts", None) if not isinstance(entry, dict) else entry.get("ts")
    dt_ist = to_ist(ts_val)

    if include_date:
        dt_str = dt_ist.strftime("%d %b %Y, %H:%M IST")
    else:
        dt_str = dt_ist.strftime("%H:%M")

    raw_hl = getattr(entry, "headline", "") if not isinstance(entry, dict) else entry.get("headline", "")
    raw_body = getattr(entry, "body", "") if not isinstance(entry, dict) else entry.get("body", "")

    topic = extract_topic_header(raw_hl, entry)

    # Clean body text
    body = str(raw_body).strip()
    # If body starts with the topic header, avoid redundant repetition
    if body.lower().startswith(topic.lower()):
        body = body[len(topic):].lstrip(": -—.,").strip()
    if body and not body.endswith((".", "!", "?")):
        body += "."

    bites_list = getattr(entry, "bites", []) if not isinstance(entry, dict) else entry.get("bites", [])
    bites_parts = []
    for b in bites_list:
        b_str = render_bite_template(b)
        b_type = b.get("type", "") if isinstance(b, dict) else getattr(b, "type", "")
        if b_str.startswith("Bite:"):
            b_str = b_str[5:].strip()

        if b_type and b_str.startswith(b_type):
            remainder = b_str[len(b_type):].lstrip(": .—").strip()
            if b_type == "WATCH" and remainder and (remainder[0].isdigit() or remainder.startswith("₹")):
                tokens = remainder.split(" ", 1)
                level_str = tokens[0].rstrip(".")
                rest = tokens[1] if len(tokens) > 1 else ""
                bites_parts.append(f"**Bite: WATCH {level_str}** — {rest}".strip())
            else:
                bites_parts.append(f"**Bite: {b_type}** — {remainder}")
        else:
            bites_parts.append(f"**Bite: {b_type or 'INFO'}** — {b_str}")

    bites_suffix = f" {' '.join(bites_parts)}" if bites_parts else ""
    return f"{dt_str} | **{topic}**: {body}{bites_suffix}".strip()

