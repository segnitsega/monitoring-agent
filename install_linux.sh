#!/usr/bin/env bash
#
# Install the monitoring agent as a systemd service on Linux.
# Run as root:  sudo ./install_linux.sh [path-to-binary]
#
# The systemd unit written here mirrors agent/service/linux_systemd.py.
set -euo pipefail

BIN_SRC="${1:-./dist/monitoring-agent}"
SVC_USER="monitoring-agent"
INSTALL_BIN="/usr/local/bin/monitoring-agent"
CONF_DIR="/etc/monitoring-agent"
CONF_FILE="${CONF_DIR}/config.json"
STATE_DIR="/var/lib/monitoring-agent"
UNIT_PATH="/etc/systemd/system/monitoring-agent.service"

if [[ $EUID -ne 0 ]]; then
    echo "This installer must be run as root (try: sudo $0)" >&2
    exit 1
fi

if [[ ! -f "$BIN_SRC" ]]; then
    echo "Agent binary not found at '$BIN_SRC'." >&2
    echo "Build it first:  pyinstaller build_pyinstaller.spec" >&2
    exit 1
fi

echo ">> Creating service user '${SVC_USER}' (if missing)"
if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

echo ">> Installing binary to ${INSTALL_BIN}"
install -m 0755 "$BIN_SRC" "$INSTALL_BIN"

echo ">> Preparing ${CONF_DIR}"
mkdir -p "$CONF_DIR"
if [[ ! -f "$CONF_FILE" ]]; then
    install -m 0600 ./config.example.json "$CONF_FILE"
    echo "   Wrote example config to ${CONF_FILE} (chmod 600)."
    echo "   *** EDIT ${CONF_FILE}: set hostname (must match inventory ipOrHostname),"
    echo "       registerSecret, and backendUrl. Token is issued on first start. ***"
else
    chmod 600 "$CONF_FILE"
    echo "   Existing ${CONF_FILE} left in place (permissions set to 600)."
fi

echo ">> Preparing state dir ${STATE_DIR}"
mkdir -p "$STATE_DIR"
chown "${SVC_USER}:${SVC_USER}" "$STATE_DIR"

echo ">> Writing systemd unit ${UNIT_PATH}"
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Server health & backup monitoring agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SVC_USER}
Group=${SVC_USER}
ExecStart=${INSTALL_BIN} --config ${CONF_FILE}
Restart=always
RestartSec=5

StateDirectory=monitoring-agent
WorkingDirectory=${STATE_DIR}

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${STATE_DIR}
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

echo ">> Reloading systemd and enabling service"
systemctl daemon-reload
systemctl enable --now monitoring-agent.service || {
    echo "Service enabled but failed to start — likely config.json still has placeholder values." >&2
    echo "Edit ${CONF_FILE} then run: systemctl restart monitoring-agent" >&2
}

echo
echo "Done. Useful commands:"
echo "  systemctl status monitoring-agent"
echo "  journalctl -u monitoring-agent -f"
