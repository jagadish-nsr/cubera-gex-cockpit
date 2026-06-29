# Information Coefficient — GEX vs forward returns, by time of day

Status: empirical findings record. Reproduce with `python scripts/analyze_ic.py`
(`--horizon`, `--days`). **Decision-support only; exploratory, not an edge.**

---

## 0. TL;DR

The GEX **magnet pull** predicts the next hour **in the morning and not the
afternoon**. Morning IC = **+0.45** (day-block bootstrap 95% CI **[+0.18, +0.68]**,
excludes 0); afternoon IC = **+0.04** (CI includes 0). The signal is **sign-free**
(magnet location is invariant to the dealer-sign landmine) and it **beats trivial
reversion anchors** (day-open +0.22, VWAP-proxy +0.13). Suggestive, **not
established**: n=11 war-confounded days, 15-min cadence.

---

## 1. Method (point-in-time correct)

- **IC** = Spearman rank correlation between a **signal at time t** and the
  **forward log-return** over the next `horizon` minutes (default 60).
- **Point-in-time:** the signal uses only data at/≤ t; the return uses only data
  > t. Forward row = the one nearest `t + horizon`, within an 8-min tolerance.
- **Primary signal — magnet gap** = `(magnet − spot) / spot`. The pin hypothesis
  predicts **IC > 0** (price pulled toward the magnet). The magnet is the peak
  `|net_gex|` strike, which is **invariant to flipping the dealer-sign
  convention**, so this IC is **sign-free** — independent of the unvalidated
  India landmine (`[[dealer-sign-validation]]`).
- **Session split:** morning < 12:00 IST, afternoon ≥ 12:00.
- **Significance:** per-cell p-values, plus a **day-block bootstrap** (resamples
  whole days, because intraday rows within a day are correlated — the honest unit).
- Data: 219 samples over 11 usable days (Jun 2026), 15-min cadence.

---

## 2. Result — magnet gap (the pin signal)

| Window | IC | p | n |
|---|---|---|---|
| ALL | +0.18 | 0.009 | 219 |
| **MORNING (<12:00)** | **+0.45** | <0.001 | 90 |
| **AFTERNOON (≥12:00)** | +0.04 | 0.62 | 129 |

**By entry hour (IST):**

| 09 | 10 | 11 | 12 | 13 | 14 |
|---|---|---|---|---|---|
| +0.39 | **+0.46*** | **+0.51*** | −0.06 | +0.20 | −0.23 |

(* p<0.05.) Predictive content concentrates in **10:00–11:00 IST** and **collapses
to ~0 after noon**.

**Day-block bootstrap 95% CI (magnet gap):**

| Window | 95% CI | median | verdict |
|---|---|---|---|
| MORNING | **[+0.178, +0.676]** | +0.445 | **excludes 0** |
| AFTERNOON | [−0.346, +0.552] | +0.061 | includes 0 |

The morning effect survives whole-day resampling; the afternoon is
indistinguishable from zero.

---

## 3. Result — the anchor benchmark (does the magnet earn its keep?)

All three are reversion-to-anchor signals `(anchor − spot)/spot`; the question is
whether the **GEX magnet** carries information beyond trivial intraday reversion.

| Anchor | Morning IC |
|---|---|
| **GEX magnet** | **+0.450** |
| Day open | +0.215 |
| VWAP-proxy (running mean) | +0.129 |

**The GEX magnet wins decisively** — ~2× the day-open anchor and ~3.5× the
running-mean anchor. So the morning predictability is **not** merely "price reverts
to some intraday average"; the **gamma magnet location adds real information**.

---

## 4. Other signals (context)

- **flip gap** `(flip − spot)/spot`: IC **−0.22** overall (−0.27 morning). Negative
  IC ⇒ price tends to move *away from* the flip — consistent with the flip acting
  as a calm⇄wild boundary / repeller rather than an attractor. Also sign-free
  (flip location is convention-invariant).
- **net_gex** (level): IC **+0.24** overall, stronger morning. **Sign-DEPENDENT** —
  its IC sign would flip under the inverted convention, so unlike magnet/flip it is
  entangled with the dealer-sign landmine; do not over-read it.

---

## 5. Interpretation

- **Why morning?** The overnight/early option structure sets a magnet; price
  gravitates toward it through the morning, then by the afternoon the move is spent
  (or expiry-day dynamics take over) and the pull stops predicting. This is the
  opposite of assuming the pin is strongest into the close.
- **Sign-free is the key strength.** Because the magnet location doesn't depend on
  the dealer sign, "morning magnet pull" holds whether or not the sign is inverted
  — the most landmine-immune result in the project so far.
- It is **mean-reversion to a GEX anchor**, and §3 shows the *GEX* anchor beats
  trivial anchors — the content is in *which* anchor, not just "reversion exists."

---

## 6. Caveats (binding)

n=11 days, all inside the Israel–Iran war window; **15-min cadence is coarse for a
60-min horizon**; the **late afternoon is forward-truncated** (entries near 15:30
have no t+60 row, mechanically thinning/biasing the afternoon set). This is a
**hint worth pursuing**, not a validated edge. Re-run as clean, non-war,
full-session days accumulate; the 1-min expiry capture (`[[gamma-blast-feature]]`)
will also sharpen the short-horizon version.

---

## 7. Reproduce / related

- `python scripts/analyze_ic.py` — full report (also `--horizon 30`, `--days N`).
- Core is pure + hand-checkable (`tests/test_analyze_ic.py`).
- Related: `[[dealer-sign-validation]]`, `[[both-signs-dealer-sign-read]]`,
  `[[timeseries-market-structure-direction]]`.
