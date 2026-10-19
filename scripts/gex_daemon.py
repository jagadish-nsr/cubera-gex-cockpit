"""
Background daemon to continuously fetch option chain data and write to local files.
This allows Streamlit to render smoothly without waiting on API calls.

Architecture:
- Works with any configured DATA_SOURCE (dhan, nse, auto)
- Fetches ALL configured indices in a round-robin loop
- Writes per-index snapshot files: data/journal/latest_snap_{SYMBOL}.pkl
- Each snapshot contains a fully processed DashboardData object (ready to use by the UI)
- Configurable poll interval (default 5s per index)
"""
import os
import sys
import time
import pickle
import logging
from datetime import datetime, timezone

cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

import config
from app.data_access import load_dashboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gex_daemon")

# Poll interval in seconds between fetches of the same index.
# With N indices this means each index is refreshed every N * POLL_INTERVAL_SEC.
POLL_INTERVAL_SEC = float(os.environ.get("GEX_DAEMON_INTERVAL", "5"))
OUTPUT_DIR = "data/journal"
OUTPUT_TEMPLATE = os.path.join(OUTPUT_DIR, "latest_snap_{symbol}.pkl")


def _snap_path(symbol: str) -> str:
    return OUTPUT_TEMPLATE.format(symbol=symbol.upper())


def run_daemon(ignore_session: bool = False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log.info("Starting GEX Daemon (adapter: %s)...", config.DATA_SOURCE)

    last_off_market_log = 0.0

    while True:
        try:
            # Reload config each loop iteration so new tokens written to .env are picked up
            import importlib
            importlib.reload(config)

            # Off-market hours guard: pause option chain live polling
            if not ignore_session and not config.is_market_open():
                now_epoch = time.time()
                if now_epoch - last_off_market_log >= 300.0:
                    log.info("Market closed (FnO hours: 09:15-15:40 IST / weekends / holidays). Option chain polling paused.")
                    last_off_market_log = now_epoch

                # ── EOD Participant OI auto-fetch (after 16:30 IST) ────────────────
                try:
                    from datetime import timedelta
                    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                    if ist_now.hour >= 16 and ist_now.minute >= 30:
                        today_ist = ist_now.strftime("%Y-%m-%d")
                        from app.participant_oi import capture_latest, load_participant_day
                        if load_participant_day(today_ist) is None:
                            result = capture_latest(today_ist=today_ist, max_back=1)
                            if result:
                                log.info("Daemon: participant OI captured for %s", result[0])
                            else:
                                log.debug("Daemon: participant OI not yet available for %s", today_ist)
                except Exception as _poi_err:
                    log.debug("Daemon: participant OI auto-fetch skipped: %s", _poi_err)

                time.sleep(60)
                continue

            window = config.EXPIRY_WINDOW_DEFAULT
            indices = list(config.INDICES.values())

            # For Dhan adapter, verify index is configured in Dhan scrip mappings
            if config.DATA_SOURCE == "dhan":
                from data.dhan import _DHAN_UNDERLYING_ID as _dhan_ids
                indices = [idx for idx in indices if idx.symbol in _dhan_ids]

            # Round-robin: fetch each index in turn
            failed_count = 0
            for idx in indices:
                symbol = idx.symbol
                t0 = time.time()
                try:
                    dashboard_data = load_dashboard(
                        window=window,
                        use_demo=False,
                        symbol=symbol,
                    )
                except Exception as e:
                    log.warning("Daemon: failed to fetch %s: %s", symbol, e)
                    failed_count += 1
                    continue  # skip this index for this round; try the next one

                # Atomic write via temp file + os.replace
                snap_path = _snap_path(symbol)
                tmp_file = snap_path + ".tmp"
                fetched_at = datetime.now(timezone.utc)
                with open(tmp_file, "wb") as f:
                    pickle.dump(
                        {
                            "data": dashboard_data,   # DashboardData object
                            "symbol": symbol,
                            "window": window,
                            "fetched_at": fetched_at,
                        },
                        f,
                    )
                os.replace(tmp_file, snap_path)

                elapsed = time.time() - t0
                log.info("Fetched & saved %s (%s) in %.2fs", symbol, window, elapsed)

                # Pace between indices to avoid hammering the API
                sleep_time = max(0.1, POLL_INTERVAL_SEC - elapsed)
                time.sleep(sleep_time)

            # If every index failed this round (e.g. transient API error), back off
            if failed_count == len(indices):
                log.warning("All indices failed this round — backing off 10s")
                time.sleep(10)

            # ── EOD Participant OI auto-fetch (after 16:30 IST) ────────────────
            # NSE publishes the clearing-corp participant OI file after market close
            # (typically 4:30–6:00 PM IST). The daemon checks once per round whether
            # today's file has been cached, and if not (and it's post-16:30 IST),
            # fetches and saves it. Fail-soft: any error is logged and skipped.
            try:
                from datetime import timedelta
                ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                if ist_now.hour >= 16 and ist_now.minute >= 30:
                    today_ist = ist_now.strftime("%Y-%m-%d")
                    from app.participant_oi import capture_latest, load_participant_day
                    if load_participant_day(today_ist) is None:
                        result = capture_latest(today_ist=today_ist, max_back=1)
                        if result:
                            log.info("Daemon: participant OI captured for %s", result[0])
                        else:
                            log.debug("Daemon: participant OI not yet available for %s", today_ist)
            except Exception as _poi_err:
                log.debug("Daemon: participant OI auto-fetch skipped: %s", _poi_err)

        except Exception as e:
            log.error("Daemon loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Cubera GEX Option Chain Daemon")
    parser.add_argument("--ignore-session", action="store_true", help="Force polling even outside NSE market hours (testing)")
    args = parser.parse_args()
    run_daemon(ignore_session=args.ignore_session)
