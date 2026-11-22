# Feed recording and paper session summary

**Paper ops recording.** Ticks, GEX/orderflow features, and signals persist to
SQLite while the CryptoCubera server runs. A paper session bounds start→end for
OOS review. Never enables exchange place (`exec.live_place`) / Auto. No paid Tardis.

## What is recorded

| Table | Contents |
| --- | --- |
| `raw_ticks` | Venue/channel payloads (WS + chain/orderflow echoes) |
| `option_chain_snapshots` | Normalized chain snapshots |
| `gex_features` | Flip, walls, regime, model agreement, by-strike |
| `orderflow_features` | CVD / imbalance / funding / OI |
| `signals` | GO / NO-GO cards + veto reasons |
| `paper_trades` | Confirm ledger (sim / demo) |
| `paper_sessions` | Session window + optional end-of-session summary JSON |

Default DB: `data/crypto/cubera.db` (repo-root resolved; override with
`CRYPTO_DB_URL`). Relative `sqlite:///./…` paths no longer depend on cwd.

## Rotation

Configured in `config/crypto.yaml`:

```yaml
database:
  url: sqlite:///./data/crypto/cubera.db
  rotate_max_bytes: 524288000   # 500 MiB
  rotate_keep: 5
recording:
  enabled: true
```

On engine open, if the live file exceeds `rotate_max_bytes`, it is renamed to
`cubera.<UTC-stamp>.db` and the newest `rotate_keep` archives are retained.
WAL/SHM sidecars are cleared after a rotate. Disable with
`rotate_max_bytes: 0`.

## Recorded paper session

```bash
# Prefer the paper launcher (Manual Decision; live_place/auto off)
./run_crypto_paper.sh
# → http://localhost:8760 · Manual Decision
# SQLite records every snapshot; a paper_sessions row opens on boot
```

While running:

```bash
curl -s localhost:8760/api/session | python3 -m json.tool
curl -s localhost:8760/api/session/summary | python3 -m json.tool
curl -s localhost:8760/api/store/status | python3 -m json.tool
```

Export (live or after stop):

```bash
curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
curl -s 'localhost:8760/api/session/export?format=csv' -o paper_session.csv

# Companion CLI (no server required if DB already written)
./run_crypto_session_summary.sh json paper_session.json
python3 -m crypto.session_cli summary --format csv -o paper_session.csv
python3 -m crypto.session_cli status
```

Summary fields: `started_at` / `ended_at`, `go_count`, `confirms`,
`veto_signal_count` (+ tallies), `realized_pnl_usd` when closed trades exist,
plus tick / GEX / orderflow row counts.

Server shutdown closes the open session and snapshots the summary into
`paper_sessions.summary`.

## Knobs

| Env / config | Role |
| --- | --- |
| `CRYPTO_DB_URL` | SQLite URL (relative → repo root) |
| `CRYPTO_RECORDING=0` | Disable session open/close (tables still writable) |
| `CRYPTO_SESSION_ID` | Sticky session id for a desk run |
| `recording.enabled` | YAML default for session lifecycle |
| `database.rotate_*` | Size threshold + archive keep count |

## Non-goals

Live Confirm, Auto, production Delta India keys, paid Tardis / CoinGlass.
