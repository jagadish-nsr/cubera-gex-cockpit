# CryptoCubera

Modular crypto GEX decision-support cockpit. **Advisory by default.** Modes:
**Advisory** · **Paper** (local sim practice) · **Manual Decision** (copy/export
levels for hand placement on Delta). Confirm **never** places live exchange
orders. **No Auto.**

Runs beside the existing NSE Cubera cockpit (`web_server.py`). This package owns providers, feature flags, durable storage, live feed collectors, the quant engine (rules baseline), trade ledger, and the CryptoCubera UI.

## Quick start

```bash
# from repo root
python3 -m pip install -r requirements.txt
python3 -m crypto.server --port 8760
# → http://localhost:8760
```

Or: `./run_crypto.sh` (Advisory / mock).

**Paper ops (preferred — Manual Decision):**

```bash
./run_crypto_paper.sh
# → http://localhost:8760  ·  Manual Decision + ETH/SOL + gemma
#    + public Deribit/Delta MD · live_place=0 · BTC default
# Optional local sim: CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh
```

Public Deribit/Delta market-data feeds are **on** (`CRYPTO_LIVE_FEEDS=1` +
`feeds.deribit` / `feeds.delta`). If WS/REST fail, providers soft-fall back to
mock fixtures (stub sources keep mock stale rules). Gemma (`intel.gemma`) is
**on**; without Ollama, classify/digests soft-fall back to keywords/templates
(`mock_when_unavailable`). Feeds ≠ exchange placement — `exec.live_place` stays off.

### Ops checklist (short smoke)

1. **Start** — `./run_crypto_paper.sh` → http://localhost:8760 · badge **Manual Decision**
2. **Switch asset** — topbar BTC / ETH / SOL (BTC is the default active underlying)
3. **Copy / Export** — GO card → Copy levels / Export JSON (or `GET /api/exec/decision`)
4. **Session export** — Operator export, or:
   ```bash
   curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
   ./run_crypto_session_summary.sh json paper_session.json
   ```

Full smoke checklist: [docs/paper-runbook.md](docs/paper-runbook.md).
Snapshot/switch latency (CRY-21, compute vs HTTP RTT): [docs/latency.md](docs/latency.md).

No API keys required for Manual Decision. Paper sim optional via `CRYPTO_PAPER_SIM=1`.

## What ships

| Piece | Location |
| --- | --- |
| Provider registry | `crypto/providers/` |
| Multi-asset (BTC / ETH / SOL) | `config/crypto.yaml` `assets:` + `assets.*` flags |
| Feature flags | `config/crypto.yaml` + `PATCH /api/flags` |
| SQLite store + session recording | `data/crypto/cubera.db` (path/rotate in `crypto/store/`; [docs/feed-recording.md](docs/feed-recording.md)) |
| Dual GEX + setups + vetoes | `crypto/engine/` |
| Market intelligence | `crypto/intel/` (schema, classify, score, calendar, blackout, further_reading) |
| Orderflow tape alerts | `crypto/engine/flow_alerts.py` (BTC/ETH/SOL; cockpit toasts) |
| Paper / Manual Decision exec | `crypto/exec/` (+ `decision.py`) + `providers/delta/execution.py` |
| Inference scaffold (off) | `crypto/engine/inference.py` |
| Replay CLI | `python3 -m crypto.replay_cli <bundle.json>` |
| Live feeds (Phase 0) | `crypto/feeds/` + `providers/deribit|delta/` |
| Field findings | `crypto/docs/feed-fields.md` |
| Cockpit UI | `crypto/web/` (asset switcher in topbar) |
| API | `GET /api/snapshot`, `/api/assets`, `PATCH /api/underlying`, `/api/flow/alerts`, `/api/intel/*`, `POST /api/intel/digest`, `/api/exec/*`, `/api/session*`, `/api/store/status`, `/api/flags`, `/api/providers`, `/api/feeds`, `/api/engine/params`, `/api/health` |

## Multi-asset (BTC · ETH · SOL)

Same GEX / setups / intel path per underlying. **BTC is the default** and always enabled. ETH and SOL are behind feature flags in YAML; **`./run_crypto_paper.sh` turns them on** for the paper path.

Quote / Manual Decision **currency** (GO card instrument) is the Delta India pair: **BTCUSD · ETHUSD · SOLUSD**. Deribit still uses `BTC` / `ETH` / `SOL` only as the option-chain MD currency code.

| Flag | YAML default | Paper helper | Role |
| --- | --- | --- | --- |
| `assets.btc` | **true** (locked on) | on | Bitcoin — default active underlying |
| `assets.eth` | false | **on** | Enable Ethereum chain + switcher |
| `assets.sol` | false | **on** | Enable Solana chain + switcher |
| `ui.asset_switcher` | true | on | Topbar BTC / ETH / SOL control |

Per-asset provider params live under `assets:` in `config/crypto.yaml` (Deribit currency/perp, Delta symbols, fixture file, CryptoPanic currencies). Mock fixtures: `crypto/fixtures/{btc,eth,sol}_chain.json`.

```bash
# Paper ops already enables ETH/SOL — or set explicitly with Manual Decision:
CRYPTO_FLAG_ASSETS_ETH=1 CRYPTO_FLAG_ASSETS_SOL=1 \
CRYPTO_FLAG_EXEC_LIVE=1 CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE_PLACE=0 \
  python3 -m crypto.server --port 8760

# Switch active underlying (BTC remains default until switched)
curl -X PATCH localhost:8760/api/underlying \
  -H 'content-type: application/json' \
  -d '{"underlying":"ETH","actor":"cli"}'

curl -s localhost:8760/api/assets | python3 -m json.tool
```

Operator panel also groups `assets.*` flags. Disabling the active ETH/SOL flag falls back to BTC. Live collectors pick Deribit currency / Delta symbols from the active asset.

## Quant engine (rules baseline)

### Dual GEX + agreement gate

```
GEX_k = Σ q_i · Γ_i(S) · S² · 0.01   (contract_size = 1 coin)
```

| Model | q_i | Flag |
| --- | --- | --- |
| Convention | +OI calls, −OI puts | always |
| Flow | −cumulative customer-signed volume from Deribit `trades.direction` (blocks stripped); volume-skew proxy if no tape | `engine.gex_flow_model` |

Regime is **uncertain** (forces NO-GO) when the two models disagree on the near-spot sign. Not a direction call.

### Orderflow features (persisted)

Written to `orderflow_features`: CVD 1/5/15m (tape when available), book imbalance, OI Δ, funding, basis_bps, option signed-premium hooks.

### Orderflow tape alerts (CRY-22)

CryptoCubera-native alerts for **BTC / ETH / SOL** only (`crypto/engine/flow_alerts.py`):
`CVD_IMPULSE_*`, `CVD_DIVERGENCE_*`, `WALL_ABSORPTION_*`, `TAPE_EXHAUSTION_*`.
Cockpit section + toast/tone when a new alert appears. Flag: `ui.flow_alerts`.
API: `GET /api/flow/alerts`. **Not** the equity Cubera cash-index tape package —
do not import that code path here.

### Three setups → GO / NO-GO cards

Configurable under `engine.setups` in `config/crypto.yaml` (starting parameters — not calibrated edge):

| Setup | Gate sketch |
| --- | --- |
| `negative_gamma_breakout` | Negative regime + CVD/imbalance confirm |
| `positive_gamma_fade` | Positive regime + stretch from magnet + CVD divergence |
| `flip_cross` | Spot crosses flip on rising CVD |

**GO card contract:** instrument, side, entry_zone, target, stop, size_cap_usd, score, three reasons, invalidation, valid_until, `model_version` + `rules_version`.

### Hard vetoes

- Live path: chain/perp age **> 2s** (`feeds.stale_threshold_s` / `engine.vetoes.stale_threshold_s`)
- Convention/flow disagreement or uncertain regime
- **News blackout** from intel: active blackout window or scheduled macro/maintenance/`IMMINENT` `occurs_at` inside **30 minutes** (not a score-only stub)
- Stub hooks (off): daily loss, margin cap, venue down — raise via `engine.vetoes.*_stub`

### Feature flags

| Flag | Default | Role |
| --- | --- | --- |
| `assets.btc` | true | Bitcoin underlying (always on) |
| `assets.eth` | **false** | Ethereum underlying |
| `assets.sol` | **false** | Solana underlying |
| `ui.asset_switcher` | true | Cockpit asset switcher |
| `ui.flow_alerts` | true | CryptoCubera orderflow tape alerts (BTC/ETH/SOL) |
| `engine.gex_flow_model` | true | Dual-model agreement gate |
| `engine.signal_rules` | true | Setup evaluation |
| `intel.news` | true | Intelligence feed + CryptoPanic/mock ingest |
| `intel.calendar` | true | Macro / maintenance calendar stubs |
| `intel.gemma` | **false** (paper helper → **on**) | Local Gemma 12B classify / GO prose / digests; mock/keywords if Ollama absent |
| `inference.feature_export` | **false** | Export feature vector on snapshot |
| `inference.regime_classifier` | **false** | Placeholder regime classifier (never creates GO) |

Env override example: `CRYPTO_FLAG_ASSETS_ETH=1` (maps to `assets.eth`); `CRYPTO_FLAG_INFERENCE_FEATURE_EXPORT=1` (maps to `inference.feature_export`).

Key parameters (also `GET /api/engine/params`):

```yaml
engine:
  atr_pct: 0.004
  size_cap_usd: 2500
  card_valid_minutes: 20
  model_version: rules-v1
  rules_version: setups-v1
```

### Replay

Same feature path as live:

```bash
python3 -m crypto.replay_cli crypto/fixtures/btc_chain.json --json
python3 -m crypto.replay_cli tests/cryptocubera/fixtures/replay_bundle.json
```

### Inference scaffold

Off by default. When flagged, snapshot includes an advisory feature vector and/or placeholder regime label. **Inference cannot create a GO.**

## Market intelligence and alerts

Ingest → normalize → classify → score → `IMMINENT` / `BREAKING` / `WATCH`. Never places trades. Keyword rules are the default; optional local Gemma 12B is flag-gated.

| Piece | Role |
| --- | --- |
| `crypto/intel/schema.py` | Normalized event (type, assets, venues, novelty, score, tier, blackout stamps) |
| `crypto/intel/classify.py` | Keyword event_type / asset / venue tags (fallback) |
| `crypto/gemma/` | Local Gemma: classify, GO prose, digests (schema-validated) |
| `crypto/intel/score.py` | Impact score; scheduled windows → IMMINENT |
| `crypto/intel/calendar.py` | CPI / FOMC / expiry / Delta maintenance stubs |
| `crypto/intel/blackout.py` | 30-minute hard-veto window for the signal path |
| `crypto/intel/further_reading.py` | Curated crypto further-reading links (Deribit / Delta / CryptoPanic / Glassnode) |
| `events` table | Persists scored events (`event_type`, `novelty`, `venues`, `occurs_at`, `blackout_until` + JSON payload) |

Event rows expose `url` when the provider/fixture supplies one (clickable in the Intelligence list). Snapshot `intel.further_reading` is the curated desk reading list — crypto venues only.

### Local Gemma 12B language layer

Language only — headlines, GO-card wording, digests. **No tools. Never creates a GO or invents prices** (numbers come from structured engine fields; invented numbers are rejected). Timeout or unavailable runtime → keyword rules / templates. `mock_when_unavailable: true` keeps the cockpit offline-friendly.

| Task | Output | Fallback |
| --- | --- | --- |
| Headline classify | `{event_type, assets, venues, confidence}` | `crypto/intel/classify.py` keywords |
| GO card reasons | exactly 3 lines | engine / template reasons |
| Digests | morning / post_trade / veto | template digest |

**Paper path (preferred):** `./run_crypto_paper.sh` already sets
`CRYPTO_FLAG_INTEL_GEMMA=1`. Optional Ollama for real Gemma answers; without it
the same flag soft-falls back via `mock_when_unavailable`.

```bash
# Optional: local Gemma 12B
ollama pull gemma2:12b
ollama serve   # default http://localhost:11434
export CRYPTO_GEMMA_BASE_URL=http://localhost:11434   # or OLLAMA_HOST
export CRYPTO_GEMMA_MODEL=gemma2:12b                  # or OLLAMA_MODEL
export CRYPTO_GEMMA_TIMEOUT_S=8

./run_crypto_paper.sh
# or Advisory with flag only:
# CRYPTO_FLAG_INTEL_GEMMA=1 python3 -m crypto.server --port 8760

# Status + digest
curl -s localhost:8760/api/intel/gemma | python3 -m json.tool
curl -s -X POST localhost:8760/api/intel/digest \
  -H 'content-type: application/json' \
  -d '{"kind":"morning"}' | python3 -m json.tool
```

Config block (`config/crypto.yaml`):

```yaml
gemma:
  base_url: http://localhost:11434
  model: gemma2:12b
  timeout_s: 8
  mock_when_unavailable: true
feature_flags:
  intel.gemma: false   # Advisory default; paper helper sets CRYPTO_FLAG_INTEL_GEMMA=1
```

Without a runtime on the paper path, keep the flag on — classify falls back to
keywords; digests/GO prose use templates. Force off: `CRYPTO_FLAG_INTEL_GEMMA=0`.

### Enable without a CryptoPanic token (default)

Mock fixtures + calendar stubs. UI works offline:

```bash
# intel.news defaults on in config/crypto.yaml
python3 -m crypto.server --port 8760
# → Intelligence section shows fixture + calendar events
curl -s localhost:8760/api/intel/feed | python3 -m json.tool
curl -s localhost:8760/api/intel/blackout | python3 -m json.tool
```

Disable the feed:

```bash
CRYPTO_FLAG_INTEL_NEWS=0 python3 -m crypto.server --port 8760
# or: curl -X PATCH localhost:8760/api/flags -H 'content-type: application/json' \
#        -d '{"name":"intel.news","value":false,"actor":"cli"}'
```

### Enable with a CryptoPanic token

```bash
export CRYPTOPANIC_TOKEN=your_token_here
# optional plan path segment (default: developer)
# export CRYPTOPANIC_API_PLAN=growth
CRYPTO_FLAG_INTEL_NEWS=1 python3 -m crypto.server --port 8760
```

Live fetch fails soft back to mock fixtures. Credit live rows with a CryptoPanic attribution note when the API returns data. Without a token, the `cryptopanic` provider serves fixtures automatically.

Alert tiers:

- **IMMINENT** — scheduled event inside 60 minutes → engine action `blackout` (veto when inside 30 minutes)
- **BREAKING** — high impact score on unscheduled headlines → push-ready in-app payload
- **WATCH** — digest

## Paper trading and Manual Decision

Advisory remains the process default when flags are off. **Paper** Confirm may
sim-fill for practice. **Manual Decision** (`exec.live` + `exec.confirm`) shows
the GO card for hand placement — instrument, side, entry/target/stop, size,
score, reasons, valid-until — with one-click **Copy levels** / **Export JSON**.
Confirm logs the decision to the ledger and **never** POSTs to Delta.
`exec.live_place` stays off and cannot unlock exchange place from Confirm.
Full paper checklist: [docs/paper-runbook.md](docs/paper-runbook.md).

| Flag | Default | Role |
| --- | --- | --- |
| `exec.paper` | **false** | Enable paper ledger + Delta demo/sim adapter + kill switch |
| `exec.confirm` | **false** | Paper: sim Confirm · Manual Decision: log decision / enable card actions |
| `exec.live` | **false** | Manual Decision mode (decision card; no exchange POST) |
| `exec.live_place` | **false** | Exchange POST from Confirm — always refused even if flipped on |
| `exec.auto` | **false** | Always refused — Auto is not supported |

### One-command paper ops (Manual Decision; no keys)

```bash
./run_crypto_paper.sh
# or:
CRYPTO_FLAG_EXEC_LIVE=1 CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE_PLACE=0 CRYPTO_FLAG_EXEC_AUTO=0 \
CRYPTO_FLAG_ASSETS_ETH=1 CRYPTO_FLAG_ASSETS_SOL=1 \
  python3 -m crypto.server --port 8760
```

Mode badge → **Manual Decision** (never Live Auto). ETH/SOL enabled for the
switcher; BTC stays the default active underlying. Copy/export levels; Confirm
logs decisions only. Optional paper sim: `CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh`.

### Smoke checklist

1. Start `./run_crypto_paper.sh` → http://localhost:8760
2. Topbar badge = **Manual Decision**; assets BTC / ETH / SOL available
3. Switch asset (topbar or `PATCH /api/underlying`) if needed
4. GO card → **Copy levels** / **Export JSON** (hand ticket for Delta)
5. Optional **Log decision** → ledger `manual_noted` (`GET /api/exec/ledger`)
6. Kill switch (two taps) clears Confirm path
7. Export ledger CSV/JSON from Operator or `/api/exec/ledger/export` (includes notes)
8. Export **session summary**: `/api/session/export` or `./run_crypto_session_summary.sh`
9. Optional paper sim: `CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh` → badge **Paper** → Confirm paper

```bash
curl -s -X POST localhost:8760/api/exec/confirm \
  -H 'content-type: application/json' -d '{"actor":"cli"}' | python3 -m json.tool
curl -s localhost:8760/api/exec/status | python3 -m json.tool
curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
curl -s localhost:8760/api/session/summary | python3 -m json.tool
curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json
```

### Feed recording (local paper)

While `./run_crypto_paper.sh` runs, ticks / GEX / orderflow / signals append to
SQLite (`data/crypto/cubera.db`, repo-root resolved). A `paper_sessions` row
opens on boot and closes on shutdown with a summary snapshot. Oversized DBs
rotate to `cubera.<UTC-stamp>.db` (see `database.rotate_*` in config).

```bash
# During or after a paper run
./run_crypto_session_summary.sh json paper_session.json
python3 -m crypto.session_cli status
curl -s localhost:8760/api/store/status | python3 -m json.tool
```

Details: [docs/feed-recording.md](docs/feed-recording.md).

Or toggle flags from the Operator panel / API (still leave `exec.live` false):

```bash
curl -X PATCH localhost:8760/api/flags -H 'content-type: application/json' \
  -d '{"name":"exec.paper","value":true,"actor":"cli"}'
curl -X PATCH localhost:8760/api/flags -H 'content-type: application/json' \
  -d '{"name":"exec.confirm","value":true,"actor":"cli"}'
```

### Delta demo / testnet keys (optional)

1. Create a **demo** account at https://testnet.delta.exchange/ (not production India).
2. Generate API keys there. Do **not** use production keys.
3. Export:

```bash
export DELTA_PAPER_API_KEY=...
export DELTA_PAPER_API_SECRET=...
# optional; defaults to India testnet
# export DELTA_PAPER_REST_URL=https://cdn-ind.testnet.deltaex.org

./run_crypto_paper.sh
```

The paper adapter **refuses** production hosts (`api.india.delta.exchange`, `api.delta.exchange`). Demo REST soft-fails to local sim so the ledger still records the intent.

## Manual Decision (hand placement on Delta)

**Decision support only.** Paper ops (`./run_crypto_paper.sh`) defaults here —
GO card as a trade ticket to place yourself on Delta. The app never sends orders.

```bash
./run_crypto_paper.sh
# equivalent flags:
CRYPTO_FLAG_EXEC_LIVE=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
CRYPTO_FLAG_EXEC_LIVE_PLACE=0 \
CRYPTO_FLAG_EXEC_AUTO=0 \
python3 -m crypto.server --port 8760
```

Mode badge → **Manual Decision**. No `DELTA_LIVE_*` keys required for this path.

### How Manual Decision works

1. GO card shows instrument, side, entry zone, target, stop, size, score, three
   reasons, invalidation, and valid-until.
2. **Copy levels** — clipboard text for pasting into Delta.
3. **Export JSON** — download the same fields (`GET /api/exec/decision`).
4. **Log decision** — Confirm writes a `manual_noted` ledger row (optional notes);
   CSV/JSON export includes the `notes` column. **No exchange POST.**
5. Place the bracket yourself on Delta. CryptoCubera does not auto-execute.

```bash
curl -s localhost:8760/api/exec/status | python3 -m json.tool
# mode badge / exec_mode.badge → "Manual Decision"
curl -s localhost:8760/api/exec/decision | python3 -m json.tool
curl -s -X POST localhost:8760/api/exec/confirm \
  -H 'content-type: application/json' \
  -d '{"actor":"cli","notes":"hand-placed at open"}' | python3 -m json.tool
```

`exec.live_place` remains **false**. Confirm refuses exchange placement even if
that flag is flipped on. Legacy live adapters / gates stay in-tree for diagnostics
only — they are not used by Confirm.

### Risk + kill switch

| Control | Behaviour |
| --- | --- |
| Size | From stop distance × `risk.risk_fraction` of `paper_equity_usd`, capped by GO `size_cap_usd` / `risk.size_cap_usd` |
| Daily loss | Realized PnL ≤ −`risk.daily_loss_limit_usd` → Confirm flips off (Advisory) |
| Kill switch | Paper intents only — two taps cancel open paper intents and flip Confirm off |

```yaml
risk:
  paper_equity_usd: 10000
  live_equity_usd: 10000
  risk_fraction: 0.005
  daily_loss_limit_usd: 250
  size_cap_usd: 2500
```

### Non-goals (still out)

Auto mode, exchange order placement from Confirm, short options.

## Enable live feeds (Phase 0)

**Paper path (preferred):** `./run_crypto_paper.sh` already sets the env gate +
Deribit/Delta flags (and forces `exec.live_place=0` / `exec.auto=0`). Soft-fallback
to mock if WS fails. Default mode is Manual Decision; `CRYPTO_PAPER_SIM=1` for
local sim practice.

**Advisory / manual:** Live collectors need **both** an env gate and a feature flag
(YAML defaults keep them off for plain `./run_crypto.sh`):

```bash
CRYPTO_LIVE_FEEDS=1 \
CRYPTO_FLAG_FEEDS_DERIBIT=1 \
CRYPTO_FLAG_FEEDS_DELTA=1 \
python3 -m crypto.server --port 8760
```

Useful checks:

```bash
curl -s localhost:8760/api/feeds | python3 -m json.tool
curl -s localhost:8760/api/gex | python3 -m json.tool
curl -s localhost:8760/api/engine/params | python3 -m json.tool
```

Raw ticks land in SQLite `raw_ticks` (WAL + busy timeout; write retries on lock).
Live Deribit option trades accumulate into the flow-model inventory
(`direction` = taker side). Paper session summary aggregates the same DB —
see [docs/feed-recording.md](docs/feed-recording.md).

### Soft-fallback + stale data

When collectors miss or REST fails, Deribit/Delta providers serve mock fixtures
(`*-stub` / fixture sources). Those sources are **not** treated as the live
signal path — mock keeps a looser ~30s stale check so the paper desk stays
usable offline. Real live chain/perp sources hard-veto when age **> 2s**.

### Glassnode hand-check

See `crypto/docs/feed-fields.md`. Compare cockpit `flip` / walls to Glassnode’s public chart within about one strike — disagreement between convention and flow is intentional NO-GO.

## Add a provider

1. Implement a class with the right protocol in `crypto/providers/<venue>/`  
   (`OptionChainProvider`, `PerpProvider`, `NewsProvider`, or `ExecutionVenue` — see `providers/types.py`).
2. Register the factory in `providers/registry.py` → `_register_builtins()`.
3. Add a row under `providers:` in `config/crypto.yaml` with `enabled`, `priority`, and `feature_flag`.
4. Gate live use with a flag (e.g. `feeds.myvenue: false` by default).

Lowest **priority number** among providers that are `enabled` **and** whose feature flag is on wins (Deribit/Delta are `5`/`6`, mock is `50`). If none qualify, mock is used.

## Add a feature flag

1. Add `my.slice: false` under `feature_flags` in `config/crypto.yaml`.
2. Read it with `get_flag("my.slice")` in API/engine/UI mount logic.
3. Optional env override: `CRYPTO_FLAG_MY_SLICE=1` (maps to `my.slice`).
4. Operators can toggle at runtime from the Operator panel or:

```bash
curl -X PATCH localhost:8760/api/flags \
  -H 'content-type: application/json' \
  -d '{"name":"ui.gex_map","value":false,"actor":"cli"}'
```

Flag changes are audited in `feature_flag_audit`.

## Tests

```bash
python3 -m pytest tests/cryptocubera -q
```

## Non-goals (this slice)

Auto mode, Gemma, paid CoinGlass/Tardis, social scrapers, mobile push plumbing beyond in-app payloads, claiming OOS edge, multi-user auth, full walk-forward champion models.
