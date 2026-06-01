"""Map regime + walls -> an ILLUSTRATIVE option structure (options-structures skill).

One-line principle: SELL premium in positive gamma, BUY premium in negative gamma;
walls pick the strikes, the flip picks the side. Every suggestion carries a
forced exit-by (no hold-to-expiry) and a defined-risk note.

IMPORTANT: this is illustrative/textbook only. The Nifty dealer-sign model is
UNVALIDATED, so these are NOT validated recommendations — paper/learning only.
We deliberately do NOT fabricate premiums; risk is stated as the spread's defined
width (the broker shows the actual credit/debit).
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
    view,
    lot_size: int = 75,
    spread_width: int = 100,
    nearest_expiry=None,
    is_expiry_day: bool = False,
) -> CandidateStructure:
    cs = _base_structure(levels, view, lot_size, spread_width, nearest_expiry)
    if is_expiry_day:
        cs.expiry_note = _expiry_note(levels, view)
    return cs


def _expiry_note(levels, view) -> str:
    """Expiry-day reading: gamma is huge, so the pin (and the blast tail) intensify."""
    if view.conflict or levels.regime == "FLAT" or levels.flip is None:
        return ("EXPIRY DAY: mixed signals + huge gamma — both a pin and a blast are "
                "possible and it's very hard to trade. Best to wait.")
    if levels.regime == "POSITIVE":
        return ("EXPIRY DAY: gamma is huge → strong PIN toward the magnet / max pain "
                "(a premium-sellers' day). A late, decisive break of a wall can flip it "
                "into a GAMMA BLAST (sharp move) — that's the tail risk, not the base case.")
    return ("EXPIRY DAY + below the flip: GAMMA-BLAST risk — moves can explode in the "
            "last hour. Buying OTM beyond the wall is a low-probability lottery (theta "
            "crush is brutal) — tiny size only, if at all.")


def _base_structure(
    levels,
    view,
    lot_size: int = 75,
    spread_width: int = 100,
    nearest_expiry=None,
) -> CandidateStructure:
    exit_by = "exit at least 1 trading day before expiry" + (
        f" ({nearest_expiry})" if nearest_expiry else ""
    )
    cw = _round_strike(levels.call_wall)
    pw = _round_strike(levels.put_wall)
    spot = levels.spot
    risk_def = f"{spread_width} x {lot_size} = Rs {spread_width * lot_size:,}"

    # --- No clean edge: conflict / no flip / flat -> wait -------------------
    if view.conflict or levels.flip is None or levels.regime == "FLAT":
        return CandidateStructure(
            name="No trade — wait",
            rationale="Signals are mixed or the calm/wild switch is unclear. "
            "De-size and wait for confirmation rather than force a trade.",
            risk_note="—",
            exit_by=exit_by,
        )

    # --- POSITIVE gamma (calm) -> SELL premium, defined risk ----------------
    if levels.regime == "POSITIVE":
        if pw <= spot <= cw:
            # Iron condor: short call ABOVE the call wall, short put BELOW the put wall.
            legs = [
                ("SELL", "C", cw), ("BUY", "C", cw + spread_width),
                ("SELL", "P", pw), ("BUY", "P", pw - spread_width),
            ]
            return CandidateStructure(
                name="Iron condor (sell premium)",
                legs=legs,
                rationale=f"Calm / range-bound between {pw:,} and {cw:,}. Sell premium "
                "with the short strikes just outside the walls; profit if Nifty stays "
                "in the range. (Sell premium in positive gamma; walls pick the strikes.)",
                risk_note=f"Defined risk per side ~ {risk_def} minus the credit you collect.",
                exit_by=exit_by,
            )
        # Calm but price is outside the wall band -> no clean condor.
        return CandidateStructure(
            name="Wait — no clean range setup",
            rationale=f"Calm regime, but price is OUTSIDE the wall band ({pw:,}–{cw:,}). "
            "The textbook condor isn't cleanly set up — wait for price to return into "
            "the range before selling premium.",
            risk_note="—",
            exit_by=exit_by,
        )

    # --- NEGATIVE gamma (wild, spot below flip) -> BUY premium with the break
    upper = _round_strike(spot)
    lower = upper - spread_width
    return CandidateStructure(
        name="Put debit spread (buy premium, downside)",
        legs=[("BUY", "P", upper), ("SELL", "P", lower)],
        rationale=f"Wild regime below the flip {levels.flip:,.0f}: trade WITH the "
        "down-move and BUY premium (a debit spread). Requires a CONFIRMED hold below "
        "the flip, not an intraday wick. (Buy premium in negative gamma; flip picks the side.)",
        risk_note=f"Defined risk = the debit paid (up to ~ {risk_def}). REDUCE size — "
        "moves run 1.5–2x normal. Never SELL premium here.",
        exit_by=exit_by,
    )
