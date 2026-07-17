# Simple dashboard (`app/simple_dashboard.py`)

A **decision-first, plain-language** alternate face of the GEX dashboard, built for a
non-technical trader (paper only). It keeps the load-bearing, validated reads and drops the
quant noise (percentiles, raw |GEX| magnitudes, Spearman ICs / p-values, participant counts,
dealer-sign model talk, the per-strike tables). Optimised for the **expiry-day gamma-blast**
strategy, with a minimal **quiet-day** fallback on non-expiry sessions.

Run it alongside the main dashboard:

    streamlit run app/simple_dashboard.py

Design mockup (static, real 15-Jul numbers): the published Artifact `simple_dashboard.html`.

## What it shows
- **Market gauge** — spot, a calm/normal/wild mood chip, and the **expected day range in points**,
  tiered by the |vanna|/charm bucket (`regime_daily_move_now`) — e.g. *"Calm → ≈180 pts, likely
  24,000–24,180"*. This replaces the percentile wording with a concrete number.
- **Today's range so far** — plotted on the **wall-band axis** (put wall → call wall): today's
  low→high is a highlighted segment, thin colour ticks mark **put wall** (🟢 left), **magnet**
  (indigo), **call wall** (🔴 right), and **▲ is the current spot** — so the walls are visible even
  though they sit outside the realised range (a plain low→high axis is the fallback when walls are
  missing). Plus the expiry-day **blast-compression flag** (`H–L before 1:45 < 1%` → "blast-friendly").
  The low/high are **seeded from today's saved snapshots** (union of the `timeseries` + `oi_strikes`
  `spot` columns), so opening/restarting mid-day recovers the whole session, then extended with the
  live spot. ⚠️ see the granularity caveat below.
- **News override** — a prominent banner when the day is headline-driven (the GEX read is confounded).
- **Ignition timing + alert** (expiry days) — once a crossover fires, the hero shows the **score
  trajectory since the cross** (e.g. `38 → 55 → 62`), the **time-to-first-TRUE**, and a classification
  from the 1-min study (§7d of gamma-blast-research): **⚡ Fast TRUE** (≤5 min — best), **Slow TRUE**
  (late/weaker), **Watching** (needs score ≥45 by ~3 min), or **No ignition** (>5 min → usually
  fizzles, fade). A **🔔 sound + toast alert** (sidebar toggle) fires once when the score first hits
  **45 within ~5 min** of a cross (the "likely TRUE" cutoff) and again on a Fast TRUE. Sound is
  best-effort (browser autoplay may need a click first); the toast is the reliable channel. This is a
  **confirmation-quality** read only — the [B1] backtest showed the edge doesn't survive spread.
- **Gamma-blast verdict** (expiry days) — one plain verdict (RUN / FAKE), the **True-run score**
  (0–100), **OI change** (call-writer ΔOI) + wall-ahead, a call-vs-put crossover chart, and the
  two watch strikes. Fade logic is direction-correct: a FAKE reverts toward the **crossover strike**,
  not the (above-spot) magnet.
- **What fits this regime** — the illustrative vol-regime structure suggestion (paper only).
- **Positioning ("Where the money is building")** — two cards:
  - **Options footprint** — the support/resistance forming + net call/put-writing summary, PLUS a
    **trailing-window strip (60 / 30 / 15 / 5-min)** showing *fresh writing* near spot for the
    **current expiry only**: 🟢 Puts→support / 🔴 Calls→resistance / balanced / thin, with the net add.
  - **Futures OI build-up** — since-open PLUS the same **60 / 30 / 15 / 5-min** trailing windows
    (price × ΔOI: long build-up / short build-up / covering / unwinding), aggregate (rollover-netted) OI.

### The rolling windows — how they're computed and the expiry-noise handling
- **Current expiry only (options).** Each window's OI change is the nearest-expiry `*_doi_near`
  (ΔOI vs prev close) differenced between the latest snapshot and the snapshot ~N min earlier; the
  constant prev-close cancels, leaving the window's **current-expiry** OI change. (Falls back to
  differencing all-expiry OI *levels* on pre-upgrade CSVs.)
- **Adds only.** Per strike only OI *additions* count (unwinds clipped to 0). This is the key
  expiry-day guard: late on expiry the current weekly's OI **decays/churns** (settlement,
  pin-hedging, gamma-blast crossings), and adds-only stops that decay from masquerading as
  directional flow — a purely-unwinding window correctly reads **"thin"**. The dominant side must
  hold ≥ ~60 % of adds, else **"balanced."**
- **Short windows are noisy on expiry — and shown as such.** At the ~3-min live cadence a "5-min"
  window is often 1–2 snapshots, so partial windows are flagged (`≤`) and on **expiry day** the
  5/15-min rows are dimmed with a caption steering to the **30/60-min** reads. All four stay
  visible so the churn is *seen*, not hidden.
- Directional options-OI edge is ~zero at scale (research arc), so this is framed **context, not a
  prediction** — same as everything else here.

Everything is framed **context, not a prediction** — consistent with what the research validated
(vol-regime is real; direction is dead). Paper only; the human confirms every trade.

## Self-sufficient capture (no separate logger required)
On every **in-session** live pull the dashboard persists its own history: `append_if_due` writes the
timeseries row and, lock-step, `append_strike_rows` writes the per-strike OI — the same day files the
range seed and the options OI windows read back. Futures snapshots are captured the same way
(`capture_futures_snapshot`, in-session only). So the range + windows fill from the dashboard's own
refreshes; a co-running `scripts/log_timeseries.py` still shares the files (a `min_gap` dedup guard
prevents double-writes), it's just no longer required. Post-close pulls are **not** written (a frozen
end-of-day chain must not poison the day's file) — matching the main dashboard's discipline.

## ⚠️ Known limitation — day high/low granularity (range + blast-compression)
The **day high/low** — and therefore the *"range so far"* bar and the expiry-day
**blast-compression precondition** (`high−low before 1:45 < 1%`) — are derived from the captured
**spot snapshots** taken at the dashboard/logger refresh cadence (~3 min on live NSE). This means:

- **We can MISS intraday extremes.** Any spike that happens *and reverts between two snapshots* is
  invisible, so `max(snapshots)`/`min(snapshots)` is a **lower bound** on the true day range — the
  captured high is ≤ the real high, the captured low is ≥ the real low.
- **Consequence for the blast filter:** an under-counted range can **false-flag a day as
  "compressed / blast-friendly"** when a missed spike actually breached the 1% threshold. Treat the
  range value and the compression flag as **approximate (optimistic on tightness)**, not exact.

**Partial mitigations available today** (not yet wired): read NSE's own reported intraday high/low
field where present, and/or use the **Dhan 1-min bar** highs/lows for the historical/EOD side (each
1-min bar's high/low captures within-bar extremes to 1-min granularity — good enough that sub-minute
spikes are negligible for this use).

**Planned fix — DHAN API websockets (realtime).** Moving live capture to the Dhan **websocket** tick
feed gives a continuous price stream, so the running high/low tracks **every tick** and the
granularity gap closes. Until that lands, the range + compression flag stay flagged as approximate in
the UI. See [[dhan-intraday-data]] for the current Dhan endpoints and caps.
