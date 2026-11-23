# Phase 0 feed field findings

Hand-checked against public docs and live samples (Nov 2026).

## Deribit

| Concern | Field | Meaning |
| --- | --- | --- |
| Aggressor / taker side | `direction` | `"buy"` \| `"sell"` — **trade direction of the taker** |
| Gamma | `greeks.gamma` | Standard Black-Scholes γ = ∂Δ/∂S per **$1** underlying move (no Deribit rescale) |
| Mark IV | `mark_iv` | **Percent** (e.g. `67.67` → 67.67%) |

GEX scaling used in-engine: `Γ × S² × 0.01 × contract_size` with `contract_size = 1` coin.

WS: `wss://www.deribit.com/ws/api/v2`  
Channels used: `ticker.{instrument}.100ms`, `trades.option.BTC.100ms`.

## Delta Exchange India

| Concern | Field | Meaning |
| --- | --- | --- |
| WS trades aggressor | `r` (buyer_role) | `"t"` = buyer was taker → aggressor **buy**; `"m"` = buyer was maker → aggressor **sell** |
| REST trades aggressor | `side` | `"buy"` \| `"sell"` — aggressor |
| WS ticker gamma | `d[].g[1]` | Compact greeks array: `[delta, gamma, rho, theta, vega]` |

WS: `wss://public-socket.india.delta.exchange`  
Channels used: `ticker`, `ob_l1`, `trades` (symbols e.g. `BTCUSD`).

## Glassnode hand-check (do not block on perfect match)

1. Enable live Deribit (`CRYPTO_LIVE_FEEDS=1 CRYPTO_FLAG_FEEDS_DERIBIT=1`), open cockpit GEX map.
2. Note `flip`, `call_wall`, `put_wall`, spot from `GET /api/gex`.
3. Open Glassnode’s public BTC options GEX / gamma map chart for the same session.
4. Accept if flip and walls land within about **one strike** of Glassnode. Regime sign may differ when the flow model disagrees with convention — that is intentional NO-GO, not a feed bug.
