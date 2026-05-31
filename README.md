# gex-indian — Nifty GEX Engine

The **Nifty (NSE)** port of the SPX GEX engine. Computes dealer gamma-exposure
levels (call/put walls, gamma flip, magnet, max pain), classifies regime, and
prints a one-sentence playbook for Nifty index options. Decision-support only —
not financial advice, no order execution.

> Read `CLAUDE.md` first, especially the **dealer-sign landmine**: the US
> long-call/short-put-gamma assumption may NOT hold for India and must be
> re-validated before trusting any output.

## Status
- ✅ Engine inherited from `../GEX` (market-agnostic) + India scaffolding.
- ✅ 20 tests pass (`python -m pytest -q`), incl. hand-checkable fixtures and an
  NSE adapter + expiry-calendar test.
- ⏳ **Not built yet:** the live NSE data pull (`data/nse.py: fetch_chain`) — the
  data source must be chosen/verified first (`BUILD_PLAN.md` Phase 0).

## Install
```
pip install -r requirements.txt
```

## Tests
```
python -m pytest -q
```

## Layout
- `gex/` — pure GEX math (copied, unchanged).
- `regime/` — regime classification + playbook (copied, unchanged).
- `data/schema.py`, `data/parity.py` — common schema + parity r/q+spot (unchanged).
- `data/nse.py` — **NSE adapter (stub)**; finish `fetch_chain` after Phase 0.
- `data/expiry.py` — **NSE expiry calendar** (last-Thursday monthly; VERIFY rules).
- `config.py` — India config: `NIFTY_LOT_SIZE` (VERIFY), expiry window, dealer
  model (UNVERIFIED for India), flip grid, OI staleness.
- `docs/nifty-nse-adapter-scope.md` — the full scoping doc.

## Verify-live before trusting (never guess — see BUILD_PLAN §3)
1. NSE data source + pricing.   2. Current Nifty lot size.
3. Current NSE expiry rules.    4. Dealer-sign model for India.

## Reused from the SPX project
`gex/`, `regime/`, `data/schema.py`, `data/parity.py`, and the synthetic fixture
tests. Bug fixes in these may need porting to/from `../GEX` (two copies can
diverge; a shared core package is a future option).
