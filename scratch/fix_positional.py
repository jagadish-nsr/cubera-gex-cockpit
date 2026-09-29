import re

def fix_positional(filepath):
    with open(filepath, "r") as f:
        content = f.read()

    # Move `key="...", ` from just after the label to the end of the argument list (or at least after the sequence argument).
    # Specifically for: window = st.selectbox(
    content = re.sub(r'st\.selectbox\("Expiry window",\s*key="([^"]+)",\s*WINDOW_CHOICES,', r'st.selectbox("Expiry window", WINDOW_CHOICES, key="\1",', content)
    
    # auto_mins = st.selectbox("Interval (min)", key="_v1_auto_mins", [1, 2, 3, 5, 10, 15, 30],
    content = re.sub(r'st\.selectbox\("Interval \(min\)",\s*key="([^"]+)",\s*\[(.*?)\]', r'st.selectbox("Interval (min)", [\2], key="\1"', content)

    # live_interval_sec = st.selectbox("Live API tick (sec)", key="_v2_live_tick", [1, 2, 5, 10, 30],
    content = re.sub(r'st\.selectbox\("Live API tick \(sec\)",\s*key="([^"]+)",\s*\[(.*?)\]', r'st.selectbox("Live API tick (sec)", [\2], key="\1"', content)

    with open(filepath, "w") as f:
        f.write(content)

fix_positional("app/dashboard_v1.py")
fix_positional("app/dashboard_v2.py")
