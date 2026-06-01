# Nifty GEX — Validation Log

## Run 1 — 2026-06-01, market hours (~12:05 IST, mid-session)
First live market-hours validation pull.

- **Live NSE pull OK:** 1096 contracts, spot ~23,511.
- **Levels:** regime **POSITIVE**, call wall **23,600**, put wall **23,500**,
  flip **23,407**, magnet 23,700, max pain 23,650.
- **External cross-check ✅:** public NSE OI sources (niftytrader, niftyinvest)
  put **support ≈ 23,500 / resistance ≈ 23,600** — matches our put/call walls.
  Validates the OI data + wall computation against a third party.
- **Forwards robust & smooth** (23,528 → 23,591 across expiries).

### Finding A: r/q fragility → FIXED (forward-based gamma)
- Per-expiry **slope-implied rates were noisy** (r 14–30%, q −7…23%) — and not a
  weekend artifact; implied *rates* from the parity slope are inherently fragile
  for short-dated options.
- The **forward is robust** (x-intercept where C−P=0); only the slope (rate) is not.
- **Fix:** derive `r = carry = ln(F/S)/T`, `q = 0` from the robust forward
  (`gex/levels.py`). Gamma's `d1` needs only the carry, which the forward gives;
  the `e^{-qT}` dividend prefactor (~1 for short index options) is negligible.
- **Verified invariant:** walls/regime unchanged, **flip 23,406.1 → 23,407.2**
  (1 pt) — proving the levels were always robust to the rate noise, and the inputs
  are now honest. The old `|r|,|q|>50%` flag is replaced by a forward-deviation
  (`|F/S−1|>3%`) flag.

### Still open: dealer-sign not independently confirmed
- No free real-time **Nifty GEX/flip** feed exists to compare regime/flip against
  (GEX tools are US-focused or paid). The walls match public OI, but the walls do
  **not** test the *sign* (regime/flip/net-GEX do).
- → Behavioural test set up below.

---

## Dealer-sign behavioural test (ongoing)
Goal: confirm the `long_call_short_put` gamma convention by checking whether price
behaves as the regime predicts — the honest, free path (no paid GEX feed).

1. Run **`python scripts/log_session.py`** near the **open** each trading day. It
   appends spot/regime/mode/walls/flip to `data/journal/sessions.csv` (gitignored).
2. At the **close**, fill the outcome columns: `eod_spot`, `stayed_in_range`
   (did spot stay between the put & call walls?), `broke_flip` (did it cross the
   flip?), `trended` (big directional move?), `notes`.
3. After **~10 sessions**, analyse:
   - **POSITIVE-gamma days should mostly RANGE/pin** between the walls; days that
     break the flip should **TREND / get volatile**.
   - Holds → **sign CONFIRMED.**
   - Inverted (positive days trend, below-flip days pin) → **flip
     `config.DEALER_SIGN_MODEL` and re-validate.**

Until confirmed, all trade suggestions stay **ILLUSTRATIVE** (the app says so).

## Acceptance status
- [x] Live market-hours pull + walls match a third-party OI read.
- [x] Forward-based gamma (rate fragility removed; levels invariant).
- [ ] Dealer-sign confirmed behaviourally (~10 sessions of `log_session.py`).
- [ ] Lot size confirmed against the current NSE circular.
