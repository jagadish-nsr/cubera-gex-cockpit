# CryptoCubera

Modular crypto GEX decision-support cockpit. **Advisory by default.** Preferred path: **local paper Confirm** (sim fills; no live keys). Keep `exec.live` off. Optional Delta demo/testnet paper keys. Live Confirm exists but is fail-closed and out of preference. **No Auto.**

Runs beside the existing NSE Cubera cockpit (`web_server.py`). This package owns providers, feature flags, durable storage, live feed collectors, the quant engine (rules baseline), trade ledger, and the CryptoCubera UI.

## Quick start

```bash
# from repo root
python3 -m pip install -r requirements.txt
python3 -m crypto.server --port 8760
# → http://localhost:8760
```

Or: `./run_crypto.sh` (Advisory / mock).

**Local paper Confirm (preferred):**

```bash
./run_crypto_paper.sh
# → http://localhost:8760  ·  exec.paper + exec.confirm on · exec.live stays off
```

See [docs/paper-runbook.md](docs/paper-runbook.md) for the smoke checklist (GO → Confirm → ledger → kill → export).

Mock/fixture chain is the default. No API keys required for local sim.

## What ships

| Piece | Location |
| --- | --- |
| Provider registry | `crypto/providers/` |
| Multi-asset (BTC / ETH / SOL) | `config/crypto.yaml` `assets:` + `assets.*` flags |
| Feature flags | `config/crypto.yaml` + `PATCH /api/flags` |
| SQLite store | `data/crypto/cubera.db` (schema in `crypto/store/`) |
| Dual GEX + setups + vetoes | `crypto/engine/` |
| Market intelligence | `crypto/intel/` (schema, classify, score, calendar, blackout) |
| Paper / Live Confirm exec | `crypto/exec/` + `providers/delta/execution.py` + `live_execution.py` |
| Inference scaffold (off) | `crypto/engine/inference.py` |
| Replay CLI | `python3 -m crypto.replay_cli <bundle.json>` |
| Live feeds (Phase 0) | `crypto/feeds/` + `providers/deribit|delta/` |
| Field findings | `crypto/docs/feed-fields.md` |
| Cockpit UI | `crypto/web/` (asset switcher in topbar) |
| API | `GET /api/snapshot`, `/api/assets`, `PATCH /api/underlying`, `/api/intel/*`, `POST /api/intel/digest`, `/api/exec/*`, `/api/flags`, `/api/providers`, `/api/feeds`, `/api/engine/params`, `/api/health` |

## Multi-asset (BTC · ETH · SOL)

Same GEX / setups / intel path per underlying. **BTC is the default** and always enabled. ETH and SOL are behind feature flags.

| Flag | Default | Role |
| --- | --- | --- |
| `assets.btc` | **true** (locked on) | Bitcoin — default active underlying |
| `assets.eth` | false | Enable Ethereum chain + switcher |
| `assets.sol` | false | Enable Solana chain + switcher |
| `ui.asset_switcher` | true | Topbar BTC / ETH / SOL control |

Per-asset provider params live under `assets:` in `config/crypto.yaml` (Deribit currency/perp, Delta symbols, fixture file, CryptoPanic currencies). Mock fixtures: `crypto/fixtures/{btc,eth,sol}_chain.json`.

```bash
# Enable ETH then SOL (env), keep BTC selected by default
CRYPTO_FLAG_ASSETS_ETH=1 CRYPTO_FLAG_ASSETS_SOL=1 python3 -m crypto.server --port 8760

# Or switch active underlying
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
| `engine.gex_flow_model` | true | Dual-model agreement gate |
| `engine.signal_rules` | true | Setup evaluation |
| `intel.news` | true | Intelligence feed + CryptoPanic/mock ingest |
| `intel.calendar` | true | Macro / maintenance calendar stubs |
| `intel.gemma` | **false** | Local Gemma 12B classify / GO prose / digests |
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
| `events` table | Persists scored events (`event_type`, `novelty`, `venues`, `occurs_at`, `blackout_until` + JSON payload) |

### Local Gemma 12B language layer

Language only — headlines, GO-card wording, digests. **No tools. Never creates a GO or invents prices** (numbers come from structured engine fields; invented numbers are rejected). Timeout or unavailable runtime → keyword rules / templates. `mock_when_unavailable: true` keeps the cockpit offline-friendly.

| Task | Output | Fallback |
| --- | --- | --- |
| Headline classify | `{event_type, assets, venues, confidence}` | `crypto/intel/classify.py` keywords |
| GO card reasons | exactly 3 lines | engine / template reasons |
| Digests | morning / post_trade / veto | template digest |

Point at a local Ollama-compatible runtime (Gemma 12B pulled locally):

```bash
# 1) Run Ollama (or any /api/chat compatible host) with a 12B Gemma tag
ollama pull gemma2:12b
ollama serve   # default http://localhost:11434

# 2) Point CryptoCubera at it and enable the flag
export CRYPTO_GEMMA_BASE_URL=http://localhost:11434   # or OLLAMA_HOST
export CRYPTO_GEMMA_MODEL=gemma2:12b                  # or OLLAMA_MODEL
export CRYPTO_GEMMA_TIMEOUT_S=8
CRYPTO_FLAG_INTEL_GEMMA=1 python3 -m crypto.server --port 8760

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
  intel.gemma: false   # flip on when runtime is ready
```

Without a runtime, leave `intel.gemma` off (default) or keep it on with `mock_when_unavailable` — classify falls back to keywords; digests/GO prose use templates.

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

## Paper trading and Confirm mode

**Preferred: local paper only.** Advisory remains the process default when flags are
off. Paper Confirm never places live capital. Keep `exec.live` **off** — do not
enable Live Confirm or set `DELTA_LIVE_*` unless you explicitly change that
preference. Full checklist: [docs/paper-runbook.md](docs/paper-runbook.md).

| Flag | Default | Role |
| --- | --- | --- |
| `exec.paper` | **false** | Enable paper ledger + Delta demo/sim adapter + kill switch |
| `exec.confirm` | **false** | One-tap Confirm on a GO card submits a paper/sim bracket (when live is off) |
| `exec.live` | **false** | **Keep off.** Live Confirm path (real capital; fail closed) |
| `exec.auto` | **false** | Always refused — Auto is not supported |

### One-command local paper (no keys)

```bash
./run_crypto_paper.sh
# or:
CRYPTO_FLAG_EXEC_PAPER=1 CRYPTO_FLAG_EXEC_CONFIRM=1 CRYPTO_FLAG_EXEC_LIVE=0 \
  python3 -m crypto.server --port 8760
```

Mode badge → **Paper Confirm** (never Live on this path). Without `DELTA_PAPER_*`
keys the adapter records **local sim fills**.

### Smoke checklist

1. Start `./run_crypto_paper.sh` → http://localhost:8760
2. Topbar badge = **Paper Confirm**
3. GO card → **Confirm paper**
4. Ledger shows open intent (`GET /api/exec/ledger`)
5. Kill switch (two taps) clears intents / flips Confirm off
6. Export CSV/JSON from Operator or `/api/exec/ledger/export`

```bash
curl -s -X POST localhost:8760/api/exec/confirm \
  -H 'content-type: application/json' -d '{"actor":"cli"}' | python3 -m json.tool
curl -s localhost:8760/api/exec/status | python3 -m json.tool
curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
```

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

## Live Confirm on Delta India (out of preference — flag-gated)

**Keep this path off** unless you explicitly want real capital. Preferred operation
is local paper Confirm above. Live Confirm places **real capital** on Delta
Exchange India and is refuse-by-default until every safety check passes. **No Auto.**

### Exact enable steps

1. **Create production India trading API keys** on Delta Exchange India (separate from demo/`DELTA_PAPER_*`). Whitelist the **static public IP** of the host that will run CryptoCubera — Delta trading keys require IP whitelist.
2. Export live credentials and acks (fail closed without these):

```bash
export DELTA_LIVE_API_KEY=...          # production India trading key
export DELTA_LIVE_API_SECRET=...
# optional; must be India production (testnet refused)
# export DELTA_LIVE_REST_URL=https://api.india.delta.exchange

# Explicit risk acknowledgement (exact string):
export CRYPTO_LIVE_CONFIRM_ACK=I_UNDERSTAND_LIVE_RISK

# After you have whitelisted this host's static IP on the Delta key:
export DELTA_LIVE_IP_WHITELIST_ACK=1
```

3. Enable flags (env or Operator / `PATCH /api/flags`):

```bash
CRYPTO_FLAG_EXEC_LIVE=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
python3 -m crypto.server --port 8760
```

Leave `exec.paper` off for a pure live path, or keep it on as a fallback when the live gate fails (mode falls back to Paper Confirm — never silent live).

4. Verify the gate before confirming:

```bash
curl -s localhost:8760/api/exec/live-gate | python3 -m json.tool
curl -s localhost:8760/api/exec/status | python3 -m json.tool
# mode_badge should be "Live Confirm" and can_confirm true only when gate.ok
```

5. One-tap Confirm on a GO card (or API) submits a **live** bracket. Kill switch still two-tap cancels open intents and flips Confirm off.

### Blockers (gate refuses live)

| Check | How it fails |
| --- | --- |
| User live keys | Missing `DELTA_LIVE_API_KEY` / `DELTA_LIVE_API_SECRET` (paper keys do **not** unlock live) |
| Ack | `CRYPTO_LIVE_CONFIRM_ACK` ≠ `I_UNDERSTAND_LIVE_RISK` |
| IP whitelist ack | `DELTA_LIVE_IP_WHITELIST_ACK` not set after operator whitelists IP on Delta |
| Host | `DELTA_LIVE_REST_URL` not `api.india.delta.exchange` (testnet refused) |
| Flags | `exec.live` or `exec.confirm` off; or `exec.auto` on (always refused) |
| Risk | Daily loss limit → Confirm flipped to Advisory |

### Risk + kill switch

| Control | Behaviour |
| --- | --- |
| Size | From stop distance × `risk.risk_fraction` of `paper_equity_usd` (paper) or `live_equity_usd` (live), capped by GO `size_cap_usd` / `risk.size_cap_usd` |
| Daily loss | Realized PnL ≤ −`risk.daily_loss_limit_usd` → Confirm flips off (Advisory) |
| Kill switch | Two taps in UI (arm → confirm) or `POST /api/exec/kill` twice (`confirmed: false` then `true`); cancels open intents and flips Confirm off |

```yaml
risk:
  paper_equity_usd: 10000
  live_equity_usd: 10000
  risk_fraction: 0.005
  daily_loss_limit_usd: 250
  size_cap_usd: 2500
```

### Non-goals (still out)

Auto mode, short options, unattended production deploy without IP whitelist / live keys.

## Enable live feeds (Phase 0)

Mock stays default. Live collectors need **both** an env gate and a feature flag:

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

Raw ticks land in SQLite `raw_ticks`. Live Deribit option trades accumulate into the flow-model inventory (`direction` = taker side).

### Stale data

Signal path hard-vetoes when live chain/perp age **> 2s**. Mock path keeps a looser 30s check.

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
