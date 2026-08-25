#!/usr/bin/env bash
#
# Remove the monitoring agent systemd service from Linux.
# Run as root:  sudo ./uninstall_linux.sh
set -euo pipefail

INSTALL_BIN="/usr/local/bin/monitoring-agent"
CONF_DIR="/etc/monitoring-agent"
STATE_DIR="/var/lib/monitoring-agent"
UNIT_PATH="/etc/systemd/system/monitoring-agent.service"
SVC_USER="monitoring-agent"

if [[ $EUID -ne 0 ]]; then
    echo "This uninstaller must be run as root (try: sudo $0)" >&2
    exit 1
fi

echo ">> Stopping and disabling monitoring-agent"
systemctl disable --now monitoring-agent.service 2>/dev/null || true

echo ">> Removing systemd unit"
rm -f "$UNIT_PATH"
systemctl daemon-reload
systemctl reset-failed monitoring-agent.service 2>/dev/null || true

echo ">> Removing binary, config, and state"
rm -f "$INSTALL_BIN"
rm -rf "$CONF_DIR"
rm -rf "$STATE_DIR"

if id "$SVC_USER" >/dev/null 2>&1; then
    echo ">> Removing service user '${SVC_USER}'"
    userdel "$SVC_USER" 2>/dev/null || true
fi
if getent group "$SVC_USER" >/dev/null 2>&1; then
    groupdel "$SVC_USER" 2>/dev/null || true
fi

echo
echo "Done. The agent is removed from this machine."
