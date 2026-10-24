# Retired scope — Nifty / NSE adapter

**Status:** Obsolete. The NSE path is production. This file is the early
scope that assumed an SPX parent. Do not rebuild from it, and do not treat
its dealer-sign section as a reason to add a second convention.

**Goal:** Run the *exact same* GEX engine on **Nifty (NSE)** by adding one new
data adapter + a few India-specific config values — without touching the maths.

> Same disclaimer: decision-support, not advice. And see the big honest caveat in
> §6 — the dealer-sign assumption may **not** transfer cleanly to the Indian
> market, and that must be researched before trusting any Nifty output.

---

## 1. Why this is (mostly) cheap

The project was deliberately built as **pure math + a swappable data adapter**.
Adapting to Nifty touches only the edges:

| Layer | Nifty change needed? |
|---|---|
| `gex/` (BS gamma, per-strike GEX, walls, flip, max pain) | **None** — identical maths |
| `regime/` (classification, playbook) | **None** — identical logic |
| `data/parity.py` (imply r/q + spot from chain) | **None** in principle (parity is market-agnostic) |
| `data/schema.py` (`OptionsSnapshot`) | **None** — same fields fit Nifty |
| `data/<nse>.py` (new adapter) | **NEW** — the main work |
| `config.py` (lot size, expiry rules, dealer model) | **Small** — India values |
| `data/expiry.py` (expiry calendar) | **Changed** — NSE rules, not US third-Friday |

If the abstraction is right, **~80% of the code is reused untouched.** Building
this is also a good test that the SPX design wasn't secretly US-specific.

---

## 2. What's genuinely new or different

### 2a. A new NSE data adapter (`data/nse.py`)
Mirrors `data/polygon.py`: pull the live Nifty option chain → map into the common
`OptionsSnapshot`. Must provide per contract: **strike, type, OI, IV (or a price
to derive IV), expiry**, plus the **Nifty spot**.

Good news vs SPX: NSE's option-chain feed typically **includes the underlying
index level directly**, so the "spot isn't in the snapshot" problem we hit with
Polygon likely *doesn't* occur — but verify (§5).

### 2b. Contract multiplier (lot size)
SPX uses `CONTRACT_SIZE = 100`. Nifty uses the **NSE lot size**, which **NSE has
changed repeatedly** (recently raised to keep contract value in a target band).
This directly scales every GEX number, so it must be a named, *verified-live*
config value — never hard-coded from memory. (See §5.)

### 2c. Expiry calendar (`data/expiry.py`)
US logic = monthly "third Friday." NSE is different and **in flux**: weekly
expiries plus a monthly (historically last-Thursday) expiry, and the **weekly
expiry day/rules were changed by SEBI/NSE in 2024–2025**. The `third_friday` /
`next_monthly_expiry` helpers must be replaced with an NSE expiry calendar.
**Verify the current rules live — do not assume Thursday.**

### 2d. Execution mapping
The SPX→XSP "signal vs execution" split does **not** apply. Nifty options are
already retail-sized and traded directly, so there's no 1/10th product to map
onto. Simpler here.

### 2e. Rates / dividends
No change needed: r and q are **implied from the chain via put-call parity**
(market-derived), so the Indian risk-free rate and Nifty dividend yield fall out
automatically. No MIBOR/T-bill feed required.

---

## 3. What stays exactly the same (the payoff)
- `gex/bs.py`, `gex/core.py`, `gex/flip.py`, `gex/maxpain.py`, `gex/levels.py`
- `regime/classify.py`, `regime/playbook.py`
- `data/schema.py`, `data/parity.py`
- All the hand-checkable fixture tests (they're synthetic, market-agnostic).

---

## 4. Proposed sub-phases (smallest-thing-first)
1. **Resolve data source** (the gating unknown, §5). Pick one NSE chain source,
   confirm coverage + cost + rate limits live.
2. **`data/nse.py`** — fetch + map one Nifty snapshot into `OptionsSnapshot`;
   a smoke test mirroring `scripts/live_smoke.py` (field-population + save raw).
3. **NSE expiry calendar** in `data/expiry.py` (or a parallel module).
4. **Config**: Nifty `CONTRACT_SIZE`/lot size, expiry-window defaults.
5. **Run the existing engine unchanged** → print the Nifty level table + playbook.
6. **Acceptance**: validate Nifty walls/flip vs a known Indian GEX/OI read.

---

## 5. Build-time unknowns — VERIFY LIVE, do not guess (CLAUDE.md #5)
These are the same "check, don't assume" gates we applied to Polygon:

1. **Data source + pricing + reliability.** Candidate options to evaluate live:
   - **Official NSE option-chain endpoint** — free, but unofficial, header/cookie-
     gated, rate-limited, and known to block automated pulls. Fragile for a
     daily-driver; fine for a PoC.
   - **Broker APIs** (e.g. Kite Connect, Upstox, Angel SmartAPI, Fyers, Dhan) —
     paid/stable, give chain + OI + LTP. Pricing and option-chain/greeks coverage
     **must be checked live** (they change), exactly as we did for Polygon.
   - **Data vendors** (TrueData, GlobalDatafeeds, etc.).
   → Decide with the same criteria used before: cheapest + good quality + lowest
     integration friction. Confirm whether **IV is provided** or must be derived
     from LTP (engine can do either).
2. **Current Nifty lot size** — changes by NSE circular; verify the live value.
3. **Current weekly/monthly expiry rules** — SEBI/NSE revised these in 2024–2025;
   verify the actual expiry calendar before coding `expiry.py`.
4. **Spot availability** — confirm the chosen feed returns the Nifty index level
   (likely yes, unlike Polygon for SPX); if not, reuse `imply_spot_from_chain`.

---

## 6. One book, closed comparison

This section used to say the engine assumes dealers are long call gamma and
short put gamma, and that a later edit should test both signs. That comparison
is closed.

Production stores both legs negative (`short_call_short_put`). Walls are the
largest call-gamma and put-gamma magnitudes. The flip and the regime total are
call magnitude minus put magnitude. Any other value of `DEALER_SIGN_MODEL`
raises. Who hedges on NSE is unmeasured. Do not restore the US long-call /
short-put reading from this scope note.

---

## 7. Acceptance check (definition of done)
- Nifty level table + one-sentence playbook print from a live NSE pull.
- Computed call/put walls and flip **validated against an independent Indian
  GEX/OI read** within a stated tolerance, with divergences explained
  (expiry window, lot size, dealer-sign model).
- Dealer-sign convention for Nifty **empirically chosen**, not assumed.

## 8. Explicit non-goals (for this phase)
- No auto-execution (unchanged hard rule).
- No broker order wiring.
- No Bank Nifty / FinNifty / single-stock options yet — Nifty index only first
  (one underlying, smallest thing that works).

---

*Bottom line: the engine is ready to point at Nifty with modest work — a new
adapter, a lot-size, and an NSE expiry calendar — but the **dealer-sign
assumption must be re-validated for India**, and the **data source + lot size +
expiry rules must be checked live**, never guessed.*
