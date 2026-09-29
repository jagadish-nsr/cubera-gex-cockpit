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
