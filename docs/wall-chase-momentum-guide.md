# Wall-Chase Momentum — Field Guide

Decision-support only. Human confirms. No auto-execution.

Walls are **OI/gamma concentration** under `short_call_short_put`. They are a
map, not a paid CE/PE forecast. NSE hedging is unmeasured; OI lags.

---

## 1. What this is (and is not)

**Wall-chase** = successive same-side call (or put) wall **breaks / migrations**
while price and futures CVD move with that side, confirmed by Sentinel
**CONTINUATION_** or **CVD_DIVERGENCE_** tape.

It is **not** a gamma-blast expansion. Blast remains a separate expiry
parity / location card (`⚡ EXPIRY BLAST` / `📍 EXPIRY PIN`). Do not wait for
blast ACTIVE on a rolling-wall day.

Empirical sample (prod, 6–19 Nov 2026): **20 STRONG** episodes; **6** peaked at
tape scores **65–69** and were blocked by the old hard gate of 70 (including
Sensex 19 Nov ~13:40–14:35).

---

## 2. Where to look on Cubera (trader path)

| Step | Surface | What to watch |
|-----:|---------|----------------|
| 1 | **Home → Alert tape** (top of cockpit) | Badge contains `WALL CHASE ×N` and/or `CONTINUATION BULL/BEAR` / `CVD DIVERGENCE…`. Stance **CALL WATCH** or **PUT WATCH**. Read Outcome / Drivers / Risk. |
| 2 | **GEX KPIs / strike ladder** | Call wall (or put wall) **migrating** with spot — through `TEST_*` into `ABOVE_CALL_WALL` / `BELOW_PUT_WALL` / `ABOVE_MAGNET`. |
| 3 | **Session Commentary** | Tags `WALL_BREAK`, `WALL_MIGRATION` stacking on the same side within ~45 minutes. |
| 4 | **Sentinel Tape** tab | `CONTINUATION_BULL/BEAR` or `CVD_DIVERGENCE_*`, score **≥ 65** in chase zones (hard floor **70** still applies to absorption / exhaustion / other types). CVD rising with the move. |
| 5 | **Gamma blast card** | Treat as **risk map**, not the momentum trigger. PIN/COILING/FAKE defending does **not** cancel a wall-chase watch. |
| 6 | **Browser push + India temple bell** | Cockpit polls `/api/wall-chase-watch` for **Nifty, Bank Nifty, and Sensex** every ~12s. Rising-edge CALL/PUT WATCH (side+stance only) fires toast/Notification + **twin India temple bell**, rate-limited **10 min/index** and shared **3 min** with the tape alert bell. Tape rings only on CALL/PUT WATCH stance flips (not every TAPE_*/zone scenario). Audition: 🛕 Temple Bell. |
| 7 | **Session Commentary** | The same rising edge **publishes into that index’s Session Commentary** (tag `WALL_CHASE`): Outcome / Drivers / Risk, map (spot, walls, magnet, flip, zone), tape type+score, event headlines, trigger/kill, and **stretch** — run from first broken/migrated wall, span, next map strike, and potential stretch (= run so far + one strike step). Ladder geometry only; not a paid forecast. |

Kill / stop language on a call chase: **beyond the last broken call wall, never at the magnet.**

---

## 3. Criteria (engine)

Shared module: `analytics/wall_chase.py`. Consumers: `analytics/market_alert.py`,
cockpit alert-tape JS, cockpit payload `wall_events` from recent commentary.

1. **≥ 2** same-side `WALL_BREAK` / `WALL_MIGRATION` in the last **45 minutes**
2. **Chase-friendly zone**
   - CALL: `ABOVE_CALL_WALL`, `ABOVE_MAGNET`, `TEST_CALL`
   - PUT: `BELOW_PUT_WALL`, `BELOW_MAGNET`, `TEST_PUT`
3. **Momentum tape** agreeing side: `CONTINUATION_*` or `CVD_DIVERGENCE_*` with
   score **≥ 65** (soft floor only in chase zones)
4. **Promote** extension zones from STAY OUT → **CALL WATCH** / **PUT WATCH**
5. **Badge** `🏃 WALL CHASE ×N` when events + agreeing tape align — separate from
   `⚡ EXPIRY BLAST`

---

## 4. Map-lag / open thrust (not wall-chase)

Large spot burst while the wall-chase composite **fails** (Sensex 19 Nov
09:30–10:00 ~+520: PUT migrates, CONT 45, ABSORPTION_BEAR 100, PINNED).

| Gate | Quiet rule |
|-----:|------------|
| Move | ≥ **120 / 300 / 400** pts in ~30m (Nifty / BN / Sensex); after 10:45 IST ×1.5 |
| Fail reasons | ≥ **2** of: &lt;2 same-side walls, soft/missing mom tape, conflict tape, non-chase zone |
| Stance | Always **STAY OUT** — never CALL/PUT WATCH |
| Noise | Soft badge `🗺 MAP LAG` + drivers only. **No** push, **no** temple bell, **no** commentary publish, **no** scenario hijack (no brass-bell flap) |
| Suppress | Same-side wall-chase already qualifies → map-lag stays off |

Week sample (12–19 Nov): morning-heavy; most “price ran, engine quiet” cases were
LT2 walls + PINNED/conflict — not a second notify path.

---

## 5. Worked shape (Sensex 19 Nov 14:00)

- Spot/FUT through / above rolling call walls 82,500 → 82,600 → 82,700 → 82,800
- Commentary: wall migrate + WALL_BREAK
- Sentinel: CONTINUATION_BULL **69** at 14:00 (would miss old ≥70 gate)
- Alert tape **before** fix: STAY OUT + “no fresh print ≥70”
- Alert tape **after** fix: CALL WATCH + WALL CHASE + tape prefix; blast stays quiet unless expansion ACTIVE

---

## 6. Related manuals

- [Master User Guide — Alert tape](/guide/user_guide) — Outcome / Drivers / Risk
- [OrderFlow Sentinel](/guide/orderflow_guide) — CONTINUATION / CVD signal types
- [Gamma-Blast Scoring](/guide/gamma_blast_scoring_guide) — expiry blast ≠ wall-chase
- [Session Commentary](/guide/session_commentary_guide) — WALL_BREAK / WALL_MIGRATION tags
