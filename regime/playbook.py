"""One-sentence book-balance line from a RegimeView.

Target form:
    "Call-heavy book, flip 65pts below, call wall 30 above — call gamma larger than put gamma"

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
