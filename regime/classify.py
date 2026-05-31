"""Regime classification with wall-strength judged RELATIVE to the gamma flip.

Core ideas (gex-math + options-structures skills, BUILD_PLAN §4):
  - The flip splits the tape: spot >= flip is the positive-gamma (pin/fade) zone;
    spot < flip is the negative-gamma (momentum/amplify) zone.
  - A put wall ABOVE the flip is real support; BELOW the flip it's a speed bump.
  - When the aggregate regime sign DISAGREES with the spot-vs-flip zone, that's a
    CONFLICT -> de-size, do not blindly fade a thesis-aligned move (CLAUDE.md).

This module is pure: it consumes already-computed levels and emits a view. It
does NOT fetch data or deliver alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from regime.playbook import one_sentence


@dataclass
class RegimeView:
    regime: str
    total_net_gex: float
    spot: float
    flip: float | None
    flip_dist_pts: int | None        # flip - spot, rounded to points (signed)
    flip_side: str | None            # "below" | "above" | None
    in_positive_zone: bool | None    # spot >= flip
    call_wall: float
    call_wall_dist_pts: int          # call_wall - spot
    put_wall: float
    put_wall_dist_pts: int           # put_wall - spot
    call_wall_role: str              # "resistance"
    put_wall_role: str               # "support" | "speed bump" | "unknown"
    magnet: float | None
    max_pain: float | None
    conflict: bool
    bias: str
    headline: str
    notes: list[str] = field(default_factory=list)


def classify(
    spot: float,
    call_wall: float,
    put_wall: float,
    flip: float | None,
    regime: str,
    total_net_gex: float,
    magnet: float | None = None,
    max_pain: float | None = None,
) -> RegimeView:
    notes: list[str] = []

    # --- spot vs flip: which gamma zone are we in? -------------------------
    if flip is None:
        flip_dist = None
        flip_side = None
        in_pos = None
        notes.append("Gamma flip is None (no zero-crossing in range) — zone undetermined.")
    else:
        flip_dist = round(flip - spot)
        flip_side = "below" if flip < spot else "above"
        in_pos = spot >= flip

    # --- wall distances + roles relative to flip ---------------------------
    call_dist = round(call_wall - spot)
    put_dist = round(put_wall - spot)
    call_role = "resistance"
    if flip is None:
        put_role = "unknown"
    else:
        put_role = "support" if put_wall >= flip else "speed bump"
        if put_role == "speed bump":
            notes.append(
                f"Put wall {put_wall:g} is below the flip {flip:g} — speed bump, "
                "not firm support (trapdoor risk if breached)."
            )

    # --- conflict: aggregate regime sign vs local flip zone ----------------
    conflict = False
    if in_pos is not None:
        if regime == "POSITIVE" and not in_pos:
            conflict = True
        elif regime == "NEGATIVE" and in_pos:
            conflict = True
    if conflict:
        notes.append(
            f"CONFLICT: aggregate regime {regime} disagrees with spot-vs-flip zone "
            "(de-size; do not blindly fade)."
        )

    # --- bias -------------------------------------------------------------
    if conflict:
        bias = "regime/flip conflict, de-size and wait for confirmation"
    elif regime == "FLAT":
        bias = "neutral, no clear edge"
    elif regime == "POSITIVE" and (in_pos is None or in_pos):
        bias = "range fade"
    else:
        bias = "momentum, trade with the break (reduce size)"

    view = RegimeView(
        regime=regime,
        total_net_gex=total_net_gex,
        spot=spot,
        flip=flip,
        flip_dist_pts=flip_dist,
        flip_side=flip_side,
        in_positive_zone=in_pos,
        call_wall=call_wall,
        call_wall_dist_pts=call_dist,
        put_wall=put_wall,
        put_wall_dist_pts=put_dist,
        call_wall_role=call_role,
        put_wall_role=put_role,
        magnet=magnet,
        max_pain=max_pain,
        conflict=conflict,
        bias=bias,
        headline="",  # filled below
        notes=notes,
    )
    view.headline = one_sentence(view)
    return view


def from_levels(levels) -> RegimeView:
    """Adapter: build a RegimeView from a gex.levels.Levels object."""
    return classify(
        spot=levels.spot,
        call_wall=levels.call_wall,
        put_wall=levels.put_wall,
        flip=levels.flip,
        regime=levels.regime,
        total_net_gex=levels.total_net_gex,
        magnet=levels.magnet,
        max_pain=levels.max_pain,
    )
