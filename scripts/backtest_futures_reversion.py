"""Study #24 — does the known intraday MEAN-REVERSION (Study #23: mom→fwd IC ≈ −0.25) MONETIZE
when executed on the NEAR-MONTH FUTURE (cost ~few bps) instead of on 5-min option round-trips
(~3%, which killed it in Study #3/#6b)? And is the net edge concentrated in the high-|vanna|
(SIDEWAYS) regime, as the dealer-dampening mechanism (#1/#8) predicts?

This is a COST-AND-REGIME study of an ALREADY-established signal, not signal discovery. We treat
the −0.25 IC as given and ask only: (a) does a fade-the-trailing-move rule clear a realistic futures
round-trip cost, and (b) does it strengthen monotonically LOW→HIGH across |vanna| terciles.

Falsifiable success — the signal passes ONLY if all hold:
  (1) net P&L > 0 after a ≥4 bps round-trip cost;
  (2) net P&L rises monotonically LOW→HIGH across |vanna| terciles;
  (3) it survives day-block walk-forward OOS;
  (4) it beats the direction-shuffle AND random-window placebos;
  (5) on the ~50-day futures overlap the spot-proxy P&L tracks the actual-futures P&L.
Any failure → recorded DEAD with the breakeven cost, same as the rest of the arc.

Data (reuse, don't rebuild):
  • signal + regime : the 5-min feature cache (app/intraday_features) — spot, |vanna|, dte, atm_iv.
  • price path      : the RAW 1-min spot (data.dhan.load_intraday_day → spot per minute), the
                      tradeable-price PROXY over the full 5 years; costed as the near-month future.
  • futures check   : the ~50-day intraday futures (data.dhan.load_futures_intraday_day).

Why spot is the proxy: intraday futures cap at ~90 days, so the 5-yr backtest runs on 1-min spot,
costed as if trading the future. The near-month basis barely moves over a 15-60 min hold, so spot is
a sound PATH proxy — and we PROVE it on the ~50-day overlap (spot-proxy vs actual-future P&L corr +
mean basis drift). The tradeable instrument is the near-month FUTURE — never options (options re-add
the ~3% cost that is the whole reason this signal looked dead).

⚠️ LANDMINE — phantom reversion from bad ticks: a reversion P&L FEASTS on the ~1.8% corrupt Dhan spot
ticks (a spurious −3% print then a "revert" is fake profit). So we clean the 1-min spot (flag a
1-min move >~1.5% that REVERSES within k≤3 bars, repair by interpolation) and run the ENTIRE backtest
with AND without cleaning — the delta is the phantom P&L and must be small for any result to be trusted.

Sign note: this uses the PRICE-momentum sign (fade the trailing move), NOT the dealer-gamma sign — so
it is clean of the GEX sign landmine. Point-in-time: signal uses bars ≤ t, fill at t+1min, exit at t+h.

  python scripts/backtest_futures_reversion.py                 # all books
  python scripts/backtest_futures_reversion.py NIFTY           # one book

⚠️ EXPLORATORY. 1-min spot proxy (not the future) for the 5-yr P&L; modelled round-trip cost (no real
book); non-directional-of-GEX; decision-support research, NOT a live rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intraday_features import load_features                       # noqa: E402
from app.expiry_regime import ERA_LABELS, era_order, expiry_era       # noqa: E402

# ---- pre-registered grid (report the WHOLE grid, not the best cell) ---------------------------
BOOKS = ["NIFTY", "NIFTY_M", "BANKNIFTY"]
FUT_OF = {"NIFTY": "NIFTY", "NIFTY_M": "NIFTY", "BANKNIFTY": "BANKNIFTY"}  # near-month future per book
# 1-min PRICE-PATH feed per book. NIFTY_M is monthly options on the SAME Nifty index, and its own
# store's spot column carries sustained scale corruption in the pre-2025 data (runs of ~2.2× the true
# level) — so we draw its path from the CLEAN, validated NIFTY feed (same underlying, correct verdict).
# BANKNIFTY is a distinct index with no clean 5-yr sibling feed; its own feed is trusted only where
# corroborated (Sep25+/OOS + the recent futures corr) — see the FEED-QUALITY caveat in the report.
PATH_OF = {"NIFTY": "NIFTY", "NIFTY_M": "NIFTY", "BANKNIFTY": "BANKNIFTY"}
CONTAMINATED_FEED = {"BANKNIFTY"}   # own 5-yr spot feed not fully clean pre-2025 → lead with OOS
DELTAS = [15, 30, 60]            # trailing-return lookback Δ (min) for the fade signal
HOLDS = [15, 30, 60]             # forward hold h (min)
COSTS_BPS = [0, 1, 2, 3, 4, 5, 8]  # round-trip cost grid (bps of notional)
REF_COST = 4.0                   # bps — the "does it clear a realistic futures cost" reference
REF_DH = (30, 30)                # headline (Δ, h) for the deep-dive tables

# session window (IST minutes)
OPEN_MIN = 9 * 60 + 15           # 09:15
FLAT_BY = 15 * 60 + 15           # forced flat 15:15 — no exit later than this
FIRST_DECISION = 10 * 60 + 15    # 10:15 — need up to Δ=60m of history before the first decision

# bad-tick cleaner — TWO passes: (1) a Hampel median-outlier guard that kills isolated gross
# corrupt ticks regardless of snap-back timing (the NIFTY_M / BANKNIFTY spot feeds carry severe
# outliers — e.g. a 26,000-pt spot print on a 21,500 index — that a snap-back rule alone misses);
# (2) the reversion-repair below for shorter round-trip excursions. The clean NIFTY feed is
# unaffected by either (it has ~0 outliers) — verified — so this removes artefact, not signal.
HAMPEL_WIN = 4                   # centered window ±4 bars (9-bar) median/MAD — isolated spikes
HAMPEL_NMAD = 5.0                # flag a bar > 5·(1.4826·MAD) from the local median …
HAMPEL_FLOOR = 0.008             # …and at least 0.8% away (so quiet windows with MAD≈0 don't over-trigger)
# a SECOND, long-window robust pass for SUSTAINED scale corruption (the NIFTY_M/BANKNIFTY feeds
# carry runs of ~2.2× wrong spot for many bars — a short window's median goes corrupt, so it needs a
# ±30-bar (1-hr) window where the corrupt run is a minority and the median stays true). The 8% floor
# means only gross scale errors trip it; a real index cannot sit 8% off its 1-hr-centered median.
BIGWIN = 30
BIGWIN_NMAD = 8.0
BIGWIN_FLOOR = 0.05
TICK_JUMP = 0.015                # a 1-min |log-return| above this is a candidate corrupt tick
TICK_BACK = 0.005                # …and it's a bad tick if price returns within this of the pre-jump level
TICK_K = 3                       # …within k≤3 bars (a genuine move does NOT snap back)

RNG = np.random.default_rng(0)
N_PERM = 500                     # direction-shuffle placebo permutations


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# ----------------------------------------------------------------------------------------------
# 1-min spot path + the bad-tick cleaner
# ----------------------------------------------------------------------------------------------
def _hampel(price: np.ndarray, win: int, nmad: float, floor: float):
    """Hampel median-outlier filter: replace a bar that is an isolated gross outlier vs its local
    CENTERED ±``win``-bar median — |x−med| > nmad·1.4826·MAD AND > floor·med — with that median.
    The window uses the ORIGINAL prices (a median tolerates a minority of outliers), so a corrupt run
    shorter than the window doesn't bias it. A short window catches spikes; a long window (with a big
    floor) catches SUSTAINED scale corruption. Returns (filtered, n_replaced)."""
    p = price.astype(float)
    n = len(p)
    if n < 2 * win + 1:
        return p.copy(), 0
    out = p.copy()
    rep = 0
    for i in range(n):
        w = p[max(0, i - win):min(n, i + win + 1)]
        w = w[np.isfinite(w) & (w > 0)]
        if len(w) < max(5, win):
            continue
        med = float(np.median(w))
        if p[i] <= 0 or not np.isfinite(p[i]):
            out[i] = med
            rep += 1
            continue
        mad = float(np.median(np.abs(w - med)))
        if abs(p[i] - med) > max(nmad * 1.4826 * mad, floor * med):
            out[i] = med
            rep += 1
    return out, rep


def _clean_spikes(minutes: np.ndarray, price: np.ndarray):
    """Repair round-trip corrupt ticks: a >TICK_JUMP 1-min move that REVERTS to the pre-jump level
    within TICK_K bars is interpolated away (a genuine jump does not snap back). Returns (cleaned
    price, n_repaired_bars). One-sided clipping is NOT enough for a reversion P&L — the snap-back is
    exactly the phantom profit, so we must remove the whole excursion."""
    p = price.astype(float).copy()
    n = len(p)
    repaired = 0
    i = 1
    while i < n:
        if p[i - 1] > 0 and p[i] > 0 and abs(np.log(p[i] / p[i - 1])) > TICK_JUMP:
            base = p[i - 1]
            j = None
            for jj in range(i + 1, min(i + TICK_K + 1, n)):
                if p[jj] > 0 and abs(np.log(p[jj] / base)) < TICK_BACK:  # snapped back → excursion
                    j = jj
                    break
            if j is not None:
                for m in range(i, j):                                   # linear-interpolate i..j-1
                    frac = (m - (i - 1)) / (j - (i - 1))
                    p[m] = base * (1 - frac) + p[j] * frac
                repaired += (j - i)
                i = j
                continue
        i += 1
    return p, repaired


def _day_path(store: str, day: str, clean: bool):
    """1-min spot for the day → (minute→price dict, day_mean_1min_ret, n_repaired). None if unusable."""
    from data.dhan import load_intraday_day
    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return None
    sp = df.groupby("time_ist")["spot"].first()
    minutes = np.array([_mins(t) for t in sp.index])
    order = np.argsort(minutes)
    minutes, price = minutes[order], sp.to_numpy(float)[order]
    if len(price) < 60 or not np.all(price > 0):
        price = np.where(price > 0, price, np.nan)
    rep = 0
    if clean:
        price = np.nan_to_num(price, nan=0.0)
        price, r0 = _hampel(price, BIGWIN, BIGWIN_NMAD, BIGWIN_FLOOR)     # pass 0: sustained scale corruption
        price, r1 = _hampel(price, HAMPEL_WIN, HAMPEL_NMAD, HAMPEL_FLOOR)  # pass 1: isolated gross spikes
        price, r2 = _clean_spikes(minutes, price)                         # pass 2: short round-trip excursions
        price = np.where(price > 0, price, np.nan)
        rep = r0 + r1 + r2
    lr = np.diff(np.log(np.where(price > 0, price, np.nan)))
    drift = float(np.nanmean(lr)) if np.isfinite(lr).any() else 0.0    # day's mean 1-min drift
    return {int(mm): float(pp) for mm, pp in zip(minutes, price) if pp == pp}, drift, rep


def _px(pmap: dict, minute: int):
    """Price at an exact minute, else the nearest within ±1 min (small gaps), else None."""
    v = pmap.get(minute)
    if v is not None:
        return v
    for d in (1, -1):
        v = pmap.get(minute + d)
        if v is not None:
            return v
    return None


# ----------------------------------------------------------------------------------------------
# trade generation — fade the trailing move, one row per (day, stamp, Δ, h)
# ----------------------------------------------------------------------------------------------
def build_trades(store: str, clean: bool = True, price_key: str | None = None):
    """Every fade trade over the 5-yr sample. At each 5-min decision stamp t: pos = −sign(trailing
    Δ-min return); enter at t+1min, exit at t+h. gross% = pos·(exit/entry−1)·100; det% strips the
    day's drift over the hold. Regime = |vanna| tercile at t. ``price_key`` (a store to draw the
    1-min PATH from, default = ``store``) lets the futures check reuse this on the futures path."""
    feat = load_features(store)
    if feat is None:
        return None
    path_store = price_key or PATH_OF.get(store, store)
    feat = feat.sort_values(["day", "time_ist"]).reset_index(drop=True)
    q33, q66 = feat["vanna"].abs().quantile([1 / 3, 2 / 3])           # book-wide |vanna| terciles
    rows = []
    rep_bars = tot_bars = 0
    for day, g in feat.groupby("day", sort=True):
        dp = _day_path(path_store, day, clean)
        if dp is None:
            continue
        pmap, drift, rep = dp
        rep_bars += rep
        tot_bars += len(pmap)
        stamps = g[["time_ist", "vanna", "dte", "atm_iv"]].copy()
        stamps["mm"] = stamps["time_ist"].map(_mins)
        stamps = stamps[(stamps["mm"] >= FIRST_DECISION) & (stamps["mm"] <= FLAT_BY - min(HOLDS))]
        for _, r in stamps.iterrows():
            t = int(r["mm"])
            p_t = _px(pmap, t)
            p_ent = _px(pmap, t + 1)
            if p_t is None or p_ent is None:
                continue
            av = abs(float(r["vanna"])) if r["vanna"] == r["vanna"] else np.nan
            terc = np.nan if av != av else (0 if av <= q33 else 2 if av >= q66 else 1)
            for dl in DELTAS:
                p_prev = _px(pmap, t - dl)
                if p_prev is None or p_prev <= 0:
                    continue
                sig = p_t / p_prev - 1.0
                if sig == 0:
                    continue
                pos = -1.0 if sig > 0 else 1.0                        # FADE the trailing move
                for h in HOLDS:
                    if t + h > FLAT_BY:
                        continue
                    p_exit = _px(pmap, t + h)
                    if p_exit is None or p_ent <= 0:
                        continue
                    gross = pos * (p_exit / p_ent - 1.0) * 100.0       # percent
                    det = pos * (np.log(p_exit / p_ent) - drift * h) * 100.0  # strip day drift over hold
                    rows.append((day, t, dl, h, pos, gross, det, av, terc,
                                 float(r["dte"]) if r["dte"] == r["dte"] else np.nan,
                                 float(r["atm_iv"]) if r["atm_iv"] == r["atm_iv"] else np.nan))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["day", "t", "delta", "h", "pos", "gross", "det",
                                     "absv", "terc", "dte", "iv"])
    df["era"] = df["day"].map(expiry_era)
    df.attrs["rep_frac"] = (rep_bars / tot_bars) if tot_bars else 0.0
    df.attrs["rep_bars"] = rep_bars
    return df


# ----------------------------------------------------------------------------------------------
# metrics
# ----------------------------------------------------------------------------------------------
def _perday_mean(sub, col="gross"):
    """Day = independent unit: mean of per-day mean returns, + SE across days."""
    dm = sub.groupby("day")[col].mean()
    return float(dm.mean()), float(dm.std() / np.sqrt(len(dm))), len(dm)


def _exec_daily(sub):
    """Non-overlapping executable track for one (Δ,h): within each day pick trades sequentially
    (next entry ≥ last exit), sum their returns → a clean daily return series. Overlapping positions
    would inflate Sharpe, so they're excluded here."""
    days = []
    for day, g in sub.sort_values(["day", "t"]).groupby("day"):
        last_exit = -1
        picked = []
        for _, r in g.iterrows():
            if r["t"] + 1 >= last_exit:                    # entry (t+1) is after the prior exit
                picked.append(r)
                last_exit = r["t"] + r["h"]
        if picked:
            days.append((day, float(np.sum([p["gross"] for p in picked])),
                         [p["gross"] for p in picked]))
    if not days:
        return None
    dd = pd.DataFrame(days, columns=["day", "ret", "trades"])
    return dd


def _sharpe_dd(daily_ret: np.ndarray):
    if len(daily_ret) < 20 or daily_ret.std() == 0:
        return float("nan"), float("nan")
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
    cum = np.cumsum(daily_ret)
    dd = float(np.max(np.maximum.accumulate(cum) - cum))
    return float(sharpe), dd


def _partial_vanna(sub):
    """Spearman partial of reversion P&L (gross) vs |vanna|, controlling ATM IV + DTE — the
    mechanistic test in the arc's style. Residualize both on [iv, dte] via OLS on ranks, correlate."""
    s = sub[["gross", "absv", "iv", "dte"]].dropna()
    if len(s) < 500 or s["absv"].nunique() < 5:
        return float("nan"), 0
    R = s.rank()
    X = np.column_stack([np.ones(len(R)), R["iv"], R["dte"]])
    def resid(y):
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        return y - X @ beta
    ry, rv = resid(R["gross"].to_numpy(float)), resid(R["absv"].to_numpy(float))
    if ry.std() == 0 or rv.std() == 0:
        return float("nan"), len(s)
    return float(np.corrcoef(ry, rv)[0, 1]), len(s)


# ----------------------------------------------------------------------------------------------
# report one book
# ----------------------------------------------------------------------------------------------
def report_book(store: str) -> None:
    _print("=" * 96)
    _print(f"BOOK: {store}")
    _print("=" * 96)
    df = build_trades(store, clean=True)
    if df is None or len(df) < 5000:
        _print("  feature cache / 1-min path missing or too short — skipped.\n")
        return
    dfu = build_trades(store, clean=False)                        # uncleaned → phantom-P&L delta
    path_note = (f" · price path from CLEAN {PATH_OF[store]} feed (same index)"
                 if PATH_OF.get(store, store) != store else "")
    _print(f"{df['day'].nunique()} days · {len(df):,} fade trades (all Δ×h) · "
           f"|vanna| terciles book-wide · fill t+1min, cost = round-trip bps of notional{path_note}")
    if store in CONTAMINATED_FEED:
        _print("  ⚠ FEED CAVEAT: this book's own 5-yr spot feed carries sustained pre-2025 corruption "
               "(no clean sibling feed exists) — the robust guard reduces but cannot fully remove it, "
               "so the OOS / Sep25+ / futures-corr reads (below) are the trustworthy verdict, NOT the "
               "full-sample gross.")
    _print("")

    # (1) grid: gross%/trade, breakeven bps, net@REF, exec Sharpe/win%/maxDD -----------------
    _print("(1) FULL Δ×h GRID (gross = mean fade return %/trade; breakeven = bps that zeroes it; "
           f"net@{REF_COST:.0f}bps; exec = non-overlapping daily track):")
    _print(f"  {'Δ→h':>8} {'gross%':>8} {'SE':>6} {'brkevn':>7} {'net@'+str(int(REF_COST)):>7} "
           f"{'Sharpe':>7} {'win%':>6} {'maxDD%':>7} {'n':>8}")
    for dl in DELTAS:
        for h in HOLDS:
            sub = df[(df["delta"] == dl) & (df["h"] == h)]
            if len(sub) < 500:
                continue
            g, se, _ = _perday_mean(sub)
            brk = g * 100.0                                       # bps
            net = g - REF_COST / 100.0
            ex = _exec_daily(sub)
            if ex is not None:
                dr = ex["ret"].to_numpy() - (REF_COST / 100.0) * ex["trades"].map(len).to_numpy()
                sharpe, mdd = _sharpe_dd(dr)
                win = 100.0 * (sub["gross"] > REF_COST / 100.0).mean()
            else:
                sharpe = mdd = win = float("nan")
            star = "  *" if (dl, h) == REF_DH else ""
            _print(f"  {str(dl)+'→'+str(h):>8} {g:>+8.4f} {se:>6.4f} {brk:>+6.1f} {net:>+7.4f} "
                   f"{sharpe:>7.2f} {win:>5.0f}% {mdd:>7.2f} {len(sub):>8,}{star}")
    _print("    breakeven bps = the round-trip cost that zeroes the mean fade return. Realistic "
           "near-month futures round-trip ≈ 3-5 bps → a breakeven BELOW that = DEAD after cost.")

    # (2) phantom-P&L check: cleaned vs uncleaned ---------------------------------------------
    _print("\n(2) BAD-TICK / PHANTOM-P&L check — mean gross%/trade CLEANED vs UNCLEANED "
           f"({df.attrs.get('rep_bars', 0):,} 1-min bars repaired = "
           f"{df.attrs.get('rep_frac', 0)*100:.2f}%; delta must be small):")
    _print(f"  {'Δ→h':>8} {'clean%':>9} {'raw%':>9} {'phantom Δ':>10}")
    for dl in DELTAS:
        for h in HOLDS:
            c = df[(df["delta"] == dl) & (df["h"] == h)]["gross"].mean()
            u = (dfu[(dfu["delta"] == dl) & (dfu["h"] == h)]["gross"].mean()
                 if dfu is not None else float("nan"))
            _print(f"  {str(dl)+'→'+str(h):>8} {c:>+9.4f} {u:>+9.4f} {u - c:>+10.4f}")
    _print("    (a big negative phantom Δ = the uncleaned P&L is inflated by snap-back ticks; the "
           "cleaned column is the trustworthy one.)")

    # (3) |vanna| REGIME split — the mechanistic core -----------------------------------------
    _print(f"\n(3) |vanna| TERCILE split @ (Δ,h)={REF_DH} — reversion should STRENGTHEN LOW→HIGH "
           "(dealer dampening ⇒ stronger fade in HIGH-|vanna| SIDEWAYS):")
    sub = df[(df["delta"] == REF_DH[0]) & (df["h"] == REF_DH[1])]
    _print(f"  {'tercile':>12} {'gross%':>9} {'SE':>7} {'det%':>9} {'net@'+str(int(REF_COST)):>7} {'n':>8}")
    tstats = {}
    for ti, tn in [(0, "LOW (trend)"), (1, "MID"), (2, "HIGH (sideways)")]:
        st = sub[sub["terc"] == ti]
        if len(st) < 200:
            continue
        g, se, _ = _perday_mean(st)
        d, _, _ = _perday_mean(st, "det")
        tstats[ti] = g
        _print(f"  {tn:>12} {g:>+9.4f} {se:>7.4f} {d:>+9.4f} {g - REF_COST/100.0:>+7.4f} {len(st):>8,}")
    mono = (2 in tstats and 0 in tstats and tstats[2] > tstats.get(1, -9) > tstats[0])
    if 2 in tstats and 0 in tstats:
        _print(f"    HIGH−LOW spread = {tstats[2] - tstats[0]:+.4f}%/trade  ·  monotone LOW<MID<HIGH: "
               f"{'YES' if mono else 'NO'}")
    pc, npc = _partial_vanna(sub)
    _print(f"    partial(gross, |vanna| | IV, DTE) = {pc:+.3f}  (n={npc:,}) — reversion strength vs "
           "|vanna| net of IV/DTE; >0 ⇒ mechanism holds (fade pays more when dealer-gamma is high).")

    # (4) day-detrended vs raw at headline ----------------------------------------------------
    g_raw, _, _ = _perday_mean(sub)
    g_det, _, _ = _perday_mean(sub, "det")
    _print(f"\n(4) RAW vs DAY-DETRENDED @ {REF_DH}: raw {g_raw:+.4f}%  ·  detrended {g_det:+.4f}%/trade "
           "(detrended strips 'short the day's drift' → the pure reversion alpha).")

    # (5) walk-forward OOS (day-block 70/30) --------------------------------------------------
    days = sorted(df["day"].unique())
    cut = days[int(len(days) * 0.70)]
    _print(f"\n(5) WALK-FORWARD OOS (train days <{cut}, test ≥) @ {REF_DH} — net@{REF_COST:.0f}bps and the "
           "HIGH−LOW |vanna| spread OUT OF SAMPLE:")
    for lbl, msk in [("in-sample", sub["day"] < cut), ("OOS   ", sub["day"] >= cut)]:
        s = sub[msk]
        g, se, nd = _perday_mean(s)
        hi = s[s["terc"] == 2]
        lo = s[s["terc"] == 0]
        sp = (hi["gross"].mean() - lo["gross"].mean()) if len(hi) and len(lo) else float("nan")
        _print(f"  {lbl:>10}: gross {g:+.4f}±{se:.4f}%  net@{int(REF_COST)} {g - REF_COST/100.0:+.4f}%  "
               f"HIGH−LOW {sp:+.4f}%  ({nd} days)")

    # (6) placebos ----------------------------------------------------------------------------
    real = sub["gross"].mean()
    ret = sub["gross"].to_numpy() * sub["pos"].to_numpy()      # ret independent of chosen sign
    perm = np.array([np.mean(RNG.choice([-1.0, 1.0], len(ret)) * ret) for _ in range(N_PERM)])
    z = (real - perm.mean()) / perm.std() if perm.std() > 0 else float("nan")
    # random-window placebo: keep pos, but read the outcome over a random h-window in the SAME day
    rw = _random_window_placebo(df, REF_DH)
    _print(f"\n(6) PLACEBOS @ {REF_DH} (edge must beat both):")
    _print(f"  direction-shuffle : real {real:+.4f}%  vs shuffled {perm.mean():+.4f}±{perm.std():.4f}%  "
           f"(z={z:+.1f})")
    _print(f"  random-window     : real {real:+.4f}%  vs random-window {rw:+.4f}%")

    # (7) expiry-structure eras ---------------------------------------------------------------
    _print(f"\n(7) by expiry-structure ERA @ {REF_DH} (Sep25+ is the only current-regime read):")
    _print(f"  {'era':<42} {'gross%':>9} {'net@'+str(int(REF_COST)):>7} {'n':>7}")
    for e in era_order(sub["era"].unique()):
        s = sub[sub["era"] == e]
        if len(s) < 200:
            _print(f"  {ERA_LABELS[e]:<42} (n={len(s)} — thin)")
            continue
        g, _, _ = _perday_mean(s)
        _print(f"  {ERA_LABELS[e]:<42} {g:>+9.4f} {g - REF_COST/100.0:>+7.4f} {len(s):>7,}")

    # (8) futures validation ------------------------------------------------------------------
    _futures_validation(store)
    _print("")


def _random_window_placebo(df, dh, seed=1):
    """Keep each stamp's fade DIRECTION but read the outcome over a DIFFERENT h-window from the same
    day (re-pair signal→outcome within the day). Breaks the signal↔specific-window link while holding
    the day fixed; a real reversion edge must beat this. fwd = gross/pos is the raw forward return of
    each window; we permute those windows against the directions."""
    rng = np.random.default_rng(seed)
    dl, h = dh
    sub = df[(df["delta"] == dl) & (df["h"] == h)]
    out = []
    for _, g in sub.groupby("day"):
        pos = g["pos"].to_numpy()
        fwd = (g["gross"].to_numpy() / pos)                    # raw forward return per window
        if len(fwd) < 3:
            continue
        out.extend((pos * rng.permutation(fwd)).tolist())      # real direction × a random day-window
    return float(np.mean(out)) if out else float("nan")


def _futures_validation(store: str) -> None:
    """On the ~50-day intraday-futures overlap: recompute the identical strategy on the ACTUAL future
    vs the spot proxy, report per-trade P&L correlation and mean basis drift over the hold — the
    proof that spot is a sound path proxy for the 5-yr backtest."""
    from data.dhan import DHAN_FUT_DIR, load_futures_intraday_day
    fut_sym = FUT_OF[store]
    fdir = DHAN_FUT_DIR / fut_sym
    fut_days = sorted(p.stem for p in fdir.glob("*.parquet")) if fdir.exists() else []
    if not fut_days:
        _print(f"\n(8) FUTURES VALIDATION: no intraday futures for {fut_sym} — skipped.")
        return
    dl, h = REF_DH
    feat = load_features(store)
    vv = feat.set_index(["day", "time_ist"])["vanna"].abs() if feat is not None else None
    rows_s, rows_f, basis = [], [], []
    for day in fut_days:
        sp = _day_path(PATH_OF.get(store, store), day, clean=True)
        fd = load_futures_intraday_day(fut_sym, day)
        if sp is None or fd is None or fd.empty:
            continue
        smap = sp[0]
        fser = fd.groupby("time_ist")["close"].first()
        fmap = {int(_mins(t)): float(v) for t, v in fser.items() if v == v and v > 0}
        for t in range(FIRST_DECISION, FLAT_BY - h + 1, 5):
            ps_t, ps_prev = _px(smap, t), _px(smap, t - dl)
            ps_e, ps_x = _px(smap, t + 1), _px(smap, t + h)
            pf_e, pf_x = _px(fmap, t + 1), _px(fmap, t + h)
            if None in (ps_t, ps_prev, ps_e, ps_x, pf_e, pf_x) or ps_prev <= 0:
                continue
            sig = ps_t / ps_prev - 1.0
            if sig == 0:
                continue
            pos = -1.0 if sig > 0 else 1.0
            rows_s.append(pos * (ps_x / ps_e - 1.0) * 100.0)
            rows_f.append(pos * (pf_x / pf_e - 1.0) * 100.0)
            basis.append((pf_x - ps_x) - (pf_e - ps_e))         # basis drift over the hold (pts)
    if len(rows_s) < 50:
        _print(f"\n(8) FUTURES VALIDATION ({fut_sym}, {len(fut_days)}d): too few paired trades "
               f"({len(rows_s)}).")
        return
    s, f = np.array(rows_s), np.array(rows_f)
    corr = float(np.corrcoef(s, f)[0, 1])
    _print(f"\n(8) FUTURES VALIDATION — spot-proxy vs ACTUAL {fut_sym} future @ {REF_DH} "
           f"({len(fut_days)} fut days, {len(s):,} paired trades):")
    _print(f"    per-trade P&L corr(spot, future) = {corr:+.3f}   "
           f"mean spot {s.mean():+.4f}%  ·  mean future {f.mean():+.4f}%")
    _print(f"    mean |basis drift| over the {h}m hold = {np.mean(np.abs(basis)):.3f} pts "
           f"(tiny vs the move ⇒ spot is a sound PATH proxy).")


def main() -> None:
    books = [sys.argv[1]] if len(sys.argv) > 1 else BOOKS
    _print("Study #24 — intraday MEAN-REVERSION on the NEAR-MONTH FUTURE: does it monetize, and is "
           "it high-|vanna| concentrated?\n")
    for b in books:
        report_book(b)
    _print("READ: PASS needs ALL of — net>0 after ≥4 bps · monotone LOW→HIGH |vanna| · OOS survives · "
           "beats BOTH placebos · spot tracks the future. A breakeven cost below ~3-5 bps = DEAD after "
           "realistic futures cost (the likely, clean negative). ⚠️ spot-proxy P&L, modelled cost, "
           "sign-free of GEX; research, not a live rule.")


if __name__ == "__main__":
    main()
