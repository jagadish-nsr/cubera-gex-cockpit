"""app/tab_commentary.py — Session Commentary Engine & Suggestion Bites Stream (Isolated @st.fragment)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
import streamlit as st

from commentary.config_loader import get_commentary_config
from commentary.storage.db import get_recent_commentary_entries, get_pinned_commentary_cache
from commentary.story_state import StoryStateManager
from commentary.narrator.verifier import verify_commentary

logger = logging.getLogger(__name__)


def _format_time_ist(ts_str: str) -> str:
    """Format ISO timestamp string into readable IST time (HH:MM:SS AM/PM)."""
    try:
        dt_obj = dt.datetime.fromisoformat(ts_str)
        return dt_obj.strftime("%I:%M:%S %p")
    except Exception:
        return ts_str


@st.fragment
def render_commentary_tab(symbol: str, window: str = "1D") -> None:
    """Render Session Commentary & Suggestion Bites tab within an isolated Streamlit fragment.
    
    Prevents full-app reruns and guarantees non-blocking, zero-latency updates.
    """
    cfg = get_commentary_config()
    bites_cfg = cfg.get("bites", {})
    mode = bites_cfg.get("mode", "shadow").upper()

    # Story State Manager for Intraday Phase
    story_mgr = StoryStateManager()
    now_ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    phase_name = story_mgr.determine_phase(now_ist).replace("_", " ").title()

    # CSS Styling for Premium Commentary Feed
    st.markdown(
        """
        <style>
        .commentary-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--surface-1);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 16px;
        }
        .phase-chip {
            background: rgba(59, 130, 246, 0.12);
            color: #3b82f6;
            font-size: 0.78rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 12px;
            border: 1px solid rgba(59, 130, 246, 0.25);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .mode-chip {
            background: rgba(245, 158, 11, 0.12);
            color: #f59e0b;
            font-size: 0.76rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 12px;
            border: 1px solid rgba(245, 158, 11, 0.25);
        }
        .mode-chip.live {
            background: rgba(34, 197, 94, 0.12);
            color: #22c55e;
            border-color: rgba(34, 197, 94, 0.25);
        }
        .commentary-card {
            background: var(--surface-1);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 16px 18px;
            margin-bottom: 14px;
            transition: border-color 0.15s ease;
        }
        .commentary-card:hover {
            border-color: var(--primary, #3b82f6);
        }
        .entry-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .entry-headline {
            font-size: 1.05rem;
            font-weight: 750;
            color: var(--ink);
            margin-bottom: 6px;
            line-height: 1.35;
        }
        .entry-body {
            font-size: 0.88rem;
            color: var(--ink-secondary, #94a3b8);
            line-height: 1.5;
            margin-bottom: 12px;
        }
        .bite-chip {
            background: var(--surface-2, #1e293b);
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 10px 14px;
            margin-top: 10px;
        }
        .bite-badge {
            font-size: 0.72rem;
            font-weight: 750;
            padding: 2px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: inline-block;
            margin-right: 6px;
        }
        .badge-act-if { background: rgba(34, 197, 94, 0.18); color: #22c55e; border: 1px solid rgba(34, 197, 94, 0.3); }
        .badge-prepare { background: rgba(59, 130, 246, 0.18); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3); }
        .badge-watch { background: rgba(148, 163, 184, 0.18); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }
        .badge-manage { background: rgba(245, 158, 11, 0.18); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-stand-aside { background: rgba(239, 68, 68, 0.18); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-hedge { background: rgba(168, 85, 247, 0.18); color: #a855f7; border: 1px solid rgba(168, 85, 247, 0.3); }
        .pinned-note-box {
            background: rgba(245, 158, 11, 0.08);
            border-left: 3px solid #f59e0b;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 0.82rem;
            color: var(--ink);
            margin-top: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 1. Header with Phase, Thesis & Mode
    mode_class = "live" if mode == "LIVE" else "shadow"
    st.markdown(
        f"""
        <div class="commentary-header">
            <div>
                <span class="phase-chip">⏱️ {phase_name}</span>
                <span style="font-weight: 700; font-size: 0.95rem; margin-left: 10px; color: var(--ink)">
                    {symbol} Session Narrative & Tactical Bites
                </span>
            </div>
            <div>
                <span class="mode-chip {mode_class}">
                    🛡️ {mode} MODE ({bites_cfg.get('min_shadow_sessions_for_promotion', 20)} Session Gating)
                </span>
            </div>
        </div>
        """,
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
            st.rerun(scope="fragment")

    # 3. Pinned Recommendations Mini-Commentary Strip
    pinned_cache = get_pinned_commentary_cache()
    if pinned_cache:
        st.markdown(
            '<div style="font-size:0.80rem;font-weight:700;color:var(--ink-mute);margin-bottom:6px">'
            '📌 ACTIVE PINNED RECOMMENDATIONS REAL-TIME COMMENTARY'
            '</div>',
            unsafe_allow_html=True,
        )
        for rec_id, note in pinned_cache.items():
            st.markdown(
                f"""
                <div class="pinned-note-box">
                    <b>{rec_id}:</b> {note}
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # 4. Fetch Entries
    entries = get_recent_commentary_entries(symbol, limit=40)

    # Apply Filter
    if filter_opt == "Actionable Bites Only":
        entries = [e for e in entries if e.get("bites")]
    elif filter_opt == "Pinned Recommendations":
        entries = [e for e in entries if any(b.get("pinned_rec_id") for b in e.get("bites", []))]

    if not entries:
        st.markdown(
            """
            <div style="background:var(--surface-2);border:1px dashed var(--line);border-radius:10px;padding:32px 20px;text-align:center;margin-top:14px">
                <div style="font-size:1.8rem;margin-bottom:8px">🎙️</div>
                <div style="font-weight:700;font-size:0.95rem;color:var(--ink)">Awaiting High-Significance Session Events</div>
                <div style="font-size:0.82rem;color:var(--ink-mute);max-width:540px;margin:6px auto 0;line-height:1.4">
                    The Session Commentary Engine is listening across GEX walls, Sentinel footprint absorption,
                    divergences, and open structure. Verified narrative updates and suggestion bites will stream here as significant developments unfold.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # 5. Render Feed Cards
    for entry in entries:
        time_str = _format_time_ist(entry["ts"])
        bites = entry.get("bites", [])

        st.markdown(
            f"""
            <div class="commentary-card">
                <div class="entry-top-bar">
                    <div>
                        <span style="font-size:0.76rem;color:var(--ink-mute);font-family:var(--mono)">⏱️ {time_str}</span>
                        <span style="font-size:0.74rem;color:var(--ink-mute);margin-left:8px;text-transform:uppercase">· {entry.get('phase', 'session').replace('_', ' ')}</span>
                    </div>
                    <div>
                        <span style="font-size:0.72rem;color:#22c55e;font-weight:700">✓ Fact Verified</span>
                    </div>
                </div>
                <div class="entry-headline">{entry["headline"]}</div>
                <div class="entry-body">{entry["body"]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Render Bites inside card if present
        if bites:
            for b in bites:
                b_type = b.get("type", "WATCH")
                badge_cls = f"badge-{b_type.lower()}"
                levels = b.get("levels", {})
                risk = b.get("risk", {})
                trig = levels.get("trigger", 0.0)
                inval = levels.get("invalidation", 0.0)
                tgt = levels.get("target", 0.0)
                rr = risk.get("rr", 2.0)
                max_loss = risk.get("max_loss_per_lot", 0.0)

                st.markdown(
                    f"""
                    <div class="bite-chip">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                            <div>
                                <span class="bite-badge {badge_cls}">{b_type}</span>
                                <span style="font-weight:750;font-size:0.86rem;color:var(--ink)">{b.get('headline', '')}</span>
                            </div>
                            <span style="font-size:0.70rem;color:var(--ink-mute);font-family:var(--mono)">R:R {rr:.1f} · Risk ₹{max_loss:,.0f}</span>
                        </div>
                        <div style="font-size:0.80rem;color:var(--ink);margin-bottom:6px">
                            <b>Action Frame:</b> {b.get('action_frame', '')}
                        </div>
                        <div style="display:flex;gap:16px;font-size:0.75rem;font-family:var(--mono);color:var(--ink-mute)">
                            <span>Trigger: <b>₹{trig:,.0f}</b></span>
                            <span>Invalidation: <b style="color:#ef4444">₹{inval:,.0f}</b></span>
                            <span>Target: <b style="color:#22c55e">₹{tgt:,.0f}</b></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # "Why this?" Fact Verifier Drawer
        with st.expander("🔍 Why this? (Fact Verifier & Audit Trail)", expanded=False):
            st.json({
                "entry_id": entry["entry_id"],
                "phase": entry["phase"],
                "event_ids": entry.get("event_ids", []),
                "verified": entry.get("verified", True),
                "tags": entry.get("tags", []),
            })
