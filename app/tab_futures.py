import streamlit as st
from datetime import timedelta
from app.simple_dashboard import _futures_html

def render_futures_tab(data):
    st.title("🔮 Futures Rollover & Buildup")
    st.caption("Intraday Open Interest buildup and roll patterns on the NSE.")
    if not data:
        st.info("No data loaded. Please load an index in Tab 1 or Tab 2 first.")
        return
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist.date()) if nearest else False
    html = _futures_html(data=data, symbol=data.symbol, ist=ist, is_expiry=is_expiry)
    st.markdown(html, unsafe_allow_html=True)
