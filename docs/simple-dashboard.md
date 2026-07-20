# Simple dashboard — component guide & how to read it

`app/simple_dashboard.py` — the **decision-first, plain-language** face of the GEX engine, built
for a non-technical trader (paper only). It keeps the load-bearing, validated reads and drops the
quant noise (percentiles, raw |GEX| magnitudes, ICs / p-values, dealer-sign talk, per-strike tables).

    streamlit run app/simple_dashboard.py

Works for **Nifty 50, Bank Nifty and Sensex** (sidebar selector). Sensex borrows Nifty's vanna
regime — see §2.1.

> **Read this first.** Every level on this screen is a **map of where positioning sits**, not a
> fence and not a forecast. The research arc (`docs/findings-summary.md`) closed with: *directional
> edge from OI/GEX is ~zero at every horizon; volatility regime is the part that's real.* §7 below
> gives the measured numbers for how often price respects vs breaks each level — read it before you
> trade off any line on this page.

---

## 1. The three modes

| Mode | How | What it does |
|---|---|---|
| **Live** | default | Dhan REST option chain (primary) → NSE (automatic fallback). |
| **Demo** | sidebar "Demo data (offline)" | Static fixture. Levels render; per-strike panels (footprint, blast, play P/L) are hidden — demo has no live premiums. |
| **Replay** | sidebar "🎬 Replay a stored day" | Steps a **real stored day** through the whole UI so you can watch the candle, TPO profile, trailing windows and regime **develop with time** while the market is closed. |

**Replay** writes to an isolated journal date (`2099-07-07`) so live data is never touched.
Pick a Day, set Speed (replay-minutes per tick), turn on **⏱️ Auto-refresh** at 1 min to make it
play, "↺ Restart from open" to rewind. The feed badge reads **🎬 Replay (stored day)** so a replay
can never be mistaken for live.

### 1.1 The status line
`Feed: 🟢 Dhan (live) · aligned to the 3-min grid · as of 13:45 IST · fetched 13:45:07`

- **🟢 Dhan (live)** / **🟡 NSE (Dhan fallback)** / **🔵 NSE** / **🎬 Replay** — always know your source.
- **Aligned to the grid** — auto-refresh fires on wall-clock boundaries (13:45:00, 13:48:00 …), with
  a ~15 s pre-fetch lead so the snapshot *is* the boundary. The pull runs on a background thread, so
  the page never dims while it fetches.

### 1.2 Refresh lanes (why the page no longer dims)
Nothing on the render path touches the network — the page only ever reads memory or a cached file,
so a repaint costs **~0.26 s** instead of the ~14 s that used to shade the whole screen.

| Lane | Cadence | Runs on |
|---|---|---|
| **Spot / day candle** | **~10 s** — one cheap index quote (~0.5 s) | background thread |
| Option chain (levels, walls, footprint) | 1 / 3 / 5 min, clock-aligned | background thread |
| Futures capture | 3 min, **gap checked before fetching** | background thread |
| Sector snap | 15 min | background thread |
| Repaint (reads the above) | 3 s | render thread, ~0.26 s |

The candle therefore moves on a **10-second** beat while the levels stay as of the last chain pull
(the header timestamp says when). That split is deliberate: the fast lane moves the *price*, not
the *map* — recomputing walls from a fresh spot against stale positioning would be a silent
inconsistency. ~10 s is comfortably inside Dhan's quote quota; a rate-limited poll simply leaves
the previous tick in place, and a tick older than 45 s is dropped rather than shown as live.
- **fetched HH:MM:SS** — the actual chain timestamp. If this stops advancing, the feed is stuck.

---

## 2. Market gauge (top strip)

```
Spot            ● Calm            Vanna tier: Calm (mid)
24,218          mood chip         Expected day range ≈ 180 pts
India VIX 11.4 −2.1%
```

- **Spot** — live underlying, plus **India VIX** with its % change (red = VIX up = fear).
- **Mood chip** — calm / normal / wild, from the intraday **|vanna| tier** (`intraday_vol_regime`).
- **Expected day range** — a concrete points number for today's likely high−low, tiered by that
  same regime bucket (`regime_daily_move_now`).

**How to read it.** This is the **most validated number on the screen**. It is not a direction —
it's a *size*. Use it to (a) sanity-check whether a target is even reachable today, (b) size
positions, (c) pick structure: calm → sell premium, wild → buy it or stand aside.

**Cross-check it against the candle:** if you've already used ~80% of the expected range by noon,
further extension is fighting the regime; if only ~30% is used at 14:00, the day is compressing
(this is also the gamma-blast precondition).

### 2.1 Sensex borrows Nifty's regime
Sensex's own vanna does **not** grade its own move (0.92× — broken by the BSE expiry-cadence churn).
Nifty's vanna grades Sensex at **1.73–1.87×**, and the two indices correlate **0.988**. So on Sensex
the tier reads *"Calm (mid) · via Nifty 50"* — the borrowing is always stated, never hidden.

### 2.2 Sector snap (the strip under the gauge)

Each sector index's **% change vs the previous day's close**, sorted **leaders → laggards**,
green up / red down, with its **approximate Nifty 50 weight** on the chip (`Bank ~30% +1.63%`). The
three **broad-market** indices (Nifty 50, Midcap 100, Smallcap 100) are outlined so you can place
the index against its own breadth. The header counts how many sectors are green (e.g. `6/11 sectors
green`) and names the feed. Hover a chip for its level.

**Only index-heavy sectors are shown.** This is a Nifty options tool, so the strip answers *"what
is moving the index I'm trading"* — a sector with no Nifty 50 constituents can be green all day and
change nothing about the tape. Realty, PSU Bank, Media and Infra are therefore excluded (they're
kept in `data/sectors.EXCLUDED`, so bringing one back is a one-line change).

⚠️ **The weights are approximate and hand-maintained, not a live feed.** No free NSE or Dhan
endpoint publishes index weights (verified: `allIndices` has none, `equity-stockIndices` 404s,
`heatmap-index` returns prices only), so they're read off the NSE factsheets — hence the `~` and
the as-of date in the header. They drift a few points a year with rebalances: good enough for
*"Bank is the big one"*, **not** for arithmetic. They also **overlap and must never be summed** —
Pvt Bank ⊂ Bank, Oil & Gas ⊂ Energy, Pharma ⊂ Healthcare.

**Source.** NSE `/api/allIndices` is primary — it states `percentChange` outright, so nothing is
derived by us. Dhan `/marketfeed/quote` is the fallback, and it deliberately shows **—** rather than
a number whenever the previous close can't be established unambiguously (Dhan has no explicit
prev-close field and its `ohlc.close` becomes *today's* close after hours — printing 0.00% for every
sector would be a silent lie).

**Refresh: 15 min, and never on the fetch path.** The option chain and futures pull are the
load-bearing data; sector rotation is slow-moving context, so it refreshes on its own **15-minute**
timer on a background thread (2 min retry after a failure) and the render always reads the cached
rows instantly. It cannot delay a chain fetch or take a worker from it.

**The strip is hidden on demo and replay** — showing today's live sector moves beside a replayed
2021 session would be a lie. If it's missing on a live feed, the cache is still cold; it appears on
the next repaint.

**How to read it.**
- **Weight is what makes a move matter.** A green sector at ~30% weight (Bank) explains the index;
  a green sector at ~2% (Consumer Durables) does not. Read the weight and the % together — that's
  why both are on the chip.
- **Breadth vs the index.** Nifty up while only 3/11 sectors are green = a narrow, top-heavy move —
  usually Bank + IT carrying it alone. Narrow moves are more fragile than broad ones.
- **The classic divergence:** the heavy sectors green and the light ones red means the *index* is
  being bought (index futures / passive flow), not the market — which is precisely the flow that
  options positioning reacts to.
- **Midcap/Smallcap vs Nifty 50.** Smallcaps leading is risk-on participation; smallcaps red while
  Nifty is green is defensive positioning or index-level flow (which is what options positioning
  reacts to).
- **Rotation, not direction.** Defensives (FMCG, Pharma, Healthcare) leading while Bank/Metal/Realty
  lag is a risk-off tilt — a *context* read for how much you trust a continuation, not an entry.
- **Sanity-check a news day.** If one sector is a huge outlier, the day is likely sector-news-driven
  and the index-level GEX read is diluted.

⚠️ **Nothing on this strip is a signal.** Sector rotation has never been tested for edge in this
project — it's here as information, on the same footing as the levels map.

---

## 3. News override banner

A red banner when the day's news-intensity gauge scores ≥5, with the top headline.

**How to read it.** *"Trust the news over these levels today."* On a headline-driven day the GEX
map is confounded — dealers reposition around an exogenous shock, and the levels describe where
positioning *was*. De-size or stand aside; do not fade a wall into a news move.

---

## 4. Value profile · candle · levels (the tall left panel)

The centrepiece. **One shared price axis, three zones:**

```
   ▏POC 24,212     ┃            ─── 24,300  Call wall
  ▏▏▏               ┃  ▲spot     ─── 24,250  Expiry pin
▏▏▏▏▏▏  ← today     ┃            ─── 24,218  Spot
  ▏▏▏     TPO       ┃            ─── 24,200  Magnet
   ▏     bars       ┃            ─── 24,100  Put wall
   ┄┄ prior VA      ┃
  LEFT           MIDDLE              RIGHT
where price      the live         where dealers
ACCEPTED         candle           are POSITIONED
```

### 4.1 LEFT — the developing value profile (TPO)
Horizontal bars = **minutes price spent at each price bin** (1-min TPO; 5 pts for Nifty, 20 for
BankNifty/Sensex). Built point-in-time from today's captured bars only.

- **Bright bar + line = POC** (point of control) — the single most-accepted price today.
- **Mid-tone bars = the value area** — the 70% band where most of the day's time was spent, with
  its edges drawn and labelled **`VAH 24,232` / `VAL 24,192`**.
- **Faint bars = the tails** — prices tested and rejected quickly.
- **Dashed grey = the PRIOR day's value**, labelled **`pVAH` / `pPOC` / `pVAL`** with prices.
- **Dotted amber = the 5-day composite** (sidebar toggle "📊 5-day value"), labelled `5D VAH` etc.

**Every level carries its price** — a line alone doesn't tell you where it is. A level that falls
**outside** the fixed axis isn't dropped: it moves to the edge as a labelled marker
(`▲ pVAH 24,480 above`), and a value area entirely outside collapses to one note with all three
prices (`▲ prior VA above · 24,420–24,450–24,480`).

**How to read it.** A profile answers *"where has this market agreed on price?"* — a fat middle
means two-sided agreement (rotation); a thin, stretched profile means one side is in control and
price is being **rejected** everywhere it goes.

- **Price inside the value area** → rotational. The POC is a magnet in the weak sense: price keeps
  coming back to it because that's where the volume is. Fading the edges of value is the natural
  play in a calm regime.
- **Price leaving the value area and *staying* out (accepting)** → the distribution is migrating.
  Watch for a *second* mode building above/below the old one — that's a double distribution, i.e. a
  trend day, and the old POC stops being a magnet.
- **Price pokes out of value and snaps back within a couple of bars** → rejection; the tail is
  informative, the excursion isn't.
- **The prior-day band is context, not a signal** (see §7 — POC direction tested null). Its real use
  is: *"if we open inside prior value, yesterday's balance still stands; if we gap outside it and
  hold, yesterday's agreement is void."*

### 4.2 MIDDLE — the live candle
Standard OHLC, green if close ≥ open. **▲ = current spot.** The caption says whether the day's
high/low came from the feed's true OHLC or was assembled from our snapshots.

**The candle's close is always the live spot**, and a feed OHLC block whose own `ltp` disagrees
with the chain's underlying is **rejected outright** (it falls back to our captured high/low).
Both rules exist because of a real glitch: around the open the quote can still describe the
*previous* session, which once drew a green body up to ~24,300 while spot was 24,182 — a price
that never traded that day. If you ever see the candle disagree with spot again, that's a bug,
not a feature; report it.

### 4.3 RIGHT — the GEX level ladder
Hover any level for `name · value · what it is`.

| Level | Colour | What it is |
|---|---|---|
| **Call wall** | red | Peak call gamma/OI — the biggest overhead positioning cluster. |
| **Expiry pin** | amber (dashed) | Max pain — the strike where most option value expires worthless. |
| **Magnet** | indigo | The gamma-weighted pull point. |
| **Gamma flip** | grey | Where net dealer gamma changes sign (long-gamma above / short-gamma below, under the assumed sign model). |
| **Put wall** | green | Peak put gamma/OI — the biggest support cluster. |
| **Spot** | ink | Now. |

### 4.4 The fixed axis and off-scale markers *(important)*
The axis is anchored on **price action only** — today's O/H/L/C + spot + the developing profile,
floored at 0.4% of spot, quantised to the profile bin. Levels may widen it by **at most 2×**.

Anything beyond that is drawn **off-scale**: an italic chip like `▼ 23,000 Gamma flip below`
pinned at the edge, or `▲ prior VA above range · 24,420–24,480` — value still readable, hover still
works, but **the geometry does not move**.

*Why this exists:* on expiry day the gamma flip swings across the whole flip grid as gamma
collapses, and a prior-day value area can sit entirely above the session. Before this, those pulled
the axis bounds and the candle shrank to a jumping sliver. **An off-scale marker is information:
it means that level is far enough away to be irrelevant to today's trade.**

### 4.5 The regime chip
Under the candle: the developing distribution's **shape × the vanna tier**.

- 🟢 **Calm × single-print · pin tight** — the two agree. Rotational, well-pinned; the calm
  short-premium structure is consistent with what the tape is doing.
- 🟡 **Calm × double-distribution** — conflict. The vol model says quiet, the distribution says the
  market has migrated to a second area. **De-size.**
- 🟡 **Wild × single-print** — mixed read.

Shape terms: *single-print* = one dominant mode, time concentrated (`tpoc ≥ 0.10`);
*double-distribution* = ≥2 modes (a trend day that relocated); *spread* = time smeared thinly
(no acceptance anywhere). Pin: *tight / loose / thin*.

---

## 5. Where the money is building (right column)

### 5.1 Options footprint card

```
🟢 Support     put OI      24,100   top 24,000
🔴 Resistance  call OI     24,300   top 24,400
💰 Most money building  calls written → resistance   24,300  ₹42.1 Cr
```

- **Support / Resistance** = the **OI-weighted centre of mass** of standing put/call OI, plus the
  **top** single strike. The weighted centre is the version that survived testing (§7); the top
  strike did not.
- **💰 Most money building** — the single strike taking the most fresh money **since open**, in ₹
  turnover (ΔOI × premium × lot). Crucially it reads **writing vs buying from the premium's
  direction**: OI up + premium down = *written* (resistance/support forming); OI up + premium up =
  *bought* (directional bet).
- **Net call-writing / put-writing near spot** — which side is dominating the fresh build.

**How to read it.** In India's writer-dominated microstructure, **OTM call OI building is usually
writing → resistance**, not bullish buying. That's why the writing/buying split matters — reading
"calls building" as bullish is the classic mistake here.

### 5.2 Trailing windows strip (60 / 30 / 15 / 5-min)

```
60m   🟢 Puts written → support     ₹18.2 Cr · @24,100
30m   🔴 Calls written → resistance ₹11.4 Cr · @24,300
15m   · Balanced
5m    · Thin build
```

Each row is the **current expiry's** OI change over that trailing window, counting **adds only**
(unwinds clipped to zero), with the dominant side needing ≥60% of adds or it reads *Balanced*.
The ₹ figure is **turnover** = ΔOI × premium × contract multiplier.

⚠️ **Feeds count OI differently** — NSE publishes **lots (contracts)**, Dhan publishes **shares**
(lots × lot size; verified — every live Dhan NIFTY OI divides exactly by 65 and never by 75). The ₹
multiplier is therefore the lot size on NSE and **1** on Dhan, where the size is already baked in.
This was wrong until 2026-07-20 and **overstated every ₹ figure on the Dhan feed by 65×**; the two
feeds now produce identical ₹ from the same book (a test asserts it). Relative comparisons within a
window were never affected — only the absolute rupee amounts.

**How to read it.**
- This is **flow**, not level — *"where is money arriving right now"*, a different question from
  *"where is the standing book"* in 5.1.
- **Adds-only is the expiry guard.** Late on expiry the weekly's OI decays and churns (settlement,
  pin-hedging); adds-only stops that decay from masquerading as directional flow. A purely-unwinding
  window correctly reads **Thin**.
- **Short windows churn on expiry** — the 5/15-min rows are dimmed on expiry day with a caption
  steering you to 30/60-min. A `≤` prefix means the window is partial (fewer snapshots than it wants).
- **Agreement across windows is the signal, not any one row.** 60m and 30m saying the same thing =
  a persistent build. 5m contradicting 60m = noise.
- ⚠️ **Fresh-build S/R tested with the *wrong* sign** (§7) — price tends to drift *through* the
  fresh build, not reject off it. Read this strip as *where the money is going*, not as a barrier.

### 5.3 Futures OI build-up

Since-open plus the same 60/30/15/5-min windows, classifying **price × ΔOI**:

| Price | OI | Read |
|---|---|---|
| ▲ | ▲ | **Long build-up** — new money long |
| ▼ | ▲ | **Short build-up** — new money short |
| ▲ | ▼ | **Short covering** — shorts squeezed out (weaker than fresh longs) |
| ▼ | ▼ | **Long unwinding** — longs giving up |

Uses **aggregate** OI (rollover-netted across serial months), so a rollover isn't misread as a
position change. **Volume = intensity, not direction.** Around monthly expiry the short windows
churn on rollover — lean on 30/60m.

**How to read it.** Futures build-up is the cleanest *directional-conviction* read on the page
because it's sign-free — no dealer-sign assumption. Build-up (either side) = conviction; covering /
unwinding = an existing position exiting, which moves price but has less staying power.

---

## 6. Suggested play

### 6.1 Non-expiry — the regime card
The structure that fits the current vol regime. In **CALM** it shows a concrete illustrative
**short strangle at the walls**: sell the call @ call-wall + put @ put-wall, with live premiums, an
expiry P/L diagram (green max-profit plateau, break-evens at strike ± credit, red wings) and the
stop **just beyond the walls**.

If the two walls land on the **same strike**, "sell at the walls" would degenerate into a naked
straddle — the card steps the strikes ±1 out to restore a real credit band.

**The honest basis:** this play backtests **positive and survives spread** (+11.0%/trade in CALM at
a 2% half-spread, 58% win, 49% stopped out) — but the edge is the **volatility premium**, *not* the
walls containing price. The walls only pick the strikes and the stop. **It is thinning in the
current regime:** Sep-25 onward (Tuesday weeklies) it's only **+4.1% with a 61% stop-rate**. The
defined-risk iron fly is *worse* after cost (8 spread crossings vs 4) — see findings §5.7.

### 6.2 Expiry day — the gamma-blast hero

Anchors freeze automatically at **13:45 IST** (the strongest window) or via "📍 Anchor blast now".
Before a crossover it watches the ATM strike; a **crossover** fires when the call/put premiums at
that strike cross (spot crossing the strike, read via put-call parity — sign-free).

```
🚀 Likely RUN          cross 13:52
True-run score  68 / 100 · TRUE
[████████░░]  Fake/pin — Toss-up — Real run
Timing   ⚡ Fast TRUE (+3m)    38 → 55 → 62 → 68
call-writer OI change  −12%    Wall ahead  1.4×
```

- **Score ≥60 = TRUE-leaning**, 35–59 = *WEAK / unconfirmed*, below = FAKE-leaning. One override:
  if writers are *adding* at the strike (cover fraction > +5%) it grades **FAKE regardless of
  score** — writers defending K beats any thrust reading.
- **Writer OI change** — **negative = writers covering = fuel**. This is the mechanism: the blast is
  writers being forced out, not fresh buying.
- **Wall ahead** — how much OI caps the move in the direction of travel. High = the run has a lid.
- **Timing** (from the 1-min study, §7d of gamma-blast-research) — **⚡ Fast TRUE (≤5 min)** is the
  best setup; *Slow TRUE* is confirmed but weaker; *Watching* needs score ≥45 by ~3 min;
  **No ignition (>5 min)** usually fizzles → lean fade.
- **🔔 Alert** — beep + toast once when the score first hits 45 within ~5 min of a cross, and again
  on a Fast TRUE. Sound is best-effort (browser autoplay); the toast is the reliable channel.
- **FAKE fades toward the crossover strike**, not the magnet — the fade logic is direction-correct.

⚠️ **This is a confirmation-quality read, not an entry signal.** The [B1] backtest showed the edge
**does not survive spread**. Use it to grade a move you're already in, or to avoid chasing one.

---

## 7. When does price RESPECT a level — and when does it break?

This is the section to internalise. We measured it: for every level, the day-detrended **push
toward the level** when price comes within a 30-min expected move (`scripts/analyze_footprint_sr.py`).
Negative = price **rejects/holds**; positive = price **breaks through**.

| Level (as shown on this dashboard) | Measured edge | Across eras | Verdict |
|---|---:|---|---|
| **Support / Resistance** — standing-OI **weighted centre** (§5.1) | **−2.6 bp** | −2.2 / −3.4 / −2.1 | ✅ **HOLDS** — right sign, robust in every era |
| Standing-OI **top** single strike | −0.2 bp | mixed | ~null |
| **GEX walls** (call/put wall on the ladder) | −0.5 bp | −0.6 / −0.5 / −0.4 | ~null (right sign, tiny) |
| **Fresh ΔOI** build (the trailing-window strip, §5.2) | **+1.6 bp** | +1.1 / +3.0 / +0.3 | ❌ **WRONG sign** — price drifts *through* it |
| Magnet-lock / wall-break / pin plays (#12/#14/#16) | ~0 | ~0 | ❌ null (generic mean-reversion, not OI pinning) |
| Prior-day **POC** as a next-day attractor (#25) | 50/50 vs its own mirror | — | ❌ null |

**What this actually means:**

1. **The single most reliable line on the screen is the OI-weighted Support/Resistance** in the
   footprint card — *not* the walls on the ladder. It rejects price with the right sign in every
   era tested. But **−2.6 bp is ~0.03%** — a trustworthy *map*, not a mechanical fence.
2. **The walls are strikes, not barriers.** Their measured containment is ≈ zero. The calm-strangle
   backtest is the proof: it's profitable, and it still gets **stopped out on a wall breach 49% of
   the time** (61% in the current Tuesday-weekly regime). Plan for the breach.
3. **Never trade the fresh-build strip as support/resistance** — it tested with the *wrong* sign.
   Price goes *toward* where money is currently building. Read it as flow, not as a floor.
4. **Nothing here predicts direction.** Every direction construction we tested — magnet pull, wall
   break, POC momentum, distribution shape, participant OI, futures build-up — decayed to ~0 at
   full sample.

### 7.1 A practical read — conditions that favour RESPECT
No single one is sufficient; look for **agreement**.

- **Calm / mid vanna tier** *and* **regime chip green** (calm × single-print, pin tight).
- Price is **inside the value area** and the level sits at or near the **POC or a value edge** —
  positioning and realised acceptance point at the same price. *Confluence is the real filter.*
- The **OI-weighted S/R** (not just the top strike) sits there, and the **standing** book is heavy.
- Used range is already a large fraction of the **expected day range** — the move has spent its fuel.
- Trailing windows show **writing into the level** on 30m *and* 60m (persistent, not a 5m blip).
- No news override.

### 7.2 Conditions that favour a BREAK
- **Wild vanna tier**, or **used range still small** relative to expected — room to travel.
- **Regime chip amber** (conflict), or the profile has gone **double-distribution** — the market has
  already relocated once today.
- Price **accepting outside the value area** (staying out for multiple bars, building a new mode).
- **News override banner is up.** Headlines override the map, full stop.
- The trailing windows show **fresh building at/beyond the level** (money going there ⇒ break, per §7).
- **Expiry day**, especially post-13:45: gamma collapses, the flip goes unstable (you'll see it fly
  **off-scale** on the ladder — that itself is the tell), and pin-hedging churns the book.
- **Wall ahead ratio is low** in the blast card — no cap in the direction of travel.

### 7.3 Rules that are not negotiable
- **Stops go just *beyond* a wall, never at the magnet.** The magnet is where price is pulled *to* —
  putting a stop there guarantees you're taken out at the worst price.
- **Confluence over any single line.** One level alone is close to noise; a level that is
  simultaneously the OI-weighted S/R, a value-area edge and the prior POC is worth respecting.
- **De-size on conflict** — regime chip amber, or thesis vs regime disagreement.
- **Paper only. You confirm every trade.** The engine never executes.

---

## 8. Data capture (the dashboard feeds itself)

On every **in-session** live pull the dashboard persists its own history: the timeseries row and,
lock-step, the per-strike OI rows — the same day files that the range seed, the TPO profile and the
OI windows read back. Futures snapshots are captured the same way. So the profile and windows fill
from the dashboard's own refreshes; `scripts/log_timeseries.py` still shares the files (a `min_gap`
guard prevents double-writes) but is no longer required.

**Sources are kept separate**, never mixed: `data/journal/oi_strikes_dhan/`,
`futures_intraday_dhan/`, `gamma_blast_dhan/` alongside the NSE originals — so the two feeds can be
compared later. Post-close pulls are **not** written (a frozen end-of-day chain must never poison
the day's file).

Missing early hours on a Dhan day can be recovered with **⏬ Backfill today's OI** in the sidebar
feed panel (also runs automatically when a >2 min gap is detected).

---

## 9. Known limitations

- **Day high/low granularity (NSE feed only).** On NSE the day range is assembled from snapshots at
  the refresh cadence, so a spike that reverts between two snapshots is invisible — the captured
  range is a **lower bound**, which can **false-flag a day as "compressed / blast-friendly"**. On a
  **Dhan** feed the true day OHLC is used and this doesn't apply; the caption under the candle tells
  you which one you're looking at.
- **The dealer-sign model is UNVALIDATED for India.** Gamma flip and the walls depend on it. Evidence
  leans toward the inverted "model B" but it's a lean, not a verdict — `DEALER_SIGN_MODEL` stays on
  the US default. Treat flip-based reads with extra suspicion.
- **Blast edge doesn't survive spread** ([B1]) — confirmation only.
- **The calm-strangle edge is thinning** in the current Tuesday-weekly regime (§6.1).
- **Sensex vanna is borrowed from Nifty** — if the two ever decorrelate, that read degrades silently
  apart from the "· via Nifty 50" tag.

---

## 10. Related docs

- `docs/findings-summary.md` — what's validated and what's null (read §5–6).
- `docs/intraday-results.md` — the numbered studies, incl. #25 (Market Profile).
- `docs/gamma-blast-research.md` — the blast strategy and §7d ignition timing.
- `docs/futures-buildup.md`, `docs/participant-oi.md`, `docs/vanna-charm.md`.
- `docs/USER_GUIDE.md` — the full (technical) dashboard.
