"""Render a session-report document to one standalone HTML page."""

from __future__ import annotations

import html
from typing import Any


def render_session_report(doc: dict[str, Any]) -> str:
    title = _esc(doc.get("title") or "Session report")
    sections = [
        _header(doc),
        _scorecard(doc.get("scorecard") or []),
        _cash(doc.get("cash_path") or {}),
        _indices(doc.get("indices") or []),
        _recommendations(doc.get("recommendations") or {}),
        _commentary(doc.get("commentary") or {}),
        _sentinel(doc.get("sentinel") or {}),
        _prior(doc.get("prior") or []),
        _notes(doc.get("notes") or []),
        f"<p class='caveat'>{_esc(doc.get('caveat') or '')}</p>",
    ]
    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #070b12; --surface-1: #0c1424; --surface-2: #111d33;
      --line: rgba(56, 189, 248, 0.18); --ink: #f8fafc; --ink-mute: #94a3b8;
      --spark: #38bdf8; --go: #22c55e; --warn: #f59e0b; --stop: #ef4444;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
    }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: var(--font); }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }}
    h1 {{ font-size: 1.55rem; margin: 0 0 6px; }}
    h2 {{ font-size: 1.05rem; margin: 28px 0 10px; color: var(--spark); }}
    p.meta, p.lead {{ color: var(--ink-mute); margin: 0 0 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .card {{ background: var(--surface-1); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }}
    .card small {{ display: block; color: var(--ink-mute); font-size: 0.75rem; letter-spacing: 0.04em; text-transform: uppercase; }}
    .card b {{ display: block; margin: 6px 0 4px; font-size: 1.05rem; }}
    .card span {{ color: var(--ink-mute); font-size: 0.82rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--ink-mute); font-weight: 600; font-size: 0.75rem; letter-spacing: 0.04em; text-transform: uppercase; }}
    td.num, th.num {{ text-align: right; font-family: var(--mono); }}
    ul {{ margin: 8px 0 0; padding-left: 18px; }}
    li {{ margin: 6px 0; }}
    .caveat {{ margin-top: 28px; color: var(--warn); }}
    .empty {{ color: var(--ink-mute); }}
  </style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _header(doc: dict) -> str:
    return (
        f"<h1>{_esc(doc.get('title'))}</h1>"
        f"<p class='meta'>Generated {_esc(doc.get('generated_at'))}. "
        f"Session {_esc(doc.get('session_date'))}.</p>"
        f"<p class='lead'>{_esc(doc.get('summary'))}</p>"
    )


def _scorecard(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        cards.append(
            "<div class='card'>"
            f"<small>{_esc(row.get('label'))}</small>"
            f"<b>{_esc(row.get('value'))}</b>"
            f"<span>{_esc(row.get('detail'))}</span>"
            "</div>"
        )
    return "<h2>Scorecard</h2><div class='cards'>" + "".join(cards) + "</div>"


def _cash(cash: dict) -> str:
    if not cash.get("present"):
        return "<h2>Cash path</h2><p class='empty'>Nifty 15-minute series is not on disk for this date.</p>"
    head = (
        "<h2>Nifty cash path</h2>"
        "<p class='lead'>15-minute prints from the timeseries file. "
        f"Book at the last print: {_esc(_book_words(cash.get('close_regime')))}. "
        f"Call wall {_px(cash.get('call_wall_close'))}, put wall {_px(cash.get('put_wall_close'))}, "
        f"flip {_px(cash.get('flip_close'))}, magnet {_px(cash.get('magnet_close'))}, "
        f"max pain {_px(cash.get('max_pain_close'))}. "
        f"India VIX {_num(cash.get('vix_open'))} to {_num(cash.get('vix_close'))}.</p>"
    )
    body = [
        "<tr><th>Time</th><th class='num'>Spot</th><th>Book</th><th class='num'>Call wall</th>"
        "<th class='num'>Put wall</th><th class='num'>Flip</th></tr>"
    ]
    for row in cash.get("prints") or []:
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('time'))}</td>"
            f"<td class='num'>{_px(row.get('spot'))}</td>"
            f"<td>{_esc(_book_words(row.get('regime')))}</td>"
            f"<td class='num'>{_px(row.get('call_wall'))}</td>"
            f"<td class='num'>{_px(row.get('put_wall'))}</td>"
            f"<td class='num'>{_px(row.get('flip'))}</td>"
            "</tr>"
        )
    return head + "<table>" + "".join(body) + "</table>"


def _indices(rows: list[dict]) -> str:
    if not rows:
        return ""
    body = [
        "<h2>Index prints</h2>",
        "<table><tr><th>Index</th><th>When</th><th class='num'>Spot</th><th>Book</th>"
        "<th class='num'>Call wall</th><th class='num'>Put wall</th><th class='num'>Flip</th>"
        "<th>Settlement</th></tr>",
    ]
    for row in rows:
        settle = "—"
        if row.get("settled"):
            settle = (
                f"{_px(row.get('settlement_spot'))} ({_pts(row.get('net_change_pts'))} pts, "
                f"{_signed_pct(row.get('net_change_pct'))})"
            )
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('symbol'))}</td>"
            f"<td>{_esc(row.get('snapshot_role'))} {_esc(row.get('snapshot_ist'))}</td>"
            f"<td class='num'>{_px(row.get('spot'))}</td>"
            f"<td>{_esc(row.get('book'))}</td>"
            f"<td class='num'>{_px(row.get('call_wall'))}</td>"
            f"<td class='num'>{_px(row.get('put_wall'))}</td>"
            f"<td class='num'>{_px(row.get('gamma_flip'))}</td>"
            f"<td>{_esc(settle)}</td>"
            "</tr>"
        )
    body.append("</table>")
    return "".join(body)


def _recommendations(block: dict) -> str:
    total = int(block.get("total") or 0)
    head = f"<h2>Recommendations</h2><p class='lead'>{total} recorded."
    if block.get("first_at"):
        head += f" First {_esc(block.get('first_at'))}. Last {_esc(block.get('last_at'))}."
    head += "</p>"
    if not total:
        return head + "<p class='empty'>No recommendation rows for this date.</p>"
    body = ["<table><tr><th>Symbol</th><th>Side</th><th>Status</th><th class='num'>Count</th></tr>"]
    for row in block.get("by_side_status") or []:
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('symbol'))}</td>"
            f"<td>{_esc(row.get('side'))}</td>"
            f"<td>{_esc(row.get('status'))}</td>"
            f"<td class='num'>{int(row.get('n') or 0)}</td>"
            "</tr>"
        )
    body.append("</table>")
    events = block.get("events") or []
    if events:
        bits = ", ".join(f"{int(e.get('n') or 0)} {e.get('event_type')}" for e in events)
        body.append(f"<p class='lead'>Lifecycle events this date: {_esc(bits)}.</p>")
    else:
        body.append("<p class='lead'>No lifecycle events were written this date.</p>")
    return head + "".join(body)


def _commentary(block: dict) -> str:
    n = int(block.get("entries") or 0)
    head = (
        f"<h2>Commentary</h2><p class='lead'>{n} entries. "
        f"{int(block.get('events') or 0)} rows in the events table.</p>"
    )
    if not n:
        return head
    items = []
    for row in block.get("headlines") or []:
        items.append(
            f"<li><b>{_esc(row.get('time'))}</b> {_esc(row.get('symbol'))} — {_esc(row.get('headline'))}</li>"
        )
    return head + "<ul>" + "".join(items) + "</ul>"


def _sentinel(block: dict) -> str:
    n = int(block.get("signals") or 0)
    head = (
        "<h2>Sentinel</h2>"
        f"<p class='lead'>{n} signals. {int(block.get('bars_1m') or 0)} one-minute bars in sentinel.db.</p>"
    )
    if not n:
        return head + "<p class='empty'>No signal rows for this date.</p>"
    edge = block.get("edge_ratio")
    edge_txt = f"{edge:.2f}" if isinstance(edge, (int, float)) else "—"
    head += (
        f"<p class='lead'>Target hits {int(block.get('target_hits') or 0)}. "
        f"Hit rate {block.get('hit_rate_pct')}%. "
        f"Average MFE {_pts(block.get('avg_mfe'))} pts. "
        f"Average MAE {_pts(block.get('avg_mae'))} pts. "
        f"MFE/MAE {edge_txt}.</p>"
    )
    body = ["<table><tr><th>Instrument</th><th>Type</th><th>Bias</th><th class='num'>Signals</th><th class='num'>Target hits</th></tr>"]
    for row in block.get("by_type") or []:
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('instrument_id'))}</td>"
            f"<td>{_esc(row.get('type'))}</td>"
            f"<td>{_esc(row.get('bias'))}</td>"
            f"<td class='num'>{int(row.get('n') or 0)}</td>"
            f"<td class='num'>{int(row.get('hits') or 0)}</td>"
            "</tr>"
        )
    body.append("</table>")
    return head + "".join(body)


def _prior(rows: list[dict]) -> str:
    if not rows:
        return "<h2>Prior sessions</h2><p class='empty'>No earlier Nifty timeseries files.</p>"
    body = [
        "<h2>Prior sessions</h2>",
        "<p class='lead'>Same stores. Nifty change is the last 15-minute print minus the first.</p>",
        "<table><tr><th>Date</th><th class='num'>Sample change</th><th class='num'>Prints</th>"
        "<th class='num'>Recommendations</th><th class='num'>Commentary</th><th class='num'>Sentinel signals</th></tr>",
    ]
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('session_date'))}</td>"
            f"<td class='num'>{_pts(row.get('sample_change_pts'))}</td>"
            f"<td class='num'>{int(row.get('intervals') or 0)}</td>"
            f"<td class='num'>{int(row.get('recommendations') or 0)}</td>"
            f"<td class='num'>{int(row.get('commentary') or 0)}</td>"
            f"<td class='num'>{int(row.get('sentinel_signals') or 0)}</td>"
            "</tr>"
        )
    body.append("</table>")
    return "".join(body)


def _notes(notes: list[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{_esc(note)}</li>" for note in notes)
    return "<h2>Recorded limits</h2><ul>" + items + "</ul>"


def _book_words(regime: str | None) -> str:
    text = str(regime or "")
    if text.upper() == "POSITIVE":
        return "call-heavy"
    if text.upper() == "NEGATIVE":
        return "put-heavy"
    return text or "—"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _px(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"₹{value:,.2f}"


def _pts(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:+,.2f}"


def _num(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:,.2f}"


def _signed_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:+.2f}%"
