#!/bin/bash
# install_restart_timer.sh
#
# Run this ONCE on the cloud server (as root / with sudo) after copying
# this whole folder to /opt/angelone_order_worker/
#
# It wires up the daily 8:30 AM restart of angelone-order-worker.service
# so the worker gets a fresh Angel One token every morning.
#
# Usage:
#   sudo bash install_restart_timer.sh

set -e  # stop immediately if any command fails

PROJECT_DIR="/opt/angelone_order_worker"
SYSTEMD_DIR="/etc/systemd/system"

echo "== Step 1: Make restart script executable =="
chmod +x "$PROJECT_DIR/restart_worker.sh"

echo "== Step 2: Copy systemd unit files into place =="
cp "$PROJECT_DIR/angelone-worker-restart.service" "$SYSTEMD_DIR/angelone-worker-restart.service"
cp "$PROJECT_DIR/angelone-worker-restart.timer" "$SYSTEMD_DIR/angelone-worker-restart.timer"

echo "== Step 3: Reload systemd so it sees the new units =="
systemctl daemon-reload

echo "== Step 4: Enable and start the timer (NOT the service directly) =="
systemctl enable angelone-worker-restart.timer
systemctl start angelone-worker-restart.timer

echo "== Step 5: Show when it's next scheduled to run =="
systemctl list-timers | grep angelone || true

echo ""
echo "Done. The worker will now restart automatically every day at 8:30 AM local server time."
echo "Check timezone with: timedatectl"
echo "Test it manually anytime with: sudo systemctl start angelone-worker-restart.service"
echo "Watch logs with: journalctl -t angel-restart -f"
