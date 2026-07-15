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
- **Today's range so far** — day low–high vs the expected range (a used/expected bar), plus the
  expiry-day **blast-compression flag** (`H–L before 1:45 < 1%` → "blast-friendly"). ⚠️ see the
  granularity caveat below.
- **News override** — a prominent banner when the day is headline-driven (the GEX read is confounded).
- **Gamma-blast verdict** (expiry days) — one plain verdict (RUN / FAKE), the **True-run score**
  (0–100), **OI change** (call-writer ΔOI) + wall-ahead, a call-vs-put crossover chart, and the
  two watch strikes. Fade logic is direction-correct: a FAKE reverts toward the **crossover strike**,
  not the (above-spot) magnet.
- **What fits this regime** — the illustrative vol-regime structure suggestion (paper only).
- **Positioning** — a simplified footprint (support/resistance forming + net call/put writing) and the
  **futures OI build-up in two windows: since-open AND a rolling last-30-min** (session vs recent —
  e.g. fresh shorts all day but a short-covering bounce in the last half-hour).

Everything is framed **context, not a prediction** — consistent with what the research validated
(vol-regime is real; direction is dead). Paper only; the human confirms every trade.

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
