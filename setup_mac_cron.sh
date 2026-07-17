#!/bin/bash
# Setup script to configure macOS cron jobs for the Indian GEX loggers.

cd "$(dirname "$0")" || exit
PROJECT_DIR="$(pwd)"

# Create a temporary file for the new cron jobs
CRON_TMP=$(mktemp)
crontab -l > "$CRON_TMP" 2>/dev/null

echo "Adding Nifty GEX jobs to crontab..."

# Remove old jobs if they exist
sed -i.bak '/Indian-GEX/d' "$CRON_TMP"
sed -i.bak '/run_logger.sh/d' "$CRON_TMP"
sed -i.bak '/run_timeseries.sh/d' "$CRON_TMP"
sed -i.bak '/run_gamma_blast.sh/d' "$CRON_TMP"

# Append new jobs (Times converted to local cron syntax - assuming system timezone is IST for the market hours)
# 1. Logger: Runs once at 10:15 AM Monday to Friday (Indian Market Opens at 9:15, logger runs a bit after open or as configured)
# The windows setup sets logger to 10:15 or 15:45? The bat says "open snapshot".
echo "# Indian-GEX Logger (Runs at 10:15 AM Mon-Fri)" >> "$CRON_TMP"
echo "15 10 * * 1-5 $PROJECT_DIR/run_logger.sh" >> "$CRON_TMP"

# 2. Timeseries: Runs every 15 minutes between 9:15 AM and 3:30 PM (Mon-Fri)
echo "# Indian-GEX Timeseries Logger (Every 15 mins during market hours Mon-Fri)" >> "$CRON_TMP"
echo "15,30,45 9 * * 1-5 $PROJECT_DIR/run_timeseries.sh" >> "$CRON_TMP"
echo "*/15 10-14 * * 1-5 $PROJECT_DIR/run_timeseries.sh" >> "$CRON_TMP"
echo "0,15,30 15 * * 1-5 $PROJECT_DIR/run_timeseries.sh" >> "$CRON_TMP"

# 3. Gamma Blast: Runs every 1 minute between 1:00 PM and 3:30 PM (Mon-Fri)
echo "# Indian-GEX Gamma Blast Logger (Every 1 min 13:00-15:30 on Expiry Days usually)" >> "$CRON_TMP"
echo "* 13-14 * * 1-5 $PROJECT_DIR/run_gamma_blast.sh" >> "$CRON_TMP"
echo "0-30 15 * * 1-5 $PROJECT_DIR/run_gamma_blast.sh" >> "$CRON_TMP"

crontab "$CRON_TMP"
rm "$CRON_TMP" "$CRON_TMP.bak"

echo "Cron jobs added successfully! (Run 'crontab -l' to view them)"
echo "Make sure your Mac is awake during these hours, or use a tool like Amphetamine."
