"""Book-balance view of Nifty GEX (+ optional DEX / hedge-pressure notes).

The regime sign and the flip are the same object: call-gamma magnitude minus
put-gamma magnitude (see gex.core.writer_imbalance_net and gex.flip). On NSE
that is which side of the written book is larger. It is not a measurement that
a dealer is long gamma and will fade the tape.

  - POSITIVE = call gamma larger than put gamma (call-heavy book).
  - NEGATIVE = put gamma larger than call gamma (put-heavy book).
  - Spot vs flip is the same ratio, evaluated at the current price.
  - Disagreement between the aggregate sign and the local side is a CONFLICT:
    de-size. Walls are concentration strikes, not floors or ceilings.

Gated hedge-pressure (option A): a second line when spot is near a named wall
or magnet. Conditional under short_call_short_put. Never "dealers will defend."
DEX sign alone never triggers that line.

Calm versus wild vol is a separate, sign-free |GEX| read. This module does not
emit it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from regime.playbook import hedge_pressure_sentence, one_sentence


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
    total_net_dex: float | None = None
    call_delta_wall: float | None = None
    put_delta_wall: float | None = None
    dex_conflict: bool = False
    hedge_pressure: str | None = None
    hedge_pressure_strike: float | None = None


def classify(
    spot: float,
    call_wall: float,
    put_wall: float,
    flip: float | None,
    regime: str,
    total_net_gex: float,
    magnet: float | None = None,
    max_pain: float | None = None,
    *,
    total_net_dex: float | None = None,
    call_delta_wall: float | None = None,
    put_delta_wall: float | None = None,
    proximity_pts: float = 100.0,
    cover_known: bool = False,
    cover_frac: float | None = None,
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

    # --- GEX vs DEX inventory conflict (de-size; not a direction call) -----
    # Call-heavy gamma books tend to carry negative short-call DEX; put-heavy
    # books tend to carry positive short-put DEX. Opposite pairing → caution.
    dex_conflict = False
    if total_net_dex is not None and total_net_dex != 0 and regime in ("POSITIVE", "NEGATIVE"):
        if regime == "POSITIVE" and total_net_dex > 0:
            dex_conflict = True
        elif regime == "NEGATIVE" and total_net_dex < 0:
            dex_conflict = True
    if dex_conflict:
        notes.append(
            "CONFLICT: GEX regime and net DEX signs disagree under short_call_short_put "
            "(de-size; inventory map is mixed)."
        )

    # --- bias: name the book. Do not prescribe a fade or a chase. ---------
    if conflict:
        bias = "book/flip conflict, de-size and wait for confirmation"
    elif dex_conflict:
        bias = "GEX/DEX conflict, de-size and wait for confirmation"
    elif regime == "FLAT":
        bias = "call and put gamma balanced"
    elif regime == "POSITIVE" and (in_pos is None or in_pos):
        bias = "call gamma larger than put gamma"
    else:
        bias = "put gamma larger than call gamma"

    # --- gated hedge-pressure (option A). Never from DEX sign alone. ------
    hp_strike = _nearest_pressure_strike(
        spot, call_wall, put_wall, magnet, proximity_pts=proximity_pts,
    )
    hedge_pressure = None
    if hp_strike is not None:
        hedge_pressure = hedge_pressure_sentence(
            hp_strike, cover_known=cover_known, cover_frac=cover_frac,
        )
        notes.append(f"Hedge-pressure gate open at {hp_strike:g} (≤{proximity_pts:g} pts).")

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
        total_net_dex=total_net_dex,
        call_delta_wall=call_delta_wall,
        put_delta_wall=put_delta_wall,
        dex_conflict=dex_conflict,
        hedge_pressure=hedge_pressure,
        hedge_pressure_strike=hp_strike,
    )
    view.headline = one_sentence(view)
    assert "dealers will defend" not in (view.headline or "").lower()
    assert "dealers will defend" not in (view.hedge_pressure or "").lower()
    return view


def _nearest_pressure_strike(
    spot: float,
    call_wall: float,
    put_wall: float,
    magnet: float | None,
    *,
    proximity_pts: float,
) -> float | None:
    """Closest named concentration strike within proximity_pts, else None.

    Requires a real positive strike. Near-book-expired walls at 0 do not gate.
    """
    candidates: list[float] = []
    for k in (call_wall, put_wall, magnet):
        if k is None:
            continue
        kf = float(k)
        if kf > 0:
            candidates.append(kf)
    if not candidates or proximity_pts <= 0:
        return None
    nearest = min(candidates, key=lambda k: abs(k - spot))
    if abs(nearest - spot) <= float(proximity_pts):
        return nearest
    return None


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
                "That strike is where call gamma peaks — not an unconditional defend level.",
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


def from_levels(levels, *, cover_known: bool = False, cover_frac: float | None = None) -> RegimeView:
    """Adapter: build a RegimeView from a gex.levels.Levels object."""
    from config import strike_step_for

    symbol = str(getattr(levels, "underlying", None) or "NIFTY")
    try:
        step = float(strike_step_for(symbol))
    except Exception:
        step = 50.0
    proximity = max(2.0 * step, 100.0)

    return classify(
        spot=levels.spot,
        call_wall=levels.call_wall,
        put_wall=levels.put_wall,
        flip=levels.flip,
        regime=levels.regime,
        total_net_gex=levels.total_net_gex,
        magnet=levels.magnet,
        max_pain=levels.max_pain,
        total_net_dex=getattr(levels, "total_net_dex", None),
        call_delta_wall=getattr(levels, "call_delta_wall", None),
        put_delta_wall=getattr(levels, "put_delta_wall", None),
        proximity_pts=proximity,
        cover_known=cover_known,
        cover_frac=cover_frac,
    )
