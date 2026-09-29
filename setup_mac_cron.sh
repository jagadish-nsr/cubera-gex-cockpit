#!/usr/bin/env bash
# Installs cron jobs for the Indian-GEX data collection scripts (IST timezone)

# Get absolute path to the directory where scripts are located
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Define the cron jobs
CRON_JOBS="
# Indian-GEX Automated Data Collection (IST Timezone)
# Timeseries snapshots: Every 15 min from 09:15 to 15:30 IST (Mon-Fri)
*/15 9-15 * * 1-5 \"$DIR/run_timeseries.sh\"

# Logger snapshots: Daily at 09:20 and 15:00 IST (Mon-Fri)
20 9 * * 1-5 \"$DIR/run_logger.sh\"
0 15 * * 1-5 \"$DIR/run_logger.sh\"

# EOD Tasks: 18:00 IST (Mon-Fri)
0 18 * * 1-5 \"$DIR/run_participant_oi.sh\"
0 18 * * 1-5 \"$DIR/run_futures_buildup.sh\"
"

# Add jobs to crontab if they aren't already there
(crontab -l 2>/dev/null; echo "$CRON_JOBS") | crontab -

echo "Cron jobs installed successfully."
echo "You can view them by running: crontab -l"
