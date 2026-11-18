"""Book-balance line and gated hedge-pressure note from a RegimeView.

Book-balance target form:
    "Call-heavy book, flip 65pts below, call wall 30 above — call gamma larger than put gamma"

Hedge-pressure is a separate, conditional line. It never says
"dealers will defend." DEX sign alone never triggers it.

Duck-typed on the view (no import of RegimeView) to avoid a circular import.
"""

from __future__ import annotations


def one_sentence(view) -> str:
    if view.regime == "POSITIVE":
        regime_word = "Call-heavy book"
    elif view.regime == "NEGATIVE":
        regime_word = "Put-heavy book"
    else:
        regime_word = "Balanced book"

    if view.flip is None:
        flip_part = "flip none (no zero-crossing in range)"
    else:
        flip_part = f"flip {abs(view.flip_dist_pts)}pts {view.flip_side}"

    cd = view.call_wall_dist_pts
    call_part = f"call wall {abs(cd)} {'above' if cd >= 0 else 'below'}"

    return f"{regime_word}, {flip_part}, {call_part} — {view.bias}"


def hedge_pressure_sentence(
    strike: float,
    *,
    cover_known: bool = False,
    cover_frac: float | None = None,
) -> str:
    """Conditional hedge-pressure note under short_call_short_put.

    Forbidden phrases: "dealers will defend", fade/chase prescriptions.
    """
    base = (
        f"Under short_call_short_put, hedge pressure at {strike:g} if writers stay short "
        "— unmeasured on NSE; OI lags; de-size if tape thrusts through."
    )
    if cover_known and cover_frac is not None and cover_frac > 0.05:
        return (
            f"{base} Recent writer OI cover at the strike ({cover_frac:+.0%}) "
            "is observed covering, not a forecast."
        )
    if cover_known and cover_frac is not None and cover_frac < -0.05:
        return (
            f"{base} Recent OI is unwinding ({cover_frac:+.0%}) — weaker pressure case."
        )
    return base
