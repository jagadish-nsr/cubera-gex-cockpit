# Sentinel OrderFlow Recommendation — Field Guide

Decision-support only. Human confirms. No auto-execution of broker orders.

## What opens a setup

A recommendation is minted only when **all** of the following hold:

1. Live futures tick path has armed the listener (`on_tick`), market is open.
2. Session sync / history / demo `save_signal(..., emit_recommendations=False)` does **not** mint.
3. Signal bias is LONG or SHORT; score ≥ 65; type is continuation / absorption / exhaustion / trapped.
4. Symbol is NIFTY, BANKNIFTY, or SENSEX (exact match — not substring).
5. Spot is live and not a display placeholder.
6. Signal `invalidation_level` is on the correct side of spot.
7. Signal `target_1_pts` is positive (never invented).
8. Live ATM IV is available from the chain cache.
9. Live option LTP exists for the ATM strike (never Black–Scholes invent).

Refusals are counted in metrics under `refusals`.

## Levels

| Field | Meaning |
| --- | --- |
| `spot_entry` | Cash/index spot at trigger |
| `spot_target` | Spot ± `target_1_pts` from the signal |
| `spot_invalidation` | Signal invalidation; stack keeps the **protective** level |
| `option_entry` | Live ATM premium |
| `option_target` | Target Engine reprice at live DTE + ATM IV |
| `option_sl` | **Display only** — not the lifecycle stop |

## Lifecycle

- **TARGET_HIT** — option LTP or cash spot target **on touch**.
- **SPOT_INVAL** — completed **5-minute cash close** beyond `spot_invalidation`. Wicks do not stop out.
- Stack: one ACTIVE row per `(symbol, strike, side)` per session day. New signals raise max target and tighten protective spot inval. When the max-target signal is invalidated, levels fall back to remaining active contributors.

## Paper trades

Default: **off**. Open with the Confirm button, or enable Auto-paper for the session.
Stack updates append notes on the linked paper trade; they do not invent a second trade.

## Contract strip

UI shows: target = touch · stop = SPOT_INVAL on 5m cash close · paper = confirm or auto-paper.
Expiry tag, DTE, and ATM IV are stored on each row when available.
