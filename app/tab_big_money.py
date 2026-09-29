import streamlit as st
from datetime import timedelta
from app.simple_dashboard import _big_money_tape_html

def render_big_money_tab(data):
    # Sidebar controls for the Big-Money Collector & Replay
    with st.sidebar:
        st.divider()
        st.caption("🐋 Big-Money Collector")
        bm_on = st.checkbox("Auto-run background collector", value=True, key="bm_auto",
                            help="Streams futures ticks and flags big prints & bursts. Needs a Dhan token.")
        
        with st.expander("🎬 Replay big-money", expanded=False):
            from app.big_money_live import available_raw_days
            symbol = data.symbol if data else "NIFTY"
            _rd = available_raw_days(symbol)
            if not _rd:
                st.caption(f"No captured raw ticks for {symbol} yet.")
            else:
                st.checkbox(f"Replay {symbol}'s captured tape", value=False, key="bm_replay_on")
                st.selectbox("Day", _rd[::-1], key="bm_replay_day")
                _times = [f"{m // 60:02d}:{m % 60:02d}" for m in range(9 * 60 + 15, 15 * 60 + 31, 5)]
                st.select_slider("Clock (IST)", options=_times, value=_times[-1], key="bm_replay_clock")
    
    # Initialize the collector if enabled
    use_demo = data is not None and (data.source or "").startswith("DEMO")
    if bm_on and not use_demo:
        try:
            import config
            from app.simple_dashboard import _get_big_money_collector
            has_token = bool(config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN)
            if has_token:
                st.session_state["_bm_status"] = _get_big_money_collector().status
        except Exception:
            pass

    st.title("🐋 Big-Money Tape")
    st.caption("Large futures prints & bursts (Lee-Ready inference). A flow read, not a prediction.")
    if not data:
        st.info("No data loaded. Please load an index in Tab 1 or Tab 2 first.")
        return
    ist = data.fetched_at + timedelta(hours=5, minutes=30)

    # Replay configuration
    _bm_replay = None
    if st.session_state.get("bm_replay_on"):
        clock = st.session_state.get("bm_replay_clock", "15:30")
        _h, _m = map(int, clock.split(":"))
        # we need to construct a proper datetime for the replay
        from datetime import datetime
        d_parts = list(map(int, st.session_state.get("bm_replay_day", "2026-06-30").split("-")))
        upto = datetime(d_parts[0], d_parts[1], d_parts[2], _h, _m).timestamp()
        _bm_replay = {"day": st.session_state["bm_replay_day"], "clock": clock, "upto_epoch": upto}

    html = _big_money_tape_html(ist=ist, symbol=data.symbol, replay=_bm_replay)
    st.markdown(html, unsafe_allow_html=True)
