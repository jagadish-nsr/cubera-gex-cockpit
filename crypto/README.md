# CryptoCubera foundation

Modular crypto GEX decision-support cockpit. **Advisory only** — no live order placement.

Runs beside the existing NSE Cubera cockpit (`web_server.py`). This package owns providers, feature flags, durable storage, and the CryptoCubera UI.

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
| Cockpit UI | `crypto/web/` |
| API | `GET /api/snapshot`, `/api/flags`, `/api/providers`, `/api/health` |

## Add a provider

1. Implement a class with the right protocol in `crypto/providers/<venue>/`  
   (`OptionChainProvider`, `PerpProvider`, `NewsProvider`, or `ExecutionVenue` — see `providers/types.py`).
2. Register the factory in `providers/registry.py` → `_register_builtins()`.
3. Add a row under `providers:` in `config/crypto.yaml` with `enabled`, `priority`, and `feature_flag`.
4. Gate live use with a flag (e.g. `feeds.myvenue: false` by default).

Highest **priority** among providers that are `enabled` **and** whose feature flag is on wins. If none qualify, mock is used.

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

## Optional Deribit public spike

Does not block the UI. Overlays live BTC-PERPETUAL mark onto the fixture chain:

```bash
CRYPTO_FLAG_FEEDS_DERIBIT=1 CRYPTO_LIVE_SPIKE_DERIBIT=1 python3 -m crypto.server
```

Or set `feeds.deribit: true` and `live_spike.deribit_ticker: true` in `config/crypto.yaml`.

## Tests

```bash
python3 -m pytest tests/crypto -q
```

## Non-goals (this foundation)

Live orders, Auto mode, paid CoinGlass/Tardis, Gemma, multi-user auth.
