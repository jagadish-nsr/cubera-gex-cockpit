# System Reliability Plan — Cubera GEX Cockpit

Assessment date: **2026-11-13**. Evidence: live tree + session evaluations through
12-Nov-2026. This is the durable plan document (the Cursor plan card only kept
todos; the body was truncated at create time — this file is the full write-up).

**Product constraint:** decision support for Nifty / Bank Nifty / Sensex. No
auto-execution. A silent bug (stale OI as live, wrong lot, fabricated premium)
can cause a real loss.

---

## Verdict

The ingest spine is **strong for a single-process desk app**: LKGS, circuit
breakers, validator, adaptive poller, spot-stale hold, event-loop watchdog
(prod 8640), Admin health daemon, and HTTP 503 when no book. Session evals show
the largest remaining risk class is **“looks healthy but wrong or empty”** —
fabrication seeds, silent UI `!res.ok` returns, shallow `/api/health`, and hard
gates that refuse correctly but were not wired with required context
(12-Nov Ideas VWAP blackout).

---

## Architecture (reliability spine)

```mermaid
flowchart LR
  DhanWS[Dhan_WS_spot] --> SpotTicker
  DhanREST[Dhan_NSE_chain] --> LiveFetcher
  LiveFetcher --> LKGS[LKGS_and_pickle]
  LiveFetcher --> RecPub[rec_publisher]
  SpotTicker --> Telemetry[/ws_telemetry]
  LKGS --> CockpitAPI["/api/cockpit-data"]
  RecPub --> IdeasAPI["/api/recommendations"]
  CockpitAPI --> UI[cockpit.js_tabs]
  IdeasAPI --> UI
  HealthDaemon[health_daemon] --> AdminDB[admin_ops_db]
  Readiness["/api/health_readiness"] --> AdminUI[Admin_checklist]
```

Single FastAPI process hosts ingest + HTTP. Watchdog restarts the whole process
on loop stall (prod only). See also [process-isolation-design.md](process-isolation-design.md).

---

## Tab / workflow scorecard

| Surface | Going well | Not going well (pre-fix) | Fix in this pass |
|---------|------------|---------------------------|------------------|
| Cockpit | Poll + WS overlay; 503 / toast on missing book; degraded tags | Client spot seed 24500; silent side paths | Seeds cleared; reliability banner |
| GO / Recommendations | Off-hours empty; placeholder withhold; VWAP refuse | Publisher missing `latest_bar` (12-Nov); silent prefs | Hard gates + context events; fail-loud fetches |
| Sentinel | Tape + signals; reseed operator-only | `!res.ok` silent return | Toast + DEGRADED banner |
| Target Engine | Live spot/IV gates improving | Hardcoded expiry `2026-10-19`; HTML fake targets | Calendar expiry; HTML `—` |
| Options Analytics | Six-card contract | HTML `+1.8%` skew seed | Cleared |
| Market Intel / Commentary / Journal / Efficiency / Scorecard / Ops / Reports | Mostly read paths | Silent catches | Fail-loud via `noteFetchResult` |
| Admin | Token/feed/WAL/gap alerts; Observability | Shallow health; no open checklist; WAL warn-only | Deep readiness; session checklist; PASSIVE checkpoint |
| Spot / chain ingest | CB + LKGS + spot hold | Dhan SPOF; single process | Documented Phase B isolation |
| Expiry / lots | Named lots in config | Holiday soft-fail returned unrolled date | Fail loud |

---

## What was already going well

1. One GEX convention (`short_call_short_put`); lots VERIFY in config.
2. Request path does not call Dhan during live hours (reads `live_fetcher` / LKGS).
3. Resilience pack: circuit breaker, LKGS, validator, freeze, adaptive poller.
4. Prod event-loop watchdog (`COCKPIT_WATCHDOG=1` on 8640).
5. Admin health daemon (token, feed silence, gaps, obs p95 / loop lag).
6. Anti-fabrication culture in recent GO / Ideas hardening (session evals).

---

## What was not (prioritized)

### P0 — real-money / false calm

1. Residual fabrication defaults (HTML seeds, client spot, reprice expiry, UI `0.65` fills).
2. Shallow `/api/health` (version only — no chain/feed/CB age).
3. Silent UI failures (Sentinel and others returned on `!res.ok` with no toast).

### P1 — survival / correctness ops

4. Single-process ingest+HTTP (stall = full outage).
5. WAL warned but never checkpointed.
6. Holiday calendar soft-fail.
7. Hard gates without required-context wiring (Ideas VWAP pattern).

### P2 — polish / measurement

8. Per-tab seed / catch audit leftovers; TV `allow_symbol_change`.
9. No documented SLIs/SLOs; session-open checklist was manual only.

---

## Implementation roadmap (executed 2026-11-13)

| ID | Work | Status |
|----|------|--------|
| p0-fabrication | Kill fabrication defaults | Done |
| p0-readiness | Deep `/api/health` + `?ready=1` | Done (`app/readiness.py`) |
| p0-ui-loud | Toast + reliability banner | Done |
| p1-process | Isolation design + WAL checkpoint + holiday fail-loud | Done |
| p1-gates | `app/hard_gates.py` + publisher regression | Done |
| p2-tab-hardening | Analytics seeds, TV popout, silent catches | Done |
| p2-sli | [sli-slo.md](sli-slo.md) + Admin session checklist | Done |

---

## Operator checks (next open)

1. `/api/health` version matches promoted tag; `ready` / `readiness.probes` populated.
2. After ~09:16: Ideas count &gt; 0 or refuse ≠ Missing session VWAP.
3. Admin → Session Open Checklist shows PASS (or named FAIL reasons).
4. `?ready=1` returns 503 only when session open and probes fail — not for CD liveness.

---

## Related docs

- [sli-slo.md](sli-slo.md)
- [process-isolation-design.md](process-isolation-design.md)
- [UI_MIGRATION_LESSONS_LEARNED.md](UI_MIGRATION_LESSONS_LEARNED.md) (open fabrication gaps — many closed this pass)
- [session-evaluation-2026-11-12.md](session-evaluation-2026-11-12.md)
