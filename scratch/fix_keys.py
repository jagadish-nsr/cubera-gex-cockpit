import re

def add_keys(filepath, prefix):
    with open(filepath, "r") as f:
        content = f.read()

    # We will look for st.something(
    # and if it doesn't already have key=..., we will inject key="{prefix}_..."
    
    # Let's just do targeted replacements for the known widgets in the sidebars
    # We'll use a simple script that matches the specific lines and replaces them.
    
    replacements = {
        r'st\.selectbox\(\s*"Index",': f'st.selectbox("Index", key="{prefix}_index",',
        r'st\.checkbox\(\s*"Use demo data \(offline\)",': f'st.checkbox("Use demo data (offline)", key="{prefix}_use_demo",',
        r'st\.selectbox\(\s*"Expiry window",': f'st.selectbox("Expiry window", key="{prefix}_window",',
        r'st\.button\(\s*"🔄 Refresh",': f'st.button("🔄 Refresh", key="{prefix}_refresh",',
        r'st\.button\(\s*"💾 Save intraday snapshot"': f'st.button("💾 Save intraday snapshot", key="{prefix}_save_ts"',
        r'st\.checkbox\(\s*"Auto-refresh': f'st.checkbox("Auto-refresh", key="{prefix}_auto"',
        r'st\.selectbox\(\s*"Interval \(min\)",': f'st.selectbox("Interval (min)", key="{prefix}_auto_mins",',
        r'st\.checkbox\(\s*"Auto-save snapshot': f'st.checkbox("Auto-save snapshot", key="{prefix}_auto_save"',
        r'st\.button\(\s*"📍 Anchor now"': f'st.button("📍 Anchor now", key="{prefix}_anchor"',
        r'st\.selectbox\(\s*"Live API tick \(sec\)",': f'st.selectbox("Live API tick (sec)", key="{prefix}_live_tick",',
        r'st\.checkbox\(\s*"Full-chain PCR \(solid\)",': f'st.checkbox("Full-chain PCR (solid)", key="{prefix}_pcr_full",',
        r'st\.checkbox\(\s*"Near-ATM proxy PCR \(dashed\)",': f'st.checkbox("Near-ATM proxy PCR (dashed)", key="{prefix}_pcr_near",',
        r'st\.checkbox\(\s*"Reference bands \(0.8 / 1.2\)",': f'st.checkbox("Reference bands (0.8 / 1.2)", key="{prefix}_pcr_bands",',
        r'st\.button\(\s*"🔊 Test Alert Sound"': f'st.button("🔊 Test Alert Sound", key="{prefix}_test_alert"',
    }

    for pattern, repl in replacements.items():
        content = re.sub(pattern, repl, content)

    with open(filepath, "w") as f:
        f.write(content)
    print(f"Updated {filepath}")

add_keys("app/dashboard_v1.py", "_v1")
add_keys("app/dashboard_v2.py", "_v2")
