# Burst analysis — reading the sliced-flow signal ([W2])

The big-money tape has two event types: **PRINTS** (one large trade) and **BURSTS** (a lot of ₹
trading in a short window). Prints are intuitive; bursts are not, yet on the futures they are the
*dominant* and more informative signal. This note is the research + a reading guide, grounded in the
23-Jul-2026 NIFTY/BankNifty session. Sign-free; descriptive; decision-support, **not a predictor**.

## What a burst is — and why it exists

A **BURST** fires when the traded notional over a trailing **10-second window** clears a ₹ floor
(default ₹3cr), rising-edge (once per crossing). It sums **both contracts** of a symbol (near + next
month), so it measures total futures flow, not one contract. `notional = price × Δvolume`.

Why it exists: **futures prints are sliced.** On 23-Jul the median NIFTY "big" print was only ₹1.48cr
(~10 lots) and the biggest all day was ₹4.18cr — algorithms work large orders as *clusters of small
trades*, so a single-print filter sees only the tip. The burst catches the cluster.

**The headline number:** that day NIFTY had **861 bursts vs 152 prints**, and the bursts summed
**₹3,488cr vs the prints' ₹249cr — ~14× the notional.** BankNifty: ₹1,378cr of bursts vs ₹135cr of
prints (~10×). **Bursts are the primary flow measure; prints are the visible tip of it.**

## The three axes to read a burst

**1 · Intensity (over time) — "when was big money active?"**
Bursts per interval = participation intensity. On 23-Jul NIFTY burst *count* ramped through the day —
75 (09h) → 139 → 150 → 134 → 145 → **171 (14h)** → 47 (partial 15h) — even as prints thinned. Clusters
mark the active windows (open, news, the close); lulls mark disinterest.

**2 · Magnitude (notional) — READ THE TAIL, not the median.**
Because a burst fires the moment the window clears the floor, the **median burst sits right at the
floor** (₹3.41cr on 23-Jul ≈ the ₹3cr threshold) — most bursts are "just over." The *signal is in the
tail*: NIFTY p90 ₹4.98cr, **p99 ₹14.94cr, max ₹42.47cr** — a 10-second window where ₹42cr changed
hands is a genuine surge, an order of magnitude above the floor-huggers. Rank bursts by notional and
watch the top decile; ignore the floor-huggers.

**3 · Location (price) — "where did fast money transact?"**
Burst notional summed by price level is a volume-at-price profile weighted toward *urgent* flow — where
a lot of money changed hands quickly. Read it alongside the print value-profile (§3 of the analysis
page): agreement = a real big-money level; divergence = prints and slicing happened at different prices.

## The print × burst cross-read (the useful part)

The *ratio* of prints to bursts over a window is a day-structure tell:

| Pattern | Reading | 23-Jul NIFTY example |
|---|---|---|
| **print-heavy + bursts** | aggressive, outright taking | the **open** (09h: 41 prints) |
| **print-light + burst-heavy** | **stealth** — orders worked via slicing, not outright prints | **midday 10–14h** (prints ~15–22, bursts 139–171) |
| **both high** | urgent *and* aggressive | the **close** (14:45 / 15:15 spikes) |

So 23-Jul NIFTY reads as: **aggressive at the open → quiet stealth-accumulation through midday →
aggressive into the close.** The burst-heavy/print-light midday is the part a print-only tape would
miss entirely — it looked "quiet" on prints while ₹-thousands-of-crores sliced through.

## What a burst does NOT tell you (how not to misread it)

- **No direction (on its own).** A burst's `side` is `None` by design — it is *total* flow in the window
  (buys AND sells). **Never infer bullish/bearish from a burst alone.** For direction, use the *print*
  aggressor lean (Lee-Ready) + price action — or, properly, **ΔOI + price direction** (the build-up
  quadrant, see the ΔOI section above). The burst by itself says only *how much*, *when*, and *where*.
- **Parameter-dependent.** The ₹3cr floor and 10-second window are **placeholder, uncalibrated**
  choices — so absolute burst *counts* are not comparable across setups. Read **relative** (clusters,
  changes, the tail), not absolute.
- **Count ≠ money.** Rising-edge means one burst per floor-crossing, so a long high-flow stretch may be
  one burst or several. **Use burst NOTIONAL (sum / tail), not the raw count**, as the money measure.
- **Roll contamination.** Because near + next are summed, a burst near expiry can be *rollover /
  calendar-spread* activity rather than directional flow. (Calendar-spread separation is a future add.)
- **Not validated.** Like everything in this arc, it is a **descriptive** flow map until a study shows
  predictive content. Treat it as context, de-size on conflict, you confirm.

## Bringing ΔOI in — from *flow* to *positioning* (the key upgrade)

Volume (and therefore a burst) tells you **how much** traded, not **whether it was new money**. **ΔOI —
the change in open interest over the same window — resolves that**, and it is the single most valuable
thing to add:

| Volume / burst | ΔOI | Meaning |
|---|---|---|
| high | **up** | **new positions built** — conviction, fresh money |
| high | **down** | positions **closed** — unwinding / short-covering (exit-driven) |
| high | ≈ flat | **churn** — intraday day-trading, no net positioning |

And crucially, **ΔOI + price direction gives a burst a DIRECTION** it otherwise lacks — the classic
four-quadrant read (reuse `app/oi_footprint.py::classify_buildup`):

- price ↑ · OI ↑ = **long build-up** (bullish conviction)
- price ↓ · OI ↑ = **short build-up** (bearish conviction)
- price ↑ · OI ↓ = **short covering** (up, but exit-driven → weaker)
- price ↓ · OI ↓ = **long unwinding** (down, but exit-driven → weaker)

So "a ₹15cr burst at 23,900" becomes "**₹15cr of new longs built at 23,900**" (or "longs unwinding") —
a far stronger read, and one that connects directly to the existing futures-OI build-up work
([[futures-oi-buildup]], [[positioning-footprint-suite]]).

**We already have the data**: Full packets carry OI (`tick.oi`), and `futures_state` stores it per
contract. **What's missing**: events don't yet carry OI/ΔOI. The build is small — annotate each event
with `doi` (ΔOI over the burst window, or vs a rolling reference), tag the build-up quadrant, and add a
**conviction ratio** `ΔOI / Δvolume` (what fraction of the flow was net-new positioning). Then the tape
and analysis split burst notional by build-up type, and the value profile shows *where positions were
built*, not just where volume printed. **Caveat:** OI is cumulative and per contract, so near expiry the
roll inflates ΔOI (near falls, next rises) — the near+next aggregation nets most of it, but roll days
need care. Needs the raw archive live (today's was empty) or the new event field going forward.

## Open research questions

The defensible edge, if any, is **vol-regime** (the family that survives in this project's arc — see
[[research-arc-conclusion]] / gamma-blast-research). Concretely, to test:

1. **Burst intensity → forward realized vol.** Does bursts/min (or ₹/min) at time *t* forecast the next
   30–60 min realised vol, beyond trailing vol? (A vol-regime read, the highest-prior shot.)
2. **Tail bursts → structure.** Do the ₹15cr+ surges mark breakouts / turning points, or just noise?
   (Race a same-magnitude mirror, per the project's placebo discipline.)
3. **Day-type from the print:burst ratio.** Does a burst-heavy/print-light morning predict a different
   afternoon than a print-heavy one?
4. **Directional content of bursts** once split by aggressor-classified signed volume (needs the raw
   tick archive, not just the events) — is signed burst flow any better than the print lean?
5. **Roll vs directional bursts** — separable only with calendar-spread detection.

Run these once the raw archive banks a real sample (`scripts/big_money_flow_ic.py` is the template;
a burst→vol harness is the natural sibling). Until then, **descriptive only.**

## Reading recipe (the checklist)

1. **Intensity first** — scan bursts-over-time: when was big money active vs idle?
2. **Read the tail** — rank bursts by notional; the p95+/max surges are the story, not the ₹3cr
   floor-huggers.
3. **Overlay prints** — print-heavy (aggressive) vs burst-only (stealth slicing)?
4. **Check location** — where (price) did the burst notional concentrate; does it agree with the prints?
5. **Direction from ΔOI + price (build-up quadrant), or the print aggressor lean — never the burst
   count alone.** A burst with OI rising into a rising price = new longs; OI falling = an exit.
6. **Stay honest** — param-dependent, roll-contaminated near expiry, unvalidated. A map of *where the
   money moved*, not a forecast of where price goes next.

Source: `scripts/big_money_analysis.py`, `app/big_money.py::_SymbolBurstTracker`. See also
`docs/big-money-tape.md` (the pipeline) and the analysis page (distribution + time + value profiles).
