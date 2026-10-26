# Simple dashboard

The Streamlit simple dashboard (`app/simple_dashboard.py`) was removed. The decision
screen is the web cockpit.

```
./run_prod.sh          # http://localhost:8640
./run_dev.sh           # http://localhost:8650
```

Book math, the live chain, and the studies live in `gex/`, `analytics/`, and the
shared modules under `app/` (`data_access.py`, `gammablast.py`, `big_money.py`,
and the rest). Those modules are not a second UI.
