"""
Background daemon to fetch EOD Participant OI data from NSE.
Runs daily after 16:30 IST to download the NSE participant OI CSV.
"""
import os
import sys
import time
import logging
from datetime import datetime, timezone

cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("participant_daemon")

def run_daemon():
    log.info("Starting Participant OI Daemon...")
    
    while True:
        try:
            from datetime import timedelta
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            
            # ── EOD Participant OI auto-fetch (after 16:30 IST) ────────────────
            # NSE publishes the clearing-corp participant OI file after market close
            # (typically 4:30–6:00 PM IST). 
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
            
        # Check every 5 minutes
        time.sleep(300)

if __name__ == "__main__":
    run_daemon()
