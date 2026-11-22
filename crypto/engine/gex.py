"""Crypto GEX features: convention model + flow proxy, walls, flip, regime.

GEX_k = sum q_i * Γ_i(S) * S^2 * 0.01

Convention (equity-style dealer inventory assumption):
  call_gex = +γ·OI·S²·0.01
  put_gex  = −γ·OI·S²·0.01
  POSITIVE net ⇒ dealers long gamma (dampen); NEGATIVE ⇒ short gamma (amplify).

Flow model: signed customer volume proxy. Foundation uses call−put volume
skew near spot as a stand-in until taker tape is recorded. A GO requires
agreement when ``engine.gex_flow_model`` is on.

Contract size = 1 (coin). Deribit gamma units VERIFY-live in Phase 0.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from crypto.config import get_flag
from crypto.providers.types import OptionChainSnapshot

CONTRACT_SIZE = 1.0


def _sign_label(net: float) -> str:
    if net > 0:
        return "positive"
    if net < 0:
        return "negative"
    return "flat"


def compute_gex_features(snap: OptionChainSnapshot) -> dict[str, Any]:
    spot = snap.spot
    scale = spot * spot * 0.01 * CONTRACT_SIZE

    call_oi: dict[float, float] = defaultdict(float)
    put_oi: dict[float, float] = defaultdict(float)
    call_g_oi: dict[float, float] = defaultdict(float)
    put_g_oi: dict[float, float] = defaultdict(float)
    call_vol: dict[float, float] = defaultdict(float)
    put_vol: dict[float, float] = defaultdict(float)

    for c in snap.contracts:
        k = float(c.strike)
        g_oi = float(c.gamma) * float(c.oi)
        if c.option_type == "C":
            call_oi[k] += c.oi
            call_g_oi[k] += g_oi
            call_vol[k] += c.volume
        else:
            put_oi[k] += c.oi
            put_g_oi[k] += g_oi
            put_vol[k] += c.volume

    strikes = sorted(set(call_oi) | set(put_oi))
    by_strike: list[dict[str, Any]] = []
    convention_total = 0.0

    for k in strikes:
        call_gex = call_g_oi[k] * scale
        put_gex = -put_g_oi[k] * scale
        net = call_gex + put_gex
        # Flow proxy per strike: customer call buys → dealer short calls → −GEX
        flow = -(call_vol[k] - put_vol[k]) * (abs(call_g_oi[k]) + abs(put_g_oi[k])) * 0.001 * scale
        convention_total += net
        by_strike.append(
            {
                "strike": k,
                "call_oi": call_oi[k],
                "put_oi": put_oi[k],
                "call_gex": call_gex,
                "put_gex": put_gex,
                "net_gex": net,
                "flow_gex": flow,
            }
        )

    call_wall = (
        max(by_strike, key=lambda r: abs(r["call_gex"]))["strike"] if by_strike else None
    )
    put_wall = (
        max(by_strike, key=lambda r: abs(r["put_gex"]))["strike"] if by_strike else None
    )
    magnet = (
        max(by_strike, key=lambda r: abs(r["net_gex"]))["strike"] if by_strike else None
    )
    flip = _estimate_flip(by_strike, spot)

    # Near-spot regime (more relevant than whole-chain total for pin/fade)
    near = [r for r in by_strike if abs(r["strike"] - spot) / spot <= 0.03]
    near_net = sum(r["net_gex"] for r in near) if near else convention_total
    conv_sign = _sign_label(near_net if near_net != 0 else convention_total)

    use_flow = get_flag("engine.gex_flow_model", default=True)
    if use_flow:
        # CVD-like options flow near spot: put volume dominance → customers hedging
        # → dealers long gamma (positive) under the equity assumption proxy.
        near_call_vol = sum(call_vol[r["strike"]] for r in near) if near else 0.0
        near_put_vol = sum(put_vol[r["strike"]] for r in near) if near else 0.0
        # Heavier put prints near spot → positive gamma (dealer long); heavier calls → negative
        flow_near = near_put_vol - near_call_vol
        flow_sign = _sign_label(flow_near)
        # If volume near-flat, inherit convention so disagreement is intentional only
        if abs(flow_near) < 1e-6:
            flow_sign = conv_sign
        models_agree = flow_sign == conv_sign and conv_sign != "flat"
    else:
        flow_sign = "disabled"
        models_agree = True

    if conv_sign == "positive":
        regime = "positive_gamma"
    elif conv_sign == "negative":
        regime = "negative_gamma"
    else:
        regime = "uncertain"
    if use_flow and not models_agree:
        regime = "uncertain"

    return {
        "underlying": snap.underlying,
        "spot": spot,
        "flip": flip,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "magnet": magnet,
        "regime": regime,
        "convention_sign": conv_sign,
        "flow_sign": flow_sign,
        "models_agree": models_agree,
        "convention_total": convention_total,
        "near_net": near_net,
        "by_strike": by_strike,
        "mark_iv": snap.mark_iv,
        "dvol": snap.dvol,
        "source": snap.source,
        "computed_at": datetime.now(timezone.utc),
        "contract_size": CONTRACT_SIZE,
        "note": (
            "Convention + flow volume proxy. Not a direction call. "
            "Deribit gamma units VERIFY-live."
        ),
    }


def _estimate_flip(by_strike: list[dict[str, Any]], spot: float) -> float | None:
    if not by_strike:
        return None
    cum = 0.0
    prev_cum = 0.0
    prev_k = by_strike[0]["strike"]
    crossed = None
    for row in by_strike:
        cum += row["net_gex"]
        if prev_cum <= 0 <= cum or prev_cum >= 0 >= cum:
            if cum != prev_cum:
                w = abs(prev_cum) / (abs(prev_cum) + abs(cum))
                crossed = prev_k + w * (row["strike"] - prev_k)
            else:
                crossed = row["strike"]
            # Prefer the crossing nearest spot
            if crossed is not None and abs(crossed - spot) < spot * 0.08:
                return crossed
        prev_cum = cum
        prev_k = row["strike"]
    if crossed is not None:
        return crossed
    return min(by_strike, key=lambda r: abs(r["strike"] - spot))["strike"]
