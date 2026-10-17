"""app/sidebar_settings.py — Global sidebar settings (e.g. Session Token update)."""

import os
import re
from pathlib import Path
import streamlit as st
import config

ROOT = Path(__file__).resolve().parents[1]

def update_env_var(key: str, value: str) -> None:
    """Update a key in the .env file."""
    env_path = ROOT / ".env"
    try:
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                env_content = f.read()
            if f"{key}=" in env_content:
                env_content = re.sub(rf'{key}=.*', f'{key}={value}', env_content)
            else:
                env_content += f'\n{key}={value}\n'
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_content)
        else:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f'{key}={value}\n')
    except Exception as e:
        st.error(f"Failed to save {key} to .env: {e}")


def render_token_updater():
    """Render the API token update form in the sidebar."""
    if config.DATA_SOURCE in ("dhan", "auto"):
        st.markdown("---")
        with st.expander("🔑 Update Dhan Access Token", expanded=False):
            st.markdown("[Generate new token here →](https://dhanhq.co/docs/latest/authentication/)")
            with st.form("global_dhan_form", clear_on_submit=True):
                new_token = st.text_input("New Dhan Access Token (JWT):", type="password")
                submitted = st.form_submit_button("✅ Update Token & Reconnect", type="primary")
                if submitted and new_token:
                    os.environ["DHAN_ACCESS_TOKEN"] = new_token
                    config.DHAN_ACCESS_TOKEN = new_token
                    update_env_var("DHAN_ACCESS_TOKEN", new_token)
                    st.success("Dhan Token saved to .env — daemon will reload automatically.")
                    st.rerun()
