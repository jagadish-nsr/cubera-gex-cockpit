"""Simple dashboard — a decision-first, plain-language face of the GEX engine (paper only).

A stripped-down alternate to app/dashboard.py for a non-technical trader: market mood, the
expected day range IN POINTS (tiered by the |vanna|/charm bucket), today's range so far, the
key levels, one 'what fits this regime' line, a simplified positioning footprint, the futures
OI build-up in TWO windows (since-open + rolling last-30-min), and — on expiry days — the
gamma-blast crossover verdict (True-run score, OI change, call/put crossover). Reuses the same
engine as the main dashboard (load_dashboard, the vol-regime gauge, oi_footprint, futures_buildup,
gammablast); it only changes the *presentation*. Docs: docs/simple-dashboard.md.

  streamlit run app/simple_dashboard.py

⚠️ Day high/low (range + blast-compression) come from spot SNAPSHOTS at the refresh cadence, so
they MISS intraday ticks and UNDERSTATE the true range — see docs/simple-dashboard.md. A future
Dhan websocket feed closes this. Decision-support only — the human confirms every trade.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from app.data_access import load_dashboard  # noqa: E402

st.set_page_config(page_title="Simple GEX", page_icon="⚡", layout="wide")

# ── design tokens + layout (ported from the approved mockup) ────────────────────────────────
CSS = """
<style>
:root{
  --ground:#f4f6f9;--surface:#fff;--surface-2:#eef1f6;--inset:#e9edf3;--ink:#111621;--ink-2:#4b5567;
  --ink-mute:#7a8494;--line:#e0e5ec;--spark:#5b54d6;--go:#0f9d6b;--go-bg:#e4f6ee;--warn:#c07d10;
  --warn-bg:#fbf0da;--stop:#cf3f4b;--stop-bg:#fbe6e8;--call:#2f80d8;--put:#b5620c;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  --r:14px;--shadow:0 1px 2px rgba(16,22,34,.05),0 8px 24px rgba(16,22,34,.06);
}
@media (prefers-color-scheme:dark){:root{
  --ground:#0c0f16;--surface:#161a22;--surface-2:#1c222d;--inset:#212836;--ink:#eef2f8;--ink-2:#aab4c4;
  --ink-mute:#6d7889;--line:#28303d;--spark:#8e86f7;--go:#2cd49b;--go-bg:#0f2a24;--warn:#f0a83a;
  --warn-bg:#2c2210;--stop:#f0616d;--stop-bg:#2c1418;--call:#3f8fe0;--put:#d17720;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);}}
.stApp{background:var(--ground)}
header[data-testid="stHeader"]{background:transparent}
.simp{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);max-width:980px;margin:0 auto}
.simp .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.simp .grid2 .col{min-width:0}
@media(max-width:760px){.simp .grid2{grid-template-columns:1fr}}
.simp .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.simp .eyebrow{font-size:.68rem;font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--ink-mute)}
.simp .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);margin-top:14px}
.simp h2.lbl{font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-mute);margin:16px 0 6px 2px}
.simp .gauge{display:grid;grid-template-columns:auto 1fr;gap:16px;align-items:center;padding:16px 18px}
.simp .spot .val{font-size:2.1rem;font-weight:700;letter-spacing:-.01em;line-height:1}
.simp .spot .chg{font-size:.82rem;color:var(--ink-mute);margin-top:3px}
.simp .mood{display:flex;flex-direction:column;gap:8px;border-left:1px solid var(--line);padding-left:16px}
.simp .chip{display:inline-flex;align-items:center;gap:7px;font-size:.82rem;font-weight:600;padding:5px 11px;border-radius:999px;width:max-content}
.simp .chip .dot{width:9px;height:9px;border-radius:50%}
.simp .chip.calm{background:var(--go-bg);color:var(--go)}.simp .chip.calm .dot{background:var(--go)}
.simp .chip.wild{background:var(--warn-bg);color:var(--warn)}.simp .chip.wild .dot{background:var(--warn)}
.simp .chip.mid{background:var(--surface-2);color:var(--ink-2)}.simp .chip.mid .dot{background:var(--ink-mute)}
.simp .range{font-size:.9rem;color:var(--ink-2)}.simp .range b{color:var(--ink);font-weight:700}
.simp .range .tier{font-size:.68rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-mute)}
.simp .rangebar{padding:13px 16px;display:flex;flex-direction:column;gap:8px}
.simp .rb-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.simp .rb-vals{font-size:.86rem;color:var(--ink-2)}.simp .rb-vals b{color:var(--ink);font-weight:700}
.simp .rb-scale{position:relative}
.simp .rb-track{height:8px;border-radius:999px;background:var(--inset);overflow:hidden;position:relative}
.simp .rb-fill{height:100%;border-radius:999px;background:var(--spark);opacity:.5}
.simp .rb-band{position:absolute;top:0;bottom:0;background:var(--ink-mute);opacity:.28;border-radius:999px}
.simp .rb-tick{position:absolute;top:-2px;height:12px;width:2px;border-radius:2px;transform:translateX(-50%)}
.simp .rb-tick.put{background:var(--go)}.simp .rb-tick.call{background:var(--stop)}.simp .rb-tick.mag{background:var(--spark)}
.simp .rb-mark{position:absolute;top:6px;transform:translateX(-50%);font-size:.66rem;line-height:1;color:var(--ink)}
.simp .rb-ends{display:flex;justify-content:space-between;font-size:.66rem;color:var(--ink-mute);margin-top:9px}
.simp .rb-ends .lo{color:var(--go)}.simp .rb-ends .hi{color:var(--stop)}
.simp .rb-note{font-size:.78rem;color:var(--go);font-weight:600}
.simp .rb-cav{font-size:.72rem;color:var(--ink-mute)}
.simp .candlecard{padding:14px 16px}
.simp .vc{margin:8px 0 0}
.simp .vc .vc-line{transition:stroke-width .12s}
.simp .vc-lvl{cursor:default}
.simp .vc-lvl:hover .vc-line{stroke-width:2.6}
.simp .vc-lvl:hover .vc-lbl{font-weight:700}
.simp .vc-lbl{font-size:12px;fill:var(--ink-2)}
.simp .vc-mp{font-size:10.5px;fill:var(--ink-2)}
.simp .vc-off{font-size:10px;opacity:.72;font-style:italic}
.simp .vc-lvl.vc-off:hover{opacity:1}
.simp .vc-val{font-weight:700}
.simp .sect{padding:11px 14px;margin-top:10px}
.simp .sect-hd{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.simp .sect-grid{display:flex;flex-wrap:wrap;gap:6px}
.simp .sq{display:inline-flex;align-items:baseline;gap:7px;padding:4px 9px;border-radius:7px;
  background:var(--surface-2);font-size:.74rem;line-height:1.25}
.simp .sq.up{background:var(--go-bg)}.simp .sq.dn{background:var(--stop-bg)}
.simp .sq .nm{color:var(--ink-2);font-weight:600}
.simp .sq .wt{color:var(--ink-mute);font-size:.64rem;font-variant-numeric:tabular-nums}
.simp .sq .pc{font-weight:700;font-variant-numeric:tabular-nums}
.simp .sq.up .pc{color:var(--go)}.simp .sq.dn .pc{color:var(--stop)}
.simp .sq.na .pc{color:var(--ink-mute)}
.simp .sq.broad{border:1px solid var(--line)}
.simp .mp-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:.7rem;color:var(--ink-mute);margin:6px 0 2px}
.simp .mp-legend span{display:inline-flex;align-items:center;gap:5px}
.simp .mp-legend i{width:16px;height:0;border-top-width:2px;border-top-style:solid;display:inline-block}
.simp .mp-chip{display:inline-flex;align-items:center;gap:6px;font-size:.72rem;font-weight:600;padding:3px 9px;border-radius:999px;background:var(--surface-2);color:var(--ink-2);margin-top:6px}
.simp .mp-chip.agree{background:var(--go-bg);color:var(--go)}
.simp .mp-chip.conflict{background:var(--warn-bg);color:var(--warn)}
.simp .cc-read{font-size:.9rem;color:var(--ink);font-weight:600;margin:9px 0 2px}
.simp .news{display:flex;gap:11px;align-items:flex-start;padding:12px 15px;border-radius:12px;background:var(--warn-bg);border:1px solid var(--warn);margin-top:14px}
.simp .news .txt{font-size:.86rem}.simp .news .txt b{color:var(--warn)}
.simp .hero{padding:18px}
.simp .verdict{display:flex;align-items:center;gap:12px;margin:8px 0 4px}
.simp .verdict .big{font-size:2.05rem;font-weight:800;letter-spacing:-.02em;line-height:1}
.simp .verdict .ic{font-size:1.6rem}
.simp .lean-warn .big{color:var(--warn)}.simp .lean-go .big{color:var(--go)}.simp .lean-stop .big{color:var(--stop)}
.simp .instruct{font-size:.98rem;margin:6px 0 2px;max-width:52ch}
.simp .subnote{font-size:.8rem;color:var(--ink-mute);margin-top:6px}
.simp .meter{margin:15px 0 4px}
.simp .meter .head{display:flex;justify-content:space-between;align-items:baseline;font-size:.78rem;color:var(--ink-mute);margin-bottom:6px}
.simp .meter .head b{color:var(--ink);font-size:.92rem;font-weight:700}
.simp .track{height:9px;border-radius:999px;background:var(--inset);position:relative;overflow:hidden}
.simp .track .fill{position:absolute;inset:0 auto 0 0;border-radius:999px}
.simp .ticks{display:flex;justify-content:space-between;font-size:.64rem;color:var(--ink-mute);margin-top:5px;text-transform:uppercase}
.simp .blast-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px}
.simp .blast-stats>div{background:var(--surface-2);border-radius:10px;padding:9px 12px}
.simp .blast-stats small{font-size:.68rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.04em}
.simp .blast-stats b{display:block;font-size:1.05rem;font-weight:700;margin:2px 0 1px}
.simp .blast-stats b.down{color:var(--stop)}
.simp .blast-stats em{font-size:.72rem;color:var(--ink-mute);font-style:normal}
.simp figure{margin:16px 0 0}
.simp .chart-cap{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.simp .chart-cap .t{font-size:.78rem;color:var(--ink-2);font-weight:600}
.simp .legend{display:flex;gap:14px;font-size:.74rem;color:var(--ink-mute)}
.simp .legend span{display:inline-flex;align-items:center;gap:5px}
.simp .legend i{width:15px;height:2.5px;border-radius:2px;display:inline-block}
.simp svg{display:block;width:100%;height:auto;overflow:visible}
.simp .strikes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.simp .strike{padding:14px 15px;margin-top:0}
.simp .strike .role{font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute)}
.simp .strike .k{font-size:1.35rem;font-weight:700;margin-top:1px}
.simp .cp{display:flex;gap:14px;margin-top:10px}.simp .cp div{font-size:.78rem;color:var(--ink-mute)}
.simp .cp b{display:block;font-size:1.02rem;color:var(--ink);font-weight:700}
.simp .cp .c b{color:var(--call)}.simp .cp .p b{color:var(--put)}
.simp .state{margin-top:11px;font-size:.78rem;font-weight:600;padding:4px 9px;border-radius:8px;width:max-content;background:var(--surface-2);color:var(--ink-2)}
.simp .state.cross{background:var(--warn-bg);color:var(--warn)}
.simp .play{padding:16px 18px}.simp .play .eyebrow{color:var(--spark)}
.simp .play .verdict-sm{font-size:1.4rem;font-weight:800;margin:7px 0 5px}
.simp .play p{font-size:.92rem;color:var(--ink-2);margin:0 0 10px;max-width:52ch}
.simp .play .play-exit{font-size:.8rem;color:var(--ink-mute);border-top:1px solid var(--line);padding-top:9px}
.simp .pnl{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}
.simp .pnl-hd{font-size:.66rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute)}
.simp .pnl-legs{font-size:.82rem;color:var(--ink);margin:6px 0 9px;font-family:var(--mono)}
.simp .pnl-note{font-size:.72rem;color:var(--warn);margin:2px 0 8px;line-height:1.35}
.simp .pnl-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.simp .pnl-grid>div{background:var(--surface-2);border-radius:9px;padding:8px 11px}
.simp .pnl-grid small{font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.03em}
.simp .pnl-grid b{display:block;font-size:.92rem;font-weight:700;margin-top:2px}
.simp .pnl-grid b.go{color:var(--go)}.simp .pnl-grid b.stop{color:var(--stop)}
.simp .pnl-payoff{margin-top:9px}
.simp .levels{padding:2px 0}
.simp .lvl{display:grid;grid-template-columns:26px 1fr auto;gap:10px;align-items:center;padding:9px 15px;border-bottom:1px solid var(--line)}
.simp .lvl:last-child{border-bottom:0}.simp .lvl .ic{text-align:center}
.simp .lvl .name{font-size:.9rem;color:var(--ink-2)}.simp .lvl .name small{color:var(--ink-mute);font-size:.76rem}
.simp .lvl .px{font-size:1.02rem;font-weight:700}
.simp .lvl.now{background:var(--surface-2)}.simp .lvl.now .name{color:var(--ink);font-weight:700}
.simp .lvl.ceil .px{color:var(--stop)}.simp .lvl.floor .px{color:var(--go)}
.simp .foot-note{font-size:.8rem;color:var(--ink-mute);padding:11px 15px 4px}
.simp .fp-row{display:grid;grid-template-columns:1fr auto auto;gap:10px;align-items:baseline;padding:10px 15px;border-bottom:1px solid var(--line)}
.simp .fp-row>span{font-size:.9rem;color:var(--ink-2)}
.simp .fp-row b{font-size:1.02rem;font-weight:700;font-family:var(--mono)}
.simp .fp-row small{font-size:.74rem;color:var(--ink-mute)}
.simp .fp-row.ceil b{color:var(--stop)}.simp .fp-row.floor b{color:var(--go)}
.simp .fp-sum{font-size:.86rem;padding:11px 15px 5px}.simp .fp-sum b{color:var(--warn)}
.simp .fut{padding:15px 16px}
.simp .fut-windows{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px}
.simp .fw{background:var(--surface-2);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:7px;align-items:flex-start}
.simp .fw-lbl{font-size:.66rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.simp .fut-badge{font-size:.82rem;font-weight:700;padding:5px 12px;border-radius:999px;white-space:nowrap}
.simp .fut-badge.bear{background:var(--stop-bg);color:var(--stop)}
.simp .fut-badge.bull{background:var(--go-bg);color:var(--go)}
.simp .fut-badge.cover{background:var(--warn-bg);color:var(--warn)}
.simp .fw-sub{font-size:.76rem;color:var(--ink-mute)}
.simp .fut-desc{font-size:.9rem;color:var(--ink-2);margin:10px 0 12px;max-width:52ch}.simp .fut-desc b{color:var(--ink)}
.simp .fut-formula{font-size:.78rem;color:var(--ink-mute)}
.simp .winstrip{margin-top:10px;border-top:1px solid var(--line)}
.simp .win-hd{font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute);padding:9px 15px 3px}
.simp .winrow{display:grid;grid-template-columns:44px 1fr auto;gap:10px;align-items:center;padding:6px 15px;border-bottom:1px solid var(--line)}
.simp .winrow:last-child{border-bottom:0}
.simp .winrow .wl{font-size:.76rem;color:var(--ink-2);font-weight:700;font-family:var(--mono)}
.simp .winrow.dim{opacity:.55}
.simp .winrow .wd{font-size:.76rem;color:var(--ink-mute);font-family:var(--mono);text-align:right;white-space:nowrap}
.simp .wbadge{font-size:.76rem;font-weight:700;padding:3px 10px;border-radius:999px;width:max-content}
.simp .wbadge.bull{background:var(--go-bg);color:var(--go)}
.simp .wbadge.bear{background:var(--stop-bg);color:var(--stop)}
.simp .wbadge.cover{background:var(--warn-bg);color:var(--warn)}
.simp .wbadge.flat{background:var(--surface-2);color:var(--ink-2)}
.simp .win-cap{font-size:.72rem;color:var(--ink-mute);padding:8px 15px 4px}
.simp .win-cap.warn{color:var(--warn)}
.simp .foot{margin-top:22px;font-size:.76rem;color:var(--ink-mute);text-align:center;line-height:1.6}
@media (max-width:430px){.simp .gauge{grid-template-columns:1fr}.simp .mood{border-left:0;padding-left:0}.simp .strikes,.simp .fut-windows{grid-template-columns:1fr}}
</style>
"""

_MOOD = {"calm": ("calm", "🟢 Calm &amp; range-bound"),
         "wild": ("wild", "🟧 Wild — wider swings"),
         "mid": ("mid", "⬜ Normal / mixed")}
_PLAY = {
    "calm": ("🎯 Sell premium near the walls",
             "Quiet, pinning regime — fade extremes back toward the magnet and sell premium near the "
             "walls. You can size up a little; keep stops just outside the walls."),
    "wild": ("🚀 Trade the break, buy convexity",
             "Trending regime — respect a wall break and don't fade it. Buy options for convexity "
             "rather than selling premium; stops beyond the walls; size down (bigger swings)."),
    "mid": ("🕒 No trade — wait",
            "Neutral, unclear regime — no clear edge either way. De-size and wait for the market to "
            "pick a side before selling or buying premium."),
}


_WINDOWS = [("60m", 60), ("30m", 30), ("15m", 15), ("5m", 5)]   # trailing rolling windows


def f(x, nd=0):
    return f"{x:,.{nd}f}" if x is not None and x == x else "—"


def _money(x):
    """₹ magnitude, compact: crore / lakh / rupees."""
    if x is None or x != x:
        return "—"
    ax = abs(x)
    return (f"₹{x/1e7:.1f}cr" if ax >= 1e7 else f"₹{x/1e5:.1f}L" if ax >= 1e5 else f"₹{x:,.0f}")


def _svg_crossover(series, strike):
    """Small call-vs-put premium crossover chart from the accumulated anchored-strike series."""
    pts = [(s["call"], s["put"]) for s in series if s.get("call") == s.get("call")
           and s.get("put") == s.get("put") and s.get("call") is not None and s.get("put") is not None]
    if len(pts) < 2:
        return ""
    calls = [c for c, _ in pts]
    puts = [p for _, p in pts]
    lo = min(min(calls), min(puts))
    hi = max(max(calls), max(puts))
    rng = (hi - lo) or 1.0
    n = len(pts)
    W, H, x0, x1, yT, yB = 520, 150, 34, 500, 20, 122

    def xy(i, v):
        x = x0 + (x1 - x0) * (i / (n - 1))
        y = yB - (yB - yT) * ((v - lo) / rng)
        return f"{x:.0f},{y:.0f}"
    cline = " ".join(xy(i, c) for i, c in enumerate(calls))
    pline = " ".join(xy(i, p) for i, p in enumerate(puts))
    ce, pe = calls[-1], puts[-1]
    return f"""
    <figure><div class="chart-cap"><span class="t">Call vs Put premium · strike {strike:,.0f}</span>
      <span class="legend"><span><i style="background:var(--call)"></i>Call</span>
      <span><i style="background:var(--put)"></i>Put</span></span></div>
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Call vs put premium over the session for strike {strike:.0f}.">
      <line x1="{x0}" y1="{yB}" x2="{x1}" y2="{yB}" stroke="var(--line)"/>
      <polyline fill="none" stroke="var(--put)" stroke-width="2.5" stroke-linejoin="round" points="{pline}"/>
      <polyline fill="none" stroke="var(--call)" stroke-width="2.5" stroke-linejoin="round" points="{cline}"/>
      <circle cx="{x1}" cy="{yB-(yB-yT)*((ce-lo)/rng):.0f}" r="4" fill="var(--call)"/>
      <circle cx="{x1}" cy="{yB-(yB-yT)*((pe-lo)/rng):.0f}" r="4" fill="var(--put)"/>
      <text x="{x1+8}" y="{yB-(yB-yT)*((ce-lo)/rng):.0f}" font-size="11" fill="var(--call)" font-weight="700" class="mono">{ce:.0f}</text>
      <text x="{x1+8}" y="{yB-(yB-yT)*((pe-lo)/rng)+11:.0f}" font-size="11" fill="var(--put)" font-weight="700" class="mono">{pe:.0f}</text>
    </svg></figure>"""


def _futures_window(day_df, minutes=30):
    """(label, badge_class, dprice, doi) for the rolling last-``minutes`` futures window, or None."""
    from app.futures_buildup import classify_futures_buildup
    if day_df is None or getattr(day_df, "empty", True):
        return None
    df = day_df.sort_values("time_ist").drop_duplicates("time_ist").reset_index(drop=True)
    if len(df) < 2:
        return None

    def _hm(t):
        try:
            h, m = str(t).split(":"); return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None
    last = df.iloc[-1]
    tnow = _hm(last["time_ist"])
    prior = df[df["time_ist"].apply(lambda t: (_hm(t) or 0) <= (tnow or 0) - minutes)]
    ref = prior.iloc[-1] if not prior.empty else df.iloc[0]

    def g(r, k):
        v = pd.to_numeric(r.get(k), errors="coerce")
        return float(v) if v == v else None
    p0, p1 = g(ref, "price"), g(last, "price")
    o0, o1 = g(ref, "agg_oi"), g(last, "agg_oi")
    dprice = (p1 - p0) if (p0 is not None and p1 is not None) else None
    doi = (o1 - o0) if (o0 is not None and o1 is not None) else None
    label = classify_futures_buildup(dprice, doi)
    return label, dprice, doi


def _fut_badge(label):
    cls = {"long build-up": "bull", "short build-up": "bear",
           "short covering": "cover", "long unwinding": "cover"}.get(label, "cover")
    ic = {"long build-up": "🐂", "short build-up": "🐻",
          "short covering": "🔄", "long unwinding": "📉"}.get(label, "•")
    return cls, ic, (label or "—").title()


_SPOT_TICK_S = 10.0     # fast SPOT lane: one cheap index quote every 10s, off the render thread
_POLL_S = 3             # fragment repaint poll while auto — a pure in-memory read of the last
                        # published snapshot (NO network here), so repaints are instant / no dim.
_FETCH_LEAD_S = 15      # background fetcher starts the pull this many s BEFORE each clock boundary
                        # so the assembled snapshot is ready AT the boundary (~12s to build).


def _sync_fetch(symbol, use_demo):
    """One blocking pull straight into session_state — used only for the FIRST paint and manual
    Refresh (auto mode fetches in the background thread, off the render path)."""
    try:
        st.session_state["simp_data"] = load_dashboard(None, use_demo, symbol)
        st.session_state["simp_asof"] = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        st.session_state["simp_err"] = None
    except Exception as e:  # noqa: BLE001
        st.session_state["simp_err"] = str(e)


def _dhan_feed_controls(symbol: str) -> None:
    """Sidebar: paste/refresh the Dhan access token (SEBI-capped at 24h) and see its status.
    The token is a SECRET — stored gitignored in data/.dhan_token, never shown or logged. A stale
    token just falls the feed back to NSE, so this is convenience, not a hard dependency."""
    from data.dhan import token_status, write_token

    st.divider()
    stt = token_status()
    if not stt["present"]:
        head, exp0 = "🔌 Dhan feed — not set (using NSE)", True
    elif stt["expired"]:
        head, exp0 = "🔴 Dhan token expired — re-paste", True
    elif stt["hours_left"] is not None:
        head, exp0 = f"🟢 Dhan token · ~{stt['hours_left']:.1f}h left", False
    else:
        head, exp0 = "🟢 Dhan token set", False

    with st.expander(head, expanded=exp0):
        st.caption("web.dhan.co → Profile → **DhanHQ Trading APIs** → generate Access Token. "
                   "Needs the **Data APIs** subscription. SEBI caps the token at 24h — re-paste daily.")
        tok = st.text_input("Access token", type="password", key="dhan_tok_in",
                            help="Stored in data/.dhan_token (gitignored); never logged or committed.")
        c1, c2 = st.columns(2)
        if c1.button("💾 Save", width="stretch"):
            if tok.strip():
                write_token(tok.strip())
                st.session_state.pop("simp_data", None)   # force a reload on the new feed
                st.success("Saved — switching to Dhan.")
                st.rerun()
            else:
                st.warning("Paste a token first.")
        if c2.button("🔍 Test", width="stretch"):
            with st.spinner("Pinging Dhan…"):
                from data import dhan_live
                p = dhan_live.ping(symbol)
            (st.success if p["ok"] else st.error)(
                f"OK · {p['expiries']} expiries · spot {p['spot']}" if p["ok"]
                else f"Failed: {p['error']}")
        if st.button("⏬ Backfill today's OI", width="stretch",
                     help="Fill any missing 1-min per-strike OI for today from Dhan history so the "
                          "trailing windows and 'since open' cover the whole session. Idempotent; "
                          "writes to the separate Dhan journal."):
            today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            with st.spinner(f"Backfilling {symbol} {today} from Dhan…"):
                from app.oi_backfill import backfill_oi_strikes_dhan
                r = backfill_oi_strikes_dhan(symbol, today, log=lambda *a: None)
            st.success(f"Filled {r['stamps']} new 1-min stamps ({r['had']} already present).")
            st.session_state.pop("simp_hl", None)


DOC_PATH = ROOT / "docs" / "simple-dashboard.md"


@st.cache_data(show_spinner=False)
def _load_doc(mtime: float) -> str:
    """The component guide, read from docs/simple-dashboard.md (cached on its mtime, so editing
    the file refreshes the in-app help without a restart)."""
    return DOC_PATH.read_text(encoding="utf-8")


def _doc_sections(md: str):
    """Split the guide into ('## heading', body) sections, with the intro under 'Overview'."""
    out, head, buf = [], "Overview", []
    for line in md.splitlines():
        if line.startswith("## "):
            out.append((head, "\n".join(buf)))
            head, buf = line[3:].strip(), [line]
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return [(h, b) for h, b in out if b.strip()]


def _help_page():
    """In-app help: the full component guide + how to read every level, rendered from the same
    markdown file that lives in docs/ (single source of truth — the doc can never drift from the
    in-app help)."""
    if not DOC_PATH.exists():
        st.warning(f"Guide not found at `{DOC_PATH}`.")
        return
    secs = _doc_sections(_load_doc(DOC_PATH.stat().st_mtime))
    titles = [h for h, _ in secs]
    st.caption("📖 How to read this dashboard — untick **Help** in the sidebar to go back.")
    pick = st.radio("Jump to", ["Everything"] + titles, horizontal=True, label_visibility="collapsed")
    body = "\n\n".join(b for _, b in secs) if pick == "Everything" else \
        next(b for h, b in secs if h == pick)
    st.markdown(body)


def main():
    with st.sidebar:
        st.header("Simple view")
        show_help = st.checkbox("📖 Help — how to read this", value=False, key="simp_help",
                                help="The full component guide: every panel, and when price tends "
                                     "to respect a level vs break it (with the measured numbers).")
        symbols = list(config.INDICES)
        symbol = st.selectbox("Index", symbols, index=symbols.index(config.DEFAULT_INDEX),
                              format_func=lambda s: config.INDICES[s].label)
        use_demo = st.checkbox("Demo data (offline)", value=False)
        refresh = st.button("🔄 Refresh", type="primary", width="stretch",
                            help="Request a fresh chain pull now. Non-blocking — the background "
                                 "fetcher does the work and the page picks it up within seconds.")
        # Controls REPAINTING, not fetching: the background lanes run either way. Defaults ON now
        # that a repaint costs ~0.26s (it defaulted off when a refresh dimmed the page for ~14s).
        auto = st.checkbox("🔴 Live updates", value=True,
                           help="Keep the page updating in place: spot & candle every 10s, levels "
                                "on the interval below. Untick to freeze the screen (the feed "
                                "keeps running in the background).")
        auto_mins = st.selectbox("Levels refresh", [1, 3, 5], index=1, format_func=lambda m: f"{m} min",
                                 disabled=not auto,
                                 help="How often the OPTION CHAIN is pulled — walls, magnet, flip, "
                                      "footprint, greeks. Spot and the day candle update every 10s "
                                      "regardless of this setting.")
        st.caption("Spot & candle 10s · levels on the interval · page repaints every 3s")
        st.checkbox("📊 5-day value (composite)", value=False, key="simp_weekly",
                    help="Overlay the 5-day composite value area / POC (dotted) on the profile, "
                         "alongside the prior-day value (dashed). A longer-horizon reference map.")
        _dhan_feed_controls(symbol)
        st.checkbox("🐋 Big-money tape (live, auto)", value=True, key="simp_bigmoney",
                    help="Auto-run a background collector that streams NIFTY / BankNifty / SENSEX "
                         "current-expiry FUTURES ticks and flags big prints & bursts on the tape below. "
                         "Starts with the dashboard; needs a Dhan token. It does NOT save raw ticks — "
                         "the raw archive is owned by the scheduled headless collector, so the two never "
                         "double-write. SENSEX needs the BSE live-data entitlement — 0 ticks without it.")
        with st.expander("🎬 Replay big-money (from raw ticks)", expanded=False):
            from app.big_money_live import available_raw_days
            _rd = available_raw_days(symbol)
            if not _rd:
                st.caption(f"No captured raw ticks for {symbol} yet — run the collector a full session.")
            else:
                st.checkbox(f"Replay {symbol}'s captured tape", value=False, key="bm_replay_on",
                            help="Scrub through a captured day's big-money tape, re-derived tick-by-tick "
                                 "from the raw archive (the source of truth) up to the chosen clock.")
                st.selectbox("Day", _rd[::-1], key="bm_replay_day")
                _times = [f"{m // 60:02d}:{m % 60:02d}" for m in range(9 * 60 + 15, 15 * 60 + 31, 5)]
                st.select_slider("Clock (IST)", options=_times, value=_times[-1], key="bm_replay_clock")
        st.divider()
        st.caption("⚡ Gamma-blast")
        preview_expiry = st.checkbox("👁️ Preview expiry mode", value=False,
                                     help="Force the gamma-blast view on a non-expiry day, to eyeball it.")
        anchor_now = st.button("📍 Anchor blast now", width="stretch",
                               help="Freeze the blast strikes at the current moment instead of "
                                    "waiting for the 13:45 IST auto-anchor.")
        st.checkbox("🔔 Sound alert on ignition", value=True, key="simp_sound",
                    help="Beep + toast the moment the score first hits 45 within ~5 min of a cross "
                         "(likely to reach TRUE) and again on a Fast TRUE. Sound is best-effort — "
                         "the browser may need a click first, and auto-refresh keeps it checking.")
        st.divider()
        with st.expander("🎬 Replay a stored day (offline)", expanded=False):
            from data import replay as rp
            rdays = rp.available_days(symbol)
            if not rdays:
                st.caption("No stored intraday days for this index yet (backfill first).")
            else:
                st.checkbox("Play replay", value=False, key="simp_replay_on",
                            help="Step a real stored day through the whole UI so you can watch the "
                                 "candle, TPO profile, trailing windows and regime develop with the "
                                 "market closed. Turn ON Auto-refresh to make it advance. Isolated journal.")
                # Practice mode: narrow to EXPIRY days and tag each TRUE / Fake gamma-blast (from the
                # blast_ml study) — pick a known-TRUE day to rehearse the run, a Fake day the pin/cap.
                gb_only = st.checkbox("🎯 Expiry days only · tag TRUE / Fake gamma-blast", value=False,
                                      key="simp_replay_gbonly",
                                      help="Filter the day list to expiries and label each by the "
                                           "study outcome — TRUE = a sustaining blast fired that "
                                           "afternoon, Fake = breaks pinned/capped. A reading drill.")
                opts = rdays[::-1]
                if gb_only:
                    opts = [d for d in opts if rp.blast_label(symbol, d)]
                    if not opts:
                        st.caption("No labelled expiry days for this index (build the blast_ml dataset).")

                def _dlabel(d):
                    lab = rp.blast_label(symbol, d)
                    return f"{d} · 🚀 TRUE GB" if lab == "TRUE" else f"{d} · 🪤 Fake GB" if lab == "FAKE" else d
                st.selectbox("Day", opts or rdays[::-1], key="simp_replay_day", format_func=_dlabel)
                st.selectbox("Speed (replay-min per tick)", [1, 5, 10, 20], index=1, key="simp_replay_speed")
                if st.button("↺ Restart from open", width="stretch"):
                    rp.reset(symbol)
                    for k in ("simp_replay_idx", "simp_replay_of", "simp_hl", "simp_data"):
                        st.session_state.pop(k, None)
                    st.rerun()
                _rsel = st.session_state.get("simp_replay_day")
                if _rsel and rp.is_expiry_day(symbol, _rsel):
                    st.caption("🚀 Expiry day — the **gamma-blast verdict** appears in the right column "
                               "once the replay clock passes **13:45**. Use a higher speed to fast-forward.")
                st.caption("Tip: turn on ⏱️ Auto-refresh (top) at 1-min to watch it play.")
        # API budget — several background lanes share one Dhan quota per endpoint; make it VISIBLE
        # rather than let a silent 429 show up as a stale panel (CLAUDE.md: make state visible).
        try:
            from data.dhan_live import rate_report
            _rr = rate_report(60.0)
            _bits = " · ".join(f"{k} {v['calls']}/{v['limit_per_min']:g}" for k, v in _rr.items())
            _tight = any(v["headroom"] < 0.2 for v in _rr.values())
            st.caption(("⚠️ " if _tight else "") + f"API last 60s: {_bits}")
        except Exception:  # noqa: BLE001
            pass
        st.caption("A simple, paper-only face of the GEX engine. Full guide: 📖 Help above "
                   "(same text as docs/simple-dashboard.md).")

    st.markdown(CSS, unsafe_allow_html=True)
    if show_help:                     # help replaces the live view (no fetching while reading)
        _help_page()
        return
    idx = config.get_index(symbol)

    # Auto-start the big-money futures collector (a singleton background thread, once per server via
    # cache_resource). Only when the toggle is on, a live Dhan token is present, and not in demo mode.
    st.session_state["_bm_status"] = None
    if st.session_state.get("simp_bigmoney", True) and not use_demo:
        try:
            from data.dhan import token_status
            if token_status().get("present"):
                st.session_state["_bm_status"] = _get_big_money_collector().status
        except Exception:  # noqa: BLE001 - the tape is convenience, never break the dashboard
            pass

    if symbol != st.session_state.get("simp_symbol"):
        st.session_state["simp_symbol"] = symbol
        st.session_state.pop("simp_data", None)
        st.session_state.pop("simp_hl", None)
        st.session_state.pop("simp_blast", None)

    # The live view runs inside st.fragment so it self-updates IN PLACE on its own timer
    # (run_every) — only this block reruns, no full-page reload/flicker (replaces
    # streamlit-autorefresh). The sidebar/CSS above stay put. Manual Refresh (a full rerun)
    # still works; older Streamlit without fragments falls back to the whole-page autorefresh.
    # POLL every _POLL_S while auto: the fragment wakes often enough to catch each wall-clock
    # boundary and pre-fetch it, but the actual network pull is gated (see _fetch_gate) to fire
    # ONCE per aligned boundary — so cheap repaints are frequent, expensive fetches are on-grid.
    _ivl = _POLL_S if auto else None
    args = (symbol, use_demo, refresh, auto, auto_mins, preview_expiry, anchor_now, idx)
    if hasattr(st, "fragment"):
        st.fragment(run_every=_ivl)(_render_live)(*args)
        return
    if auto:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=auto_mins * 60_000, key="simp_tick")
        except ModuleNotFoundError:
            st.sidebar.warning("Install streamlit-autorefresh for auto mode.")
    _render_live(*args)


def _replay_step(symbol, auto):
    """Advance the offline replay clock one step and rebuild the snapshot from a stored day, so the
    UI develops with time. Writes an isolated replay journal (REPLAY_DATE) the profile/footprint read."""
    from data import replay as rp
    rday = st.session_state.get("simp_replay_day")
    sts = rp.stamps(symbol, rday) if rday else []
    if not sts:
        return
    tag = f"{symbol}:{rday}"
    if st.session_state.get("simp_replay_of") != tag:          # day/symbol changed -> restart clean
        rp.reset(symbol)
        st.session_state["simp_replay_idx"] = min(45, len(sts) - 1)
        st.session_state["simp_replay_of"] = tag
        st.session_state.pop("simp_hl", None)
    idx = int(st.session_state.get("simp_replay_idx", 45))
    if auto:                                                    # advance while auto-refresh is on
        idx = min(idx + int(st.session_state.get("simp_replay_speed", 5)), len(sts) - 1)
    st.session_state["simp_replay_idx"] = idx
    upto = sts[idx]
    rp.ensure_journal(symbol, rday, upto)
    st.session_state["simp_data"] = rp.build_snapshot(symbol, rday, upto)
    st.session_state["simp_asof"] = None


def _render_live(symbol, use_demo, refresh, auto, auto_mins, preview_expiry, anchor_now, idx):
    replay_on = bool(st.session_state.get("simp_replay_on") and st.session_state.get("simp_replay_day"))
    if replay_on:
        _replay_step(symbol, auto)
    elif not use_demo:
        # The background thread does the ~12s pull off the render path and runs whether or not
        # live updates are on — so the render only ever READS the latest published snapshot
        # (instant, no network → no page dim), and 🔄 Refresh is a non-blocking *request* for an
        # off-grid pull rather than a 14s blocking fetch. The toggle controls REPAINTING, not
        # fetching. Only a cold start (nothing published AND no prior data) falls back to one
        # blocking fetch, so the first paint isn't empty.
        from app import live_fetcher
        live_fetcher.ensure(symbol, use_demo, None, int(auto_mins * 60), _FETCH_LEAD_S)
        if refresh:
            live_fetcher.force(symbol)
        pub = live_fetcher.latest(symbol)
        if pub and pub.get("data") is not None:
            st.session_state["simp_data"] = pub["data"]
            st.session_state["simp_asof"] = pub["as_of"]
            st.session_state["simp_err"] = pub.get("err")
        elif st.session_state.get("simp_data") is None:
            _sync_fetch(symbol, use_demo)
    elif refresh or st.session_state.get("simp_data") is None:
        _sync_fetch(symbol, use_demo)
    data = st.session_state.get("simp_data")
    if data is None:
        st.error(f"Could not load data. {st.session_state.get('simp_err','')}")
        st.stop()

    L = data.levels
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    asof = st.session_state.get("simp_asof") or ist
    # feed transparency — which live source produced this snapshot (CLAUDE.md: make state visible)
    _src = data.source or "?"
    _feed = ("🎬 Replay (stored day)" if "replay" in _src
             else "🟢 Dhan (live)" if _src == "dhan"
             else "🟡 NSE (Dhan fallback)" if _src.startswith("nse (Dhan")
             else "🔵 NSE" if _src == "nse" else _src)
    _sched = (f"levels on the {auto_mins:g}-min grid · as of {asof:%H:%M} IST · spot 10s" if auto
              else "⏸ frozen (live updates off) · feed still running")
    st.caption(f"Feed: {_feed} · {_sched} · fetched {ist:%H:%M:%S}")
    if getattr(data, "source_note", None):
        st.caption(f"⚠ {data.source_note}")
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist.date()) if nearest else False
    # Replay dates the snapshot to a synthetic REPLAY_DATE (isolated journal), and its synthetic
    # near-expiry is +2d — so the live "nearest == today" test never fires. Ask instead whether the
    # REAL stored day being replayed is an expiry, so a replayed 0-DTE day shows the blast view.
    if "replay" in (data.source or "").lower():
        from data import replay as rp
        is_expiry = rp.is_expiry_day(symbol, st.session_state.get("simp_replay_day"))
    show_blast = is_expiry or preview_expiry          # preview forces the blast view (dev)
    if anchor_now:
        st.session_state["simp_force_anchor"] = True

    # Self-sufficient capture: persist this pull (timeseries + per-strike OI) BEFORE the
    # range seed / OI windows read the day, so this app feeds its own history without the
    # logger. min_gap dedups rapid refreshes and any co-running writer (shared day file).
    save_gap = max(1.0, auto_mins - 1.0) if auto else 2.0
    if "replay" not in (data.source or "").lower():           # replay writes its own journal
        _persist_snapshot(data, symbol, save_gap)

    # regime mood + expected day range (points). SENSEX borrows NIFTY's |vanna| regime (its own
    # doesn't grade its move — verified; NIFTY's does at 1.73x, corr 0.988); other indices use own.
    from app.intraday_features import (intraday_vol_regime, regime_daily_move_now,
                                       regime_daily_move_proxy, regime_now)
    proxy = getattr(data, "proxy_symbol", None)
    try:
        if proxy and data.proxy_vanna is not None:
            vr = intraday_vol_regime(proxy, data.proxy_vanna, metric="vanna")
            dm = regime_daily_move_proxy(symbol, proxy, data.proxy_vanna)
        else:
            vr = regime_now(symbol, data.vanna_now, data.charm_now, data.dte_now)
            dm = regime_daily_move_now(symbol, data.vanna_now, data.charm_now, data.dte_now)
    except Exception:  # noqa: BLE001
        vr = dm = None
    mood_cls, mood_txt = _MOOD.get(vr["tag"] if vr else "mid", _MOOD["mid"])
    exp_pts = (dm["hl_pct"] / 100.0 * L.spot) if (dm and dm.get("hl_pct") == dm.get("hl_pct")) else None
    tier = {"calm": "Calm (mid)", "wild": "Wild", "mid": "Normal"}.get(vr["tag"], "—") if vr else "—"
    if proxy and data.proxy_vanna is not None:            # transparency: borrowed regime
        tier += f" · via {config.INDICES[proxy].label}"

    # today's range so far — SEED from today's saved snapshots (so a mid-day open or a
    # restart recovers the whole session, not just spots seen since this process started),
    # then keep extending with the live spot. Still snapshot-cadence — see caveat.
    date_ist = ist.strftime("%Y-%m-%d")
    # FAST SPOT LANE: the chain costs ~12-15s, but the index quote is one ~0.5s call, so the candle
    # tracks price on its own ~10s beat. Levels stay as of the chain pull (the header says when) —
    # this moves the price, not the map.
    tick = None
    _src_l = str(data.source or "").lower()          # Dhan-only lane; replay/demo keep chain spot
    if _is_live(data) and _src_l.startswith("dhan") and "replay" not in _src_l:
        try:
            from app import spot_ticker
            spot_ticker.ensure(symbol, _SPOT_TICK_S)
            tick = spot_ticker.latest(symbol)
        except Exception:  # noqa: BLE001
            tick = None
    spot_live = float(tick["ltp"]) if (tick and tick.get("ltp")) else float(L.spot)
    spot_now = spot_live
    hl = st.session_state.get("simp_hl")
    if hl is None or hl.get("day") != date_ist:
        from app.timeseries import oistrikes_dir_for
        hl = _seed_hl(symbol, date_ist, spot_live, oistrikes_dir_for(data.source))
        st.session_state["simp_hl"] = hl
    hl["lo"] = min(hl["lo"], spot_live); hl["hi"] = max(hl["hi"], spot_live)

    vix = data.india_vix or {}
    if vix.get("last") is not None:
        _vp = vix.get("pct_change")
        # VIX up = fear (red), down = calm (green)
        _vc = ("" if _vp is None else
               f' <span style="color:{"var(--stop)" if _vp >= 0 else "var(--go)"}">{_vp:+.1f}%</span>')
        vtxt = f'India VIX <b>{vix["last"]:.2f}</b>{_vc}'
    else:
        vtxt = ""

    # ── render ──
    H = ['<div class="simp">']
    H.append('<div class="eyebrow" style="color:var(--spark)">'
             f'{idx.label} · Gamma-Blast</div>'
             f'<div class="mono" style="font-size:.8rem;color:var(--ink-mute)">{ist:%a %d %b · %H:%M IST}'
             f'{" · ⚡ EXPIRY DAY" if is_expiry else (" · 👁️ preview" if preview_expiry else "")}</div>')

    # gauge
    rng_line = (f'<span class="tier">Vanna tier: {tier}</span><br>Expected day range '
                f'<b class="mono">≈ {f(exp_pts)} pts</b>' if exp_pts else
                '<span class="tier">Expected range</span><br>building…')
    H.append(f'''<div class="gauge card"><div class="spot"><div class="eyebrow">Spot</div>
      <div class="val mono">{f(spot_now)}</div><div class="chg mono">{vtxt}</div></div>
      <div class="mood"><span class="chip {mood_cls}"><span class="dot"></span>{mood_txt}</span>
      <div class="range">{rng_line}</div></div></div>''')

    # live futures big-money tape (separate feed) — sits up top now; or scrub a captured day (replay)
    _bm_replay = None
    if st.session_state.get("bm_replay_on") and st.session_state.get("bm_replay_day"):
        from app.big_money import IST as _IST
        _rday, _clock = st.session_state["bm_replay_day"], st.session_state.get("bm_replay_clock", "15:30")
        try:
            _upto = int(datetime.strptime(f"{_rday} {_clock}", "%Y-%m-%d %H:%M")
                        .replace(tzinfo=_IST).timestamp())
            _bm_replay = {"day": _rday, "clock": _clock, "upto_epoch": _upto}
        except (ValueError, TypeError):
            _bm_replay = None
    H.append(_big_money_tape_html(ist, symbol, replay=_bm_replay))

    # today's range so far — candle + developing TPO profile + prior/5D value + GEX ladder
    oh = _resolve_ohlc(data, hl, spot_live, day_ohlc=tick)
    actual = oh["h"] - oh["l"]
    spot_pos = ((spot_live - oh["l"]) / actual * 100.0) if actual > 0 else 50.0
    show_weekly = bool(st.session_state.get("simp_weekly"))
    prof = _profile_overlay(symbol, data.source, date_ist, spot_live, show_weekly)
    candle = _range_candle(L, oh, prof, show_weekly, spot_live=spot_live)
    read = f'{f(actual)} pts · spot {spot_pos:.0f}% up range'
    # reflects what was actually USED — a stale/unverifiable feed OHLC is rejected in _resolve_ohlc
    src_note = ("true day OHLC (feed)" if oh.get("true_ohlc")
                else "≈ from snapshots — can miss fast spikes")
    # regime chip: developing distribution shape × the vanna tier (a map, not a signal)
    mp_chip = _mp_regime_chip(prof, vr)
    mp_legend = ('<div class="mp-legend"><span><i style="border-color:var(--spark)"></i>today '
                 'VAH/POC/VAL</span><span><i style="border-color:var(--ink-mute);border-top-style:dashed"></i>'
                 'prior day (p·)</span>' + ('<span><i style="border-color:var(--warn);border-top-style:dotted"></i>'
                 '5-day (5D·)</span>' if show_weekly else '')
                 + '<span>▲▼ = outside today’s range</span></div>') if (prof and prof.get("today")) else ''

    # news override
    news = data.news or {}
    if news.get("impact", 0) and news["impact"] >= 5:
        top = (news.get("top") or "").strip()
        H.append(f'<div class="news"><span>📰</span><div class="txt"><b>Big news day.</b> '
                 f'A headline is driving the market — trust the news over these levels today.'
                 f'{" <i>“"+top[:90]+"”</i>" if top else ""}</div></div>')

    # ── 2-column deck: LEFT = the tall live candle vs levels, RIGHT = positioning + the play ──
    H.append('<div class="grid2"><div class="col">')
    H.append(f'''<div class="candlecard card"><div class="rb-top"><span class="eyebrow">Value profile · candle · levels</span>
      <span class="rb-vals mono">{f(oh["l"])} – {f(oh["h"])}</span></div>{candle}{mp_legend}
      <div class="cc-read">{read}</div>{mp_chip}
      <span class="rb-cav">{src_note}. Left = where price ACCEPTED (a map); right = dealer levels. Stops <i>just beyond</i> a wall — never at the magnet.</span></div>''')

    # Right column. On a BLAST day the verdict sits at the TOP, level with the candle: the cross
    # is read against the candle (did it break the level, is it holding?), so the two belong
    # side by side rather than needing a scroll between them. Positioning follows.
    # On a quiet day the play card is not time-critical, so positioning leads and it sits beneath.
    H.append('</div><div class="col">')
    if show_blast:
        H.append('<h2 class="lbl">Suggested play</h2>')
        H.append(_blast_hero(data, L, ist, spot_live, prof))
    H.append('<h2 class="lbl">Where the money is building</h2>')
    H.append(_footprint_html(data, L, idx, ist, is_expiry))
    H.append(_futures_html(data, symbol, ist, is_expiry))
    if not show_blast:
        H.append('<h2 class="lbl">Suggested play</h2>')
        H.append(_play_card(vr, data, L, idx))
    H.append('</div></div>')                          # close right col + grid

    # sector rotation snap — what's carrying / dragging the index today (context, not a signal)
    H.append(_sector_strip(data))

    H.append(f'<div class="foot">A map and a logbook, not an oracle. <b>Paper trading only</b> — '
             f'you confirm every trade.<br>New here? Tick <b>📖 Help</b> in the sidebar — it explains '
             f'every panel and when price tends to respect a level vs break it.'
             f'<br><span class="mono" style="opacity:.7">lot {idx.lot_size} · '
             f'source {data.source} · {ist:%H:%M IST}</span></div>')
    H.append('</div>')
    # Drop empty fragments: a blank line between HTML blocks makes Streamlit's markdown treat the
    # (indented) block that follows as a code fence — the bug that once showed raw footprint HTML.
    st.markdown("\n".join(x for x in H if x.strip()), unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _get_big_money_collector():
    """The one background big-money collector for this server (started once; shared across reruns and
    sessions by cache_resource). Streams the futures feed and filters big prints/bursts into the event
    DB for the live tape. It does NOT save raw ticks — the raw archive is OWNED by the scheduled
    headless collector (run_big_money_collector.py), so the two never double-write the raw CSV."""
    from app.big_money_live import Collector
    return Collector(save_raw=False).start()


def _bm_status_html(status, day: str) -> str:
    """One-line collector health strip: connection, freshness, counts, and PER-SYMBOL tick counts so a
    silent book (e.g. SENSEX without the BSE feed entitlement) is visible, not hidden."""
    if status is None:
        return ('<span class="rb-cav">Auto-collector off or no Dhan token — tick the sidebar '
                '<b>🐋 Big-money tape</b> and set a token to stream + archive ticks.</span>')
    import time as _t
    if status.error and not status.connected:
        dot = f'<span style="color:var(--stop)">● {status.error}</span>'
    elif status.connected:
        dot = '<span style="color:var(--go)">● live</span>'
    else:
        dot = '<span style="color:var(--warn)">● connecting…</span>'
    age = ""
    if status.last_tick_epoch:
        secs = max(0, int(_t.time()) - int(status.last_tick_epoch))
        age = f" · last tick {secs}s ago" if secs < 3600 else ""
    per = "  ".join(
        f'{s} {"<b>0</b> ⚠️" if n == 0 else n}' for s, n in (status.per_symbol or {}).items())
    raw = f" · {status.n_raw:,} raw ticks saved" if status.save_raw else ""
    return (f'<span class="rb-cav">{dot}{age} · {status.n_events} events{raw} · ticks: {per}</span>')


def _exp_label(expiry) -> str:
    """Short contract label from an event's expiry: month abbrev ('Jul') for a print, 'agg' for a
    symbol-aggregate burst, '' if unknown."""
    if not expiry:
        return ""
    if expiry == "AGG":
        return "agg"
    try:
        from datetime import datetime as _dt
        return _dt.strptime(str(expiry)[:10], "%Y-%m-%d").strftime("%b")
    except (ValueError, TypeError):
        return ""


def _oi_tag(r) -> str:
    """A compact ΔOI build-up tag for a tape row: for a print, the four-quadrant read (side + ΔOI);
    else a plain OI arrow. '' if no ΔOI on the event (pre-ΔOI-capture rows)."""
    doi = r.get("doi")
    if doi is None or doi != doi:
        return ""
    if r.get("kind") == "print" and r.get("side") in ("buy", "sell"):
        from app.big_money import buildup
        q = buildup(1 if r["side"] == "buy" else -1, doi)
        ab = {"long build-up": ("L+", "var(--go)"), "short build-up": ("S+", "var(--stop)"),
              "short covering": ("S⤴", "var(--go)"), "long unwinding": ("L⤵", "var(--stop)")}.get(q or "")
        if ab:
            return f'<span title="{q}" style="color:{ab[1]};font-weight:700">{ab[0]}</span>'
    a = "▲" if doi > 0 else "▼" if doi < 0 else "·"
    # burst: show conviction (ΔOI/Δvol over the positioning window) — positioning vs churn
    cv = r.get("conv")
    if cv is not None and cv == cv and r.get("kind") == "burst":
        col = "var(--go)" if cv > 0.15 else "var(--stop)" if cv < -0.15 else "var(--ink-mute)"
        return f'<span title="ΔOI {doi:+,.0f} · conviction {cv:+.2f}" style="color:{col}">{a}{abs(cv):.1f}</span>'
    return f'<span title="ΔOI {doi:+,.0f}" style="color:var(--ink-mute)">{a}OI</span>'


def _bm_rollover_html() -> str:
    """Per-symbol rollover gauge from `futures_state`: next-month OI share (% rolled) + near/next prices.
    Context on where the roll stands — descriptive, not a signal. Empty until the collector writes state."""
    try:
        from app.big_money_db import rollover
        rows = rollover()
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""

    def _px(d):
        p = (d or {}).get("price")
        return f"{p:,.0f}" if isinstance(p, (int, float)) else "—"
    lines = []
    for r in rows:
        near, nxt = r["near"], r.get("next")
        xlab = _exp_label(nxt.get("expiry")) if nxt else "—"
        lines.append(
            f'<div style="display:flex;justify-content:space-between;gap:10px;padding:1px 0">'
            f'<span style="font-weight:700">{r["symbol"]}</span>'
            f'<span class="mono" style="color:var(--ink-2)">rolled <b>{r["pct_rolled"]:.0f}%</b> → {xlab}</span>'
            f'<span class="mono" style="color:var(--ink-mute)">near {_exp_label(near.get("expiry"))} '
            f'{_px(near)} · next {xlab} {_px(nxt)}</span></div>')
    return ('<div style="margin:2px 0 8px;padding:6px 10px;border:1px dashed var(--line);border-radius:8px">'
            '<div class="eyebrow" style="margin-bottom:2px">Rollover · next-month OI share</div>'
            + "".join(lines) + '</div>')


def _bm_flow_html(day: str) -> str:
    """Today's big-money FLOW BIAS — per symbol, buy- vs sell-initiated ₹ (Lee-Ready) across today's big
    prints/bursts, with a net lean. This is the closest honest thing to a 'which way is big money
    leaning' read, and it is DESCRIPTIVE order flow, NOT a validated forecast (accuracy improves as we
    gather data + study it). Empty until events exist. Sign-free; decision-support; you confirm."""
    try:
        from app.big_money_db import daily_rollup
        roll = daily_rollup(days=[day])
    except Exception:  # noqa: BLE001
        return ""
    roll = [r for r in roll if (r["buy_notional"] + r["sell_notional"]) > 0]
    if not roll:
        return ""
    pill = {"BUY": ("var(--go)", "▲ BUY-leaning"), "SELL": ("var(--stop)", "▼ SELL-leaning"),
            "flat": ("var(--ink-mute)", "• balanced")}
    lines = []
    for r in roll:
        col, lab = pill[r["lean"]]
        lines.append(
            f'<div style="display:flex;justify-content:space-between;gap:10px;padding:2px 0">'
            f'<span style="font-weight:700">{r["symbol"]}</span>'
            f'<span class="mono" style="color:var(--ink-2)">buy ₹{r["buy_notional"]/1e7:.1f}cr · '
            f'sell ₹{r["sell_notional"]/1e7:.1f}cr</span>'
            f'<span class="mono">net <b>{r["net_notional"]/1e7:+.1f}cr</b></span>'
            f'<span style="color:{col};font-weight:700">{lab} ({abs(r["net_pct"]):.0f}%)</span></div>')
    return (
        '<div style="margin:2px 0 8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px">'
        '<div class="eyebrow" style="margin-bottom:4px">Big-money flow bias · today</div>'
        + "".join(lines) +
        '<span class="rb-cav">Buy- vs sell-initiated ₹ (Lee-Ready) across today\'s big prints/bursts — a '
        'DESCRIPTIVE order-flow lean, <b>not a validated forecast</b> (we sharpen it as data + study '
        'accrue). De-size on conflict with your thesis; you confirm.</span></div>')


def _bm_day_readout_html(a) -> str:
    """Compact 'day so far' analytics for one symbol from an analyse() dict — the aggregate the standalone
    analysis page shows, folded into the tape: counts, day OI trend, build-up split, conviction, heaviest
    prices. Descriptive; a map, not a signal."""
    if not a:
        return ""
    oi, pos, conv = a.get("oi_day"), a.get("positioning") or {}, a.get("conviction")
    t = [f'events <b>{a["n_events"]}</b> ({a["n_print"]}P/{a["n_burst"]}B)']
    if a.get("sum_cr") is not None:
        t.append(f'prints <b>₹{a["sum_cr"]}cr</b>')
    s = a.get("sides") or {}
    if s:
        t.append(f'buy/sell <b>{s.get("buy", 0)}/{s.get("sell", 0)}</b>')
    if oi:
        ch = oi["change"]
        t.append(f'day OI <b style="color:{"var(--go)" if ch > 0 else "var(--stop)"}">'
                 f'{"▲" if ch > 0 else "▼"}{abs(ch):,.0f}</b>')
    if conv:
        t.append(f'conviction <b style="color:{"var(--go)" if conv["net"] > 0 else "var(--stop)"}">'
                 f'{conv["net"]:+.2f}</b>·{conv["posn_share"]:.0f}% posn')
    bhtml = ""
    if pos:
        segs = [f'<span style="color:{"var(--go)" if ("long build" in k or "covering" in k) else "var(--stop)"}">'
                f'{k} ₹{v:.0f}cr</span>' for k, v in pos.items()]
        bhtml = '<div style="margin-top:4px;font-size:.82rem">build-up: ' + ' · '.join(segs) + '</div>'
    vp = sorted(a.get("value_profile") or [], key=lambda r: -r["total"])[:3]
    vhtml = ('<div style="margin-top:2px;font-size:.82rem;color:var(--ink-2)">heaviest prices: '
             + ' · '.join(f'{v["price"]:,.0f} ₹{v["total"]:.0f}cr' for v in vp) + '</div>') if vp else ''
    return ('<div style="margin:2px 0 8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px">'
            f'<div class="eyebrow" style="margin-bottom:4px">Day so far · {a["symbol"]}</div>'
            f'<div class="mono" style="font-size:.84rem">{" &nbsp;·&nbsp; ".join(t)}</div>{bhtml}{vhtml}</div>')


# Shared horizontal time axis for ALL THREE big-money charts (build-up grid, prints, bursts) so their
# timelines line up pixel-for-pixel when stacked: same viewBox width + plot margins + minute→x mapping.
_BM_OPEN, _BM_CLOSE = 9 * 60 + 15, 15 * 60 + 30           # NSE session (minutes of day)
_BM_W, _BM_L, _BM_R = 720, 40, 10                         # viewBox width; left/right plot margins


def _bm_x(m: float) -> float:
    """Minute-of-day → x on the shared big-money time axis (identical across the 3 charts)."""
    return _BM_L + (m - _BM_OPEN) / (_BM_CLOSE - _BM_OPEN) * (_BM_W - _BM_L - _BM_R)


def _bm_buildup_grid_html(a, key: str = "time", label: str = "15-min", step_min: int = 15) -> str:
    """A positioning heatmap on a FIXED session grid (9:15→15:30, one block per ``step_min``). TWO stacked
    rows, same colour (green = price up, red = down) and same time axis, differing only in what opacity means:
    row 1 = OI BUILD-UP (opacity = size of ΔOI — how much positioned); row 2 = CONVICTION (opacity = ΔOI/ΔVol,
    the ₹-weighted mean of that slot's bursts — how *sticky* the flow was). Dark in both = large & sticky;
    dark top / faint bottom = churn. Future blocks sit empty and the live block recolours as ticks arrive.
    Hover a block for detail. No price line — price lives in the hover. Descriptive; a map, not a signal."""
    t = (a or {}).get(key)
    if not t:
        return ""
    from app.oi_footprint import classify_buildup

    def _m(hhmm: str) -> int:
        h, mm = hhmm[:5].split(":")
        return int(h) * 60 + int(mm)

    b: dict[int, dict] = {}                                  # bucket keyed by start-minute of the day
    prev_oi = prev_px = None
    for x in t:
        px, oi = x.get("px"), x.get("oi")
        doi = (oi - prev_oi) if (oi is not None and prev_oi is not None) else None
        dpx = (px - prev_px) if (px is not None and prev_px is not None) else None
        b[_m(x["t"])] = {"t": x["t"][:5], "px": px, "doi": doi, "dpx": dpx,
                         "q": classify_buildup(doi, dpx) if (doi is not None and dpx is not None) else None}
        if oi is not None:
            prev_oi = oi
        if px is not None:
            prev_px = px
    if not any(v["doi"] is not None for v in b.values()):
        return ""
    maxdoi = max((abs(v["doi"]) for v in b.values() if v["doi"] is not None), default=1.0) or 1.0
    slots = list(range(_BM_OPEN, _BM_CLOSE, step_min))       # fixed 9:15→15:30 grid on the shared time axis

    # Row 2 data: per-slot CONVICTION = ₹-weighted mean ΔOI/ΔVol of the bursts that fell in each slot.
    agg: dict[int, list[float]] = {}
    for z in ((a or {}).get("bursts") or []):
        if z.get("conv") is None:
            continue
        sl = int((z["min"] - _BM_OPEN) // step_min) * step_min + _BM_OPEN
        acc = agg.setdefault(sl, [0.0, 0.0])
        acc[0] += z["conv"] * z["cr"]
        acc[1] += z["cr"]
    conv_by_slot = {k: (v[0] / v[1]) for k, v in agg.items() if v[1]}
    two_row = bool(conv_by_slot)

    top, h1, gap, h2 = 8, 66, 10, 44                         # build-up band / gap / conviction band
    y2 = top + h1 + gap
    labY = (y2 + h2 if two_row else top + h1) + 14
    H = labY + 4

    s = [f'<svg viewBox="0 0 {_BM_W} {H}" width="100%" style="display:block" preserveAspectRatio="none" '
         f'role="img" aria-label="{label} OI build-up and conviction on a fixed session grid">']
    for mm in slots:                                         # ROW 1 — build-up (opacity = |ΔOI|, the size)
        x0, x1 = _bm_x(mm), _bm_x(mm + step_min)             # edges pinned to the shared time axis
        v = b.get(mm)
        lab = f'{mm // 60:02d}:{mm % 60:02d}'
        if v and v["doi"] is not None and v["dpx"] is not None:
            fill = "var(--go)" if v["dpx"] > 0 else "var(--stop)" if v["dpx"] < 0 else "var(--ink-mute)"
            op = 0.16 + 0.80 * min(1.0, abs(v["doi"]) / maxdoi)
            tip = f'{lab}  {v["q"] or "flat"}  ΔOI {v["doi"]:+,.0f}  px {v["px"]:,.0f}'
        else:
            fill, op, tip = "var(--ink-mute)", 0.05, lab
        s.append(f'<rect x="{x0 + 0.6:.1f}" y="{top}" width="{x1 - x0 - 1.2:.1f}" height="{h1}" rx="1.5" '
                 f'style="fill:{fill};opacity:{op:.2f}"><title>{tip}</title></rect>')
    if two_row:
        for mm in slots:                                     # ROW 2 — conviction (opacity = |ΔOI/ΔVol|, sticky)
            x0, x1 = _bm_x(mm), _bm_x(mm + step_min)
            v, cv = b.get(mm), conv_by_slot.get(mm)
            lab = f'{mm // 60:02d}:{mm % 60:02d}'
            if cv is not None:
                dpx = (v or {}).get("dpx") or 0
                fill = "var(--go)" if dpx > 0 else "var(--stop)" if dpx < 0 else "var(--ink-mute)"
                op = 0.16 + 0.80 * min(1.0, abs(cv))
                tip = f'{lab}  conviction {cv:+.2f} ({"sticky" if abs(cv) >= 0.25 else "churn"})'
            else:
                fill, op, tip = "var(--ink-mute)", 0.05, lab
            s.append(f'<rect x="{x0 + 0.6:.1f}" y="{y2}" width="{x1 - x0 - 1.2:.1f}" height="{h2}" rx="1.5" '
                     f'style="fill:{fill};opacity:{op:.2f}"><title>{tip}</title></rect>')
        s.append(f'<text x="3" y="{top + h1 / 2 + 3:.0f}" style="font-size:8px;fill:var(--ink-mute)">size</text>')
        s.append(f'<text x="3" y="{y2 + h2 / 2 + 3:.0f}" style="font-size:8px;fill:var(--ink-mute)">grip</text>')
    for mm in slots:                                         # shared time ticks (45-min → align w/ scatters)
        if (mm - _BM_OPEN) % 45 == 0:
            s.append(f'<text x="{_bm_x(mm):.1f}" y="{labY:.0f}" text-anchor="middle" '
                     f'style="font-size:8.5px;fill:var(--ink-mute)">{mm // 60:02d}:{mm % 60:02d}</text>')
    s.append("</svg>")
    legend = (f'<span class="rb-cav">{label} grid, fixed 9:15→15:30 · colour = price up (green) / down (red) · '
              + ('<b>top (size)</b> opacity = ΔOI, how much positioned · <b>bottom (grip)</b> opacity = '
                 'conviction ΔOI/ΔVol, how sticky · dark in both = large &amp; sticky; dark top / faint bottom '
                 '= churn · ' if two_row else 'opacity = size of ΔOI (positioning) · ')
              + 'live block recolours as ticks arrive · hover for detail.</span>')
    return ('<div style="margin:6px 0 8px;padding:8px 10px 4px;border:1px solid var(--line);border-radius:8px">'
            f'<div class="eyebrow" style="margin-bottom:4px">Build-up × time × price · {a["symbol"]} · {label}</div>'
            + "".join(s) + legend + '</div>')


def _bm_print_scatter_html(a) -> str:
    """PRINTS-ONLY graph: every big single-trade PRINT as a dot at its (time, price), coloured by
    aggressor (green buy / red sell / grey mid) and sized by ₹ notional, with the futures price line for
    context. Bursts are excluded — this is the actual block trades that hit the tape. Map, not a signal."""
    pr = (a or {}).get("prints")
    if not pr:
        return ""
    import math
    OPEN, CLOSE = _BM_OPEN, _BM_CLOSE
    prices = [p["price"] for p in pr]
    pl = [x["px"] for x in (a.get("time") or []) if x.get("px") is not None]
    pmin, pmax = min(prices + pl), max(prices + pl)
    span = (pmax - pmin) or 1.0
    maxcr = max((p["cr"] for p in pr), default=1.0) or 1.0
    W, top, plotH = _BM_W, 10, 120
    X = _bm_x

    def Y(px):
        return top + (pmax - px) / span * plotH

    col = {"buy": "var(--go)", "sell": "var(--stop)", None: "var(--ink-mute)"}
    s = [f'<svg viewBox="0 0 {W} {top + plotH + 22}" width="100%" style="display:block" '
         f'role="img" aria-label="big prints by time and price">']
    bpts = " ".join(f'{X(_secs(x["t"]) / 60):.1f},{Y(x["px"]):.1f}'
                    for x in (a.get("time") or []) if x.get("px") is not None)
    if bpts:
        s.append(f'<polyline points="{bpts}" fill="none" stroke="var(--ink)" stroke-width="1.2" opacity="0.4"/>')
    for p in sorted(pr, key=lambda z: -z["cr"]):              # big dots first so small sit on top
        r = 2.2 + 5.5 * math.sqrt(p["cr"] / maxcr)
        s.append(f'<circle cx="{X(p["min"]):.1f}" cy="{Y(p["price"]):.1f}" r="{r:.1f}" '
                 f'style="fill:{col[p["side"]]};opacity:0.6"><title>{p["t"]}  '
                 f'{(p["side"] or "mid").upper()}  ₹{p["cr"]:.2f}cr  @{p["price"]:,.0f}</title></circle>')
    for px in (pmax, pmin):
        s.append(f'<text x="2" y="{Y(px) + 3:.0f}" style="font-size:9px;fill:var(--ink-mute)">{px:,.0f}</text>')
    for m in range(OPEN, CLOSE + 1, 45):
        s.append(f'<text x="{X(m):.1f}" y="{top + plotH + 16:.0f}" text-anchor="middle" '
                 f'style="font-size:8.5px;fill:var(--ink-mute)">{m // 60:02d}:{m % 60:02d}</text>')
    s.append("</svg>")
    legend = ('<span class="rb-cav">Every big PRINT (single block trade ≥ floor) at its price &amp; time · '
              '<span style="color:var(--go)">buy</span> / <span style="color:var(--stop)">sell</span> '
              '(aggressor) · dot size = ₹ · faint line = price. Bursts excluded.</span>')
    return ('<div style="margin:6px 0 8px;padding:8px 10px 4px;border:1px solid var(--line);border-radius:8px">'
            f'<div class="eyebrow" style="margin-bottom:4px">Big prints · time × price · {a["symbol"]}</div>'
            + "".join(s) + legend + '</div>')


def _bm_burst_scatter_html(a) -> str:
    """BURSTS-ONLY graph: every burst as a dot at its (time, price), coloured by CONVICTION (green =
    positioning-building, red = unwinding, grey = churn — bursts have no aggressor side), sized by ₹
    notional, with the futures price line for context. Prints excluded. Map, not a signal."""
    bs = (a or {}).get("bursts")
    if not bs:
        return ""
    import math
    OPEN, CLOSE = _BM_OPEN, _BM_CLOSE
    prices = [b["price"] for b in bs]
    pl = [x["px"] for x in (a.get("time") or []) if x.get("px") is not None]
    pmin, pmax = min(prices + pl), max(prices + pl)
    span = (pmax - pmin) or 1.0
    maxcr = max((b["cr"] for b in bs), default=1.0) or 1.0
    W, top, plotH = _BM_W, 10, 120
    X = _bm_x

    def Y(px):
        return top + (pmax - px) / span * plotH

    def cfill(c):
        if c is None:
            return "var(--ink-mute)"
        return "var(--go)" if c > 0.15 else "var(--stop)" if c < -0.15 else "var(--ink-mute)"

    s = [f'<svg viewBox="0 0 {W} {top + plotH + 22}" width="100%" style="display:block" '
         f'role="img" aria-label="bursts by time and price">']
    bpts = " ".join(f'{X(_secs(x["t"]) / 60):.1f},{Y(x["px"]):.1f}'
                    for x in (a.get("time") or []) if x.get("px") is not None)
    if bpts:
        s.append(f'<polyline points="{bpts}" fill="none" stroke="var(--ink)" stroke-width="1.2" opacity="0.4"/>')
    for b in sorted(bs, key=lambda z: -z["cr"]):
        r = 2.0 + 5.0 * math.sqrt(b["cr"] / maxcr)
        cv = f'{b["conv"]:+.2f}' if b["conv"] is not None else '—'
        s.append(f'<circle cx="{X(b["min"]):.1f}" cy="{Y(b["price"]):.1f}" r="{r:.1f}" '
                 f'style="fill:{cfill(b["conv"])};opacity:0.5"><title>{b["t"]}  ₹{b["cr"]:.2f}cr  '
                 f'@{b["price"]:,.0f}  conv {cv}</title></circle>')
    for px in (pmax, pmin):
        s.append(f'<text x="2" y="{Y(px) + 3:.0f}" style="font-size:9px;fill:var(--ink-mute)">{px:,.0f}</text>')
    for m in range(OPEN, CLOSE + 1, 45):
        s.append(f'<text x="{X(m):.1f}" y="{top + plotH + 16:.0f}" text-anchor="middle" '
                 f'style="font-size:8.5px;fill:var(--ink-mute)">{m // 60:02d}:{m % 60:02d}</text>')
    s.append("</svg>")
    legend = ('<span class="rb-cav">Every BURST (sliced flow ≥ floor) at its price &amp; time · '
              '<span style="color:var(--go)">building</span> / <span style="color:var(--stop)">unwinding</span>'
              ' / grey = churn (conviction) · dot size = ₹ · faint line = price. Prints excluded.</span>')
    return ('<div style="margin:6px 0 8px;padding:8px 10px 4px;border:1px solid var(--line);border-radius:8px">'
            f'<div class="eyebrow" style="margin-bottom:4px">Bursts · time × price · {a["symbol"]}</div>'
            + "".join(s) + legend + '</div>')


def _big_money_tape_html(ist, symbol="NIFTY", replay=None, limit: int = 20) -> str:
    """Full-width 'big-money tape' card. LIVE: tails the event DB the collector writes (all symbols),
    plus a 'day so far' readout for the selected symbol. REPLAY: re-derives the selected symbol's events
    from its raw archive up to the replay clock (raw = source of truth) and shows the tape + readout as
    of that moment. Honest: large PRINTS / BURSTS (inference, not orders); direction = aggressor side.
    Decision-support; a readout, never a trade call."""
    try:
        from app.big_money import load_events
        from app.big_money_analysis import analyse, analyse_frame
        import pandas as pd
    except Exception:  # noqa: BLE001
        return ""
    status = st.session_state.get("_bm_status")
    if replay:
        from dataclasses import asdict
        from app.big_money_live import rederive_events
        day = replay["day"]
        allrows = [asdict(e) for e in rederive_events(symbol, day, upto_epoch=replay["upto_epoch"])]
        rows = sorted(allrows, key=lambda r: r.get("epoch", 0), reverse=True)[:limit]
        readout = analyse_frame(pd.DataFrame(allrows), symbol, day) if allrows else None
        sub = f'🎬 REPLAY {day} @ {replay["clock"]} · {symbol} (from raw)'
    else:
        day = ist.strftime("%Y-%m-%d")
        rows = []
        for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
            try:
                rows.extend(load_events(sym, day))
            except Exception:  # noqa: BLE001
                pass
        rows.sort(key=lambda r: r.get("epoch", 0), reverse=True)
        rows = rows[:limit]
        try:
            readout = analyse(day, symbol)
        except Exception:  # noqa: BLE001
            readout = None
        sub = 'NIFTY · BankNifty · SENSEX · current expiry · live'

    head = ('<div class="card" style="margin-top:14px"><div class="rb-top">'
            '<span class="eyebrow">🐋 Big-money futures tape</span>'
            f'<span class="rb-vals mono">{sub}</span></div>')
    if not rows:
        note = ('No big-money events in this replay window yet — scrub later.' if replay else
                'No big-money events yet today — big prints & bursts appear here as they fire during '
                'market hours.')
        extra = _bm_day_readout_html(readout) if replay else (_bm_rollover_html() + _bm_status_html(status, day))
        return (head + extra + f'<div style="padding:10px 4px;color:var(--ink-2);font-size:.86rem">{note}'
                '</div></div>')

    side_badge = {"buy": '<span style="color:var(--go);font-weight:700">▲ BUY</span>',
                  "sell": '<span style="color:var(--stop);font-weight:700">▼ SELL</span>',
                  None: '<span style="color:var(--ink-mute)">•</span>'}
    tr = []
    for r in rows:
        cr = (r.get("notional") or 0) / 1e7
        lots = r.get("lots")
        lots_s = f"{lots:.0f}" if lots is not None else "?"
        kind = (r.get("kind") or "").upper()
        tr.append(
            f'<tr><td class="mono" style="color:var(--ink-2)">{r.get("ts_ist","")[11:]}</td>'
            f'<td style="font-weight:700">{r.get("symbol","")} '
            f'<span class="mono" style="font-size:.68rem;color:var(--ink-mute)">{_exp_label(r.get("expiry"))}</span></td>'
            f'<td>{side_badge.get(r.get("side"))}</td>'
            f'<td style="color:var(--ink-mute);font-size:.78rem">{kind}</td>'
            f'<td class="mono" style="text-align:right">{lots_s}L</td>'
            f'<td class="mono" style="text-align:right;font-weight:700">₹{cr:.2f}cr</td>'
            f'<td class="mono" style="text-align:right;color:var(--ink-2)">@{r.get("price",0):.1f}</td>'
            f'<td class="mono" style="text-align:center;font-size:.74rem">{_oi_tag(r)}</td></tr>')
    table = ('<table style="width:100%;border-collapse:collapse;font-size:.9rem;margin-top:6px">'
             '<tbody>' + "".join(tr) + '</tbody></table>')
    cav = ('<span class="rb-cav">Large prints & bursts on the aggregated feed — an <i>inference</i> of '
           'big activity, not orders; side = aggressor (Lee-Ready), a flow read not a prediction. '
           'Exchange time. Decision-support.</span>')
    grids = (_bm_buildup_grid_html(readout, "time", "15-min")
             + _bm_print_scatter_html(readout) + _bm_burst_scatter_html(readout))
    if replay:
        return head + _bm_day_readout_html(readout) + grids + table + cav + '</div>'
    return (head + _bm_flow_html(day) + _bm_rollover_html() + _bm_day_readout_html(readout)
            + grids + table + _bm_status_html(status, day) + cav + '</div>')


_BLAST_TICK_S = 10          # spot-lane resolution for timing the crossover


def _secs(t):
    """'HH:MM' or 'HH:MM:SS' -> seconds since midnight (the blast now mixes both resolutions)."""
    try:
        parts = [int(x) for x in str(t).split(":")]
    except (ValueError, AttributeError):
        return None
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _blast_hero(data, L, ist, spot_live=None, prof=None):
    """Expiry-day gamma-blast verdict, reusing app.gammablast with session-accumulated series.

    Two resolutions, deliberately: the CROSS is timed off the ~10s spot lane (parity says spot
    crossing K is the same event as the premium crossover), while the SCORE keeps the option-chain
    cadence because it needs writer OI + premiums. So ignition timing is ~10s-accurate even though
    the fuel reading is 1-5 min old."""
    try:
        from app.gammablast import (blast_score, crossover_state, detect_crossovers,
                                    detect_crossovers_spot, next_strike, pick_anchors, strike_oi,
                                    strike_premiums)
    except Exception:  # noqa: BLE001
        return '<div class="card hero"><div class="eyebrow">Gamma-blast</div><p class="instruct">Blast engine unavailable.</p></div>'
    if not _is_live(data) or not data.oi_strikes:
        return ('<div class="card hero"><span class="eyebrow">Gamma-blast</span>'
                '<p class="instruct">Live feed only — per-strike premiums aren\'t on demo data.</p></div>')

    ist_today = ist.strftime("%Y-%m-%d")
    anchor_min = 13 * 60 + 45
    ist_min = ist.hour * 60 + ist.minute
    bl = st.session_state.get("simp_blast")
    if not bl or bl.get("date") != ist_today:
        bl = {"date": ist_today, "anchors": None, "points": {}}
        st.session_state["simp_blast"] = bl
    force = st.session_state.pop("simp_force_anchor", False)   # manual "Anchor blast now" button
    if force:
        bl["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet), "at": ist.strftime("%H:%M")}
        bl["points"] = {}                                       # strikes changed → reset the series
    elif bl["anchors"] is None and ist_min >= anchor_min:
        bl["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet), "at": ist.strftime("%H:%M")}
    if bl["anchors"] is None:
        return ('<div class="card hero"><span class="eyebrow">Gamma-blast · watching</span>'
                '<p class="instruct">Anchors freeze automatically at <b>13:45 IST</b> (strongest window). '
                'Until then, watch the levels and range above.</p></div>')

    a = bl["anchors"]
    ac, ap = strike_premiums(data.oi_strikes, a["atm"])
    mc, mp = strike_premiums(data.oi_strikes, a["magnet"])
    aco, apo = strike_oi(data.oi_strikes, a["atm"])
    mco, mpo = strike_oi(data.oi_strikes, a["magnet"])

    # ── 10s OPTION lane: the NEAREST-EXPIRY chain (1.0s for all 448 strikes vs 12.4s for the
    # 4-expiry window), so the score's FUEL reading (writer covering, premium expansion, wall
    # ahead) moves at 10s instead of the window chain's 1-5 min beat. Falls back to the window
    # snapshot whenever the lane has nothing fresh.
    tq = None
    if str(data.source or "").lower().startswith("dhan") and "replay" not in str(data.source).lower():
        try:
            from app import blast_ticker
            blast_ticker.ensure(data.symbol, getattr(config.get_index(data.symbol), "band_pts", None),
                                interval_s=_BLAST_TICK_S)
            tq = blast_ticker.latest(data.symbol)          # the WHOLE nearest-expiry ladder
        except Exception:  # noqa: BLE001
            tq = None
    fast_fuel = bool(tq and a["atm"] in tq)
    if fast_fuel:
        cell = tq[a["atm"]]
        ac = cell.get("call_ltp", ac); ap = cell.get("put_ltp", ap)
        aco = cell.get("call_oi", aco); apo = cell.get("put_oi", apo)
        _pb = (ist.second // _BLAST_TICK_S) * _BLAST_TICK_S
        _pkey = f"{ist:%H:%M}:{_pb:02d}"          # 10s points → the score moves at 10s
    else:
        _pkey = ist.strftime("%H:%M")             # chain cadence
    bl["points"][_pkey] = {"atm_call": ac, "atm_put": ap, "magnet_call": mc,
                           "magnet_put": mp, "atm_coi": aco, "atm_poi": apo}
    series = [{"time_ist": t, "call": v.get("atm_call"), "put": v.get("atm_put")}
              for t, v in sorted(bl["points"].items(), key=lambda kv: _secs(kv[0]) or 0)]
    # 10s spot lane, bucketed so repaints (every 3s) don't bloat the series
    if spot_live:
        _b = (ist.second // _BLAST_TICK_S) * _BLAST_TICK_S
        bl.setdefault("spot", {})[f"{ist:%H:%M}:{_b:02d}"] = float(spot_live)
    sser = [{"time_ist": t, "spot": v} for t, v in sorted(bl.get("spot", {}).items(),
                                                          key=lambda kv: _secs(kv[0]) or 0)]
    # prefer the finely-timed spot cross; fall back to the premium pair before the lane warms up
    evs = detect_crossovers_spot(sser, a["atm"]) if len(sser) >= 2 else []
    fine = bool(evs)
    if not evs:
        evs = detect_crossovers(series)

    K = a["atm"]
    if not evs:
        chart = _svg_crossover(series, K)
        return (f'<div class="card hero"><span class="eyebrow">Gamma-blast · watching {K:,.0f}</span>'
                f'<div class="verdict lean-warn"><span class="ic">👀</span><span class="big">No cross yet</span></div>'
                f'<p class="instruct">Watching the ATM strike <b class="mono">{K:,.0f}</b> — a crossover fires '
                f'when spot crosses it. No blast setup until then.</p>{chart}</div>')

    last = evs[-1]
    direction = "up" if last["to"] == "call_rich" else "down"
    _cut = _secs(last["time_ist"]) or 0
    win = [(t, v) for t, v in sorted(bl["points"].items(), key=lambda kv: _secs(kv[0]) or 0)
           if (_secs(t) or 0) >= _cut]
    crossed = [{"call_oi": v.get("atm_coi"), "put_oi": v.get("atm_poi"),
                "call_ltp": v.get("atm_call"), "put_ltp": v.get("atm_put")} for _, v in win]
    ahead = None
    nb = next_strike(data.oi_strikes, K, direction)
    if nb is not None:
        if fast_fuel and nb in (tq or {}):
            nco, npo = tq[nb].get("call_oi"), tq[nb].get("put_oi")
        else:
            nco, npo = strike_oi(data.oi_strikes, nb)
        ahead = [{"call_oi": nco, "put_oi": npo, "call_ltp": None, "put_ltp": None}]
    sc = blast_score(direction, crossed, ahead) or {}
    score = sc.get("score", 0)
    grade = sc.get("grade", "")
    run = grade.startswith("TRUE")
    lean = "lean-go" if run else "lean-warn"
    ic = "🚀" if run else "🪤"
    word = "Likely RUN" if run else "Likely FAKE"
    dword = "up" if direction == "up" else "down"
    if run:
        instr = (f'<b>The break is sustaining.</b> Spot crossed {dword} through <b class="mono">{K:,.0f}</b> '
                 f'and writers are covering (fuel). Bias: continuation {dword}. Confirmation, not a fresh entry — act on your plan.')
    else:
        instr = (f'<b>Don\'t chase the {dword}-move.</b> Spot crossed {dword} through <b class="mono">{K:,.0f}</b>, '
                 f'but the score is weak / a wall is likely to cap it. More likely it stalls and slips back toward '
                 f'<b class="mono">{K:,.0f}</b>. If you lean, <i>fade</i> it, small size.')
    cover = sc.get("cover_frac")
    wr = sc.get("wall_ratio")
    fillc = "var(--go)" if run else "var(--warn)"

    # ── ignition TIMING (§7d): how fast did the score reach TRUE from the cross? ──
    def _m(t):                       # minutes (float) — the cross can now be sub-minute
        sec = _secs(t)
        return None if sec is None else sec / 60.0
    times = [t for t, _ in win]
    traj = [(blast_score(direction, crossed[: j + 1], ahead) or {}).get("score", 0)
            for j in range(len(crossed))]
    t0 = _m(last["time_ist"])        # the CROSS time (10s-accurate), not the first window point
    now_m = (ist.hour * 3600 + ist.minute * 60 + ist.second) / 60.0
    since = max(0.0, now_m - t0) if t0 is not None else None
    k_true_min = next((_m(times[j]) - t0 for j, s in enumerate(traj)
                       if s >= 60 and t0 is not None), None)
    _res = "10s" if fine else "chain-cadence"
    _fuel = "10s" if fast_fuel else "chain-cadence"
    def _mm(x):                      # +2.5m under a minute of resolution, +3m above
        return f"{x:.1f}".rstrip("0").rstrip(".") if x is not None and x < 10 else f"{x:.0f}"
    if k_true_min is not None and k_true_min <= 5:
        tcls, tlab = "bull", f"⚡ Fast TRUE (+{_mm(k_true_min)}m)"
        tnote = f"Best setup — ignited within 5 min of the cross (timed at {_res})."
    elif k_true_min is not None:
        tcls, tlab = "cover", f"Slow TRUE (+{_mm(k_true_min)}m)"
        tnote = f"Confirmed but late — weaker than a fast ignition (timed at {_res})."
    elif since is not None and since <= 5:
        tcls, tlab, tnote = "flat", f"Watching (+{_mm(since)}m)", "Needs score ≥45 by ~3 min to likely reach TRUE."
    else:
        tcls, tlab, tnote = "bear", f"No ignition (+{_mm(since)}m)", "No TRUE within 5 min — usually fizzles; lean fade."
    traj_txt = " → ".join(f"{s:.0f}" for s in traj[-5:])
    timing_html = (f'<div class="winstrip" style="margin-top:12px"><div class="winrow">'
                   f'<span class="wl">Timing</span><span class="wbadge {tcls}">{tlab}</span>'
                   f'<span class="wd">{traj_txt}</span></div>'
                   f'<div class="win-cap">{tnote}</div></div>')

    # ── ALERT: bell + toast the moment the §7d cutoff (score ≥45 within ~5m) or a Fast
    # TRUE first fires — once per crossover per tier (dedup'd), escalating early→fast. ──
    try:
        early = [s for j, s in enumerate(traj) if t0 is not None and (_m(times[j]) - t0) <= 5]
        early45 = bool(early) and max(early) >= 45
        fast_true = k_true_min is not None and k_true_min <= 5
        akey = f"{ist:%Y-%m-%d}|{last['time_ist']}|{K:.0f}"
        A = st.session_state.setdefault("simp_alerts", {"key": None, "fired": set()})
        if A["key"] != akey:
            A["key"], A["fired"] = akey, set()

        def _alert(tier, msg, icon, sound):
            if tier in A["fired"]:
                return
            A["fired"].add(tier)
            try:
                st.toast(msg, icon=icon)
            except Exception:  # noqa: BLE001
                pass
            if st.session_state.get("simp_sound", True):
                _beep(sound)
        if fast_true:
            _alert("fast", f"⚡ FAST TRUE at {K:,.0f} (+{k_true_min}m) — best blast setup", "⚡", "fast")
        elif early45:
            _alert("early", f"👀 Igniting at {K:,.0f}: score 45+ within 5 min — likely TRUE", "👀", "early")
    except Exception:  # noqa: BLE001 - alerts must never break the render
        pass
    chart = _svg_crossover(series, K)
    return f'''<div class="card hero {lean}"><div style="display:flex;justify-content:space-between">
      <span class="eyebrow">Gamma-blast verdict · {K:,.0f}</span>
      <span class="subnote mono">cross {last["time_ist"]}</span></div>
      <div class="verdict"><span class="ic">{ic}</span><span class="big">{word}</span></div>
      <p class="instruct">{instr}</p>{_pin_context_line(prof, L)}
      <div class="meter"><div class="head"><span>True-run score</span>
        <span><b class="mono">{score}</b> / 100 · {grade}</span></div>
        <div class="track"><div class="fill" style="width:{max(3,min(score,100))}%;background:{fillc}"></div></div>
        <div class="ticks"><span>Fake / pin</span><span>Toss-up</span><span>Real run</span></div>
        <div class="win-cap">cross timed {_res} · OI/premium {_fuel}</div></div>
      {timing_html}
      <div class="blast-stats">
        <div><small>{sc.get("itm_side","")}-writer OI change</small>
          <b class="mono">{f(cover*100,0) if cover is not None else "—"}%</b><em>−ve = covering (fuel)</em></div>
        <div><small>Wall ahead</small><b class="down mono">{f(wr,1)+"×" if wr is not None else "—"}</b><em>cap overhead</em></div>
      </div>{chart}</div>'''


_OHLC_TOL = 0.004          # 0.4% — a fresh quote's ltp must agree with the chain's live underlying


def _resolve_ohlc(data, hl, spot, day_ohlc=None):
    """Today's candle OHLC. Prefers the feed's TRUE day OHLC (accurate high/low); falls back to the
    snapshot-seeded low/high + open. Two invariants, both learned from a real glitch where the
    candle printed green up to ~24,300 while spot was 24,182:

    1. **The close IS the live spot.** A developing candle's close is the current price. The feed's
       ``ohlc.close`` is the PREVIOUS session's close *by design* (verified live), so it is never a
       candle close — using it drew a body up to a price that never traded and flipped it green.
       It is carried as ``prev_close`` and used only for the day's % change.
    2. **Check the quote's own timestamp.** ``last_trade_time`` says which session the block
       describes: if it isn't today (IST), the high/low are another day's — drop them for our own
       captured high/low, which is always today. Unioning with spot cannot save a stale HIGH.
       When no timestamp is available (older payloads, replay) fall back to an ``ltp``-vs-spot
       sanity check rather than trusting blindly."""
    d = day_ohlc or data.day_ohlc or {}     # prefer the fast spot tick when one is available
    as_of = d.get("as_of")
    if as_of:                                    # authoritative: the feed's own trade timestamp
        today_ist = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        fresh = str(as_of)[:10] == today_ist
    else:                                        # no stamp → fall back to a consistency check
        ltp = d.get("ltp")
        fresh = bool(ltp is not None and spot
                     and abs(float(ltp) - float(spot)) / float(spot) <= _OHLC_TOL)
    if not fresh:
        d = {}                                   # another session / unverifiable → don't trust it
    o, h, lo = d.get("open"), d.get("high"), d.get("low")
    o = o if o is not None else hl.get("open", spot)
    h = h if h is not None else hl["hi"]
    lo = lo if lo is not None else hl["lo"]
    c = spot                                     # invariant 1: running close == current price
    h = max(h, o, c, spot)
    lo = min(lo, o, c, spot)
    return {"o": float(o), "h": float(h), "l": float(lo), "c": float(c), "true_ohlc": bool(d)}


def _sector_strip(data):
    """Sector rotation snap: every NSE sector index's % change vs the PREVIOUS day's close, sorted
    leaders → laggards, green/red. Broad-market indices (Nifty 50 / Midcap / Smallcap) are outlined
    so breadth is readable at a glance. Pure CONTEXT — sector rotation has never been tested for
    edge here, so nothing on this strip is a signal."""
    # Read the snap at RENDER time, not off the fetched snapshot: it is an instant cache read, so
    # the strip appears as soon as the 15-min background refresh lands, instead of waiting for the
    # NEXT chain pull to bake it in (which hid it for a whole cycle after a cold start).
    # Demo/replay are skipped — today's live sector moves beside a replayed 2021 day would be a lie.
    if not _is_live(data) or "replay" in (data.source or "").lower():
        return ''
    try:
        from data.sectors import fetch_sector_snap
        snap = fetch_sector_snap() or getattr(data, "sectors", None) or {}
    except Exception:  # noqa: BLE001 - a context panel never breaks the page
        snap = getattr(data, "sectors", None) or {}
    rows = snap.get("rows") or []
    if not rows:
        return ''
    ups = sum(1 for r in rows if (r.get("pct") or 0) > 0 and r["group"] == "sector")
    tot = sum(1 for r in rows if r["group"] == "sector")
    asof = snap.get("weights_as_of", "")
    chips = []
    for r in rows:
        pct = r.get("pct")
        cls = "na" if pct is None else "up" if pct > 0 else "dn" if pct < 0 else ""
        if r["group"] == "broad":
            cls += " broad"
        val = "—" if pct is None else f"{pct:+.2f}%"
        wt = r.get("weight")
        # weight is an approximation (no live feed publishes it) — the "~" is load-bearing
        wtag = f'<span class="wt">~{wt:g}%</span>' if wt else ''
        ttl = r["label"]
        if r.get("last"):
            ttl += f' {f(r["last"])}'
        if wt:
            ttl += f' · ~{wt:g}% of Nifty 50 (approx, {asof})'
        chips.append(f'<span class="sq {cls}" title="{ttl}"><span class="nm">{r["label"]}</span>'
                     f'{wtag}<span class="pc">{val}</span></span>')
    src = snap.get("source", "")
    return (f'<div class="card sect"><div class="sect-hd"><span class="eyebrow">Sector snap · '
            f'vs previous close</span><span class="rb-cav">{ups}/{tot} sectors green · {src} · '
            f'~% = approx Nifty 50 weight ({asof}), overlapping — don\'t sum</span></div>'
            f'<div class="sect-grid">{"".join(chips)}</div></div>')


def _profile_overlay(symbol, source, date_ist, live_spot, show_weekly):
    """Best-effort TPO overlay bundle (today developing + prior-day + 5D composite) for the candle.
    Never raises / blocks the page — returns None if the profile can't be built (e.g. too few
    captured bars yet)."""
    try:
        from app.market_profile import overlay_profiles
        return overlay_profiles(symbol, source, date_ist, weekly=show_weekly, live_spot=live_spot)
    except Exception:  # noqa: BLE001
        return None


def _mp_regime_chip(prof, vr):
    """A one-line synthesis chip: the developing distribution's shape vs the vanna tier — agree
    (confidence) or conflict (de-size). Decision-support framing, never a direction."""
    try:
        from app.market_profile import profile_regime
        reg = profile_regime((prof or {}).get("today"))
    except Exception:  # noqa: BLE001
        reg = None
    if not reg:
        return ''
    tag = vr["tag"] if vr else "mid"
    rotational = reg["shape"] == "single-print"
    calm = tag == "calm"
    if calm and rotational:
        cls, txt = "agree", f"🟢 Calm × {reg['shape']} · pin {reg['pin']} — consistent, size the vol-sell"
    elif calm and not rotational:
        cls, txt = "conflict", f"🟡 Calm × {reg['shape']} — regime conflict, de-size"
    elif (not calm) and rotational:
        cls, txt = "conflict", f"🟡 {tag.title()} × {reg['shape']} — mixed read"
    else:
        cls, txt = "", f"{tag.title()} × {reg['shape']} · pin {reg['pin']}"
    return f'<span class="mp-chip {cls}">{txt}</span>'


def _pin_context_line(prof, L):
    """One-line pin-regime prior for the blast verdict — the same #26/#27 read, phrased for a cross."""
    try:
        from app.market_profile import pin_regime
        pr = pin_regime((prof or {}).get("today"), getattr(L, "max_pain", None))
    except Exception:  # noqa: BLE001
        pr = None
    if not pr:
        return ''
    if pr["pinned"]:
        msg = ("<b>PINNED</b> — the pin sits inside today’s value area, so the break is more likely to "
               "<b>fade</b> back toward it than to run (mild prior).")
    else:
        msg = ("<b>UN-PINNED</b> — value has migrated off the pin (directional morning), so a TRUE break "
               "is a little more likely to <b>run</b> (mild prior).")
    return (f'<div class="win-cap" style="margin-top:8px">Day-type context · {msg} '
            f'<span style="opacity:.7">#26/#27, sign-free</span></div>')


def _range_candle(L, ohlc, prof=None, show_weekly=False, spot_live=None):
    """One shared price axis, three zones: LEFT = realized VALUE — the developing TPO profile
    (time-at-price bars, POC + value-area coloured) with prior-day (dashed) and 5-day composite
    (dotted, toggle) value; MIDDLE = the live candle; RIGHT = the GEX ladder (call/put wall, magnet,
    flip, pin, spot). So 'where price accepted' (left) never collides with 'where dealers are
    positioned' (right). Hover a GEX level to emphasise it. A map, not a signal."""
    o, h, lo, c = ohlc["o"], ohlc["h"], ohlc["l"], ohlc["c"]
    spot = float(spot_live) if spot_live else float(L.spot)   # fast lane price for the marker
    W, HT, top, plot_h = 486, 560, 28, 496
    # left gutter sized for the widest value label ("5D VAH 24,250" ≈ 78px at 10.5px type) — the
    # labels are anchored END at LBLX, so a narrow gutter clips them off the viewBox.
    LBLX, PBX0, PBLEN, MPX1 = 90, 98, 62, 196           # left value zone
    cx, bw = 214, 28                                     # candle
    GX0, GLADDER, GLABEL = 238, 344, 350                 # right GEX ladder
    today = (prof or {}).get("today")
    prior = (prof or {}).get("prior")
    weekly = (prof or {}).get("weekly") if show_weekly else None

    gex = [("Call wall", L.call_wall, "var(--stop)", False, "resistance"),
           ("Expiry pin", L.max_pain, "var(--warn)", True, "max pain"),
           ("Magnet", L.magnet, "var(--spark)", False, "pull"),
           ("Gamma flip", L.flip, "var(--ink-mute)", False, "γ sign flip"),
           ("Put wall", L.put_wall, "var(--go)", False, "support")]
    # ── AXIS: anchored on PRICE ACTION, never on the levels ──────────────────────────────────
    # A far level (an expiry-day gamma flip can swing the whole FLIP_GRID_RANGE, a prior-day VA can
    # sit entirely above today) must NOT rescale the axis — that squeezes the candle to a sliver and
    # makes it jump every tick. So: the core span is today's range + spot + the developing profile;
    # levels may widen it only up to STRETCH; anything past that is drawn OFF-SCALE at the edge with
    # an ▲/▼ marker (the value is still readable, the geometry stays put).
    MIN_SPAN_PCT, STRETCH = 0.004, 2.0         # floor at the open (range≈0); max widening by levels
    core = [h, lo, o, c, spot] + ([today["hi"], today["lo"]] if today else [])
    cmin, cmax = min(core), max(core)
    mid = (cmin + cmax) / 2.0
    half = max((cmax - cmin) / 2.0, spot * MIN_SPAN_PCT / 2.0) * 1.12
    lim = half * STRETCH                                   # how far a level may pull the axis
    for pr in ([pr for _, pr, *_ in gex if pr is not None]
               + [v for pf in (prior, weekly) if pf for v in (pf["vah"], pf["val"], pf["poc"])]):
        if abs(pr - mid) <= lim:
            half = max(half, abs(pr - mid) * 1.04)
    step = float(today["bin_pts"]) if today else (5.0 if spot < 30000 else 20.0)
    pmin = math.floor((mid - half) / step) * step          # quantised so small ticks don't jitter it
    pmax = math.ceil((mid + half) / step) * step

    def Y(v):
        return top + (pmax - v) / (pmax - pmin) * plot_h

    def _clampy(v):
        return min(max(Y(v), top), top + plot_h)

    def _off(v):
        """None if on-scale, else 'up'/'down' — the level is outside the fixed axis."""
        return "up" if v > pmax else "down" if v < pmin else None

    p = [f'<svg viewBox="0 0 {W} {HT}" role="img" aria-label="market profile, candle and GEX levels">']
    mp_labels = []                                       # left value labels (dodged)
    mp_notes = []                                        # off-scale value areas (text, no geometry)

    # ── LEFT: developing TPO profile bars (time-at-price) ──
    if today:
        cnt, cen, bp = today["counts"], today["centers"], today["bin_pts"]
        maxc = float(cnt.max()) or 1.0
        binh = max(1.0, abs(Y(cen[0] - bp / 2) - Y(cen[0] + bp / 2)))
        for i in range(len(cnt)):
            if cnt[i] <= 0:
                continue
            yv = Y(cen[i]); wdt = (cnt[i] / maxc) * PBLEN
            if i == today["poc_i"]:
                cl, op = "var(--spark)", 0.92
            elif today["val_i"] <= i <= today["vah_i"]:
                cl, op = "var(--ink-2)", 0.5
            else:
                cl, op = "var(--ink-mute)", 0.28
            p.append(f'<rect x="{PBX0}" y="{yv-binh/2:.1f}" width="{wdt:.1f}" height="{binh:.1f}" '
                     f'style="fill:{cl};opacity:{op}"></rect>')
        yp = Y(today["poc"])
        p.append(f'<line x1="{PBX0}" y1="{yp:.1f}" x2="{MPX1}" y2="{yp:.1f}" '
                 f'style="stroke:var(--spark)" stroke-width="1.6"></line>')
        mp_labels.append((today["poc"], f'POC {f(today["poc"])}', "var(--spark)"))
        # today's value-area EDGES, with their prices — the band a rotational day trades inside
        for key, nm in (("vah", "VAH"), ("val", "VAL")):
            yv = Y(today[key])
            p.append(f'<line x1="{PBX0}" y1="{yv:.1f}" x2="{MPX1}" y2="{yv:.1f}" '
                     f'style="stroke:var(--ink-2);opacity:.55" stroke-width="1"></line>')
            mp_labels.append((today[key], f'{nm} {f(today[key])}', "var(--ink-2)"))

    def _band(pf, dash, col, tag, pre):
        """A prior/composite value area, CLIPPED to the fixed axis. Every level carries its PRICE
        (a line alone doesn't tell you where it is). A level outside the window is still reported —
        as a labelled off-scale marker at the edge — so it is never silently dropped, and a stale
        VA can never stretch the scale."""
        if not pf:
            return
        if pf["val"] > pmax or pf["vah"] < pmin:           # whole band outside → one compact note
            up = pf["val"] > pmax
            mp_notes.append((f'{"▲" if up else "▼"} {tag} {"above" if up else "below"} · '
                             f'{f(pf["val"])}–{f(pf["poc"])}–{f(pf["vah"])}', col, up))
            return
        yh, yl = _clampy(pf["vah"]), _clampy(pf["val"])
        p.append(f'<rect x="{PBX0-4}" y="{yh:.1f}" width="{MPX1-PBX0+4}" height="{max(0.0, yl-yh):.1f}" '
                 f'style="fill:{col};opacity:.05"></rect>')
        for key, nm, w in (("vah", "VAH", 1.0), ("poc", "POC", 1.3), ("val", "VAL", 1.0)):
            lv_ = pf[key]
            side = _off(lv_)
            if side:                                       # outside the window → label it, no line
                mp_notes.append((f'{"▲" if side == "up" else "▼"} {pre}{nm} {f(lv_)} '
                                 f'{"above" if side == "up" else "below"}', col, side == "up"))
                continue
            yy = Y(lv_)
            p.append(f'<line x1="{PBX0-4}" y1="{yy:.1f}" x2="{MPX1}" y2="{yy:.1f}" '
                     f'style="stroke:{col};opacity:.7" stroke-width="{w}" stroke-dasharray="{dash}"></line>')
            mp_labels.append((lv_, f'{pre}{nm} {f(lv_)}', col))
    _band(prior, "4 3", "var(--ink-mute)", "prior VA", "p")
    _band(weekly, "1 3", "var(--warn)", "5D VA", "5D ")

    mp_labels.sort(key=lambda t: -t[0])
    prev = -1e9
    for px, txt, col in mp_labels:
        ly = max(_clampy(px), prev + 14.0); prev = ly
        p.append(f'<text class="vc-mp" x="{LBLX}" y="{ly+3.5:.1f}" text-anchor="end" '
                 f'style="fill:{col}">{txt}</text>')
    nu = nd = 0                                          # off-scale value areas, pinned to the edge
    for txt, col, up in mp_notes:
        if up:
            ny = top + 10 + nu * 13; nu += 1
        else:
            ny = top + plot_h - 4 - nd * 13; nd += 1
        p.append(f'<text class="vc-mp vc-off" x="{MPX1}" y="{ny:.1f}" text-anchor="end" '
                 f'style="fill:{col}">{txt}</text>')

    # ── MIDDLE: the live candle ──
    cc = "var(--go)" if c >= o else "var(--stop)"
    yb0, yb1 = Y(max(o, c)), Y(min(o, c))
    p.append(f'<line x1="{cx}" y1="{Y(h):.1f}" x2="{cx}" y2="{Y(lo):.1f}" style="stroke:{cc}" '
             f'stroke-width="2.4"></line><rect x="{cx-bw/2}" y="{yb0:.1f}" width="{bw}" '
             f'height="{max(2.0, yb1-yb0):.1f}" rx="3" style="fill:{cc};opacity:.9;stroke:{cc};stroke-width:1"></rect>')
    ys = Y(spot)
    p.append(f'<polygon points="{cx-bw/2-11},{ys:.1f} {cx-bw/2-3},{ys-5:.1f} {cx-bw/2-3},{ys+5:.1f}" '
             f'style="fill:var(--ink)"></polygon>')

    # ── RIGHT: GEX ladder ──
    lv = [{"name": n, "px": pr, "col": cl, "dash": dd, "sub": ss}
          for n, pr, cl, dd, ss in gex if pr is not None]
    lv.append({"name": "Spot", "px": spot, "col": "var(--ink)", "dash": False, "sub": "now"})
    lv.sort(key=lambda d: -d["px"])
    prev = -1e9
    for d in lv:
        d["dir"] = _off(d["px"])
        d["yv"] = _clampy(d["px"])                       # off-scale levels pin to the axis edge
        d["ly"] = max(d["yv"], prev + 17.0); prev = d["ly"]
    for d in lv:
        dash = ' stroke-dasharray="4 3"' if d["dash"] else ''
        if d["dir"]:                                     # OFF-SCALE: an arrow chip, no rule drawn —
            arw = "▲" if d["dir"] == "up" else "▼"       # geometry stays fixed, value stays readable
            side = "above" if d["dir"] == "up" else "below"
            p.append(f'<g class="vc-lvl vc-off"><title>{d["name"]} {f(d["px"])} · {d["sub"]} — '
                     f'{side} today\'s range</title>'
                     f'<text class="vc-lbl" x="{GX0}" y="{d["ly"]+4:.1f}">'
                     f'<tspan style="fill:{d["col"]}">{arw}</tspan> '
                     f'<tspan class="vc-val" style="fill:{d["col"]}">{f(d["px"])}</tspan> '
                     f'<tspan>{d["name"]} {side}</tspan></text></g>')
            continue
        p.append(f'<g class="vc-lvl"><title>{d["name"]} {f(d["px"])} · {d["sub"]}</title>'
                 f'<line class="vc-line" x1="{GX0}" y1="{d["yv"]:.1f}" x2="{GLADDER}" '
                 f'y2="{d["yv"]:.1f}" style="stroke:{d["col"]}" stroke-width="1.2"{dash}></line>'
                 f'<line class="vc-line" x1="{GLADDER}" y1="{d["yv"]:.1f}" x2="{GLABEL}" '
                 f'y2="{d["ly"]:.1f}" style="stroke:{d["col"]}" stroke-width="1.2"></line>'
                 f'<text class="vc-lbl" x="{GLABEL+6}" y="{d["ly"]+4:.1f}">'
                 f'<tspan class="vc-val" style="fill:{d["col"]}">{f(d["px"])}</tspan> '
                 f'<tspan>{d["name"]}</tspan></text></g>')
    p.append('</svg>')
    return f'<figure class="vc">{"".join(p)}</figure>'


def _is_live(data):
    """A real live feed (Dhan or NSE, incl. the Dhan→NSE fallback) — not demo. The per-strike OI,
    footprint, gamma-blast and self-capture all work on ANY live feed (the Dhan adapter produces
    the same NSE-shaped per-strike OI/ΔOI/premium fields), so these must not be gated to NSE only."""
    s = (data.source or "")
    return bool(s) and "DEMO" not in s.upper()


def _persist_snapshot(data, symbol, min_gap):
    """Self-sufficient capture: on each in-session live pull, append the timeseries row
    and (lock-step) the per-strike OI rows — so the range seed AND the options OI windows
    are fed by THIS app's own refreshes, with no separate logger required. Mirrors the main
    dashboard's discipline: in-session only (a frozen post-close chain must never poison the
    day's file), and ``append_if_due`` dedups rapid refreshes / a co-running logger (shared
    file). Returns True if a row was written. Best-effort — never blocks the page."""
    if not _is_live(data):
        return False
    try:
        from app.timeseries import (append_if_due, append_strike_rows, build_row,
                                    build_strike_rows, in_nse_session, oistrikes_dir_for)
        if not in_nse_session(data.fetched_at):
            return False
        _, written = append_if_due(build_row(data), min_gap_minutes=min_gap, symbol=symbol)
        if written:                                          # same fetch, lock-step, source-routed
            append_strike_rows(build_strike_rows(data), symbol=symbol,
                               directory=oistrikes_dir_for(data.source))
        return written
    except Exception:  # noqa: BLE001 - capture is best-effort, never blocks the page
        return False


def _seed_hl(symbol, date_ist, spot_now, strikes_dir=None):
    """Seed today's low/high/open from the day's SAVED snapshots (timeseries + oi_strikes
    `spot` columns), so opening/restarting mid-day recovers the whole session instead of
    starting from the current spot. Unions both streams with the live spot; open = the
    earliest saved spot. Snapshot-cadence (~3 min) — still misses sub-cadence ticks.
    ``strikes_dir`` routes the per-strike read to the current feed's journal (Dhan vs NSE)."""
    lo = hi = op = float(spot_now)
    spots = []
    try:
        from app.timeseries import load_day, load_strikes_day
        for loader in (load_day, lambda d, s: load_strikes_day(d, s, directory=strikes_dir)):
            df = loader(date_ist, symbol)
            if df is not None and not getattr(df, "empty", True) and "spot" in df.columns:
                s = pd.to_numeric(df["spot"], errors="coerce").dropna()
                if len(s):
                    spots.append(s)
    except Exception:  # noqa: BLE001 - range seed is best-effort, never blocks the page
        pass
    if spots:
        alls = pd.concat(spots)
        lo = min(lo, float(alls.min())); hi = max(hi, float(alls.max()))
        op = float(spots[0].iloc[0])          # earliest snapshot of the timeseries stream
    return {"day": date_ist, "lo": lo, "hi": hi, "open": op}


def _opt_window_row(lbl, minutes, w, is_expiry):
    """One row of the options rolling-window strip (60/30/15/5-min current-expiry build)."""
    if w is None:
        return (f'<div class="winrow dim"><span class="wl">{lbl}</span>'
                '<span class="wbadge flat">— building…</span><span class="wd"></span></div>')
    cls, txt = {"put-writing": ("bull", "🟢 Puts written → support"),
                "put-buying": ("bear", "🔴 Puts bought → bearish"),
                "call-writing": ("bear", "🔴 Calls written → resistance"),
                "call-buying": ("bull", "🟢 Calls bought → bullish"),
                "balanced": ("flat", "· Balanced"),
                "thin": ("flat", "· Thin build")}.get(w["label"], ("flat", "· —"))
    directional = w["label"] in ("put-writing", "put-buying", "call-writing", "call-buying")
    val = w.get("flow_val") or 0.0                        # dominant flow magnitude (unsigned)
    money = (_money(val) if w.get("unit") == "inr" else f"{f(val)}") if directional else ""
    peak = f'@{f(w["peak_strike"])}' if w.get("peak_strike") else ""   # top strike this window
    dtxt = " · ".join(x for x in (money, peak) if x)
    dim = " dim" if (is_expiry and minutes <= 15) else ""     # short windows churn on expiry
    shown = ("≤" + lbl) if w.get("partial") else lbl
    return (f'<div class="winrow{dim}"><span class="wl">{shown}</span>'
            f'<span class="wbadge {cls}">{txt}</span><span class="wd">{dtxt}</span></div>')


def _beep(kind):
    """Best-effort browser bell via a WebAudio tone. 'fast' = a higher double-beep; 'early' =
    a single tone. Runs in the main page (st.html + unsafe_allow_javascript) so it shares the
    page's gesture context — but browser autoplay policy can still mute it until the tab has
    had a click; the st.toast is the always-on channel. Also fires a desktop Notification if
    permission was granted."""
    freq, reps = (880, 2) if kind == "fast" else (660, 1)
    js = """<script>(function(){try{
      var C=window.AudioContext||window.webkitAudioContext; if(!C)return;
      var ctx=new C(); if(ctx.state==='suspended'){ctx.resume();}
      var t=ctx.currentTime;
      for(var i=0;i<REPS;i++){var o=ctx.createOscillator(),g=ctx.createGain();
        o.type='sine';o.frequency.value=FREQ;o.connect(g);g.connect(ctx.destination);
        g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(0.3,t+0.02);
        g.gain.exponentialRampToValueAtTime(0.0001,t+0.35);o.start(t);o.stop(t+0.37);t+=0.45;}
      if('Notification'in window){if(Notification.permission==='granted'){
        try{new Notification('Gamma-blast ignition');}catch(e){}}
        else if(Notification.permission!=='denied'){try{Notification.requestPermission();}catch(e){}}}
    }catch(e){}})();</script>""".replace("REPS", str(reps)).replace("FREQ", str(freq))
    try:
        st.html(js, unsafe_allow_javascript=True)          # main-page, non-deprecated
    except TypeError:
        try:
            import streamlit.components.v1 as components    # older Streamlit fallback (iframe)
            components.html(js, height=0)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - a bell must never break the render
        pass


def _strangle_payoff_svg(kp, kc, credit, spot, lot):
    """Expiry P/L diagram for a short strangle (sell put@kp + call@kc, net ``credit`` pts): a green
    max-profit plateau between the sold strikes, sloping to the break-evens (strike ± credit) and
    into red loss wings beyond. Amber dashes = the walls/sold strikes; grey dashes = break-evens;
    the solid vertical = current spot."""
    be_lo, be_hi = kp - credit, kc + credit
    xlo, xhi = kp - 2 * credit, kc + 2 * credit
    W, H, x0, x1, pad = 520, 150, 40, 502, 20
    ymax = credit or 1.0

    def X(px):
        return x0 + (x1 - x0) * ((px - xlo) / (xhi - xlo))

    def Y(pl):
        return H / 2 - (H / 2 - pad) * (pl / ymax)
    verts = [(xlo, -credit), (be_lo, 0.0), (kp, credit), (kc, credit), (be_hi, 0.0), (xhi, -credit)]
    payoff = " ".join(f"{X(px):.0f},{Y(pl):.0f}" for px, pl in verts)
    prof = (f"{X(be_lo):.0f},{Y(0):.0f} {X(kp):.0f},{Y(credit):.0f} {X(kc):.0f},{Y(credit):.0f} "
            f"{X(be_hi):.0f},{Y(0):.0f}")
    lossl = f"{X(xlo):.0f},{Y(0):.0f} {X(be_lo):.0f},{Y(0):.0f} {X(xlo):.0f},{Y(-credit):.0f}"
    lossr = f"{X(be_hi):.0f},{Y(0):.0f} {X(xhi):.0f},{Y(0):.0f} {X(xhi):.0f},{Y(-credit):.0f}"
    sx = X(min(max(spot, xlo), xhi))

    def vg(px, col, dash):
        return (f'<line x1="{X(px):.0f}" y1="{pad}" x2="{X(px):.0f}" y2="{H-pad}" '
                f'stroke="{col}" stroke-width="1" stroke-dasharray="{dash}"/>')

    def lbl(px, txt, col):
        return (f'<text x="{X(px):.0f}" y="{H-4}" font-size="9" fill="{col}" '
                f'text-anchor="middle" class="mono">{txt}</text>')
    return f'''<figure><svg viewBox="0 0 {W} {H}" role="img" aria-label="Short-strangle expiry profit and loss.">
      <polygon points="{lossl}" fill="var(--stop)" opacity=".16"/><polygon points="{lossr}" fill="var(--stop)" opacity=".16"/>
      <polygon points="{prof}" fill="var(--go)" opacity=".22"/>
      <line x1="{x0}" y1="{Y(0):.0f}" x2="{x1}" y2="{Y(0):.0f}" stroke="var(--line)"/>
      {vg(kp,"var(--warn)","3 3")}{vg(kc,"var(--warn)","3 3")}
      {vg(be_lo,"var(--ink-mute)","1 3")}{vg(be_hi,"var(--ink-mute)","1 3")}
      <polyline points="{payoff}" fill="none" stroke="var(--spark)" stroke-width="2.3" stroke-linejoin="round"/>
      <line x1="{sx:.0f}" y1="{pad}" x2="{sx:.0f}" y2="{H-pad}" stroke="var(--ink)" stroke-width="1.5"/>
      <text x="{(X(kp)+X(kc))/2:.0f}" y="{Y(credit)+12:.0f}" font-size="9.5" fill="var(--go)" text-anchor="middle" font-weight="700" class="mono">+{_money(credit*lot)}</text>
      <text x="{sx:.0f}" y="{pad-6:.0f}" font-size="8.5" fill="var(--ink)" text-anchor="middle">spot</text>
      {lbl(be_lo, f"{be_lo:,.0f}", "var(--ink-mute)")}{lbl(kp, f"{kp:,.0f}", "var(--warn)")}
      {lbl(kc, f"{kc:,.0f}", "var(--warn)")}{lbl(be_hi, f"{be_hi:,.0f}", "var(--ink-mute)")}
    </svg></figure>'''


def _play_card(vr, data, L, idx):
    """Suggested-play card. For the CALM (pinning) regime it shows a concrete illustrative
    short-strangle at the walls: sell the call at the call-wall + put at the put-wall, with the
    P/L structure — max profit (net credit), the break-even band, the stop-loss (just beyond the
    walls) and the max-loss note. Live premiums; paper only. The vol edge is validated (S11); the
    walls are a map, not a fence (docs §4d)."""
    tag = vr["tag"] if vr else "mid"
    head, body = _PLAY.get(tag, _PLAY["mid"])
    pnl = ""
    if (tag == "calm" and _is_live(data) and data.oi_strikes
            and L.call_wall and L.put_wall):
        from app.gammablast import next_strike, strike_premiums
        kc, kp, stepped = L.call_wall, L.put_wall, False
        if kc == kp:            # walls COINCIDE → a straddle (a degenerate strangle: zero-width band,
                                # and every move breaches a wall). Step ±1 listed strike out each side
                                # to restore a real full-credit band and separated wall-stops.
            kp = next_strike(data.oi_strikes, kc, "down") or kp
            kc = next_strike(data.oi_strikes, L.call_wall, "up") or kc
            stepped = kc != kp
        cc = pp = None
        try:
            cc = strike_premiums(data.oi_strikes, kc)[0]   # call premium @ call strike
            pp = strike_premiums(data.oi_strikes, kp)[1]   # put premium  @ put strike
        except Exception:  # noqa: BLE001
            pass
        lot = getattr(idx, "lot_size", None)
        if cc and pp and lot and kc != kp:
            credit_pts = cc + pp
            credit = credit_pts * lot
            be_lo, be_hi = kp - credit_pts, kc + credit_pts
            graph = _strangle_payoff_svg(kp, kc, credit_pts, L.spot, lot)
            note = (f'<div class="pnl-note">⚠ Call &amp; put walls coincide at {f(L.call_wall)} '
                    f'(a strong single-strike pin) — a straddle there has a zero-width credit band and '
                    f'no valid wall-stop, so this steps ±1 strike out to {f(kp)}/{f(kc)}.</div>'
                    if stepped else '')
            pnl = f'''<div class="pnl"><div class="pnl-hd">Illustrative structure · short strangle · paper</div>
              <div class="pnl-legs">SELL {f(kc)} CE @ {f(cc,1)} &nbsp;·&nbsp; SELL {f(kp)} PE @ {f(pp,1)}</div>{note}
              {graph}
              <div class="pnl-grid" style="margin-top:8px">
                <div><small>Max profit · credit</small><b class="go">{_money(credit)} · {f(credit_pts,0)} pt</b></div>
                <div><small>Keep full credit between</small><b class="mono">{f(kp)} – {f(kc)}</b></div>
                <div><small>Break-even (expiry)</small><b class="mono">{f(be_lo)} – {f(be_hi)}</b></div>
                <div><small>Stop · break of a wall</small><b class="stop">a conservative cut — inside the break-evens (a breach runs fast near expiry)</b></div>
              </div></div>'''
    return f'''<div class="card play"><span class="eyebrow">Suggested play · paper only</span>
      <div class="verdict-sm">{head}</div><p>{body}</p>{pnl}
      <div class="play-exit">Illustrative — you decide. The walls (sold strikes) sit <i>inside</i> the
      break-evens; you keep the full credit between them and only turn net-negative past a break-even.
      The VOL edge is backtested +11%/trade in calm (thinning to +4% now); the walls are a map, <b>not</b>
      a fence (§4d). Not advice.</div></div>'''


_FUT_CAP: dict = {}                 # symbol -> epoch of the last futures capture (claimed, not done)
_FUT_GAP_S = 180.0                  # 3 min between captures, matching the old min_gap_minutes=3
_DAY_CACHE: dict = {}               # (kind, key...) -> (epoch, frame)
_DAY_TTL_S = 20.0                   # day files only change when a capture lands (every 1-3 min)


def _cached_day(kind, key, loader, ttl=_DAY_TTL_S):
    """Micro-cache for the day CSV reads. The fragment repaints every few seconds but these files
    only change when a capture lands, so re-parsing them on every repaint is pure waste."""
    import time as _t
    k = (kind, *key)
    hit = _DAY_CACHE.get(k)
    now = _t.time()
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    val = loader()
    _DAY_CACHE[k] = (now, val)
    return val


def _futures_day_cached(date_ist, symbol, fdir):
    from app.futures_buildup import load_futures_intraday_day
    return _cached_day("fut", (date_ist, symbol, str(fdir)),
                       lambda: load_futures_intraday_day(date_ist, symbol, directory=fdir))


def _strikes_day_cached(date_ist, symbol, sdir):
    from app.timeseries import load_strikes_day
    return _cached_day("str", (date_ist, symbol, str(sdir)),
                       lambda: load_strikes_day(date_ist, symbol, directory=sdir))


def _capture_futures_bg(symbol, source, fetched_at):
    """Capture the futures snapshot on a BACKGROUND thread, and only when it is actually due.

    This used to run inline in ``_futures_html``: ``capture_futures_snapshot`` fetches FIRST and
    lets ``append_futures_snapshot`` apply the min-gap AFTER, so every single repaint paid a live
    Dhan round-trip (measured 7.1s with the scrip-master load + a 429 backoff) and then usually
    threw the result away. At a 3s fragment poll that is what shaded the page constantly.

    Now the gap is checked BEFORE any network call, the slot is claimed before the thread starts
    (so overlapping repaints can't double-fire), and the render only ever READS the day file."""
    import threading
    import time as _t
    now = _t.time()
    if now - _FUT_CAP.get(symbol, 0.0) < _FUT_GAP_S:
        return
    _FUT_CAP[symbol] = now                                   # claim the slot up-front
    dhan_feed = str(source or "").lower().startswith("dhan")

    def _run():
        try:
            from app.futures_buildup import capture_futures_snapshot, futures_intraday_dir_for
            from app.timeseries import in_nse_session
            if not in_nse_session(fetched_at):               # never write a frozen post-close row
                return
            fdir = futures_intraday_dir_for(source)
            if dhan_feed:
                from data.dhan_live import fetch_futures_dhan
                capture_futures_snapshot(symbol, fetch_fn=fetch_futures_dhan,
                                         parse_fn=lambda p, s: p, min_gap_minutes=3.0, directory=fdir)
            else:
                capture_futures_snapshot(symbol, min_gap_minutes=3.0, directory=fdir)
        except Exception:  # noqa: BLE001 - capture is best-effort, never surfaces
            pass

    threading.Thread(target=_run, daemon=True, name=f"fut-capture-{symbol}").start()


def _oi_unit(source) -> str:
    """Which unit this feed counts OI in. NSE publishes CONTRACTS (lots); Dhan publishes SHARES
    (lots × lot_size — verified: live Dhan NIFTY OI divides exactly by 65, never by 75). The ₹
    turnover multiplier depends on it, so it is read from the source rather than assumed."""
    return "shares" if str(source or "").lower().startswith("dhan") else "lots"


def _footprint_html(data, L, idx, ist, is_expiry):
    from app.oi_footprint import build_footprint, oi_weighted_levels, window_footprint
    from app.timeseries import oistrikes_dir_for
    if not _is_live(data) or not data.oi_strikes:
        return ''
    _sdir = oistrikes_dir_for(data.source)          # read the trailing windows from the SAME feed's journal
    band = getattr(idx, "band_pts", None)
    fp = build_footprint(data.oi_strikes, L.spot, band)
    if not fp:
        return ''
    skew = fp.get("net_skew", 0.0)
    who = "call-writing" if skew < 0 else "put-writing"
    sub = ("big players forming resistance" if skew < 0 else "big players forming support")
    lv = oi_weighted_levels(data.oi_strikes, L.spot, band) or {}   # validated S/R (standing OI)

    # rolling-window strip (current expiry only, from today's per-strike captures)
    day_df = _strikes_day_cached(ist.strftime("%Y-%m-%d"), idx.symbol, _sdir)
    now_hm = ist.hour * 60 + ist.minute
    unit = _oi_unit(data.source)          # Dhan OI is in SHARES, NSE in LOTS — the ₹ multiplier differs
    rows = "".join(_opt_window_row(lbl, m,
                   window_footprint(day_df, m, L.spot, band, now_hm=now_hm,
                                    lot_size=getattr(idx, "lot_size", None), oi_unit=unit), is_expiry)
                   for lbl, m in _WINDOWS)
    cap = ('⚠ Near expiry the 5/15-min reads churn (settlement) — lean on 30/60-min. @ = top strike.'
           if is_expiry else '₹ turnover (ΔOI×premium×lot), current expiry · @ = strike taking the '
                             'most money that window. Context, not a prediction.')
    strip = (f'<div class="winstrip"><div class="win-hd">Fresh positioning — trailing windows</div>'
             f'{rows}<div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div>')

    # where the biggest ₹ of FRESH writing is landing today (single strike, since open) — user ask
    sow = window_footprint(day_df, 600, L.spot, band, now_hm=now_hm,
                           lot_size=getattr(idx, "lot_size", None), oi_unit=unit)
    peak_row = ""
    if sow and sow.get("peak_strike") and sow.get("unit") == "inr":
        # writing vs buying (premium direction) changes the meaning entirely:
        desc, pcls = {
            ("call", "write"): ("calls written → resistance", "ceil"),
            ("call", "long"): ("calls bought → bullish", "floor"),
            ("put", "write"): ("puts written → support", "floor"),
            ("put", "long"): ("puts bought → bearish", "ceil"),
        }.get((sow["peak_side"], sow.get("peak_kind")), (f'{sow["peak_side"]}s building', ""))
        peak_row = (f'<div class="fp-row {pcls}"><span>💰 Most money building '
                    f'<small>{desc}</small></span><b class="mono">{f(sow["peak_strike"])}</b>'
                    f'<small>{_money(sow["peak_val"])}</small></div>')

    # One continuous line (no leading indent, no standalone {peak_row} line) so an empty optional
    # can't create a blank line that Streamlit renders the following indented HTML as a code block.
    return (
        f'<div class="card"><div class="levels">'
        f'<div class="fp-row floor"><span>🟢 Support <small>put OI</small></span>'
        f'<b class="mono">{f(lv.get("support_w"))}</b><small>top {f(lv.get("support_top"))}</small></div>'
        f'<div class="fp-row ceil"><span>🔴 Resistance <small>call OI</small></span>'
        f'<b class="mono">{f(lv.get("resistance_w"))}</b><small>top {f(lv.get("resistance_top"))}</small></div>'
        f'{peak_row}'
        f'<div class="fp-sum">Support/Resistance = OI-weighted centre (validated to hold best) + '
        f'<b>top</b> single strike. 💰 = strike taking the most fresh money today (writing vs buying '
        f'read from the premium’s direction). Net <b>{who} near spot</b> — {sub}.</div></div>{strip}</div>')


def _fut_window_row(lbl, minutes, win, is_expiry):
    """One row of the futures rolling-window strip (price×ΔOI over the trailing window)."""
    if win is None:
        return (f'<div class="winrow dim"><span class="wl">{lbl}</span>'
                '<span class="wbadge flat">— building…</span><span class="wd"></span></div>')
    label, dp, doi = win
    cls, icn, lab = _fut_badge(label)
    dtxt = (f'P {"▲" if (dp or 0) >= 0 else "▼"}{f(abs(dp)) if dp is not None else "—"} · '
            f'OI {"▲" if (doi or 0) >= 0 else "▼"}{f(abs(doi)) if doi is not None else "—"}')
    dim = " dim" if (is_expiry and minutes <= 15) else ""
    return (f'<div class="winrow{dim}"><span class="wl">{lbl}</span>'
            f'<span class="wbadge {cls}">{icn} {lab}</span><span class="wd">{dtxt}</span></div>')


def _futures_html(data, symbol, ist, is_expiry):
    from app.futures_buildup import futures_intraday_dir_for, intraday_futures_buildup
    if not _is_live(data):
        return ''
    date_ist = ist.strftime("%Y-%m-%d")
    flab = config.get_index(symbol).label                 # "Nifty 50" / "Bank Nifty" / "Sensex"
    fdir = futures_intraday_dir_for(data.source)          # source-separated journal
    # Capture runs OFF the render thread and only when due — the render just reads the day file.
    _capture_futures_bg(symbol, data.source, data.fetched_at)
    day_df = _futures_day_cached(date_ist, symbol, fdir)
    since = intraday_futures_buildup(day_df)
    wins = {m: _futures_window(day_df, m) for _, m in _WINDOWS}
    if not since and not any(wins.values()):
        return (f'<div class="card fut"><span class="eyebrow">{flab} futures OI build-up</span>'
                '<p class="fut-desc">Waiting for 2+ futures snapshots this session…</p></div>')
    since_cell = ''
    if since:
        cls, icn, lab = _fut_badge(since.get("buildup"))
        dp = since.get("dprice"); doi_pct = since.get("doi_pct")
        since_cell = (f'<div class="fw" style="margin-top:8px"><span class="fw-lbl">Since open</span>'
                      f'<span class="fut-badge {cls}">{icn} {lab}</span>'
                      f'<span class="fw-sub mono">Price {"▲" if (dp or 0)>=0 else "▼"}{f(abs(dp)) if dp is not None else "—"} · '
                      f'OI {f(doi_pct,1)+"%" if doi_pct is not None else "—"}</span></div>')
    rows = "".join(_fut_window_row(lbl, m, wins[m], is_expiry) for lbl, m in _WINDOWS)
    cap = ('⚠ Around monthly expiry the short windows churn (rollover) — lean on 30/60-min.'
           if is_expiry else 'Aggregate OI (rollover-netted); volume = intensity, not direction.')
    return f'''<div class="card fut"><span class="eyebrow">{flab} futures OI build-up</span>
      {since_cell}
      <div class="winstrip"><div class="win-hd">Trailing windows · price × ΔOI</div>{rows}
        <div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div></div>'''


if __name__ == "__main__":
    main()
