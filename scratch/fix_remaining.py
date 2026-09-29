import re

def fix_remaining(filepath, prefix):
    with open(filepath, "r") as f:
        content = f.read()

    # auto = st.checkbox("⏱️ Auto-refresh", value=False, ...
    content = re.sub(r'st\.checkbox\(\s*"⏱️ Auto-refresh",\s*value=False,', f'st.checkbox("⏱️ Auto-refresh", key="{prefix}_auto", value=False,', content)
    
    # auto_mins = st.selectbox("Interval", [1, 3, 5, 15], index=3, ...
    content = re.sub(r'st\.selectbox\(\s*"Interval",\s*\[1,\s*3,\s*5,\s*15\],\s*index=3,', f'st.selectbox("Interval", [1, 3, 5, 15], key="{prefix}_auto_mins", index=3,', content)

    # auto_save = st.checkbox("Save each tick to time-series", value=False, disabled=not auto, ...
    content = re.sub(r'st\.checkbox\(\s*"Save each tick to time-series",\s*value=False,\s*disabled=not auto,', f'st.checkbox("Save each tick to time-series", key="{prefix}_auto_save", value=False, disabled=not auto,', content)

    with open(filepath, "w") as f:
        f.write(content)

fix_remaining("app/dashboard_v1.py", "_v1")
fix_remaining("app/dashboard_v2.py", "_v2")
