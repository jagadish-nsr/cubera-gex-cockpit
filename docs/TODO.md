# TO-DO / backlog

Running backlog of planned work. Newest entries on top. Each entry is timestamped
(IST). Decision-support project — verify-live before trusting NSE data.

---

## 2026-07-02 03:12 IST — Precise per-strike build-up classification (fix the expiry/window mismatch)

**Why:** the dashboard "Top support/resistance builds" tables (footprint section,
`app/oi_footprint.py::classify_buildup`) tag each strike as writing / long build-up /
short covering / long unwinding from the **price+OI matrix**. It works, but the tag is
**approximate** because of a data mismatch:
- **ΔOI** (`call_doi`/`put_doi`) is `changeinOpenInterest` **summed across ALL expiries**,
  measured **vs previous close**.
- **Premium change** is computed intraday (**first → latest snapshot LTP**), and the
  captured `call_ltp`/`put_ltp` is the **NEAREST expiry only**.
So price and OI use different expiries AND different windows. Fine for near-money,
nearest-expiry-dominated strikes; wrong for multi-expiry or far strikes.

**Fix (schema addition to the capture):** log, per strike, the fields that let price and
OI share the same expiry + window:
1. Capture **per-strike, per-nearest-expiry OI + OI-change-vs-prev-close** (not summed
   across expiries) so ΔOI matches the LTP's expiry. OR additionally store the
   **previous-close premium** per strike so "premium change vs prev close" lines up with
   `call_doi` (which is already vs prev close).
2. Then `classify_buildup` uses same-expiry, same-window ΔOI + Δpremium → an exact tag.
3. Update `STRIKE_FIELDS` (`app/timeseries.py`) + `per_strike_oi`/`nearest_expiry_premiums`
   (`data/nse.py`); keep back-compat with existing CSVs (missing columns → degrade to the
   current approximate tag).

**Scope note:** low urgency — the approximate intraday tag is usable now. Do this when
tightening the footprint, ideally together with the participant-OI work below (both are
positioning-precision improvements). See `memory/positioning-footprint-suite.md`.

---

## 2026-07-02 02:55 IST — Add NSE participant-wise OI capture  (STARTED, endpoint verified; parser + wiring pending)

**Why:** the direct **big-player positioning** read (the thesis in
`memory/positioning-footprint-suite.md`): GEX/VIX are *responses* to news; participant
OI is *who is actually positioned*. It is **sign-free** and is the cleanest shot at
finally resolving the **dealer-sign landmine** (it tells us WHO writes index options —
Client/DII/FII/Pro long vs short calls/puts).

**Endpoint — VERIFIED LIVE 2026-07-01 (works; the www host 404s):**
```
https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
```
- Fetch with the primed session (`data.nse._primed_session`); host is `nsearchives.nseindia.com`.
- Daily EOD file, one per trading day. `DDMMYYYY` (e.g. `fao_participant_oi_01072026.csv`).
- There is a sibling `fao_participant_vol_DDMMYYYY.csv` (volume) — same shape.

**File format (verified, 01-Jul-2026):**
- Line 0: title `""Participant wise Open Interest ... as on Jul 01, 2026""` (has stray quotes; skip).
- Line 1: header (note some columns have trailing spaces — strip). **Column order:**
  `Client Type, Future Index Long, Future Index Short, Future Stock Long, Future Stock Short,`
  `Option Index Call Long, Option Index Put Long, Option Index Call Short, Option Index Put Short,`
  `Option Stock Call Long, Option Stock Put Long, Option Stock Call Short, Option Stock Put Short,`
  `Total Long Contracts, Total Short Contracts`
  ⚠️ note the option order is **Call Long, Put Long, Call Short, Put Short** (not Call L/S then Put L/S).
- Rows: `Client`, `DII`, `FII`, `Pro`, `TOTAL` (numbers are contract counts).

**What to build (next session):**
1. `data/nse.py`: `fetch_participant_oi(date)` (build URL, primed GET, return text) +
   `parse_participant_oi(text) -> dict` keyed by participant (Client/DII/FII/Pro) with
   the index F&O fields. Focus on **index** columns (Future Index Long/Short,
   Option Index Call/Put Long/Short); stock columns are out of scope for now.
   Fail-soft (best-effort, like VIX/USDINR) — a miss must not break a GEX snapshot.
2. Derive per-participant signals: net index-future position (Long−Short), and net
   option-writing (short calls+puts − long) — i.e. **who is net SHORT options** (the
   dealer-sign clue). FII net fut, Pro/Client option-writing skew.
3. Capture: it's DAILY (not intraday), so a once-a-day fetch (piggyback the last
   session capture, or a separate small daily task) into a new journal
   `data/journal/participant_oi/<SYMBOL-agnostic>/YYYY-MM-DD.csv` (participant data is
   market-wide, not per-index — store once, not per index).
4. Dashboard readout: "Big players today — FII net futures {+/-}, option-writing by
   Client/Pro/FII" + a **dealer-sign hint** (if Client/Pro are the dominant option
   writers → supports the inverted-sign lean). Sign-free.
5. Tests: hand-check `parse_participant_oi` on the captured sample (Client row:
   Future Index Long 251066 / Short 63543; Option Index Call Long 2163161 ... etc.).

**Caveats:** EOD only (T+0 after close), market-wide (not Nifty-vs-BankNifty split),
index+stock combined file (use the *index* columns). Cross-reference with
`[[both-signs-dealer-sign-read]]` to see if participant option-writing corroborates
the inverted dealer sign.

**Sample row captured (Client, 01-Jul-2026):** FutIdx L 251066 / S 63543 ·
OptIdx CallL 2163161 / PutL 1888743 / CallS 2028147 / PutS 2586510.
