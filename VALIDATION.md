# Nifty GEX — Validation Log

**Production status (2026-10-24).** The behavioural test below was aimed at a
`long_call_short_put` convention this tree no longer accepts. Walls matching
public OI still stands. The regime word is call-heavy versus put-heavy, not a
pin test.

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

1. **Automated:** `setup_scheduler.bat` (one-time) logs **twice each weekday** via
   `scripts/log_session.py` → `data/journal/sessions.csv` (gitignored):
   - **OPEN** ~09:20 IST = **04:50 BST**, and **AFTERNOON** 15:00 IST = **10:30 BST**.
   - (UK machine. DST: in GMT use 03:50 / 09:30 — see the .bat.)
2. **Score:** after ~10 sessions, run **`python scripts/score_sessions.py`**. It
   pairs each day's open + 3PM snapshots and checks whether the 3PM price **pinned
   inside the open's wall range** — so scoring is largely automatic (the manual
   outcome columns are optional backup).
3. **This verdict is closed.** Do not flip `config.DEALER_SIGN_MODEL`. Production
   is `short_call_short_put`: positive means the call book is larger, negative
   means the put book is larger. Neither word is a pin test. The scorer may
   still report whether the close sat inside the morning gamma strikes. That
   is a distance check, not a reason to restore a second convention.

### Audit — 2026-06-06: out-of-hours captures were contaminating the score
Auditing `sessions.csv` showed `date_ist` is computed correctly (every row matches
`timestamp_utc + 5:30`), but only **3 of 7** snapshots were taken during NSE hours.
The 4 off-hours rows were evening UK runs of stale end-of-day OI; worse, the ones
captured after ~18:30 UTC roll past IST midnight and get filed under the **next**
trading day, where the scorer (earliest row of the day = "open") mistook them for
that day's open. Concretely, **2026-06-02 was mislabelled POSITIVE/pinned** off a
ghost midnight row — the real 09:20 open was **NEGATIVE and price escaped the
walls** (+162, outside 23,250–23,300).

Fixes:
- `scripts/log_session.py` now **skips outside 09:15–15:30 IST (weekdays)**
  (`--ignore-session` to force) — stops the contamination at the source.
- `scripts/score_sessions.py` **drops out-of-hours rows before scoring** and
  reports how many it ignored, so a stray capture can never become an "open".

After the fix the clean log has **1 paired day (NEGATIVE, escaped) and 0 paired
POSITIVE days** — i.e. effectively no signal yet. This is concrete evidence that
the **Phase A 15-min intraday time-series (session-guarded by design) supersedes
this twice-daily log** for settling the dealer-sign question.

## Acceptance status
- [x] Live market-hours pull + walls match a third-party OI read.
- [x] Forward-based gamma (rate fragility removed; levels invariant).
- [x] Sign convention closed: one book-balance model. Who hedges remains unmeasured.
- [ ] Lot size confirmed against the current NSE circular.
