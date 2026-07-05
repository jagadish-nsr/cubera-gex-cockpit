# True-vs-fake gamma-blast score

A **sign-free** grade for whether a strike-crossing (the gamma-blast pivot) will
**sustain** (a "true" blast that runs) or **fade** (a "fake" blast that reverts to
the pin). Companion to the parity-based crossover watch in `app/gammablast.py`.

## The mechanic (why it's not folklore)
When spot crosses a fixed strike **K**, the option side that goes **ITM** is the
writers' pain side — **calls** when spot rises through K, **puts** when it falls
through K. Reading the captured OI + premium data around every near-money crossing
on the fine-cadence expiry days (2026-06-29, 07-01, 07-02, 07-03), a **sustaining**
break and a **fading** one look different at the crossed strike and one strike ahead:

| | TRUE (sustains) | FAKE (pins / caps) |
|---|---|---|
| ITM-side OI at K | **falling** (writers covering = fuel) | flat / **rising** (writers defending) |
| ITM-side premium | **expanding** | flat / compressing |
| wall one strike ahead | thin or **eroding** (clear path) | **big & intact** (absorbs the move) |

Two clean contrasting events: **07-03 down-cross of 24350** — put writers covered
−36%, premium +18%, wall ahead eroding → ran ~90 pts (**TRUE**). **07-02 up-cross of
24150** — call writers covered only −12%, but a huge intact call wall at 24200 (2.4×
the strike's OI) → kissed 24200 and faded (**WEAK/capped**).

## The score (`app/gammablast.py::blast_score`)
Three sign-free components, each 0–1, on the crossed strike's window from the crossing
forward plus the next strike ahead:

```
ignition = ITM-side writers covering   (−cover_frac, saturates at 30% OI shrink)
thrust   = ITM-side premium expansion  (saturates at +20%)
path     = 1 − wall-ahead intactness   (big, non-eroding wall ahead ⇒ cap)
score    = 100·(0.5·ignition + 0.2·thrust + 0.3·path)
```

- **≥60 → TRUE-leaning** (break sustaining) · **35–60 → WEAK/unconfirmed** ·
  **<35 → FAKE-leaning** (pin/cap).
- ITM-side OI **rising** (writers actively adding at K) forces **FAKE (defending)**
  regardless of score.

Saturation scales (`_FULL_COVER=0.30`, `_FULL_THRUST=0.20`) were calibrated on the two
NIFTY events above.

## Replay / calibration (`scripts/analyze_blast.py`)
Replays the score over every near-money crossing in the captured `oi_strikes` days and
compares it to the **realized** forward continuation (did spot hold past K over the next
40 min?). Point-in-time: the score uses only OI/premium from the crossing forward; the
move is measured after.

- **NIFTY: Spearman(score, end-continuation) = +0.86** (n=15) — every "writers
  defending" fake reverted; every high score held.
- **BANKNIFTY: +0.37** (n=11) — weaker. The thresholds were fit on NIFTY; BankNifty's
  wider strikes / different microstructure don't transfer cleanly (some pin/cap-graded
  moves still held). Re-calibrate per index before trusting it there.

```
python scripts/analyze_blast.py            # NIFTY
python scripts/analyze_blast.py BANKNIFTY
```

## Dashboard
Under **⚡ Gamma-blast crossover watch**, once an anchored strike (magnet / 1:45 ATM)
prints a crossover, a **Blast score N/100 + grade** appears with its drivers
(ITM-writer ΔOI, premium move, wall-ahead ratio). The live path stores per-anchor OI in
the session points and reads the wall ahead from the current snapshot.

## Honest limits
- **Tiny sample** — a handful of expiry-ish days, all quiet/range days where moves got
  absorbed. No clean cascade-blast day is in the journal yet. The `data/journal/
  gamma_blast/` 1-min capture is still empty; run it on expiry days to add real blasts.
- **Overlapping crossings/days autocorrelate** — the +0.86 is encouraging, not proven.
- **Calibrated on NIFTY** — re-fit `_FULL_COVER`/`_FULL_THRUST` per index.
- **Sign-free staying-power grade, NOT a direction call.** Whether the pivot resolves up
  or down still depends on the unvalidated India dealer sign (see CLAUDE.md landmine).
  This grades *how hard a break, once it happens, is likely to run* — you confirm.
