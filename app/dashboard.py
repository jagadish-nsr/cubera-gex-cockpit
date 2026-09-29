"""Indian GEX — Master Routing Shell

Hosts the 3 main views:
1. GEX & Market Profile (v1 remote logic)
2. GEX & Market Profile (v2 SCSP local logic)
3. Positioning Divergence
"""

from __future__ import annotations

import sys
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Indian GEX", page_icon="📈", layout="wide")

st.markdown("""
<style>
/* Global tab styling */
.stTabs [data-baseweb="tab-list"] {
    gap: 24px;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.1);
}
.stTabs [data-baseweb="tab"] {
    padding-top: 8px;
    padding-bottom: 8px;
    font-size: 1.1rem;
    font-weight: 500;
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    from app.sidebar_settings import render_token_updater
    render_token_updater()

tab_v1, tab_v2, tab_pd, tab_nse, tab_bm, tab_fut = st.tabs([
    "📊 GEX & Market Profile-v1",
    "🔬 GEX & Market Profile-v2(SCSP)",
    "📈 Positioning Divergence",
    "🧭 NSE Positioning",
    "🐋 Big-Money Tape",
    "🔮 Futures Rollover",
])

with tab_v1:
    from app.dashboard_v1 import render_gex_v1_tab
    render_gex_v1_tab()

with tab_v2:
    from app.dashboard_v2 import render_gex_scsp_tab
    render_gex_scsp_tab()

with tab_pd:
    from app.dashboard_v2 import render_positioning_divergence_tab
    # v2 tab sets st.session_state.data which PD uses
    render_positioning_divergence_tab(st.session_state.get("data"))

with tab_nse:
    from app.positioning_tab import render_nse_positioning_tab
    render_nse_positioning_tab(st.session_state.get("data"))

with tab_bm:
    from app.tab_big_money import render_big_money_tab
    render_big_money_tab(st.session_state.get("data") or st.session_state.get("_v1_data"))

with tab_fut:
    from app.tab_futures import render_futures_tab
    render_futures_tab(st.session_state.get("data") or st.session_state.get("_v1_data"))
