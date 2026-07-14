"""Build a self-contained HTML equity-curve chart from strategy_curves.json (scripts/strategy_backtest).
Embeds the data inline (Artifact-safe: no external fetch). Writes to the scratchpad."""
from __future__ import annotations
import json, sys
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\navee\AppData\Local\Temp\claude\C--Users-navee-Python-gex-indian-gex"
    r"\eb204e31-8169-4259-8f28-affcbf0aa699\scratchpad\strategy_curves.json")
OUT = SRC.with_name("strategy_chart.html")

d = json.loads(SRC.read_text(encoding="utf-8"))
curves = d["curves"]

META = {
    "S1 Short straddle (every cycle)":     {"code": "S1", "hue": "amber",  "role": "raw VRP — thin & tail-risky"},
    "S2 Short straddle (high IV only)":    {"code": "S2", "hue": "teal",   "role": "the one supported edge — sell rich IV"},
    "S3 Short straddle (SIDEWAYS regime)": {"code": "S3", "hue": "rose",   "role": "LOSES — high-vanna = low IV = wrong time"},
    "S4 ORB breakout (directional)":       {"code": "S4", "hue": "violet", "role": "≈ market drift, not an ORB edge"},
    "S5 Buy & hold Nifty":                 {"code": "S5", "hue": "sky",    "role": "benchmark — just own the index"},
}

def summary(eq, dates):
    peak = eq[0]; mdd = 0.0
    for v in eq:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak)
    yrs = (int(dates[-1][:4]) + int(dates[-1][5:7])/12) - (int(dates[0][:4]) + int(dates[0][5:7])/12)
    cagr = (eq[-1] / eq[0]) ** (1 / max(yrs, 0.5)) - 1
    trades = sum(1 for i in range(1, len(eq)) if eq[i] != eq[i-1])
    return {"final": eq[-1], "cagr": cagr*100, "mdd": mdd*100, "trades": trades,
            "start": dates[0], "end": dates[-1]}

def thin(dates, eq, step):
    idx = list(range(0, len(eq), step))
    if idx[-1] != len(eq)-1: idx.append(len(eq)-1)
    return [dates[i] for i in idx], [eq[i] for i in idx]

series = []
for name, c in curves.items():
    eq, dates = c["equity"], c["dates"]
    if not eq: continue
    s = summary(eq, dates)
    step = 5 if len(eq) > 400 else 1                # weekly-ish for the daily series
    td, te = thin(dates, eq, step)
    m = META[name]
    series.append({"name": name, **m, "summary": s,
                   "pts": [[dt, round(v)] for dt, v in zip(td, te)]})

payload = json.dumps({"series": series}, separators=(",", ":"))

HTML = r"""<title>Nifty strategy backtest — equity curves</title>
<style>
:root{
  --bg:#0d1017; --panel:#141a22; --panel2:#1a212b; --ink:#e7edf4; --ink2:#aeb9c6; --mut:#6d7a8a;
  --line:#232c38; --grid:#1c242f; --base:#8a97a6;
  --teal:#16c8b0; --amber:#f0a63a; --rose:#f6607f; --violet:#a78bfa; --sky:#38b6f0;
}
@media (prefers-color-scheme: light){:root{
  --bg:#eef2f6; --panel:#ffffff; --panel2:#f4f7fa; --ink:#141b24; --ink2:#3f4c5a; --mut:#75828f;
  --line:#e2e8ef; --grid:#eceff3; --base:#9aa6b3;
  --teal:#0c9a86; --amber:#c07d16; --rose:#e0335a; --violet:#7c5cf0; --sky:#0f86c4;
}}
:root[data-theme="dark"]{
  --bg:#0d1017; --panel:#141a22; --panel2:#1a212b; --ink:#e7edf4; --ink2:#aeb9c6; --mut:#6d7a8a;
  --line:#232c38; --grid:#1c242f; --base:#8a97a6;
  --teal:#16c8b0; --amber:#f0a63a; --rose:#f6607f; --violet:#a78bfa; --sky:#38b6f0;
}
:root[data-theme="light"]{
  --bg:#eef2f6; --panel:#ffffff; --panel2:#f4f7fa; --ink:#141b24; --ink2:#3f4c5a; --mut:#75828f;
  --line:#e2e8ef; --grid:#eceff3; --base:#9aa6b3;
  --teal:#0c9a86; --amber:#c07d16; --rose:#e0335a; --violet:#7c5cf0; --sky:#0f86c4;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto;padding:34px 22px 56px}
.eyebrow{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.18em;
  text-transform:uppercase;color:var(--mut)}
h1{font-size:27px;line-height:1.15;margin:12px 0 6px;font-weight:680;text-wrap:balance;letter-spacing:-.01em}
.lede{color:var(--ink2);font-size:15px;max-width:65ch;margin:0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px}
.tbl{margin:26px 0 8px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:560px}
th{font:600 10.5px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;
  color:var(--mut);text-align:right;padding:0 14px 10px;border-bottom:1px solid var(--line)}
th.l,td.l{text-align:left}
td{padding:11px 14px;border-bottom:1px solid var(--grid);font-size:13.5px}
tr:last-child td{border-bottom:none}
.chip{display:inline-flex;align-items:center;gap:9px;font-weight:600}
.dot{width:11px;height:11px;border-radius:3px;flex:none;box-shadow:0 0 0 3px color-mix(in srgb,var(--c) 18%,transparent)}
.role{color:var(--mut);font-size:12px}
.num{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;text-align:right}
.pos{color:var(--teal)} .neg{color:var(--rose)}
.chartcard{margin-top:22px;padding:18px 16px 10px}
.chart-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:0 6px 4px}
.chart-h h2{font-size:14px;margin:0;font-weight:640} .chart-h span{color:var(--mut);font-size:12px}
svg{display:block;width:100%;height:auto;touch-action:none}
.gl{stroke:var(--grid);stroke-width:1}
.axl{fill:var(--mut);font:500 11px ui-monospace,monospace;font-variant-numeric:tabular-nums}
.baseln{stroke:var(--base);stroke-width:1;stroke-dasharray:3 4;opacity:.8}
.basel{fill:var(--base);font:600 10.5px ui-monospace,monospace;letter-spacing:.04em}
.ser{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ser.hi{stroke-width:3}
.endlab{font:600 11px ui-monospace,monospace;font-variant-numeric:tabular-nums}
.cross{stroke:var(--mut);stroke-width:1;stroke-dasharray:2 3;opacity:0}
.tip{position:absolute;pointer-events:none;background:var(--panel2);border:1px solid var(--line);
  border-radius:9px;padding:9px 11px;font-size:12px;opacity:0;transition:opacity .1s;min-width:172px;
  box-shadow:0 8px 26px rgba(0,0,0,.28)}
.tip .d{color:var(--mut);font:600 10.5px ui-monospace,monospace;letter-spacing:.06em;margin-bottom:6px}
.tip .row{display:flex;justify-content:space-between;gap:14px;padding:1.5px 0}
.tip .row b{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;font-weight:600}
.notes{margin-top:30px;display:grid;gap:12px}
.note{padding:13px 16px;border-left:3px solid var(--c);background:var(--panel);border-radius:0 11px 11px 0}
.note b{font-size:13.5px} .note p{margin:3px 0 0;color:var(--ink2);font-size:13px}
.foot{margin-top:26px;color:var(--mut);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
</style>

<div class="wrap">
  <div class="eyebrow">Nifty GEX engine · situational-analysis findings</div>
  <h1>What actually made money — and what didn't</h1>
  <p class="lede">Five strategies drawn from the study set, each compounding a ₹10,000 stake over five
  years of 1-minute Nifty data. The findings killed every <em>directional</em> idea; the only real edge
  is selling premium <em>when implied vol is rich</em> — and the GEX/vanna regime, which forecasts vol,
  does <em>not</em> translate into a good straddle-timing rule.</p>

  <div class="tbl card" style="padding:6px 6px 2px">
    <table id="tbl">
      <thead><tr>
        <th class="l">Strategy</th><th>Final (₹10k→)</th><th>CAGR</th><th>Max drawdown</th>
        <th>Trades</th><th class="l" style="padding-left:22px">What it is</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="chartcard card">
    <div class="chart-h"><h2>Equity curve · ₹10,000 → </h2><span id="span"></span></div>
    <div style="position:relative">
      <svg id="svg" viewBox="0 0 920 460" preserveAspectRatio="none" aria-label="Equity curves"></svg>
      <div class="tip" id="tip"></div>
    </div>
  </div>

  <div class="notes" id="notes"></div>

  <div class="foot">Backtest is <b>gross</b> of real slippage, liquidity, and margin-call risk; short-vol
  curves carry a fat left tail (see max drawdown). Sizing: straddles deploy 50% of equity on ~12% margin,
  2%/premium cost; ORB unlevered. One straddle per weekly cycle, held to expiry. <b>Sign-free</b> and
  decision-support only — not financial advice; you confirm every trade.</div>
</div>

<script>
const HUE={amber:'--amber',teal:'--teal',rose:'--rose',violet:'--violet',sky:'--sky'};
const CV=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const D=__DATA__;
const S=D.series;
const inr=n=>'₹'+Math.round(n).toLocaleString('en-IN');
const pct=n=>(n>=0?'+':'')+n.toFixed(1)+'%';
const ts=s=>{const[y,m,d]=s.split('-').map(Number);return Date.UTC(y,m-1,d);};

/* ---- summary table ---- */
const tb=document.querySelector('#tbl tbody');
S.forEach(s=>{const q=s.summary,c=CV(HUE[s.hue]);
  tb.insertAdjacentHTML('beforeend',`<tr>
    <td class="l"><span class="chip"><span class="dot" style="--c:${c};background:${c}"></span>
      <span>${s.code} · ${s.name.replace(/^S\d\s/,'')}</span></span></td>
    <td class="num">${inr(q.final)}</td>
    <td class="num ${q.cagr>=0?'pos':'neg'}">${pct(q.cagr)}</td>
    <td class="num">${q.mdd.toFixed(1)}%</td>
    <td class="num">${q.trades}</td>
    <td class="l role" style="padding-left:22px">${s.role}</td></tr>`);});
document.querySelector('#span').textContent=S[0].summary.start+'  →  '+S[0].summary.end;

/* ---- notes ---- */
const NOTES=[
 ['teal','S2 is the only finding-backed edge','Sell the straddle only when implied vol is in its top third. +12% CAGR at half the drawdown of selling blindly — the classic variance-risk premium, richest exactly when IV is high. This is “sell expensive insurance”, not a GEX signal.'],
 ['rose','S3 loses — the regime is not a timing rule','Selling in the SIDEWAYS (high-vanna) regime loses money. High vanna ≈ low IV, so you collect thin premium at the wrong time. The vanna gauge forecasts realised vol (Study #17), but IV already prices it — you can’t monetise it by selling straddles.'],
 ['amber','S1 barely beats cash, with a 48% drawdown','Selling every week is real but thin net of costs and brutally tail-risky. Short vol without an IV filter is not worth the ride.'],
 ['violet','S4 is market drift, not an ORB edge','The opening-range breakout looks profitable only because a rising index breaks up more often — it tracks buy-and-hold. Study #16 removed the drift and the ORB-specific signal was a coin flip. Lower drawdown only because it sits in cash overnight.'],
];
const nd=document.querySelector('#notes');
NOTES.forEach(([h,t,p])=>nd.insertAdjacentHTML('beforeend',
  `<div class="note" style="--c:${CV(HUE[h])}"><b>${t}</b><p>${p}</p></div>`));

/* ---- chart ---- */
const W=920,H=460,L=52,R=64,T=18,B=30;
let xmin=1e18,xmax=-1e18,ymin=1e18,ymax=-1e18;
S.forEach(s=>s.pts.forEach(([d,v])=>{const x=ts(d);
  xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,v);ymax=Math.max(ymax,v);}));
ymin=Math.min(ymin,10000);ymax=Math.max(ymax,10000);
ymin=Math.floor(ymin/1000)*1000;ymax=Math.ceil(ymax/1000)*1000;
const X=x=>L+(x-xmin)/(xmax-xmin)*(W-L-R);
const Y=v=>T+(1-(v-ymin)/(ymax-ymin))*(H-T-B);
const svg=document.querySelector('#svg');
const NS='http://www.w3.org/2000/svg';
const el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};

for(let v=ymin;v<=ymax;v+=2000){const y=Y(v);
  svg.appendChild(el('line',{class:'gl',x1:L,y1:y,x2:W-R,y2:y}));
  const tx=el('text',{class:'axl',x:L-8,y:y+3.5,'text-anchor':'end'});tx.textContent=(v/1000)+'k';svg.appendChild(tx);}
const y0=Y(10000);
svg.appendChild(el('line',{class:'baseln',x1:L,y1:y0,x2:W-R,y2:y0}));
const bl=el('text',{class:'basel',x:L+3,y:y0-5});bl.textContent='₹10,000 start';svg.appendChild(bl);
for(let yr=2022;yr<=2026;yr++){const x=X(Date.UTC(yr,0,1));if(x<L||x>W-R)continue;
  svg.appendChild(el('line',{class:'gl',x1:x,y1:T,x2:x,y2:H-B}));
  const tx=el('text',{class:'axl',x:x,y:H-B+16,'text-anchor':'middle'});tx.textContent=yr;svg.appendChild(tx);}

S.forEach(s=>{const c=CV(HUE[s.hue]);
  const dpath=s.pts.map(([d,v],i)=>(i?'L':'M')+X(ts(d)).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
  svg.appendChild(el('path',{class:'ser'+(s.code==='S2'?' hi':''),d:dpath,stroke:c,
    opacity:(s.code==='S4'||s.code==='S5')?0.85:1}));
  const last=s.pts[s.pts.length-1];
  svg.appendChild(el('circle',{cx:X(ts(last[0])),cy:Y(last[1]),r:s.code==='S2'?3.6:2.8,fill:c}));
  const lt=el('text',{class:'endlab',x:X(ts(last[0]))+6,y:Y(last[1])+3.6,fill:c});
  lt.textContent=s.code;svg.appendChild(lt);});

/* ---- hover crosshair + tooltip ---- */
const cross=el('line',{class:'cross',y1:T,y2:H-B});svg.appendChild(cross);
const tip=document.querySelector('#tip');
const flat=S.map(s=>({code:s.code,name:s.name.replace(/^S\d\s/,''),hue:CV(HUE[s.hue]),pts:s.pts}));
function near(px,arr){let best=arr[0],bd=1e18;for(const p of arr){const d=Math.abs(X(ts(p[0]))-px);if(d<bd){bd=d;best=p;}}return best;}
svg.addEventListener('pointermove',ev=>{
  const r=svg.getBoundingClientRect();const px=(ev.clientX-r.left)/r.width*W;
  if(px<L||px>W-R){cross.style.opacity=0;tip.style.opacity=0;return;}
  cross.setAttribute('x1',px);cross.setAttribute('x2',px);cross.style.opacity=.9;
  const anchor=near(px,flat[4].pts);
  let rows='';flat.forEach(s=>{const p=near(px,s.pts);
    rows+=`<div class="row"><span style="color:${s.hue}">${s.code} ${s.name}</span><b>${inr(p[1])}</b></div>`;});
  tip.innerHTML=`<div class="d">${anchor[0]}</div>${rows}`;tip.style.opacity=1;
  let tx=ev.clientX-r.left+16;if(tx>r.width-186)tx=ev.clientX-r.left-186;
  tip.style.left=tx+'px';tip.style.top=Math.max(4,ev.clientY-r.top-10)+'px';});
svg.addEventListener('pointerleave',()=>{cross.style.opacity=0;tip.style.opacity=0;});
</script>
"""

OUT.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
print("wrote", OUT, "bytes:", OUT.stat().st_size)
