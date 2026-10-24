"""Book-balance view of Nifty GEX. Does not turn the ratio into a pin forecast.

The regime sign and the flip are the same object: call-gamma magnitude minus
put-gamma magnitude (see gex.core.writer_imbalance_net and gex.flip). On NSE
that is which side of the written book is larger. It is not a measurement that
a dealer is long gamma and will fade the tape.

  - POSITIVE = call gamma larger than put gamma (call-heavy book).
  - NEGATIVE = put gamma larger than call gamma (put-heavy book).
  - Spot vs flip is the same ratio, evaluated at the current price.
  - Disagreement between the aggregate sign and the local side is a CONFLICT:
    de-size. Walls are concentration strikes, not floors or ceilings.

Calm versus wild vol is a separate, sign-free |GEX| read. This module does not
emit it.
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
    in_positive_zone: bool | None    # spot >= flip (call side of the balance)
    call_wall: float
    call_wall_dist_pts: int          # call_wall - spot
    put_wall: float
    put_wall_dist_pts: int           # put_wall - spot
    call_wall_role: str              # "call concentration"
    put_wall_role: str               # "put concentration" | "put concentration, below balance" | "unknown"
    magnet: float | None
    max_pain: float | None
    conflict: bool
    bias: str
    headline: str
    mode_label: str = ""             # CALL-HEAVY | PUT-HEAVY | CAUTION | NEUTRAL
    mode_plain: str = ""             # one-sentence plain-English description
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

    # --- wall distances. Roles name concentration, not a floor. -----------
    call_dist = round(call_wall - spot)
    put_dist = round(put_wall - spot)
    call_role = "call concentration"
    if flip is None:
        put_role = "unknown"
    elif put_wall < flip:
        put_role = "put concentration, below balance"
        notes.append(
            f"Put-gamma strike {put_wall:g} is below the call−put balance {flip:g}."
        )
    else:
        put_role = "put concentration"

    # --- conflict: aggregate regime sign vs local flip zone ----------------
    conflict = False
    if in_pos is not None:
        if regime == "POSITIVE" and not in_pos:
            conflict = True
        elif regime == "NEGATIVE" and in_pos:
            conflict = True
    if conflict:
        notes.append(
            f"CONFLICT: aggregate book {regime} disagrees with spot-vs-balance "
            "(de-size; the ratio is not stable)."
        )

    # --- bias: name the book. Do not prescribe a fade or a chase. ---------
    if conflict:
        bias = "book/flip conflict, de-size and wait for confirmation"
    elif regime == "FLAT":
        bias = "call and put gamma balanced"
    elif regime == "POSITIVE" and (in_pos is None or in_pos):
        bias = "call gamma larger than put gamma"
    else:
        bias = "put gamma larger than call gamma"

    # --- plain-language mode (for non-experts) ----------------------------
    mode_label, mode_plain = _plain_mode(
        regime, conflict, in_pos, spot, call_wall, put_wall, flip, magnet
    )

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
        mode_label=mode_label,
        mode_plain=mode_plain,
        notes=notes,
    )
    view.headline = one_sentence(view)
    return view


def _plain_mode(regime, conflict, in_pos, spot, call_wall, put_wall, flip, magnet):
    """Name the call-versus-put book. Vol (calm vs wild) is |GEX|, not this sign."""
    del in_pos, magnet  # spot and the walls carry the geography
    if conflict or flip is None:
        return (
            "CAUTION",
            "The call−put balance is mixed or has no zero-crossing in range. "
            "Read the strikes as a map and wait.",
        )
    if regime == "FLAT":
        return ("NEUTRAL", "Call gamma and put gamma are balanced.")

    if regime == "POSITIVE":
        if put_wall <= spot <= call_wall:
            return (
                "CALL-HEAVY",
                f"Call gamma is larger than put gamma. Spot sits between the put-gamma "
                f"strike {put_wall:,.0f} and the call-gamma strike {call_wall:,.0f}. "
                f"Balance {flip:,.0f}. Concentration map, not a fade signal.",
            )
        if spot > call_wall:
            return (
                "CALL-HEAVY (above call strike)",
                f"Spot is above the call-gamma strike {call_wall:,.0f}. "
                "That strike is where call gamma peaks, not a ceiling dealers must defend.",
            )
        return (
            "CALL-HEAVY (below put strike)",
            f"Spot is below the put-gamma strike {put_wall:,.0f}. "
            "The book is still call-heavy versus put gamma.",
        )

    return (
        "PUT-HEAVY",
        f"Put gamma is larger than call gamma. Balance {flip:,.0f}. "
        f"Put-gamma strike {put_wall:,.0f}, call-gamma strike {call_wall:,.0f}. "
        "This does not by itself mean the downside will accelerate.",
    )


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
