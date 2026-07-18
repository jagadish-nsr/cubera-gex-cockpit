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
.simp .vc-val{font-weight:700}
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


def main():
    with st.sidebar:
        st.header("Simple view")
        symbols = list(config.INDICES)
        symbol = st.selectbox("Index", symbols, index=symbols.index(config.DEFAULT_INDEX),
                              format_func=lambda s: config.INDICES[s].label)
        use_demo = st.checkbox("Demo data (offline)", value=False)
        refresh = st.button("🔄 Refresh", type="primary", width="stretch")
        auto = st.checkbox("⏱️ Auto-refresh", value=False)
        auto_mins = st.selectbox("Interval", [1, 3, 5], index=1, format_func=lambda m: f"{m} min",
                                 disabled=not auto)
        _dhan_feed_controls(symbol)
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
        st.caption("A simple, paper-only face of the GEX engine. See docs/simple-dashboard.md.")

    st.markdown(CSS, unsafe_allow_html=True)
    idx = config.get_index(symbol)

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


def _render_live(symbol, use_demo, refresh, auto, auto_mins, preview_expiry, anchor_now, idx):
    if auto and not use_demo:
        # AUTO: a background thread does the ~12s pull off the render path; here we only READ the
        # latest published snapshot (instant, no network → no page dim). First paint (nothing
        # published yet AND no prior data) falls back to one blocking fetch so the page isn't empty.
        from app import live_fetcher
        live_fetcher.ensure(symbol, use_demo, None, int(auto_mins * 60), _FETCH_LEAD_S)
        pub = live_fetcher.latest(symbol)
        if pub and pub.get("data") is not None:
            st.session_state["simp_data"] = pub["data"]
            st.session_state["simp_asof"] = pub["as_of"]
            st.session_state["simp_err"] = pub.get("err")
        elif refresh or st.session_state.get("simp_data") is None:
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
    _feed = ("🟢 Dhan (live)" if _src == "dhan"
             else "🟡 NSE (Dhan fallback)" if _src.startswith("nse (Dhan")
             else "🔵 NSE" if _src == "nse" else _src)
    _sched = (f"aligned to the {auto_mins:g}-min grid · as of {asof:%H:%M} IST" if auto
              else "manual refresh")
    st.caption(f"Feed: {_feed} · {_sched} · fetched {ist:%H:%M:%S}")
    if getattr(data, "source_note", None):
        st.caption(f"⚠ {data.source_note}")
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist.date()) if nearest else False
    show_blast = is_expiry or preview_expiry          # preview forces the blast view (dev)
    if anchor_now:
        st.session_state["simp_force_anchor"] = True

    # Self-sufficient capture: persist this pull (timeseries + per-strike OI) BEFORE the
    # range seed / OI windows read the day, so this app feeds its own history without the
    # logger. min_gap dedups rapid refreshes and any co-running writer (shared day file).
    save_gap = max(1.0, auto_mins - 1.0) if auto else 2.0
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
    hl = st.session_state.get("simp_hl")
    if hl is None or hl.get("day") != date_ist:
        from app.timeseries import oistrikes_dir_for
        hl = _seed_hl(symbol, date_ist, L.spot, oistrikes_dir_for(data.source))
        st.session_state["simp_hl"] = hl
    hl["lo"] = min(hl["lo"], L.spot); hl["hi"] = max(hl["hi"], L.spot)

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
      <div class="val mono">{f(L.spot)}</div><div class="chg mono">{vtxt}</div></div>
      <div class="mood"><span class="chip {mood_cls}"><span class="dot"></span>{mood_txt}</span>
      <div class="range">{rng_line}</div></div></div>''')

    # today's range so far — vertical LIVE candle + level ladder on one price axis
    oh = _resolve_ohlc(data, hl, L.spot)
    actual = oh["h"] - oh["l"]
    spot_pos = ((L.spot - oh["l"]) / actual * 100.0) if actual > 0 else 50.0
    candle = _range_candle(L, oh)
    read = f'{f(actual)} pts · spot {spot_pos:.0f}% up range'
    src_note = ("true day OHLC (Dhan)" if data.day_ohlc
                else "≈ from snapshots — can miss fast spikes")

    # news override
    news = data.news or {}
    if news.get("impact", 0) and news["impact"] >= 5:
        top = (news.get("top") or "").strip()
        H.append(f'<div class="news"><span>📰</span><div class="txt"><b>Big news day.</b> '
                 f'A headline is driving the market — trust the news over these levels today.'
                 f'{" <i>“"+top[:90]+"”</i>" if top else ""}</div></div>')

    # ── 2-column deck: LEFT = the tall live candle vs levels, RIGHT = positioning + the play ──
    H.append('<div class="grid2"><div class="col">')
    H.append(f'''<div class="candlecard card"><div class="rb-top"><span class="eyebrow">Today — live candle vs levels</span>
      <span class="rb-vals mono">{f(oh["l"])} – {f(oh["h"])}</span></div>{candle}
      <div class="cc-read">{read}</div>
      <span class="rb-cav">{src_note}. Hover a level for its value. Stops <i>just beyond</i> a wall — never at the magnet.</span></div>''')

    # right column: positioning first, then the suggested play beneath it
    H.append('</div><div class="col">')
    H.append('<h2 class="lbl">Where the money is building</h2>')
    H.append(_footprint_html(data, L, idx, ist, is_expiry))
    H.append(_futures_html(data, symbol, ist, is_expiry))
    H.append('<h2 class="lbl">Suggested play</h2>')
    H.append(_blast_hero(data, L, ist) if show_blast else _play_card(vr, data, L, idx))
    H.append('</div></div>')                          # close right col + grid

    H.append(f'<div class="foot">A map and a logbook, not an oracle. <b>Paper trading only</b> — '
             f'you confirm every trade.<br><span class="mono" style="opacity:.7">lot {idx.lot_size} · '
             f'source {data.source} · {ist:%H:%M IST}</span></div>')
    H.append('</div>')
    st.markdown("\n".join(H), unsafe_allow_html=True)


def _blast_hero(data, L, ist):
    """Expiry-day gamma-blast verdict, reusing app.gammablast with session-accumulated series."""
    try:
        from app.gammablast import (blast_score, crossover_state, detect_crossovers,
                                    next_strike, pick_anchors, strike_oi, strike_premiums)
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
    bl["points"][ist.strftime("%H:%M")] = {"atm_call": ac, "atm_put": ap, "magnet_call": mc,
                                           "magnet_put": mp, "atm_coi": aco, "atm_poi": apo}
    series = [{"time_ist": t, "call": v.get("atm_call"), "put": v.get("atm_put")}
              for t, v in sorted(bl["points"].items())]
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
    win = [(t, v) for t, v in sorted(bl["points"].items()) if t >= last["time_ist"]]
    crossed = [{"call_oi": v.get("atm_coi"), "put_oi": v.get("atm_poi"),
                "call_ltp": v.get("atm_call"), "put_ltp": v.get("atm_put")} for _, v in win]
    ahead = None
    nb = next_strike(data.oi_strikes, K, direction)
    if nb is not None:
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
    def _m(t):
        try:
            h, mm = str(t).split(":"); return int(h) * 60 + int(mm)
        except (ValueError, AttributeError):
            return None
    times = [t for t, _ in win]
    traj = [(blast_score(direction, crossed[: j + 1], ahead) or {}).get("score", 0)
            for j in range(len(crossed))]
    t0 = _m(times[0]) if times else None
    since = max(0, (ist.hour * 60 + ist.minute) - t0) if t0 is not None else None
    k_true_min = next((_m(times[j]) - t0 for j, s in enumerate(traj)
                       if s >= 60 and t0 is not None), None)
    if k_true_min is not None and k_true_min <= 5:
        tcls, tlab, tnote = "bull", f"⚡ Fast TRUE (+{k_true_min}m)", "Best setup — ignited within 5 min of the cross."
    elif k_true_min is not None:
        tcls, tlab, tnote = "cover", f"Slow TRUE (+{k_true_min}m)", "Confirmed but late — weaker than a fast ignition."
    elif since is not None and since <= 5:
        tcls, tlab, tnote = "flat", f"Watching (+{since}m)", "Needs score ≥45 by ~3 min to likely reach TRUE."
    else:
        tcls, tlab, tnote = "bear", f"No ignition (+{since}m)", "No TRUE within 5 min — usually fizzles; lean fade."
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
      <p class="instruct">{instr}</p>
      <div class="meter"><div class="head"><span>True-run score</span>
        <span><b class="mono">{score}</b> / 100 · {grade}</span></div>
        <div class="track"><div class="fill" style="width:{max(3,min(score,100))}%;background:{fillc}"></div></div>
        <div class="ticks"><span>Fake / pin</span><span>Toss-up</span><span>Real run</span></div></div>
      {timing_html}
      <div class="blast-stats">
        <div><small>{sc.get("itm_side","")}-writer OI change</small>
          <b class="mono">{f(cover*100,0) if cover is not None else "—"}%</b><em>−ve = covering (fuel)</em></div>
        <div><small>Wall ahead</small><b class="down mono">{f(wr,1)+"×" if wr is not None else "—"}</b><em>cap overhead</em></div>
      </div>{chart}</div>'''


def _resolve_ohlc(data, hl, spot):
    """Today's candle OHLC. Prefers Dhan's TRUE day OHLC (accurate high/low); falls back to the
    snapshot-seeded low/high + open with the live spot as the running close. Unions the live spot
    so an in-progress candle never clips it."""
    d = data.day_ohlc or {}
    o, h, lo, c = d.get("open"), d.get("high"), d.get("low"), d.get("close")
    o = o if o is not None else hl.get("open", spot)
    h = h if h is not None else hl["hi"]
    lo = lo if lo is not None else hl["lo"]
    c = c if c is not None else spot
    h = max(h, o, c, spot)
    lo = min(lo, o, c, spot)
    return {"o": float(o), "h": float(h), "l": float(lo), "c": float(c)}


def _range_candle(L, ohlc):
    """A TALL vertical LIVE daily candle (left) + a right-hand level ladder (call/put wall, magnet,
    gamma flip, expiry pin, spot) sharing ONE price axis — so a level's height lines up with where
    price sits on the candle. Hover a level to emphasise its line (pure CSS, no JS). Labels dodge
    so clustered levels stay legible."""
    o, h, lo, c = ohlc["o"], ohlc["h"], ohlc["l"], ohlc["c"]
    W, HT, top, plot_h = 330, 520, 30, 458
    cx, bw, line_x0, ladder_x, label_x = 76, 34, 46, 152, 158
    lv_specs = [
        ("Call wall", L.call_wall, "var(--stop)", False, "resistance"),
        ("Expiry pin", L.max_pain, "var(--warn)", True, "max pain"),
        ("Magnet", L.magnet, "var(--spark)", False, "pull"),
        ("Gamma flip", L.flip, "var(--ink-mute)", False, "γ sign flip"),
        ("Put wall", L.put_wall, "var(--go)", False, "support"),
    ]
    prices = [h, lo, o, c] + [p for _, p, *_ in lv_specs if p is not None]
    pmin, pmax = min(prices), max(prices)
    pad = (pmax - pmin) * 0.07 or 1.0
    pmin -= pad
    pmax += pad

    def Y(v):
        return top + (pmax - v) / (pmax - pmin) * plot_h

    p = [f'<svg viewBox="0 0 {W} {HT}" role="img" aria-label="daily candle with GEX levels">']
    # level lines + dodged labels FIRST, so the candle draws cleanly on top of them
    lv = [{"name": n, "px": pr, "col": cc, "dash": dd, "sub": ss}
          for n, pr, cc, dd, ss in lv_specs if pr is not None]
    lv.append({"name": "Spot", "px": L.spot, "col": "var(--ink)", "dash": False, "sub": "now"})
    lv.sort(key=lambda d: -d["px"])
    prev = -1e9
    for d in lv:
        d["yv"] = Y(d["px"])
        d["ly"] = max(d["yv"], prev + 17.0)     # dodge labels down so clustered levels stay legible
        prev = d["ly"]
    for d in lv:
        dash = ' stroke-dasharray="4 3"' if d["dash"] else ''
        p.append(f'<g class="vc-lvl"><title>{d["name"]} {f(d["px"])} · {d["sub"]}</title>'
                 f'<line class="vc-line" x1="{line_x0}" y1="{d["yv"]:.1f}" x2="{ladder_x}" '
                 f'y2="{d["yv"]:.1f}" style="stroke:{d["col"]}" stroke-width="1.2"{dash}></line>'
                 f'<line class="vc-line" x1="{ladder_x}" y1="{d["yv"]:.1f}" x2="{label_x}" '
                 f'y2="{d["ly"]:.1f}" style="stroke:{d["col"]}" stroke-width="1.2"></line>'
                 f'<text class="vc-lbl" x="{label_x+6}" y="{d["ly"]+4:.1f}">'
                 f'<tspan class="vc-val" style="fill:{d["col"]}">{f(d["px"])}</tspan> '
                 f'<tspan>{d["name"]}</tspan></text></g>')
    col = "var(--go)" if c >= o else "var(--stop)"
    yb0, yb1 = Y(max(o, c)), Y(min(o, c))
    p.append(f'<line x1="{cx}" y1="{Y(h):.1f}" x2="{cx}" y2="{Y(lo):.1f}" style="stroke:{col}" '
             f'stroke-width="2.4"></line><rect x="{cx-bw/2}" y="{yb0:.1f}" width="{bw}" '
             f'height="{max(2.0, yb1-yb0):.1f}" rx="3" style="fill:{col};opacity:.9;stroke:{col};stroke-width:1"></rect>')
    ys = Y(L.spot)
    p.append(f'<polygon points="{cx-bw/2-11},{ys:.1f} {cx-bw/2-3},{ys-5:.1f} {cx-bw/2-3},{ys+5:.1f}" '
             f'style="fill:var(--ink)"></polygon></svg>')
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
    if (tag == "calm" and data.source == "nse" and data.oi_strikes
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


def _footprint_html(data, L, idx, ist, is_expiry):
    from app.oi_footprint import build_footprint, oi_weighted_levels, window_footprint
    from app.timeseries import load_strikes_day, oistrikes_dir_for
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
    day_df = load_strikes_day(ist.strftime("%Y-%m-%d"), idx.symbol, directory=_sdir)
    now_hm = ist.hour * 60 + ist.minute
    rows = "".join(_opt_window_row(lbl, m,
                   window_footprint(day_df, m, L.spot, band, now_hm=now_hm,
                                    lot_size=getattr(idx, "lot_size", None)), is_expiry)
                   for lbl, m in _WINDOWS)
    cap = ('⚠ Near expiry the 5/15-min reads churn (settlement) — lean on 30/60-min. @ = top strike.'
           if is_expiry else '₹ turnover (ΔOI×premium×lot), current expiry · @ = strike taking the '
                             'most money that window. Context, not a prediction.')
    strip = (f'<div class="winstrip"><div class="win-hd">Fresh positioning — trailing windows</div>'
             f'{rows}<div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div>')

    # where the biggest ₹ of FRESH writing is landing today (single strike, since open) — user ask
    sow = window_footprint(day_df, 600, L.spot, band, now_hm=now_hm,
                           lot_size=getattr(idx, "lot_size", None))
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

    return f'''<div class="card"><div class="levels">
      <div class="fp-row floor"><span>🟢 Support <small>put OI</small></span><b class="mono">{f(lv.get("support_w"))}</b><small>top {f(lv.get("support_top"))}</small></div>
      <div class="fp-row ceil"><span>🔴 Resistance <small>call OI</small></span><b class="mono">{f(lv.get("resistance_w"))}</b><small>top {f(lv.get("resistance_top"))}</small></div>
      {peak_row}
      <div class="fp-sum">Support/Resistance = OI-weighted centre (validated to hold best) + <b>top</b> single strike. 💰 = strike taking the most fresh money today (writing vs buying read from the premium's direction). Net <b>{who} near spot</b> — {sub}.</div></div>{strip}</div>'''


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
    from app.futures_buildup import (capture_futures_snapshot, intraday_futures_buildup,
                                    load_futures_intraday_day)
    # Futures build-up still sources NSE LIVE futures (fetch_futures_live). Dhan-native futures
    # (scrip-master near-month id + quote OI) is a follow-up; until then this card is NSE-only and
    # simply hides on the Dhan feed rather than showing an empty card.
    if data.source != "nse":
        return ''
    date_ist = ist.strftime("%Y-%m-%d")
    try:                                  # best-effort live snapshot (in-session only), then read the day
        from app.timeseries import in_nse_session
        if in_nse_session(data.fetched_at):   # don't write a frozen post-close row
            capture_futures_snapshot(symbol, min_gap_minutes=3.0)
    except Exception:  # noqa: BLE001
        pass
    day_df = load_futures_intraday_day(date_ist, symbol)
    since = intraday_futures_buildup(day_df)
    wins = {m: _futures_window(day_df, m) for _, m in _WINDOWS}
    if not since and not any(wins.values()):
        return ('<div class="card fut"><span class="eyebrow">Nifty futures OI build-up</span>'
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
    return f'''<div class="card fut"><span class="eyebrow">Nifty futures OI build-up</span>
      {since_cell}
      <div class="winstrip"><div class="win-hd">Trailing windows · price × ΔOI</div>{rows}
        <div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div></div>'''


if __name__ == "__main__":
    main()
