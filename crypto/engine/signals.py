"""Rules-only GO / NO-GO card builder (foundation).

A GO fires only when gamma regime, orderflow, and no hard veto line up.
Numbers are starting parameters — not calibrated edge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from crypto.config import get_flag
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
from crypto.providers.mock.fixtures import load_fixture
from crypto.providers.types import PerpSnapshot


def build_signal_card(
    gex: dict[str, Any],
    perp: PerpSnapshot,
    *,
    news_blackout: bool = False,
    chain_fetched_at: datetime | None = None,
    live_signal_path: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    vetoes: list[str] = []

    if not get_flag("engine.signal_rules", default=True):
        return _nogo("signal rules flag off", gex, perp, now, ["engine.signal_rules disabled"])

    if not gex.get("models_agree", False):
        vetoes.append("convention/flow GEX regime disagreement")

    if gex.get("regime") == "uncertain":
        vetoes.append("regime uncertain")

    # Staleness: live signal path uses the design-plan 2s hard veto; mock stays loose.
    if live_signal_path:
        chain_age = age_seconds(chain_fetched_at, now=now)
        if chain_fetched_at is not None and is_stale(
            chain_fetched_at, threshold_s=STALE_THRESHOLD_S, now=now
        ):
            vetoes.append(f"chain data stale ({chain_age:.1f}s > {STALE_THRESHOLD_S}s)")
        perp_age = age_seconds(perp.fetched_at, now=now)
        if is_stale(perp.fetched_at, threshold_s=STALE_THRESHOLD_S, now=now):
            vetoes.append(f"perp data stale ({perp_age:.1f}s > {STALE_THRESHOLD_S}s)")
    else:
        age_s = (now - perp.fetched_at).total_seconds() if perp.fetched_at else 0
        if age_s > 30:
            vetoes.append(f"perp data stale ({age_s:.0f}s)")

    if news_blackout:
        vetoes.append("news blackout window")

    # Prefer fixture template when mock source for polished demo cards
    if "mock" in (gex.get("source") or "") or "fixture" in (gex.get("source") or "") or "stub" in (gex.get("source") or ""):
        tmpl = load_fixture().get("signal_template") or {}
        if vetoes:
            return _nogo("hard veto", gex, perp, now, vetoes, tmpl)
        if gex.get("models_agree") and gex.get("regime") == "positive_gamma":
            valid_until = now + timedelta(minutes=int(tmpl.get("valid_minutes") or 25))
            return {
                "status": "GO",
                "setup": tmpl.get("setup") or "positive_gamma_fade",
                "instrument": tmpl.get("instrument") or perp.symbol,
                "side": tmpl.get("side") or "sell",
                "entry_zone": tmpl.get("entry_zone") or [perp.mark, perp.mark * 1.001],
                "target": tmpl.get("target"),
                "stop": tmpl.get("stop"),
                "size_cap_usd": tmpl.get("size_cap_usd") or 2500,
                "score": int(tmpl.get("score") or 70),
                "reasons": list(tmpl.get("reasons") or []),
                "invalidation": tmpl.get("invalidation") or "",
                "valid_until": valid_until,
                "created_at": now,
                "veto_reasons": [],
                "model_version": "rules-v0",
                "mode": "advisory",
                "gex_regime": gex.get("regime"),
                "spot": gex.get("spot"),
                "flip": gex.get("flip"),
                "call_wall": gex.get("call_wall"),
                "put_wall": gex.get("put_wall"),
            }

    # Live-ish heuristic path
    spot = float(gex["spot"])
    flip = gex.get("flip") or spot
    call_wall = gex.get("call_wall")
    put_wall = gex.get("put_wall")
    atr_proxy = spot * 0.004  # ~0.4% foundation ATR stand-in

    setup = None
    side = None
    entry = None
    target = None
    stop = None
    reasons: list[str] = []
    score = 50

    if gex.get("regime") == "positive_gamma" and gex.get("models_agree"):
        stretch = abs(perp.mark - (gex.get("magnet") or spot))
        if stretch > 1.5 * atr_proxy and perp.cvd_15m * (perp.mark - spot) < 0:
            setup = "positive_gamma_fade"
            side = "sell" if perp.mark > spot else "buy"
            entry = [perp.mark * 0.999, perp.mark * 1.001]
            target = gex.get("magnet") or spot
            stop = (call_wall if side == "sell" else put_wall) or (perp.mark + (1 if side == "buy" else -1) * atr_proxy)
            reasons = [
                "Convention and flow GEX agree positive around spot.",
                f"Stretch {stretch / atr_proxy:.1f}× ATR from magnet with CVD divergence.",
                "Fade toward heaviest-gamma strike; stop beyond nearer wall.",
            ]
            score = 68
    elif gex.get("regime") == "negative_gamma" and gex.get("models_agree"):
        if abs(perp.cvd_5m) > 50 and perp.book_imbalance * perp.cvd_5m > 0:
            setup = "negative_gamma_breakout"
            side = "buy" if perp.cvd_5m > 0 else "sell"
            entry = [perp.mark, perp.mark]
            target = call_wall if side == "buy" else put_wall
            stop = flip
            reasons = [
                "Negative gamma regime — dealers amplify moves.",
                "Perp CVD and book imbalance confirm direction.",
                "Target next wall; invalidate back through flip.",
            ]
            score = 65

    if vetoes or setup is None:
        if setup is None and not vetoes:
            vetoes.append("no setup conditions met")
        return _nogo("no go", gex, perp, now, vetoes)

    return {
        "status": "GO",
        "setup": setup,
        "instrument": perp.symbol,
        "side": side,
        "entry_zone": entry,
        "target": target,
        "stop": stop,
        "size_cap_usd": 2500,
        "score": score,
        "reasons": reasons,
        "invalidation": f"Stop {stop} or regime disagreement",
        "valid_until": now + timedelta(minutes=20),
        "created_at": now,
        "veto_reasons": [],
        "model_version": "rules-v0",
        "mode": "advisory",
        "gex_regime": gex.get("regime"),
        "spot": spot,
        "flip": flip,
        "call_wall": call_wall,
        "put_wall": put_wall,
    }


def _nogo(
    why: str,
    gex: dict[str, Any],
    perp: PerpSnapshot,
    now: datetime,
    vetoes: list[str],
    tmpl: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "NO-GO",
        "setup": (tmpl or {}).get("setup") or "none",
        "instrument": perp.symbol,
        "side": "",
        "entry_zone": [],
        "target": None,
        "stop": None,
        "size_cap_usd": 0,
        "score": 0,
        "reasons": [why, *vetoes[:3]],
        "invalidation": "",
        "valid_until": now + timedelta(minutes=5),
        "created_at": now,
        "veto_reasons": vetoes,
        "model_version": "rules-v0",
        "mode": "advisory",
        "gex_regime": gex.get("regime"),
        "spot": gex.get("spot"),
        "flip": gex.get("flip"),
        "call_wall": gex.get("call_wall"),
        "put_wall": gex.get("put_wall"),
    }
