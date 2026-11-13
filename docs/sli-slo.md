# SLIs / SLOs — Cubera GEX Cockpit

Decision-support reliability. A silent miss (stale OI as live, empty Ideas while
the UI looks healthy) is worse than a loud 503.

## Service level indicators

| SLI | Measurement | Where |
|-----|-------------|--------|
| **Liveness** | Process answers `GET /api/health` with version | CD probe |
| **Readiness** | `ready=true` when F&O session open | `/api/health` + `app/readiness.py` |
| **Chain freshness** | Max publish age across NIFTY/BN/SX ≤ 120s during ingest | readiness probes.chains |
| **Spot freshness** | Spot quote age ≤ 45s during session | readiness probes.spots |
| **Feed freshness** | WS last tick age ≤ 30s during 09:15–15:30 | readiness probes.feed / health daemon |
| **Circuit health** | No OPEN circuit on any index | readiness probes.circuits |
| **Ingest heartbeat** | When `COCKPIT_INGEST_EXTERNAL=1`, heartbeat age ≤ 90s in session | readiness probes.ingest / `data/journal/ingest.heartbeat` |
| **Ideas gate wiring** | After ~09:16, Ideas refuse reason ≠ `Missing session VWAP` for a live bar | Admin session checklist |
| **Zero fabrication** | Execute / serialize / HTML seeds never invent premium/strike/spot | hard_gates + UI lessons |
| **Token floor** | `hours_left ≥ 2` pre-open and in session | readiness probes.token / checklist `token_hours` |
| **Degrade mode** | Enum `OK` / `OFF_SESSION` / `TOKEN_DEAD` / `FEED_DOWN` / `CHAIN_DEGRADED` / `NOT_READY` | readiness.degrade_mode → reliability banner |
| **NSE fallback** | Active scrape books flagged (not silent) | readiness probes.nse_fallback + checklist |
| **Spot path** | Header fed by `ws` vs `rest` vs `snapshot` | readiness probes.spot_source / Admin KPI |

## Service level objectives (session)

| SLO | Target | Window |
|-----|--------|--------|
| Readiness during continuous session | ≥ 99% of minutes `ready=true` (excluding known CB cooldown ≤ 120s) | 09:20–15:20 IST |
| Spot feed silence CRITICAL alerts | 0 unexplained (>30s with WS started) | per session |
| Watchdog mid-session restarts (serve) | 0 | per session |
| Ingest heartbeat gaps (external mode) | 0 stretches > 90s while session open | per session |
| Ideas VWAP blackout | 0 sessions with continuous `rec.refused` Missing session VWAP after first 1m bar | per session |
| Fabricated paper ticket | 0 (route must 400) | always |

## Operator session-open checklist (automated)

Admin tab → Session Open Checklist (`GET /api/admin/session-checklist`):

1. Health version present
2. Dhan token ≥ 2h (blocking before 09:15 and during session)
3. Readiness ready (or off-session) — see `degrade_mode`
4. At least one fresh chain + spot
5. Feed live when market open
6. No OPEN circuits
7. Ingest heartbeat OK when dual-process (EXTERNAL) mode
8. After 09:16 IST: Ideas batch present or refuse ≠ Missing session VWAP
9. Book from Dhan (not NSE scrape) when market open

Manual confirmations that stay human: GO FIRE under trend, BN shared-gamma soft flag when walls equal.

## Error budget policy

- Burn budget → investigate Admin Observability (chain p95, loop lag, CB) before changing model thresholds.
- Do not lower CONT / VWAP gates to clear an Ideas blackout — fix wiring first (`app/hard_gates.py`).

## Enforcement (Phase C)

| Detector | Where | Alert |
|----------|-------|-------|
| Minute readiness rollup | `slo.readiness.minute` ops event | — |
| Ready=false ≥ 3 consecutive session minutes | `app/slo_monitor.py` + health daemon | `SLO\|readiness-streak` CRITICAL |
| Sustained `Missing session VWAP` refuses (≥20 / 10m) | health daemon | `SLO\|vwap-blackout` CRITICAL |
| Tape bars ≪ expected (live lag or post-close incomplete) | health daemon | `SLO\|tape-completeness` WARNING/CRITICAL |
| Post-open smoke | `scripts/session_open_smoke.py` / `GET /api/admin/session-smoke` | exit 1 / Admin button |


