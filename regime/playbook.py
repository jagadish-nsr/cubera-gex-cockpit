"""One-sentence playbook headline from a RegimeView.

Target form (BUILD_PLAN §6 example):
    "Positive gamma, flip 65pts below, call wall 30 above — range fade"

Duck-typed on the view (no import of RegimeView) to avoid a circular import.
"""

from __future__ import annotations


def one_sentence(view) -> str:
    regime_word = f"{view.regime.title()} gamma"

    if view.flip is None:
        flip_part = "flip none (no zero-crossing in range)"
    else:
        flip_part = f"flip {abs(view.flip_dist_pts)}pts {view.flip_side}"

    cd = view.call_wall_dist_pts
    call_part = f"call wall {abs(cd)} {'above' if cd >= 0 else 'below'}"

    return f"{regime_word}, {flip_part}, {call_part} — {view.bias}"
