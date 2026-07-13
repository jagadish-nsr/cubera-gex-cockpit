"""Map the VOL-REGIME + walls -> an ILLUSTRATIVE option structure.

Driven by the sign-free VOL-REGIME (the |vanna|/|charm| gauge — HIGH dealer-gamma concentration =
calm/pinning, LOW = wild/trending; validated |GEX|/vanna -> forward realized vol), NOT the
unvalidated dealer-sign. Walls pick the strikes. Every suggestion carries a forced exit-by (no
hold-to-expiry) and a defined-risk note.

Principle: SELL premium in a CALM (high-concentration) regime, BUY convexity in a WILD (low-
concentration) regime; direction is NOT forecastable so the wild structure is non-directional.
The REGIME read is validated; the structure MAPPING is illustrative textbook only — paper/learning
only. We deliberately do NOT fabricate premiums; risk is the spread's defined width (the broker
shows the actual credit/debit).
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
    lot_size: int = 75,
    spread_width: int = 100,
    nearest_expiry=None,
    is_expiry_day: bool = False,
) -> CandidateStructure:
    """``vol_regime`` is the sign-free gauge tag: 'calm' (HIGH concentration / pinning),
    'wild' (LOW / trending), or 'mid'/None (neutral / unclear -> wait)."""
    cs = _base_structure(levels, vol_regime, lot_size, spread_width, nearest_expiry)
    if is_expiry_day:
        cs.expiry_note = _expiry_note(vol_regime)
    return cs


def _expiry_note(vol_regime) -> str:
    """Expiry-day reading: gamma is huge, so the pin (and the blast tail) intensify."""
    if vol_regime == "calm":
        return ("EXPIRY DAY: HIGH dealer-gamma concentration -> strong PIN toward the magnet / "
                "max pain (a premium-sellers' day). A late, decisive break of a wall can flip it "
                "into a GAMMA BLAST (sharp move) — that's the tail risk, not the base case.")
    if vol_regime == "wild":
        return ("EXPIRY DAY + LOW gamma concentration -> GAMMA-BLAST risk: moves can explode in "
                "the last hour. Buying OTM beyond the wall is a low-probability lottery (theta "
                "crush is brutal) — tiny size only, if at all.")
    return ("EXPIRY DAY: mixed / neutral gamma — both a pin and a blast are possible and it's very "
            "hard to trade. Best to wait.")


def _base_structure(
    levels,
    vol_regime,
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

    # --- MID / unclear regime -> wait --------------------------------------
    if vol_regime not in ("calm", "wild"):
        return CandidateStructure(
            name="No trade — wait",
            rationale="Neutral / unclear vol regime (MID concentration). De-size and wait for the "
            "regime to pick a side before committing to a premium-selling or -buying structure.",
            risk_note="—",
            exit_by=exit_by,
        )

    # --- CALM (high concentration / pinning) -> SELL premium, defined risk --
    if vol_regime == "calm":
        if pw <= spot <= cw:
            legs = [
                ("SELL", "C", cw), ("BUY", "C", cw + spread_width),
                ("SELL", "P", pw), ("BUY", "P", pw - spread_width),
            ]
            return CandidateStructure(
                name="Iron condor (sell premium)",
                legs=legs,
                rationale=f"HIGH gamma concentration -> calm / range-bound between {pw:,} and "
                f"{cw:,}. Sell premium with the short strikes just outside the walls; profit if "
                "Nifty stays in the range (pinning favours premium sellers).",
                risk_note=f"Defined risk per side ~ {risk_def} minus the credit you collect.",
                exit_by=exit_by,
            )
        return CandidateStructure(
            name="Wait — no clean range setup",
            rationale=f"Calm regime, but price is OUTSIDE the wall band ({pw:,}-{cw:,}). The "
            "textbook condor isn't cleanly set up — wait for price to return into the range "
            "before selling premium.",
            risk_note="—",
            exit_by=exit_by,
        )

    # --- WILD (low concentration / trending) -> BUY convexity (direction not forecastable) --
    return CandidateStructure(
        name="Long strangle (buy premium, either way)",
        legs=[("BUY", "C", cw), ("BUY", "P", pw)],
        rationale=f"LOW gamma concentration -> wild / trending: a big move is likely but the "
        f"DIRECTION isn't forecastable. Buy a strangle at the walls ({pw:,} put / {cw:,} call) for "
        "convexity — profit if Nifty breaks hard EITHER way; or wait for a CONFIRMED wall break "
        "and buy a debit spread in that direction. Never SELL premium here.",
        risk_note="Defined risk = the debit paid. Theta bleeds if it stays quiet — size SMALL; "
        "exit on a clear break or by the cutoff.",
        exit_by=exit_by,
    )
