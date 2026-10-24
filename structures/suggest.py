"""Map absolute-GEX concentration and the gamma strikes to an illustrative structure.

The input is the |GEX| bucket (high / low / mid), not the call−put sign. High concentration
has meant calmer forward vol in the sample; low concentration, wider swings. A short strike,
when one is used, sits one step outside the call-gamma or put-gamma concentration. Every
suggestion carries a forced exit and a defined-risk width. The broker shows the actual
credit or debit. The user confirms. No order is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CandidateStructure:
    name: str
    legs: list[tuple] = field(default_factory=list)  # (action, type, strike): ("SELL","P",23700)
    rationale: str = ""
    risk_note: str = ""
    exit_by: str = ""
    expiry_note: str = ""   # set on expiry day (pin vs gamma-blast context)
    illustrative: bool = True


def _round_strike(x: float, step: int = 50) -> int:
    return int(round(x / step) * step)


def suggest_structure(
    levels,
    vol_regime,
    lot_size: int = 65,
    spread_width: int = 100,
    nearest_expiry=None,
    is_expiry_day: bool = False,
    bias_score: float | None = None,
    sentinel_signals: list[Any] | None = None,
    strike_step: int = 50,
) -> CandidateStructure:
    """Return the primary candidate structure (backwards-compatible)."""
    return suggest_structures(
        levels,
        vol_regime,
        lot_size=lot_size,
        spread_width=spread_width,
        nearest_expiry=nearest_expiry,
        is_expiry_day=is_expiry_day,
        bias_score=bias_score,
        sentinel_signals=sentinel_signals,
        strike_step=strike_step,
    )[0]


def suggest_structures(
    levels,
    vol_regime,
    lot_size: int = 65,
    spread_width: int = 100,
    nearest_expiry=None,
    is_expiry_day: bool = False,
    bias_score: float | None = None,
    sentinel_signals: list[Any] | None = None,
    strike_step: int = 50,
) -> list[CandidateStructure]:
    """Return 1-2 candidate structures: primary + optional alternate structure."""
    candidates = _base_structures(
        levels, vol_regime, lot_size, spread_width, nearest_expiry,
        bias_score=bias_score, strike_step=strike_step,
    )
    if is_expiry_day:
        note = _expiry_note(vol_regime)
        for cs in candidates:
            cs.expiry_note = note

    # Enrich with OrderFlow Sentinel signals if available
    if sentinel_signals:
        high_conf = [s for s in sentinel_signals if getattr(s, "score", 0) >= 65]
        if high_conf:
            top_sig = high_conf[0]
            sig_name = getattr(top_sig, "type", "")
            score = getattr(top_sig, "score", 0)
            rvol = getattr(top_sig, "rvol_tod", 1.0)
            for cs in candidates:
                if "call" in cs.name.lower() and "BEAR" in sig_name:
                    cs.rationale += f" [⚡ Confirmed by Sentinel: {sig_name} at Resistance (Score: {score}, RVol: {rvol:.1f}×)]"
                elif "put" in cs.name.lower() and "BULL" in sig_name:
                    cs.rationale += f" [⚡ Confirmed by Sentinel: {sig_name} at Support (Score: {score}, RVol: {rvol:.1f}×)]"
                elif "condor" in cs.name.lower() and ("ABSORPTION" in sig_name or "EXHAUSTION" in sig_name):
                    cs.rationale += f" [⚡ Confirmed by Sentinel: {sig_name} Pinning (Score: {score})]"

    return candidates



def _expiry_note(vol_regime) -> str:
    """Expiry-day reading. Concentration is the vol context. It is not a pin call."""
    if vol_regime == "calm":
        return ("EXPIRY DAY: HIGH gamma concentration. A PIN toward the crowded strikes is the "
                "historical base case for this bucket, and a late break can become a GAMMA BLAST. "
                "The blast is the tail. Do not sell the magnet.")
    if vol_regime == "wild":
        return ("EXPIRY DAY + LOW gamma concentration -> GAMMA-BLAST risk: moves can widen in "
                "the last hour. Buying beyond the concentration is a low-probability lottery "
                "(theta crush is brutal) — tiny size only, if at all.")
    return ("EXPIRY DAY: mixed gamma concentration — both a PIN and a BLAST are possible. "
            "Wait.")


def _beyond(wall: float, step: int, side: int) -> int:
    """One listed strike outside the gamma concentration. side +1 is above, -1 is below."""
    return _round_strike(wall, step) + side * step


def _base_structures(
    levels,
    vol_regime,
    lot_size: int = 65,
    spread_width: int = 100,
    nearest_expiry=None,
    bias_score: float | None = None,
    strike_step: int = 50,
) -> list[CandidateStructure]:
    exit_by = "exit at least 1 trading day before expiry" + (
        f" ({nearest_expiry})" if nearest_expiry else ""
    )
    step = int(strike_step) if int(strike_step) > 0 else 50
    cw = _round_strike(levels.call_wall, step)
    pw = _round_strike(levels.put_wall, step)
    short_call = _beyond(levels.call_wall, step, +1)
    short_put = _beyond(levels.put_wall, step, -1)
    spot = levels.spot
    risk_def = f"{spread_width} x {lot_size} = Rs {spread_width * lot_size:,}"

    # --- MID / unclear regime -> wait --------------------------------------
    if vol_regime not in ("calm", "wild"):
        return [
            CandidateStructure(
                name="No trade — wait",
                rationale="Absolute GEX is in the middle bucket. De-size and wait. "
                "The call−put label is not a trade.",
                risk_note="—",
                exit_by=exit_by,
            )
        ]

    # --- CALM (high |GEX|) -> defined-risk short premium, shorts outside the strikes --
    if vol_regime == "calm":
        if pw <= spot <= cw:
            ic_legs = [
                ("SELL", "C", short_call), ("BUY", "C", short_call + spread_width),
                ("SELL", "P", short_put), ("BUY", "P", short_put - spread_width),
            ]
            primary = CandidateStructure(
                name="Iron condor (sell premium)",
                legs=ic_legs,
                rationale=f"Absolute GEX is in the high-concentration bucket, which has meant "
                f"calmer forward vol. Spot is between the put-gamma strike {pw:,} and the "
                f"call-gamma strike {cw:,}. Short strikes sit one step outside those "
                f"concentrations ({short_put:,} put / {short_call:,} call). You confirm.",
                risk_note=f"Defined risk per side ~ {risk_def} minus the credit collected.",
                exit_by=exit_by,
            )

            mid = (pw + cw) / 2.0
            if spot >= mid:
                alt_legs = [("SELL", "C", short_call), ("BUY", "C", short_call + spread_width)]
                alt_name = "Bear call spread (alt: sell upside wall)"
                alt_rationale = (
                    f"Alternative to the condor. Spot ({spot:,.0f}) is in the upper half of the "
                    f"concentrations. Short call at {short_call:,}, one step above the call-gamma "
                    f"strike, defined risk ({risk_def})."
                )
            else:
                alt_legs = [("SELL", "P", short_put), ("BUY", "P", short_put - spread_width)]
                alt_name = "Bull put spread (alt: sell downside floor)"
                alt_rationale = (
                    f"Alternative to the condor. Spot ({spot:,.0f}) is in the lower half of the "
                    f"concentrations. Short put at {short_put:,}, one step below the put-gamma "
                    f"strike, defined risk ({risk_def})."
                )

            alt = CandidateStructure(
                name=alt_name,
                legs=alt_legs,
                rationale=alt_rationale,
                risk_note=f"Defined risk ~ {risk_def} minus credit collected.",
                exit_by=exit_by,
            )
            return [primary, alt]

        return [
            CandidateStructure(
                name="Wait — no clean range setup",
                rationale=f"High concentration, but spot is outside the gamma strikes "
                f"({pw:,}-{cw:,}). Wait until spot is back between them before selling premium.",
                risk_note="—",
                exit_by=exit_by,
            )
        ]

    # --- WILD (low |GEX|) -> buy convexity. Direction is not a forecast. --
    strangle_legs = [("BUY", "C", cw), ("BUY", "P", pw)]
    primary = CandidateStructure(
        name="Long strangle (buy premium, either way)",
        legs=strangle_legs,
        rationale=f"Absolute GEX is in the low-concentration bucket, which has meant wider "
        f"swings. Direction is not a forecast. Buy the put-gamma strike {pw:,} and the "
        f"call-gamma strike {cw:,}.",
        risk_note="Defined risk = debit paid. Theta bleeds if quiet — small size only.",
        exit_by=exit_by,
    )

    bullish = (bias_score is not None and bias_score > 1.0) or (spot > (pw + cw) / 2.0)
    if bullish:
        alt_legs = [("BUY", "C", pw), ("SELL", "C", short_call)]
        alt_name = "Bull call debit spread (alt: momentum break)"
        alt_rationale = (
            f"Alternative to the strangle. Buy the {pw:,} call and sell {short_call:,}, "
            f"one step above the call-gamma strike. Direction is still not a forecast."
        )
    else:
        alt_legs = [("BUY", "P", cw), ("SELL", "P", short_put)]
        alt_name = "Bear put debit spread (alt: breakdown)"
        alt_rationale = (
            f"Alternative to the strangle. Buy the {cw:,} put and sell {short_put:,}, "
            f"one step below the put-gamma strike. Direction is still not a forecast."
        )

    alt = CandidateStructure(
        name=alt_name,
        legs=alt_legs,
        rationale=alt_rationale,
        risk_note="Defined risk = debit paid.",
        exit_by=exit_by,
    )
    return [primary, alt]
