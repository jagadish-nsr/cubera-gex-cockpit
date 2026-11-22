# Local paper trading runbook

**Preference: LOCAL PAPER TRADING ONLY.** Never enable Live Confirm. Never require
`DELTA_LIVE_*` keys. Keep `exec.live` off.

## One-command start

```bash
# from repo root — paper Confirm + ETH/SOL; forces exec.live=0
./run_crypto_paper.sh
# → http://localhost:8760
```

Equivalent:

```bash
CRYPTO_FLAG_EXEC_PAPER=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE=0 \
CRYPTO_FLAG_ASSETS_ETH=1 \
CRYPTO_FLAG_ASSETS_SOL=1 \
python3 -m crypto.server --port 8760
```

No API keys needed. Confirm records **local sim fills**. Optional Delta
demo/testnet keys (`DELTA_PAPER_*` from https://testnet.delta.exchange/) are
allowed; production India hosts are refused by the paper adapter.

BTC remains the **default active underlying**. ETH and SOL are enabled for the
topbar switcher (or `PATCH /api/underlying`).

## What stays off

| Knob | Value | Why |
| --- | --- | --- |
| `exec.live` / `CRYPTO_FLAG_EXEC_LIVE` | **off** | Live Confirm places real capital |
| `DELTA_LIVE_API_KEY` / `SECRET` | unset | Not needed for paper |
| `CRYPTO_LIVE_CONFIRM_ACK` | unset | Live gate stays closed |
| `exec.auto` | **off** | Auto is never supported |

Mode badge must show **Advisory** or **Paper Confirm** — never **Live Confirm**
on this path.

## Ops checklist (paper + ETH/SOL)

1. **Start paper** — `./run_crypto_paper.sh` → http://localhost:8760  
   Badge = **Paper Confirm**. Assets BTC / ETH / SOL available; BTC selected.
2. **Switch asset** — topbar BTC → ETH → SOL, or:
   ```bash
   curl -s -X PATCH localhost:8760/api/underlying \
     -H 'content-type: application/json' \
     -d '{"underlying":"ETH","actor":"cli"}' | python3 -m json.tool
   curl -s localhost:8760/api/assets | python3 -m json.tool
   ```
3. **Confirm** — wait for a GO (or fixtures/replay); tap **Confirm paper**, or:
   ```bash
   curl -s -X POST localhost:8760/api/exec/confirm \
     -H 'content-type: application/json' \
     -d '{"actor":"cli"}' | python3 -m json.tool
   curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
   ```
4. **Session export**
   ```bash
   curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
   curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
   ./run_crypto_session_summary.sh json paper_session.json
   ```

## Smoke checklist (extended)

1. **Start** — `./run_crypto_paper.sh` → open http://localhost:8760
2. **Mode badge** — topbar reads **Paper Confirm** (not Live)
3. **Assets** — switcher shows BTC / ETH / SOL; BTC is active by default
4. **GO card** — wait for a GO (or use fixtures/replay); Confirm paper button enabled
5. **Confirm** — one tap submits a paper/sim bracket
6. **Ledger** — Operator → open intents / PnL note updates; or:
   ```bash
   curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
   curl -s localhost:8760/api/exec/status | python3 -m json.tool
   ```
7. **Kill switch** — two taps (arm → confirm) cancels open intents and flips Confirm off
8. **Export trades**
   ```bash
   curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
   curl -s 'localhost:8760/api/exec/ledger/export?format=json' -o paper_trades.json
   ```
   Or use Operator → Export CSV / Export JSON.
9. **Session summary** (GO / confirms / vetoes / PnL / tick counts)
   ```bash
   curl -s localhost:8760/api/session/summary | python3 -m json.tool
   curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
   curl -s 'localhost:8760/api/session/export?format=csv' -o paper_session.csv
   # after stop (or anytime against the SQLite file):
   ./run_crypto_session_summary.sh json paper_session.json
   ```
   Feed ticks + features keep landing in `data/crypto/cubera.db` for the whole
   run. See [feed-recording.md](feed-recording.md).

## Kill via API

```bash
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":false}'
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":true}'
```

## Non-goals

Live Confirm, Auto, production Delta India keys.
