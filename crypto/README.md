# CryptoCubera foundation

Modular crypto GEX decision-support cockpit. **Advisory only** — no live order placement.

Runs beside the existing NSE Cubera cockpit (`web_server.py`). This package owns providers, feature flags, durable storage, live feed collectors, and the CryptoCubera UI.

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
| GEX + GO rules | `crypto/engine/` |
| Live feeds (Phase 0) | `crypto/feeds/` + `providers/deribit|delta/` |
| Field findings | `crypto/docs/feed-fields.md` |
| Cockpit UI | `crypto/web/` |
| API | `GET /api/snapshot`, `/api/flags`, `/api/providers`, `/api/feeds`, `/api/health` |

## Enable live feeds (Phase 0)

Mock stays default. Live collectors need **both** an env gate and a feature flag:

```bash
# Deribit option tickers + trades WS, Delta India public MD
CRYPTO_LIVE_FEEDS=1 \
CRYPTO_FLAG_FEEDS_DERIBIT=1 \
CRYPTO_FLAG_FEEDS_DELTA=1 \
python3 -m crypto.server --port 8760
```

Or in `config/crypto.yaml`:

```yaml
feature_flags:
  feeds.deribit: true
  feeds.delta: true
live_spike:
  deribit_ws: true
  delta_ws: true
```

Then still set `CRYPTO_LIVE_FEEDS=1` (or the matching `live_spike.*` keys) so collectors start on boot.

Useful checks:

```bash
curl -s localhost:8760/api/feeds | python3 -m json.tool
curl -s localhost:8760/api/feeds/fields | python3 -m json.tool
curl -s localhost:8760/api/gex | python3 -m json.tool
```

Raw ticks land in SQLite `raw_ticks` (venue/channel/payload). GEX map uses the live (or REST-seeded) Deribit chain when `feeds.deribit` wins the registry.

Legacy one-shot ticker overlay (fixture chain + live mark only):

```bash
CRYPTO_FLAG_FEEDS_DERIBIT=1 CRYPTO_LIVE_SPIKE_DERIBIT=1 python3 -m crypto.server
```

### Stale data

Signal path hard-vetoes when live chain/perp age **> 2s** (`feeds.stale_threshold_s`). Mock path keeps a looser 30s check.

### Glassnode hand-check

See `crypto/docs/feed-fields.md`. Compare cockpit `flip` / walls to Glassnode’s public chart within about one strike — do not block the spike on a perfect match.

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

Live orders, Auto mode, paid CoinGlass/Tardis, Gemma, full bus/Timescale, multi-user auth.
