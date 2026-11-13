"""Staged pre-open opening watch — confirm-only map before Trade Ideas.

Built from a discovered auction regime + sealed walls. No session VWAP, no
Sentinel tape, no generate_trade_recommendations. Continuous session still
starts at is_market_open (09:15); this card is SUPERSEDED then.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import config

log = logging.getLogger(__name__)

# Regimes that allow a CE/PE structure candidate after a post-open hold.
_CE_STRUCTURE_REGIMES = frozenset({"CALL_WALL_BREACH_SQUEEZE", "GAP_UP_RUNAWAY"})
_PE_STRUCTURE_REGIMES = frozenset({"PUT_WALL_BREAKDOWN_CASCADE", "GAP_DOWN_TRAP"})
# Touch / map-only: trigger text only, never a priced structure from the open alone.
_HOLD_FAIL_REGIMES = frozenset({"SHORT_GAMMA_WALL_TEST", "PUT_WALL_BREAKDOWN_TEST"})
_MAP_ONLY_REGIMES = frozenset({
    "GAP_UP_INTO_POS_GAMMA",
    "GAP_DOWN_INTO_POS_GAMMA",
    "AUCTION_BALANCED",
    "MODERATE_DRIFT",
})


@dataclass
class PreOpenStructure:
    name: str
    side: str  # CE | PE
    buy_strike: float
    short_strike: float
    width: float
    lot_size: int
    max_loss_inr: float | None
    premium_note: str  # "priced" | "unpriced"
    net_debit_pts: float | None = None
    expiry_note: str = ""
    rationale: str = ""
    # Spot + premium target for the recommended (long) strike — estimate, not Ideas T1.
    target_spot: float | None = None
    target_buy_pts: float | None = None  # long-leg premium at target_spot
    target_net_pts: float | None = None  # vertical value at target_spot
    target_note: str = ""


@dataclass
class PreOpenWatch:
    status: str  # STAGED | SUPERSEDED | UNAVAILABLE
    symbol: str
    regime: str
    favored_play: str
    side_hint: str  # CE | PE | NONE
    title: str
    trigger: str
    invalidation: str
    confirm_after: str
    caution: str
    structure: dict | None = None
    first_5m_estimate: dict | None = None
    open_txt: str = ""
    map_strip: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _inr(v: float) -> str:
    if v <= 0:
        return "unavailable"
    return f"₹{v:,.0f}"


def _estimate_first_5m(
    open_px: float,
    atm_iv: float | None,
    *,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
) -> dict | None:
    """Symmetric ±1σ opening-noise envelope for the first 5-minute candle.

    Uses the shared trading-session clock: σ₅ = open · IV · √(5 / (375 · 252)).
    Not a target or stop — estimate only. Missing IV → None (never invent vol).
    """
    if open_px <= 0 or atm_iv is None:
        return None
    try:
        iv = float(atm_iv)
    except (TypeError, ValueError):
        return None
    if iv <= 0:
        return None
    try:
        from target_engine.time_utils import horizon_sigma
        sigma = float(horizon_sigma(open_px, iv, 5, floor=0.0))
    except Exception as exc:
        log.debug("first_5m horizon_sigma failed: %s", exc)
        return None
    if sigma <= 0:
        return None

    high = round(open_px + sigma, 1)
    low = round(open_px - sigma, 1)
    sigma_pts = round(sigma, 1)

    notes: list[str] = [
        "Opening-noise estimate (±1σ IV over 5m), not a forecast. Confirm on the live 09:15–09:20 print.",
    ]
    cw = float(call_wall or 0)
    pw = float(put_wall or 0)
    if cw > 0 and high >= cw:
        notes.append(
            f"Call-gamma {_inr(cw)} sits inside/at the estimated high — map location, not a ceiling."
        )
    if pw > 0 and low <= pw:
        notes.append(
            f"Put-gamma {_inr(pw)} sits inside/at the estimated low — map location, not a floor."
        )

    return {
        "high": high,
        "low": low,
        "sigma_pts": sigma_pts,
        "method": "iv_sigma_5m",
        "note": " ".join(notes),
    }


def _round_strike(x: float, step: int) -> float:
    if step <= 0:
        return round(x)
    return float(int(round(x / step) * step))


def _beyond(wall: float, step: int, side: int) -> float:
    """One listed strike outside the gamma concentration. +1 above, -1 below."""
    return _round_strike(wall, step) + side * step


def _side_and_title(regime: str) -> tuple[str, str, bool]:
    """Return (side_hint, title, allow_structure)."""
    r = (regime or "").upper()
    if r in _CE_STRUCTURE_REGIMES:
        return "CE", "Staged CE watch after open hold", True
    if r in _PE_STRUCTURE_REGIMES:
        return "PE", "Staged PE watch after open hold", True
    if r == "SHORT_GAMMA_WALL_TEST":
        return "NONE", "Watch the call-gamma strike (hold/fail)", False
    if r == "PUT_WALL_BREAKDOWN_TEST":
        return "NONE", "Watch the put-gamma strike (hold/fail)", False
    if r in _MAP_ONLY_REGIMES:
        titles = {
            "GAP_UP_INTO_POS_GAMMA": "Map only — no fade from the open",
            "GAP_DOWN_INTO_POS_GAMMA": "Map only — no bounce from the open",
            "AUCTION_BALANCED": "Flat open — no book trade",
            "MODERATE_DRIFT": "Moderate gap — map only",
        }
        return "NONE", titles.get(r, "Map only"), False
    return "NONE", "Opening map", False


def _trigger_invalidation(
    regime: str,
    *,
    open_px: float,
    cw: float,
    pw: float,
    flip: float,
    prev: float,
) -> tuple[str, str]:
    r = (regime or "").upper()
    o, c, p, f, pr = _inr(open_px), _inr(cw), _inr(pw), _inr(flip), _inr(prev)

    if r == "CALL_WALL_BREACH_SQUEEZE":
        return (
            f"After 09:15, first 3-minute candle holds above call-gamma {c}.",
            f"Close back below {c}. Do not fade because the label says squeeze.",
        )
    if r == "SHORT_GAMMA_WALL_TEST":
        return (
            f"Watch hold versus reject at call-gamma {c} in the first minutes after 09:15.",
            f"Clean reclaim above or reject below {c} — size only after that print.",
        )
    if r == "PUT_WALL_BREAKDOWN_CASCADE":
        return (
            f"After 09:15, first 3-minute candle holds below put-gamma {p}.",
            f"Close back above {p}.",
        )
    if r == "PUT_WALL_BREAKDOWN_TEST":
        return (
            f"Watch hold versus reclaim at put-gamma {p} after 09:15.",
            f"Clean reclaim above {p}, or clean extension below it.",
        )
    if r == "GAP_UP_RUNAWAY":
        return (
            f"First continuous prints hold open {o} (still under call-gamma {c}).",
            f"Reclaim of prior close {pr} that fills the gap against the open.",
        )
    if r == "GAP_DOWN_TRAP":
        return (
            f"First continuous prints hold open {o} (still above put-gamma {p}).",
            f"Reclaim of prior close {pr} that fills the gap against the open.",
        )
    if r == "GAP_UP_INTO_POS_GAMMA":
        return (
            f"Do not fade to {pr}. Map CW {c} / PW {p}; wait for a later hold/fail.",
            f"A later hold/fail at {c} or {p} — not a reclaim of {pr} alone.",
        )
    if r == "GAP_DOWN_INTO_POS_GAMMA":
        return (
            f"Do not buy a bounce to {pr}. Map PW {p}; wait for a later hold/fail.",
            f"A later hold/fail at {p} or {c} — not a reclaim of {pr} alone.",
        )
    if r == "AUCTION_BALANCED":
        return (
            f"Let the first 15-minute range form around {o} before inventing direction.",
            f"Sustained opening-range break with expanding volume toward {c} or {p}.",
        )
    if r == "MODERATE_DRIFT":
        return (
            f"Map {o} vs flip {f}; wait for VWAP/tape alignment after 09:20.",
            f"Later breach of nearer gamma strike ({c} / {p}) against the working thesis.",
        )
    return (
        "Locate the open on the book. Confirm after 09:15.",
        "Confirm against the live walls before sizing.",
    )


def _target_spot_for_structure(
    *,
    side: str,
    open_px: float,
    short: float,
    atm_iv: float | None,
) -> tuple[float | None, str]:
    """Early spot target: open ± σ₅, capped at the short strike (vertical max-value map)."""
    if open_px <= 0 or short <= 0:
        return None, ""
    sigma = None
    if atm_iv is not None and float(atm_iv) > 0:
        try:
            from target_engine.time_utils import horizon_sigma
            sigma = float(horizon_sigma(open_px, float(atm_iv), 5, floor=0.0))
        except Exception:
            sigma = None

    if side == "CE":
        raw = open_px + sigma if sigma is not None else short
        tgt = min(float(short), float(raw))
        note = (
            f"Spot target {_inr(tgt)} = min(short {short:.0f}, open+σ₅). "
            "Illustrative only — confirm after the live 5m print."
        )
        return round(tgt, 1), note
    if side == "PE":
        raw = open_px - sigma if sigma is not None else short
        tgt = max(float(short), float(raw))
        note = (
            f"Spot target {_inr(tgt)} = max(short {short:.0f}, open−σ₅). "
            "Illustrative only — confirm after the live 5m print."
        )
        return round(tgt, 1), note
    return None, ""


def _illustrative_vertical(
    *,
    symbol: str,
    side: str,
    open_px: float,
    cw: float,
    pw: float,
    atm_iv: float | None,
    dte_days: float | None,
    expiry_tag: str = "",
) -> dict | None:
    """Defined-risk debit vertical; short strike beyond the gamma concentration."""
    idx = config.INDICES.get(config.index_key(symbol))
    if idx is None or open_px <= 0:
        return None
    step = int(idx.strike_step)
    lot = int(idx.lot_size)
    width = float(step)  # one step vertical

    if side == "CE":
        if cw <= 0:
            return None
        buy = _round_strike(cw, step)  # at/near call concentration
        short = _beyond(cw, step, +1)
        name = "Illustrative bull call debit vertical"
        rationale = (
            f"Long {buy:.0f} CE / short {short:.0f} CE — short beyond call-gamma, never on the magnet."
        )
        is_call = True
    elif side == "PE":
        if pw <= 0:
            return None
        buy = _round_strike(pw, step)
        short = _beyond(pw, step, -1)
        name = "Illustrative bear put debit vertical"
        rationale = (
            f"Long {buy:.0f} PE / short {short:.0f} PE — short beyond put-gamma, never on the magnet."
        )
        is_call = False
    else:
        return None

    net_debit = None
    premium_note = "unpriced"
    max_loss_inr = width * lot  # ceiling if debit unknown; true max is net debit × lot
    target_spot, target_note = _target_spot_for_structure(
        side=side, open_px=open_px, short=short, atm_iv=atm_iv,
    )
    target_buy_pts = None
    target_net_pts = None

    if atm_iv is not None and float(atm_iv) > 0 and dte_days is not None and float(dte_days) >= 0:
        try:
            from analytics.trade_ideas import _approx_bs_price_and_delta
            iv = float(atm_iv)
            dte = max(float(dte_days), 0.25)
            # ~5 trading minutes of session time off DTE for the early target reprice.
            dte_tgt = max(0.25, dte - (5.0 / 375.0))
            buy_px, _ = _approx_bs_price_and_delta(open_px, buy, is_call, dte_days=dte, iv=iv)
            short_px, _ = _approx_bs_price_and_delta(open_px, short, is_call, dte_days=dte, iv=iv)
            if buy_px is not None and short_px is not None and buy_px > 0:
                net_debit = max(0.5, round(float(buy_px) - float(short_px), 1))
                max_loss_inr = round(net_debit * lot, 0)
                premium_note = "priced"

            if target_spot is not None and target_spot > 0:
                buy_tgt, _ = _approx_bs_price_and_delta(
                    target_spot, buy, is_call, dte_days=dte_tgt, iv=iv,
                )
                short_tgt, _ = _approx_bs_price_and_delta(
                    target_spot, short, is_call, dte_days=dte_tgt, iv=iv,
                )
                if buy_tgt is not None and buy_tgt > 0:
                    target_buy_pts = round(float(buy_tgt), 1)
                if buy_tgt is not None and short_tgt is not None:
                    target_net_pts = max(0.0, round(float(buy_tgt) - float(short_tgt), 1))
                    if net_debit is not None and target_net_pts is not None:
                        target_note = (
                            f"{target_note} Long {buy:.0f} ~₹{target_buy_pts}; "
                            f"vertical ~₹{target_net_pts} (entry debit ~₹{net_debit})."
                        )
        except Exception as exc:
            log.debug("pre_open_watch BS price failed: %s", exc)

    struct = PreOpenStructure(
        name=name,
        side=side,
        buy_strike=buy,
        short_strike=short,
        width=width,
        lot_size=lot,
        max_loss_inr=max_loss_inr,
        premium_note=premium_note,
        net_debit_pts=net_debit,
        expiry_note=expiry_tag or "Nearest listed expiry — exit before expiry; you confirm.",
        rationale=rationale,
        target_spot=target_spot,
        target_buy_pts=target_buy_pts,
        target_net_pts=target_net_pts,
        target_note=target_note,
    )
    return asdict(struct)


def build_pre_open_watch(
    pm: dict | None,
    *,
    symbol: str = "NIFTY",
    market_open: bool | None = None,
    atm_iv: float | None = None,
    dte_days: float | None = None,
    expiry_tag: str = "",
) -> dict:
    """Build the staged opening watch from a pre_market state dict.

    Never invents VWAP. Never calls the Ideas engine.
    """
    sym = config.index_key(symbol)
    if market_open is None:
        market_open = bool(config.is_market_open())

    empty = PreOpenWatch(
        status="UNAVAILABLE",
        symbol=sym,
        regime="",
        favored_play="MAP ONLY",
        side_hint="NONE",
        title="Awaiting indicative open",
        trigger="No staged watch until discovery locks the open.",
        invalidation="N/A",
        confirm_after="Confirm only after a post-09:15 hold/fail print.",
        caution="No auto-execution. Decision support only.",
    )

    if not pm or not isinstance(pm, dict):
        return empty.to_dict()

    discovered = bool(pm.get("is_discovered") and float(pm.get("discovered_spot") or 0) > 0)
    if not discovered:
        return empty.to_dict()

    regime = str(pm.get("gap_regime") or "").upper()
    favored = str(pm.get("favored_play") or "MAP ONLY")
    open_px = float(pm.get("discovered_spot") or 0)
    cw = float(pm.get("call_wall") or 0)
    pw = float(pm.get("put_wall") or 0)
    flip = float(pm.get("gamma_flip") or 0)
    prev = float(pm.get("prev_close") or 0)

    side_hint, title, allow_structure = _side_and_title(regime)
    fav_u = favored.upper()
    if "MAP ONLY" in fav_u or "NO FADE" in fav_u or "NO BOUNCE" in fav_u or "NO BOOK" in fav_u:
        allow_structure = False
    trigger, invalidation = _trigger_invalidation(
        regime, open_px=open_px, cw=cw, pw=pw, flip=flip, prev=prev,
    )

    structure = None
    if allow_structure and side_hint in ("CE", "PE"):
        structure = _illustrative_vertical(
            symbol=sym,
            side=side_hint,
            open_px=open_px,
            cw=cw,
            pw=pw,
            atm_iv=atm_iv,
            dte_days=dte_days,
            expiry_tag=expiry_tag,
        )

    first_5m = _estimate_first_5m(open_px, atm_iv, call_wall=cw, put_wall=pw)

    status = "SUPERSEDED" if market_open else "STAGED"
    if market_open:
        title = f"{title} · superseded by session Ideas"
        confirm_after = "Continuous session is open — Trade Ideas own the card. This watch is historical context only."
    else:
        confirm_after = (
            "Confirm only after a post-09:15 hold/fail. Do not stage on the auction print alone."
        )

    watch = PreOpenWatch(
        status=status,
        symbol=sym,
        regime=regime,
        favored_play=favored,
        side_hint=side_hint,
        title=title,
        trigger=trigger,
        invalidation=invalidation,
        confirm_after=confirm_after,
        caution=(
            "No auto-execution. MAP ONLY regimes never invent a fade/bounce structure. "
            "Short strike (if any) sits beyond the gamma concentration. "
            "The 5m high/low is an IV noise estimate, not a target or stop."
        ),
        structure=structure,
        first_5m_estimate=first_5m,
        open_txt=_inr(open_px),
        map_strip={
            "open": open_px,
            "call_wall": cw,
            "put_wall": pw,
            "gamma_flip": flip,
            "prev_close": prev,
            "gap_pts": float(pm.get("gap_pts") or 0),
            "gap_pct": float(pm.get("gap_pct") or 0),
        },
    )
    return watch.to_dict()
