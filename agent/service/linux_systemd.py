"""systemd integration for running the agent as a Linux service (AGENT_SPEC.md §7).

Single source of truth for the unit file. ``build_unit`` renders it; the CLI
(``python -m agent.service.linux_systemd install|uninstall|render``) installs it
via ``systemctl``. The production installer (``install_linux.sh``) mirrors this
unit for the standalone PyInstaller binary where Python isn't present.

The unit runs as a dedicated unprivileged user with ``Restart=always`` so the
agent comes back after crashes and reboots, and applies standard systemd
hardening. Logs go to stderr, which journald captures (``journalctl -u
monitoring-agent``); the retry queue lives under the systemd ``StateDirectory``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

SERVICE_NAME = "monitoring-agent"
SERVICE_USER = "monitoring-agent"
DEFAULT_EXEC_PATH = "/usr/local/bin/monitoring-agent"
DEFAULT_CONFIG_PATH = "/etc/monitoring-agent/config.json"
DEFAULT_UNIT_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"
STATE_DIR = f"/var/lib/{SERVICE_NAME}"


def build_unit(
    exec_path: str = DEFAULT_EXEC_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    user: str = SERVICE_USER,
) -> str:
    """Render the systemd unit file text."""
    return f"""[Unit]
Description=Server health & backup monitoring agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
ExecStart={exec_path} --config {config_path}
Restart=always
RestartSec=5

# Persistent state (retry queue) — systemd creates {STATE_DIR} owned by the service user.
StateDirectory={SERVICE_NAME}
WorkingDirectory={STATE_DIR}

# Hardening: the agent only needs to read metrics and POST them out.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={STATE_DIR}
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
"""


def _run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def install(exec_path: str, config_path: str, unit_path: str = DEFAULT_UNIT_PATH) -> int:
    if os.geteuid() != 0:
        print("install requires root (try sudo)", file=sys.stderr)
        return 1
    with open(unit_path, "w", encoding="utf-8") as fh:
        fh.write(build_unit(exec_path, config_path))
    print(f"wrote {unit_path}")
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", SERVICE_NAME])
    print(f"{SERVICE_NAME} installed and started. Logs: journalctl -u {SERVICE_NAME} -f")
    return 0


def uninstall(unit_path: str = DEFAULT_UNIT_PATH) -> int:
    if os.geteuid() != 0:
        print("uninstall requires root (try sudo)", file=sys.stderr)
        return 1
    subprocess.run(["systemctl", "disable", "--now", SERVICE_NAME], check=False)
    if os.path.exists(unit_path):
        os.remove(unit_path)
        print(f"removed {unit_path}")
    _run(["systemctl", "daemon-reload"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.service.linux_systemd")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="print the systemd unit to stdout")
    p_render.add_argument("--exec", dest="exec_path", default=DEFAULT_EXEC_PATH)
    p_render.add_argument("--config", dest="config_path", default=DEFAULT_CONFIG_PATH)

    p_install = sub.add_parser("install", help="write unit, enable and start (root)")
    p_install.add_argument("--exec", dest="exec_path", default=DEFAULT_EXEC_PATH)
    p_install.add_argument("--config", dest="config_path", default=DEFAULT_CONFIG_PATH)

    sub.add_parser("uninstall", help="stop, disable and remove the unit (root)")

    args = parser.parse_args(argv)
    if args.cmd == "render":
        print(build_unit(args.exec_path, args.config_path))
        return 0
    if args.cmd == "install":
        return install(args.exec_path, args.config_path)
    return uninstall()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
