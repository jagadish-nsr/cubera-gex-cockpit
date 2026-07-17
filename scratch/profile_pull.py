import time
from datetime import datetime, timezone, timedelta
import config
from data.demo import build_demo_rows
from data.nse import from_nse_results
from gex.levels import compute_levels
import numpy as np

def main():
    idx = config.get_index("NIFTY")
    fetched_at = datetime.now(timezone.utc)
    
    t0 = time.time()
    rows = []
    spot = 24000
    for i in range(30): # 30 expiries
        r, _ = build_demo_rows(fetched_at=fetched_at, expiry=(fetched_at.date() + timedelta(days=i*7)))
        for row in r:
            row["iv"] = None
        rows.extend(r)
        
    t1 = time.time()
    print(f"Fetch chain (Demo, expanded): {t1 - t0:.4f} seconds ({len(rows)} rows)")
    
    snap = from_nse_results(
        rows, spot=spot, expiry_window="all",
        dealer_sign_model=config.DEALER_SIGN_MODEL, source="DEMO-SYNTHETIC",
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=idx.symbol,
    )
    t2 = time.time()
    print(f"from_nse_results: {t2 - t1:.4f} seconds")
    
    levels = compute_levels(
        snap, contract_size=idx.lot_size,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
    )
    t3 = time.time()
    print(f"compute_levels: {t3 - t2:.4f} seconds")
    print(f"Total time: {t3 - t0:.4f} seconds")

if __name__ == "__main__":
    main()
