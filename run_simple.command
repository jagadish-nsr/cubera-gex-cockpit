#!/usr/bin/env bash
# One-click launcher for the SIMPLE Nifty GEX dashboard on macOS / Linux — the mirror of
# run_simple.bat (Windows). On a Mac you can double-click this in Finder (first time: right-click ->
# Open, to clear Gatekeeper); or run it from a terminal:  ./run_simple.command
# First run installs dependencies (fast afterwards), then opens the app in your browser.
# Close this window / press Ctrl-C to stop it.
set -euo pipefail

# Run from the repo root regardless of where it was launched from.
cd "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# macOS ships "python3"; some setups only have "python". Pick whichever is a real Python 3.
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3 was not found on your PATH."
  echo "Install it from https://www.python.org/downloads/ (macOS) or your package manager, then re-run."
  read -r -p "Press Enter to close." _ || true
  exit 1
fi

echo "Starting the Simple Nifty GEX dashboard (using: $($PY --version 2>&1))..."
echo "(First launch may take a minute to install dependencies.)"
"$PY" -m pip install -r requirements.txt --quiet --disable-pip-version-check

# Run on 8610, not Streamlit's default 8501: a stale/hung instance can keep 8501 bound and serve a
# white screen (mirrors the Windows launcher, so both machines use the same URL).
"$PY" -m streamlit run app/simple_dashboard.py --server.port 8610
