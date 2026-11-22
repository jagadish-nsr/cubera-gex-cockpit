"""Crypto GEX features: convention + flow models, walls, flip, regime.

GEX_k = sum q_i * Γ_i(S) * S^2 * 0.01

Convention (equity-style dealer inventory assumption):
  q_call = +OI, q_put = −OI
  call_gex = +γ·OI·S²·0.01
  put_gex  = −γ·OI·S²·0.01
  POSITIVE net ⇒ dealers long gamma (dampen); NEGATIVE ⇒ short gamma (amplify).

Flow model:
  q_i = −cumulative customer-signed volume (Deribit trades.direction).
  When no signed tape is available, fall back to a volume-skew proxy near spot
  so the agreement gate still has a reading.

A GO requires agreement when ``engine.gex_flow_model`` is on.

Contract size = 1 (coin). Deribit ``greeks.gamma`` is standard BS ∂Δ/∂S
per $1 underlying (Phase 0 confirmed vs docs); mark_iv is percent.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from crypto.config import get_flag
from crypto.engine.flow_inventory import FlowInventory
from crypto.providers.types import OptionChainSnapshot

CONTRACT_SIZE = 1.0
NEAR_SPOT_BAND_PCT = 0.03


def _sign_label(net: float) -> str:
    if net > 0:
        return "positive"
    if net < 0:
        return "negative"
    return "flat"


def compute_gex_features(
    snap: OptionChainSnapshot,
    *,
    flow_inventory: Optional[FlowInventory] = None,
) -> dict[str, Any]:
    spot = snap.spot
    scale = spot * spot * 0.01 * CONTRACT_SIZE

    call_oi: dict[float, float] = defaultdict(float)
    put_oi: dict[float, float] = defaultdict(float)
    call_g_oi: dict[float, float] = defaultdict(float)
    put_g_oi: dict[float, float] = defaultdict(float)
    call_vol: dict[float, float] = defaultdict(float)
    put_vol: dict[float, float] = defaultdict(float)
    # Per-instrument gamma for flow q_i weighting
    gamma_by_inst: dict[str, float] = {}

    for c in snap.contracts:
        k = float(c.strike)
        g_oi = float(c.gamma) * float(c.oi)
        gamma_by_inst[c.instrument] = float(c.gamma)
        if c.option_type == "C":
            call_oi[k] += c.oi
            call_g_oi[k] += g_oi
            call_vol[k] += c.volume
        else:
            put_oi[k] += c.oi
            put_g_oi[k] += g_oi
            put_vol[k] += c.volume

    # Aggregate flow q_i × Γ by strike
    flow_call_g: dict[float, float] = defaultdict(float)
    flow_put_g: dict[float, float] = defaultdict(float)
    flow_mode = "volume_proxy"
    if flow_inventory is not None and flow_inventory.total_trades > 0:
        flow_mode = "signed_tape"
        for name, row in flow_inventory.by_instrument.items():
            g = gamma_by_inst.get(name)
            if g is None:
                # Instrument not in current chain band — still attribute by strike
                # using a tiny gamma so tape is not silently dropped from regime.
                g = 1e-8
            contrib = row.q_i * g
            if row.option_type == "C":
                flow_call_g[row.strike] += contrib
            else:
                flow_put_g[row.strike] += contrib

    strikes = sorted(set(call_oi) | set(put_oi) | set(flow_call_g) | set(flow_put_g))
    by_strike: list[dict[str, Any]] = []
    convention_total = 0.0
    flow_total = 0.0

    for k in strikes:
        call_gex = call_g_oi[k] * scale
        put_gex = -put_g_oi[k] * scale
        net = call_gex + put_gex
        if flow_mode == "signed_tape":
            # q already signed; call contrib and put contrib both enter as q·Γ·scale
            flow = (flow_call_g[k] + flow_put_g[k]) * scale
        else:
            # Volume proxy: customer call buys → dealer short calls → −GEX
            flow = (
                -(call_vol[k] - put_vol[k])
                * (abs(call_g_oi[k]) + abs(put_g_oi[k]))
                * 0.001
                * scale
            )
        convention_total += net
        flow_total += flow
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

    near = [r for r in by_strike if abs(r["strike"] - spot) / spot <= NEAR_SPOT_BAND_PCT]
    near_net = sum(r["net_gex"] for r in near) if near else convention_total
    conv_sign = _sign_label(near_net if near_net != 0 else convention_total)

    use_flow = get_flag("engine.gex_flow_model", default=True)
    if use_flow:
        if flow_mode == "signed_tape":
            near_flow = sum(r["flow_gex"] for r in near) if near else flow_total
            flow_sign = _sign_label(near_flow)
            if abs(near_flow) < 1e-12:
                flow_sign = conv_sign
        else:
            # CVD-like options volume proxy near spot
            near_call_vol = sum(call_vol[r["strike"]] for r in near) if near else 0.0
            near_put_vol = sum(put_vol[r["strike"]] for r in near) if near else 0.0
            # Heavier put prints near spot → positive gamma lean under equity proxy
            flow_near = near_put_vol - near_call_vol
            flow_sign = _sign_label(flow_near)
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

    dist_flip_pct = None
    if flip is not None and spot:
        dist_flip_pct = (spot - flip) / spot * 100.0

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
        "flow_total": flow_total,
        "near_net": near_net,
        "dist_flip_pct": dist_flip_pct,
        "by_strike": by_strike,
        "mark_iv": snap.mark_iv,
        "dvol": snap.dvol,
        "source": snap.source,
        "computed_at": datetime.now(timezone.utc),
        "contract_size": CONTRACT_SIZE,
        "flow_mode": flow_mode if use_flow else "disabled",
        "flow_inventory": (
            flow_inventory.summary() if flow_inventory is not None else None
        ),
        "note": (
            "Convention + flow (signed tape when available, else volume proxy). "
            "Not a direction call. Deribit greeks.gamma = BS ∂Δ/∂S per $1 "
            "underlying; mark_iv is percent."
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
            if crossed is not None and abs(crossed - spot) < spot * 0.08:
                return crossed
        prev_cum = cum
        prev_k = row["strike"]
    if crossed is not None:
        return crossed
    return min(by_strike, key=lambda r: abs(r["strike"] - spot))["strike"]
