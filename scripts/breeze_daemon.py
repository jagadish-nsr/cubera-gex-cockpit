"""
Background daemon to continuously fetch option chain data and write to local files.
This allows Streamlit to render smoothly without waiting on API calls.

Architecture:
- Works with any configured DATA_SOURCE (icici_breeze, dhan, nse)
- Fetches ALL configured indices in a round-robin loop
- Writes per-index snapshot files: data/journal/latest_snap_{SYMBOL}.pkl
- Each snapshot contains a fully processed DashboardData object (ready to use by the UI)
- Configurable poll interval (default 5s per index)

Note: File is named breeze_daemon.py for back-compat with existing shell scripts/cron.
The daemon is fully adapter-agnostic and routes through load_dashboard().
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
from data.icici_breeze import SessionExpiredError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gex_daemon")

# Poll interval in seconds between fetches of the same index.
# With N indices this means each index is refreshed every N * POLL_INTERVAL_SEC.
POLL_INTERVAL_SEC = float(os.environ.get("GEX_DAEMON_INTERVAL", "5"))
OUTPUT_DIR = "data/journal"
OUTPUT_TEMPLATE = os.path.join(OUTPUT_DIR, "latest_snap_{symbol}.pkl")


def _snap_path(symbol: str) -> str:
    return OUTPUT_TEMPLATE.format(symbol=symbol.upper())


def run_daemon():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log.info("Starting GEX Daemon (adapter: %s)...", config.DATA_SOURCE)

    session_expired = False

    while True:
        try:
            # Reload config each loop iteration so a new session token written
            # to .env is picked up without restarting the daemon.
            import importlib
            importlib.reload(config)

            window = config.EXPIRY_WINDOW_DEFAULT
            indices = list(config.INDICES.values())

            # For Dhan adapter, skip SENSEX (not supported)
            if config.DATA_SOURCE == "dhan":
                from data.dhan import _DHAN_UNDERLYING_ID as _dhan_ids
                indices = [idx for idx in indices if idx.symbol in _dhan_ids]

            if session_expired:
                # Back-off: wait for the user to update the token in the UI.
                log.error("Session Token Expired (adapter: %s). Please update credentials.",
                          config.DATA_SOURCE)
                time.sleep(30)
                session_expired = False
                continue

            # Round-robin: fetch each index in turn
            failed_count = 0
            for idx in indices:
                symbol = idx.symbol
                t0 = time.time()
                try:
                    # Use load_dashboard() so the pkl contains a ready-to-use
                    # DashboardData object, not a raw tuple that the UI would
                    # have to post-process (which was the original bug).
                    dashboard_data = load_dashboard(
                        window=window,
                        use_demo=False,
                        symbol=symbol,
                    )
                except SessionExpiredError:
                    session_expired = True
                    log.error(
                        "Session Token Expired fetching %s — waiting for UI token update.", symbol
                    )
                    break  # exit the inner for-loop; the while loop will handle back-off
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

        except SessionExpiredError:
            session_expired = True
            log.error("Session Token Expired (adapter: %s). Please update credentials.",
                      config.DATA_SOURCE)
        except Exception as e:
            log.error("Daemon loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_daemon()
