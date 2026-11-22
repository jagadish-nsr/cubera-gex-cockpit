# Local paper trading runbook

**Preference: LOCAL PAPER TRADING ONLY.** Never enable Live Confirm. Never require
`DELTA_LIVE_*` keys. Keep `exec.live` off.

## One-command start

```bash
# from repo root — sets CRYPTO_FLAG_EXEC_PAPER=1 + CRYPTO_FLAG_EXEC_CONFIRM=1 only
./run_crypto_paper.sh
# → http://localhost:8760
```

Equivalent:

```bash
CRYPTO_FLAG_EXEC_PAPER=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE=0 \
python3 -m crypto.server --port 8760
```

No API keys needed. Confirm records **local sim fills**. Optional Delta
demo/testnet keys (`DELTA_PAPER_*` from https://testnet.delta.exchange/) are
allowed; production India hosts are refused by the paper adapter.

## What stays off

| Knob | Value | Why |
| --- | --- | --- |
| `exec.live` / `CRYPTO_FLAG_EXEC_LIVE` | **off** | Live Confirm places real capital |
| `DELTA_LIVE_API_KEY` / `SECRET` | unset | Not needed for paper |
| `CRYPTO_LIVE_CONFIRM_ACK` | unset | Live gate stays closed |
| `exec.auto` | **off** | Auto is never supported |

Mode badge must show **Advisory** or **Paper Confirm** — never **Live Confirm**
on this path.

## Smoke checklist

1. **Start** — `./run_crypto_paper.sh` → open http://localhost:8760
2. **Mode badge** — topbar reads **Paper Confirm** (not Live)
3. **GO card** — wait for a GO (or use fixtures/replay); Confirm paper button enabled
4. **Confirm** — one tap submits a paper/sim bracket
5. **Ledger** — Operator → open intents / PnL note updates; or:
   ```bash
   curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
   curl -s localhost:8760/api/exec/status | python3 -m json.tool
   ```
6. **Kill switch** — two taps (arm → confirm) cancels open intents and flips Confirm off
7. **Export trades**
   ```bash
   curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
   curl -s 'localhost:8760/api/exec/ledger/export?format=json' -o paper_trades.json
   ```
   Or use Operator → Export CSV / Export JSON.
8. **Session summary** (GO / confirms / vetoes / PnL / tick counts)
   ```bash
   curl -s localhost:8760/api/session/summary | python3 -m json.tool
   curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
   curl -s 'localhost:8760/api/session/export?format=csv' -o paper_session.csv
   # after stop (or anytime against the SQLite file):
   ./run_crypto_session_summary.sh json paper_session.json
   ```
   Feed ticks + features keep landing in `data/crypto/cubera.db` for the whole
   run. See [feed-recording.md](feed-recording.md).

## API Confirm (optional)

```bash
curl -s -X POST localhost:8760/api/exec/confirm \
  -H 'content-type: application/json' \
  -d '{"actor":"cli"}' | python3 -m json.tool
```

## Kill via API

```bash
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":false}'
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":true}'
```

## Non-goals

Live Confirm, Auto, production Delta India keys, paid Tardis, Gemma/multi-asset
beyond existing flags.
