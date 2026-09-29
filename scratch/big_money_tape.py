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
    grids = (_bm_buildup_grid_html(readout, "time", "15-min") + _bm_buildup_grid_html(readout, "time3", "3-min")
             + _bm_burst_scatter_html(readout) + _bm_print_scatter_html(readout))
    if replay:
        return head + _bm_day_readout_html(readout) + grids + table + cav + '</div>'
    return (head + _bm_flow_html(day) + _bm_rollover_html() + _bm_day_readout_html(readout)
            + grids + table + _bm_status_html(status, day) + cav + '</div>')


_BLAST_TICK_S = 10          # spot-lane resolution for timing the crossover
