# Nifty GEX Dashboard — How to Use It

*A simple guide. No coding needed. This is a **map and a read**, not an autopilot.*

> **Important:** This is for **practice and learning only** right now. It does
> **not** place any trades, and it does **not** yet tell you exactly what to buy.
> Naveen is still checking that the signal is correct for Nifty — until then,
> treat everything here as paper practice.

---

## 1. What it is (in one line)
Every day it shows **where the big option positions sit** and whether the market
is likely to be **calm (range/fade)** or **wild (trend)** — so you can read the
day before you trade. (For the *why*, see `gex-cheatsheet.md` and
`strategy-explained-simple.md`.)

## 2. One-time setup (ask Naveen to do this once)
1. Install **Python** on the laptop (tick "Add Python to PATH" during install).
2. Put the **gex-indian** folder on the laptop.
That's it — after this you never touch setup again.

## 3. Opening it (every day)
- **Double-click `run_app.bat`.**
- The first time it spends a minute getting ready, then your **web browser opens**
  with the dashboard. Leave the little black window open while you use it.
- Best used during market hours (**9:15–3:30 pm IST**).

## 4. What you see on screen (top to bottom)
- **Red banner** — a reminder: unvalidated + paper only. (It stays until the
  signal is proven.)
- **Sidebar (left):**
  - **🔄 Refresh** — pulls the latest data and recomputes. Press it to update.
  - **Use demo data (offline)** — practice with *fake* data when the market is
    closed or the internet is down. (Clearly marked "synthetic — don't trade on it.")
  - **Expiry window** — which expiries to include. Leave on the default.
- **📋 The playbook sentence** — the headline read, e.g.
  *"Positive gamma, flip 200pts below, call wall 300 above — range fade."*
  - **range fade** = calm day → price likely chops between the walls.
  - **momentum** = wild day → price likely trends; be careful, smaller size.
  - **conflict / de-size** = mixed signals → wait, don't force a trade.
- **Three boxes** — **Spot** (Nifty level), **Regime** (POSITIVE = calm /
  NEGATIVE = wild), **Total net GEX** (how strong).
- **The 5 levels table:**
  | Level | Plain meaning |
  |---|---|
  | 🧱 **Call wall** | resistance above — price struggles to push past |
  | 🧱 **Put wall** | support below (if it's a "support", not a "speed bump") |
  | 🔀 **Gamma flip** | the **calm ⇄ wild switch** — above it = calm, below = wild |
  | 🧲 **Magnet** | where price tends to get pulled in calm mode |
  | 🎯 **Max pain** | where most options expire worthless (you know this one) |
  The **Δ from spot** column tells you how many points away each level is.
- **The chart** — green/red bars = net GEX at each strike; **black dashed line =
  spot**, **blue line = gamma flip**. Tall bars near price = strong walls.
- **Caveats panel** — the assumptions (lot size, expiry weighting, data time) and
  any ⚠️ warnings (e.g. "stale prices — re-run during market hours").

## 5. "What trade fits this mode?" (illustrative)
There's an expandable section **"💡 What trade fits this mode?"**. Open it to see
a **textbook structure** for the current mode — e.g. an iron condor (sell premium)
in a calm range, or a debit spread (buy premium) in a wild trend, with the strikes
taken from the walls and a defined-risk note.

> **Read this carefully:** it is **illustrative only** — a textbook example from
> the rules, **not a validated recommendation**. The signal isn't proven for Nifty
> yet (the dealer-sign could even be inverted). Use it to *learn the logic* and to
> *paper trade*, never as a real instruction.

## 6. What it does NOT do (yet)
- ❌ It does **not** keep a trade journal/scorecard — *coming later.*
- ❌ It **never** places or sends orders. You decide and trade yourself.
- ❌ The trade idea is **not validated** — practice only.

## 7. Reading the levels on EXPIRY DAY (Nifty = Tuesday)
On expiry day the app shows a **⚡ EXPIRY DAY** banner. Gamma is at its strongest
near expiry, so the levels matter most — in **two opposite ways**:

- **Calm (positive gamma) → strong PIN.** Price tends to get glued toward the
  **magnet / max pain** into the close. This is the common case — a
  premium-sellers' day, low movement, fast time decay.
- **Wild (negative gamma, below the flip) → GAMMA BLAST risk.** If price breaks a
  wall or the flip late in the day, the move can **explode** in the last hour
  (sellers get squeezed and chase it). Big, fast, one-directional.

How to read each level on expiry:
- 🔀 **Gamma flip** — the on/off switch. *Above it* = pin mode; *breaking below it*
  = blast mode. Watch it closely in the afternoon.
- 🧱 **Walls** — the **trigger** levels. A decisive break **above the call wall**
  (upside blast) or **below the put wall** (downside blast) is where a blast ignites.
- 🧲 **Magnet / 🎯 Max pain** — the **pin targets**; in calm mode price drifts here
  into the close.

> **Honest warning about gamma blasts:** most expiries **pin** (the blast is the
> rare tail). Buying cheap OTM options for a blast is a **low-probability lottery**
> and **time decay is brutal** on expiry day. Also, our open-interest data is
> end-of-day, so it's **least reliable on 0DTE** — exactly when blasts happen. Treat
> the app as showing you the *map* (where a blast could trigger), **not** a signal
> to take one. Paper only, especially here.

## 8. If something looks off
- **"Refresh failed"** → it shows the last good data with a time. Just wait and
  press Refresh again (the NSE website sometimes throttles).
- **A ⚠️ "implausible/stale" flag** → the prices are stale (e.g. outside market
  hours). The walls are still roughly right; re-run during market hours for clean
  numbers.

## 9. The mindset
**Calm market → be the casino** (sell premium around the walls).
**Wild market → be the careful gambler** (buy premium with the trend).
The **flip** tells you which one today is. The app measures it; **you** decide.

*Decision-support only — not financial advice. Paper trading only for now.*
