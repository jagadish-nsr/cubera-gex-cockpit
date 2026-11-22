# CryptoCubera

Modular crypto GEX decision-support cockpit. **Advisory by default.** Optional paper Confirm on Delta demo/testnet or local sim — **never live capital**.

Runs beside the existing NSE Cubera cockpit (`web_server.py`). This package owns providers, feature flags, durable storage, live feed collectors, the quant engine (rules baseline), paper ledger, and the CryptoCubera UI.

## Quick start

```bash
# from repo root
python3 -m pip install -r requirements.txt
python3 -m crypto.server --port 8760
# → http://localhost:8760
```

Or: `./run_crypto.sh`

Mock/fixture mode is the default. The UI is reviewable without API keys.

## What ships

| Piece | Location |
| --- | --- |
| Provider registry | `crypto/providers/` |
| Feature flags | `config/crypto.yaml` + `PATCH /api/flags` |
| SQLite store | `data/crypto/cubera.db` (schema in `crypto/store/`) |
| Dual GEX + setups + vetoes | `crypto/engine/` |
| Market intelligence | `crypto/intel/` (schema, classify, score, calendar, blackout) |
| Paper / Confirm exec | `crypto/exec/` + `providers/delta/execution.py` |
| Inference scaffold (off) | `crypto/engine/inference.py` |
| Replay CLI | `python3 -m crypto.replay_cli <bundle.json>` |
| Live feeds (Phase 0) | `crypto/feeds/` + `providers/deribit|delta/` |
| Field findings | `crypto/docs/feed-fields.md` |
| Cockpit UI | `crypto/web/` |
| API | `GET /api/snapshot`, `/api/intel/*`, `/api/exec/*`, `/api/flags`, `/api/providers`, `/api/feeds`, `/api/engine/params`, `/api/health` |

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
| `engine.gex_flow_model` | true | Dual-model agreement gate |
| `engine.signal_rules` | true | Setup evaluation |
| `intel.news` | true | Intelligence feed + CryptoPanic/mock ingest |
| `intel.calendar` | true | Macro / maintenance calendar stubs |
| `inference.feature_export` | **false** | Export feature vector on snapshot |
| `inference.regime_classifier` | **false** | Placeholder regime classifier (never creates GO) |

Env override example: `CRYPTO_FLAG_INFERENCE_FEATURE_EXPORT=1` (maps to `inference.feature_export`).

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

Ingest → normalize → keyword-classify → score → `IMMINENT` / `BREAKING` / `WATCH`. Never places trades. Gemma is out of scope (keyword rules only).

| Piece | Role |
| --- | --- |
| `crypto/intel/schema.py` | Normalized event (type, assets, venues, novelty, score, tier, blackout stamps) |
| `crypto/intel/classify.py` | Keyword event_type / asset / venue tags |
| `crypto/intel/score.py` | Impact score; scheduled windows → IMMINENT |
| `crypto/intel/calendar.py` | CPI / FOMC / expiry / Delta maintenance stubs |
| `crypto/intel/blackout.py` | 30-minute hard-veto window for the signal path |
| `events` table | Persists scored events (`event_type`, `novelty`, `venues`, `occurs_at`, `blackout_until` + JSON payload) |

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

Advisory remains the default. Paper Confirm never places live capital.

| Flag | Default | Role |
| --- | --- | --- |
| `exec.paper` | **false** | Enable paper ledger + Delta demo/sim adapter + kill switch |
| `exec.confirm` | **false** | One-tap Confirm on a GO card submits a paper/sim bracket |

### Safe enable (local sim — no keys)

```bash
CRYPTO_FLAG_EXEC_PAPER=1 \
CRYPTO_FLAG_EXEC_CONFIRM=1 \
python3 -m crypto.server --port 8760
```

Or toggle from the Operator panel / API:

```bash
curl -X PATCH localhost:8760/api/flags -H 'content-type: application/json' \
  -d '{"name":"exec.paper","value":true,"actor":"cli"}'
curl -X PATCH localhost:8760/api/flags -H 'content-type: application/json' \
  -d '{"name":"exec.confirm","value":true,"actor":"cli"}'
```

Without `DELTA_PAPER_*` keys the adapter records **local sim fills**. Mode badge → **Paper Confirm**. GO card shows a Confirm button and paper status.

```bash
# One-tap Confirm (uses latest GO card, or pass card JSON)
curl -s -X POST localhost:8760/api/exec/confirm \
  -H 'content-type: application/json' \
  -d '{"actor":"cli"}' | python3 -m json.tool

curl -s localhost:8760/api/exec/status | python3 -m json.tool
curl -s localhost:8760/api/exec/ledger | python3 -m json.tool
curl -s 'localhost:8760/api/exec/ledger/export?format=csv' -o paper_trades.csv
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

CRYPTO_FLAG_EXEC_PAPER=1 CRYPTO_FLAG_EXEC_CONFIRM=1 \
python3 -m crypto.server --port 8760
```

The adapter **refuses** production hosts (`api.india.delta.exchange`, `api.delta.exchange`). Demo REST soft-fails to local sim so the ledger still records the intent.

### Risk + kill switch

| Control | Behaviour |
| --- | --- |
| Size | From stop distance × `risk.risk_fraction` of `risk.paper_equity_usd`, capped by GO `size_cap_usd` / `risk.size_cap_usd` |
| Daily loss | Realized paper PnL ≤ −`risk.daily_loss_limit_usd` → Confirm flips off (Advisory) |
| Kill switch | Two taps in UI (arm → confirm) or `POST /api/exec/kill` twice (`confirmed: false` then `true`); cancels open paper intents and flips Confirm off |

```yaml
risk:
  paper_equity_usd: 10000
  risk_fraction: 0.005
  daily_loss_limit_usd: 250
  size_cap_usd: 2500
```

### Non-goals (still out)

Live Confirm/Auto with real capital, production IP whitelist deploy, short options, Auto mode.

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

Live Auto / live Confirm with real capital, production IP whitelist deploy, Gemma, paid CoinGlass/Tardis, social scrapers, mobile push plumbing beyond in-app payloads, claiming OOS edge, multi-user auth, full walk-forward champion models.
