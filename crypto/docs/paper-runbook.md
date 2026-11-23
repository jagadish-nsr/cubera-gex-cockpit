# Local paper ops runbook

**Preference: no auto trade placement.** Paper ops default to **Manual Decision**
(copy/export levels; Log decision). Confirm never places on Delta.
`exec.live_place` stays off. Paper sim is optional. Never enable Auto. Never
require `DELTA_LIVE_*` keys.

## One-command start (Manual Decision)

```bash
# from repo root — Manual Decision + ETH/SOL + Gemma + public Deribit/Delta MD
# forces exec.live_place=0 / exec.auto=0 (never exchange POST / never Auto)
./run_crypto_paper.sh
# → http://localhost:8760
# Badge: Manual Decision
```

Equivalent:

```bash
CRYPTO_FLAG_EXEC_LIVE=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE_PLACE=0 \
CRYPTO_FLAG_EXEC_AUTO=0 \
CRYPTO_LIVE_FEEDS=1 \
CRYPTO_FLAG_FEEDS_DERIBIT=1 \
CRYPTO_FLAG_FEEDS_DELTA=1 \
CRYPTO_FLAG_ASSETS_ETH=1 \
CRYPTO_FLAG_ASSETS_SOL=1 \
CRYPTO_FLAG_INTEL_GEMMA=1 \
python3 -m crypto.server --port 8760
```

No trading API keys needed. Public market-data WebSockets are attempted;
providers **soft-fall back to mock fixtures** if WS/REST fail so the desk stays
up. GO card actions: **Copy levels**, **Export JSON**, **Log decision**.

Force mock-only MD: `CRYPTO_LIVE_FEEDS=0 ./run_crypto_paper.sh`.

### Optional paper sim (local practice)

```bash
CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh
# → Badge: Paper · local sim Confirm · exec.live=0
```

BTC remains the **default active underlying**. ETH and SOL are enabled for the
topbar switcher (or `PATCH /api/underlying`).

**Gemma:** `intel.gemma` defaults **on** in the paper helper. With
`gemma.mock_when_unavailable: true`, missing Ollama soft-falls back to keyword
classify and template digests/GO prose. Optional local runtime:

```bash
ollama pull gemma2:12b && ollama serve   # http://localhost:11434
# optional overrides:
# export CRYPTO_GEMMA_BASE_URL=http://localhost:11434
# export CRYPTO_GEMMA_MODEL=gemma2:12b
./run_crypto_paper.sh
curl -s localhost:8760/api/intel/gemma | python3 -m json.tool
```

Force keywords-only: `CRYPTO_FLAG_INTEL_GEMMA=0 ./run_crypto_paper.sh`.

## What stays off

| Knob | Value | Why |
| --- | --- | --- |
| `exec.live_place` | **off** | Exchange POST from Confirm refused |
| `DELTA_LIVE_API_KEY` / `SECRET` | unset | Not needed for Manual Decision or paper sim |
| `exec.auto` | **off** | Auto is never supported |

Mode badge on the default helper path: **Manual Decision**. Optional
`CRYPTO_PAPER_SIM=1` → **Paper**. Never **Live Auto**. Public Deribit/Delta
**feeds** are on for this helper; that is market data only, not live trading.

## Smoke checklist (short)

1. **Start** — `./run_crypto_paper.sh` → http://localhost:8760  
   Badge = **Manual Decision**. Assets BTC / ETH / SOL; BTC selected.
2. **Feeds** — banner notes live MD (or soft-fallback);  
   `curl -s localhost:8760/api/feeds | python3 -m json.tool`
3. **GO card** — Copy levels / Export JSON (or `GET /api/exec/decision`)
4. **Log decision** (optional) — Confirm / Log decision with notes;  
   `curl -s -X POST localhost:8760/api/exec/confirm -H 'content-type: application/json' -d '{"actor":"cli","notes":"hand ticket"}'`
5. **Session export** —  
   `curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json`

Paper sim smoke: `CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh` → badge **Paper** → Confirm paper → ledger.

## Ops checklist (Manual Decision + ETH/SOL + live MD)

1. **Start** — `./run_crypto_paper.sh` → http://localhost:8760  
   Badge = **Manual Decision**. Banner shows live feeds + Gemma.
   ```bash
   curl -s localhost:8760/api/exec/status | python3 -m json.tool
   # exec_mode.badge → "Manual Decision"
   curl -s localhost:8760/api/feeds | python3 -m json.tool
   ```
2. **Switch asset** — topbar BTC → ETH → SOL, or:
   ```bash
   curl -s -X PATCH localhost:8760/api/underlying \
     -H 'content-type: application/json' \
     -d '{"underlying":"ETH","actor":"cli"}' | python3 -m json.tool
   ```
3. **Copy / Export** — on a GO card, or:
   ```bash
   curl -s localhost:8760/api/exec/decision | python3 -m json.tool
   ```
4. **Log decision** (optional) — ledger row `manual_noted`; no exchange POST:
   ```bash
   curl -s -X POST localhost:8760/api/exec/confirm \
     -H 'content-type: application/json' \
     -d '{"actor":"cli","notes":"placed by hand"}' | python3 -m json.tool
   curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
   ```
5. **Session export**
   ```bash
   curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
   curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
   ./run_crypto_session_summary.sh json paper_session.json
   ```

## Kill via API

```bash
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":false}'
curl -s -X POST localhost:8760/api/exec/kill \
  -H 'content-type: application/json' -d '{"confirmed":true}'
```

## Non-goals

Exchange order placement from Confirm, Auto, production Delta India keys.
