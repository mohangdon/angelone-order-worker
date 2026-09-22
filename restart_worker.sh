#!/bin/bash
# restart_worker.sh
#
# Purpose: Restart the Angel One order worker systemd service so it
# re-runs its startup login flow and generates a fresh auth token.
#
# This is meant to be triggered automatically every day at 8:30 AM by
# the angelone-worker-restart.timer (see that file + the .service file
# in this same folder). You normally won't run this by hand.

SERVICE_NAME="angelone-order-worker.service"
LOG_TAG="angel-restart"

echo "$(date '+%Y-%m-%d %H:%M:%S') - Restarting $SERVICE_NAME for fresh token" | systemd-cat -t "$LOG_TAG"

# --no-block: fire the restart request and return immediately, instead of
# waiting on the job. Waiting here caused systemd to treat this script's
# own unit as tangled up in the target unit's restart transaction, which
# systemd resolved by killing this script with SIGTERM.
systemctl restart --no-block "$SERVICE_NAME"

# Wait a moment, then confirm the service actually came back up
sleep 5
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $SERVICE_NAME restarted successfully" | systemd-cat -t "$LOG_TAG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING: $SERVICE_NAME failed to come back up" | systemd-cat -t "$LOG_TAG"
fi
