"""app/tab_commentary.py — Session Commentary Engine & Suggestion Bites Stream (Isolated @st.fragment)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
import importlib
import textwrap
import streamlit as st

import commentary.storage.db as _comm_db
try:
    importlib.reload(_comm_db)
except Exception:
    pass

from commentary.config_loader import get_commentary_config
from commentary.storage.db import get_recent_commentary_entries, get_pinned_commentary_cache, clear_commentary_cache
from commentary.story_state import StoryStateManager
from commentary.narrator.verifier import verify_commentary
import commentary.narrator.templates as _narr_templates
try:
    importlib.reload(_narr_templates)
except Exception:
    pass
from commentary.narrator.templates import format_story_entry, render_bite_template, extract_topic_header
from commentary.models import to_ist

logger = logging.getLogger(__name__)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _format_time_ist(ts_str: Any) -> str:
    """Format timestamp into readable IST time (HH:MM:SS AM/PM IST)."""
    try:
        dt_ist = to_ist(ts_str)
        return dt_ist.strftime("%I:%M:%S %p IST")
    except Exception:
        return str(ts_str)


@st.fragment
def render_commentary_tab(symbol: str, window: str = "current_week", data: Any | None = None) -> None:
    """Render Session Commentary & Suggestion Bites in a clean, freeflowing storytelling stream.
    
    Prevents full-app reruns and guarantees non-blocking, zero-latency updates.
    """
    # 1. Evaluate Live Commentary against actual market ticks & GEX levels
    try:
        live_data = data
        if live_data is None:
            try:
                from app.cockpit import _load_data
                live_data, _ = _load_data(symbol, window)
            except Exception:
                from app.data_access import load_dashboard
                valid_win = window if window in ["current_week", "weekly", "next_week", "monthly", "all", "0dte"] else "current_week"
                live_data = load_dashboard(symbol=symbol, window=valid_win)
        
        # Only run live detector evaluation during live market hours or replay simulation.
        # In Mode 2 (Offline Auto-Switch), remain in read-only review of the recorded session.
        from app.cockpit import is_live_session
        if is_live_session() and live_data is not None:
            from commentary.engine import get_session_commentary_engine
            engine = get_session_commentary_engine(symbol)
            recent_existing = get_recent_commentary_entries(symbol, limit=1)
            # Evaluate live ticks; establish baseline entry if stream is currently empty
            engine.evaluate_live(symbol, live_data, force_publish_top=(len(recent_existing) == 0))
    except Exception as exc:
        logger.warning("Session commentary live evaluation error: %s", exc)

    cfg = get_commentary_config()
    bites_cfg = cfg.get("bites", {})
    mode = bites_cfg.get("mode", "shadow").upper()

    # Story State Manager for Intraday Phase
    story_mgr = StoryStateManager()
    now_ist = dt.datetime.now(IST)
    phase_name = story_mgr.determine_phase(now_ist).replace("_", " ").title()

    # 1. Header with Symbol, Phase & Engine Mode
    mode_badge = "🟢 LIVE" if mode == "LIVE" else f"🛡️ {mode} MODE ({bites_cfg.get('min_shadow_sessions_for_promotion', 20)} Session Gating)"
    col_hdr_left, col_hdr_right = st.columns([3, 1])
    with col_hdr_left:
        st.markdown(f"#### 🎙️ {symbol} Session Narrative & Suggestion Bites `⏱️ {phase_name.upper()}`")
    with col_hdr_right:
        st.markdown(
            textwrap.dedent("""
            <div style="text-align:right">
                <a href="/?view=session_commentary_guide" target="_blank" style="font-size:.76rem;background:var(--surface-2);color:var(--spark);padding:4px 8px;border-radius:6px;border:1px solid var(--line);text-decoration:none;font-weight:700">📖 Commentary Guide ↗</a>
            </div>
            """).strip(),
            unsafe_allow_html=True,
        )

    # Inject Vibrant Modern CSS for Session Commentary
    st.markdown(
        textwrap.dedent("""
        <style>
        .comm-stream-wrap {
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-top: 6px;
            margin-bottom: 16px;
        }
        .comm-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.025) 0%, rgba(255, 255, 255, 0.005) 100%);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 13px 17px;
            transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
        }
        .comm-card:hover {
            border-color: rgba(56, 189, 248, 0.35);
            box-shadow: 0 4px 18px -4px rgba(0, 0, 0, 0.4);
            transform: translateY(-1px);
        }
        .comm-card-bullish {
            border-left: 4px solid #10b981;
            background: linear-gradient(90deg, rgba(16, 185, 129, 0.09) 0%, rgba(16, 185, 129, 0.01) 100%);
        }
        .comm-card-bearish {
            border-left: 4px solid #ef4444;
            background: linear-gradient(90deg, rgba(239, 68, 68, 0.09) 0%, rgba(239, 68, 68, 0.01) 100%);
        }
        .comm-card-wall {
            border-left: 4px solid #a855f7;
            background: linear-gradient(90deg, rgba(168, 85, 247, 0.09) 0%, rgba(168, 85, 247, 0.01) 100%);
        }
        .comm-card-warning {
            border-left: 4px solid #f59e0b;
            background: linear-gradient(90deg, rgba(245, 158, 11, 0.09) 0%, rgba(245, 158, 11, 0.01) 100%);
        }
        .comm-card-impulse {
            border-left: 4px solid #0ea5e9;
            background: linear-gradient(90deg, rgba(14, 165, 233, 0.09) 0%, rgba(14, 165, 233, 0.01) 100%);
        }
        .comm-meta-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 7px;
            flex-wrap: wrap;
        }
        .comm-time-badge {
            font-family: var(--mono);
            font-size: 0.74rem;
            font-weight: 700;
            color: #38bdf8;
            background: rgba(56, 189, 248, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.28);
            padding: 2px 8px;
            border-radius: 5px;
        }
        .comm-topic-badge {
            font-size: 0.73rem;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 5px;
            letter-spacing: 0.4px;
            text-transform: uppercase;
        }
        .topic-bullish {
            background: rgba(16, 185, 129, 0.14);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.35);
        }
        .topic-bearish {
            background: rgba(239, 68, 68, 0.14);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.35);
        }
        .topic-wall {
            background: rgba(168, 85, 247, 0.14);
            color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.35);
        }
        .topic-warning {
            background: rgba(245, 158, 11, 0.14);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.35);
        }
        .topic-impulse {
            background: rgba(14, 165, 233, 0.14);
            color: #38bdf8;
            border: 1px solid rgba(14, 165, 233, 0.35);
        }
        .comm-body {
            font-size: 0.90rem;
            line-height: 1.6;
            color: var(--ink);
        }
        .comm-bite-box {
            margin-top: 8px;
            padding: 7px 12px;
            border-radius: 6px;
            background: rgba(0, 0, 0, 0.22);
            border: 1px dashed rgba(255, 255, 255, 0.14);
            font-size: 0.82rem;
            line-height: 1.5;
            display: flex;
            align-items: baseline;
            gap: 8px;
            flex-wrap: wrap;
        }
        .comm-bite-pill {
            font-size: 0.70rem;
            font-weight: 800;
            padding: 2px 7px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            white-space: nowrap;
        }
        .bite-watch {
            background: rgba(245, 158, 11, 0.22);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.45);
        }
        .bite-prepare {
            background: rgba(56, 189, 248, 0.22);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.45);
        }
        .bite-actif {
            background: rgba(99, 102, 241, 0.22);
            color: #a5b4fc;
            border: 1px solid rgba(99, 102, 241, 0.45);
        }
        .bite-triggered {
            background: rgba(16, 185, 129, 0.22);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.45);
        }
        .bite-manage {
            background: rgba(168, 85, 247, 0.22);
            color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.45);
        }
        .bite-standaside {
            background: rgba(148, 163, 184, 0.22);
            color: #cbd5e1;
            border: 1px solid rgba(148, 163, 184, 0.45);
        }
        </style>
        """).strip(),
        unsafe_allow_html=True,
    )

    # 2. Controls & Filtering
    col_filter, col_refresh = st.columns([4, 1], gap="medium")
    with col_filter:
        filter_opt = st.radio(
            "Filter Feed:",
            options=["All Entries", "Actionable Bites Only", "Pinned Recommendations"],
            horizontal=True,
            label_visibility="collapsed",
            key="commentary_feed_filter",
        )
    with col_refresh:
        if st.button("🔄 Refresh Stream", use_container_width=True, key="commentary_refresh_btn"):
            clear_commentary_cache()
            st.rerun(scope="fragment")

    # 3. Pinned Recommendations Alert Strip
    pinned_cache = get_pinned_commentary_cache()
    if pinned_cache:
        for rec_id, note in pinned_cache.items():
            st.info(f"📌 **Pinned {rec_id} Update**: {note}")

    # 4. Resolve Active Session Date (Strictly single trading day: current day if live/today, or last trading day)
    from data.holidays import is_trading_day, roll_back_to_trading_day
    from data.pre_post_market import render_pre_market_commentary_card, render_post_market_commentary_card

    today_str = now_ist.strftime("%Y-%m-%d")
    raw_entries = get_recent_commentary_entries(symbol, limit=100)
    available_dates = sorted(list({to_ist(e["ts"]).strftime("%Y-%m-%d") for e in raw_entries if e.get("ts")}), reverse=True)

    if is_trading_day(now_ist.date()) and (today_str in available_dates or now_ist.hour >= 9):
        active_session_date = today_str
        date_badge_text = f"📅 Today ({now_ist.strftime('%d %b %Y')})"
    elif available_dates:
        active_session_date = available_dates[0]
        try:
            d_obj = dt.datetime.strptime(active_session_date, "%Y-%m-%d")
            date_badge_text = f"📅 Session: {d_obj.strftime('%d %b %Y')} (Last Trading Day)"
        except Exception:
            date_badge_text = f"📅 Session: {active_session_date}"
    else:
        prev_d = roll_back_to_trading_day(now_ist.date() - dt.timedelta(days=1))
        active_session_date = prev_d.strftime("%Y-%m-%d")
        date_badge_text = f"📅 Session: {prev_d.strftime('%d %b %Y')} (Last Trading Day)"

    # Render Pre-Market Tactical Baseline at the TOP (Morning Anchor)
    render_pre_market_commentary_card(symbol, live_data, session_date=active_session_date)

    # 5. Fetch Entries strictly for active single trading session date
    entries = [e for e in raw_entries if to_ist(e.get("ts")).strftime("%Y-%m-%d") == active_session_date]

    # If commentary is sparse (< 5 entries), automatically synthesize the full session narrative from timeseries and microstructure feeds
    if len(entries) < 5:
        try:
            from commentary.engine import get_session_commentary_engine
            engine = get_session_commentary_engine(symbol)
            synth = engine.synthesize_session_narrative(symbol, active_session_date)
            if synth:
                raw_entries = get_recent_commentary_entries(symbol, limit=100)
                entries = [e for e in raw_entries if to_ist(e.get("ts")).strftime("%Y-%m-%d") == active_session_date]
        except Exception as e:
            logger.warning("Session commentary synthesis error: %s", e)

    # Apply Filter
    if filter_opt == "Actionable Bites Only":
        entries = [e for e in entries if e.get("bites")]
    elif filter_opt == "Pinned Recommendations":
        entries = [e for e in entries if any(b.get("pinned_rec_id") for b in e.get("bites", []))]

    if not entries:
        st.info(
            f"🎙️ **Awaiting High-Significance Session Events ({date_badge_text})** — The Session Commentary Engine "
            f"is listening across GEX walls, Sentinel footprint absorption, divergences, and opening structure. "
            f"Verified narrative updates and suggestion bites will stream here as significant developments unfold."
        )
        # Render Post-Market Settlement & Pinning Scorecard at the BOTTOM
        render_post_market_commentary_card(symbol, live_data, session_date=active_session_date)
        return

    # Deduplicate consecutive identical notifications
    clean_entries: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    for e in entries:
        t_key = f"{e.get('headline', '')}_{e.get('body', '')[:35]}"
        if t_key not in seen_texts:
            seen_texts.add(t_key)
            clean_entries.append(e)

    # Sort chronological for freeflowing story reading (earliest morning at top, latest at bottom)
    chronological_entries = list(reversed(clean_entries))

    # Helper function to render a single colorful card
    def _render_colorful_card(entry: dict[str, Any]) -> str:
        dt_ist = to_ist(entry.get("ts"))
        time_str = dt_ist.strftime("%d %b · %H:%M IST")

        raw_hl = entry.get("headline", "")
        topic = extract_topic_header(raw_hl, entry)

        raw_body = entry.get("body", "")
        body = str(raw_body).strip()
        if body.lower().startswith(topic.lower()):
            body = body[len(topic):].lstrip(": -—.,").strip()
        if body and not body.endswith((".", "!", "?")):
            body += "."

        # Color themes by event archetype
        hl_lower = (str(raw_hl) + " " + str(topic)).lower()
        if any(k in hl_lower for k in ["gap-up", "high break", "target hit", "bull", "rally", "call squeeze", "reclaim"]):
            card_cls, topic_cls, icon = "comm-card-bullish", "topic-bullish", "🟢"
        elif any(k in hl_lower for k in ["gap-down", "low break", "invalidation", "bear", "selloff", "put breakdown", "loss"]):
            card_cls, topic_cls, icon = "comm-card-bearish", "topic-bearish", "🔴"
        elif any(k in hl_lower for k in ["call wall", "put wall", "wall test", "wall break", "wall migrat", "gamma flip", "gex", "max pain"]):
            card_cls, topic_cls, icon = "comm-card-wall", "topic-wall", "🟣"
        elif any(k in hl_lower for k in ["divergence", "footprint", "absorption", "warning", "unwind", "decay"]):
            card_cls, topic_cls, icon = "comm-card-warning", "topic-warning", "⚡"
        else:
            card_cls, topic_cls, icon = "comm-card-impulse", "topic-impulse", "🌊"

        # Bites
        bites = entry.get("bites", [])
        bite_html = ""
        if bites:
            b = bites[0]
            b_type = (b.get("type", "WATCH") if isinstance(b, dict) else getattr(b, "type", "WATCH")).upper()
            b_text = render_bite_template(b)
            if b_text.startswith("Bite:"):
                b_text = b_text[5:].strip()
            if b_text.startswith(b_type):
                b_text = b_text[len(b_type):].lstrip(": .—").strip()

            pill_map = {
                "WATCH": "bite-watch",
                "PREPARE": "bite-prepare",
                "ACT-IF": "bite-actif",
                "TRIGGERED": "bite-triggered",
                "MANAGE": "bite-manage",
                "STAND-ASIDE": "bite-standaside",
                "HEDGE": "bite-watch",
            }
            b_cls = pill_map.get(b_type, "bite-watch")
            bite_html = f'<div class="comm-bite-box"><span class="comm-bite-pill {b_cls}">💡 BITE: {b_type}</span><span style="color:var(--ink);font-weight:500">{b_text}</span></div>'

        return f'<div class="comm-card {card_cls}"><div class="comm-meta-row"><span class="comm-time-badge">⏱️ {time_str}</span><span class="comm-topic-badge {topic_cls}">{icon} {topic.upper()}</span></div><div class="comm-body">{body}</div>{bite_html}</div>'

    # 6. Render Colorful Freeflowing Storytelling Stream (Chronological Order)
    cards_html_list = []
    for entry in chronological_entries:
        cards_html_list.append(_render_colorful_card(entry))

    cards_stream = "".join(cards_html_list)
    st.markdown(
        textwrap.dedent(f"""
        <div class="comm-stream-wrap">
            {cards_stream}
        </div>
        """).strip(),
        unsafe_allow_html=True,
    )

    # 7. Post-Market Settlement & Pinning Scorecard at the BOTTOM (Chronological Day Conclusion)
    render_post_market_commentary_card(symbol, live_data, session_date=active_session_date)
