# Session Evaluation — DAYNAME, DD-Mon-YYYY

## Reliability scorecard

Session: `YYYY-MM-DD` · cite SLI burn from `docs/sli-slo.md`.

| Metric | Value | Notes |
|---|---|---|
| Process version | | Must match promoted tag |
| Degrade mode (as-of) | | From `/api/health` readiness |
| Ready minutes (session) | | From `slo.readiness.minute` — do not invent |
| Not-ready minutes | | Target burn ≤ 1% of 09:20–15:20 |
| Watchdog restarts | | SLO: 0 mid-session on serve |
| Ingest heartbeat | | Dual-process age_s |
| Session checklist | PASS/FAIL | Failed ids |
| VWAP / Ideas refuses | | Zero sustained Missing session VWAP |

Helper: `app.reliability_scorecard.build_reliability_scorecard(...)` or
`GET /api/admin/reliability-scorecard`.

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | | |
| Tape completeness | | |
| Trade Ideas | | |
| Sentinel OF | | |
| GO Desk | | |
| Stability | | |

## What burned / what was loud

-

## Follow-ups

1.
