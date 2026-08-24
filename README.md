# Monitoring Agent

A lightweight, cross-platform (Linux + Windows) agent that collects **server
health metrics** and **backup evidence** and pushes them to a monitoring
portal's ingestion API. It runs as a background OS service, uses a single
codebase for both platforms, and is designed to keep a negligible footprint on
the servers it watches.

The agent **does not perform backups** — it verifies evidence of the backups
your existing tooling (rsync, Veeam, Windows Server Backup, …) already produces.

---

## How it works

Every `intervalSeconds` the agent runs one cycle:

```
collect health (psutil)  ─┐
                          ├─►  build payload  ─►  POST /api/v1/health
run backup checker       ─┘                          │
                                                      ├─ 2xx  → drain offline queue
                                                      ├─ 401/403 → drop (bad token)
                                                      └─ 5xx/network/timeout → buffer & retry
```

- **Resilient loop** — an unexpected error in one cycle is logged and the loop
  continues; it never crashes the service.
- **Offline tolerant** — if the backend is unreachable, payloads are buffered in
  an on-disk SQLite queue and resent oldest-first once it recovers. Buffered
  payloads survive restarts and reboots and are dropped after a retention window
  (default 24h).
- **Auth-aware** — `401/403` means a bad per-server token; the agent stops
  retrying that payload (retrying won't help) but keeps collecting.

---

## Requirements

- Python **3.10+** (only for building/development — deployed binaries bundle their
  own runtime).
- [`psutil`](https://pypi.org/project/psutil/), [`requests`](https://pypi.org/project/requests/).
- `pywin32` on Windows (for the Windows Service wrapper).

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

---

## Configuration

Copy `config.example.json` to `config.json` and edit it. The file holds
secrets — keep it readable only by the service account (the installers do
this for you: `chmod 600` on Linux, a restrictive ACL on Windows).

On first start, if `token` is empty, the agent `POST`s
`/api/v1/agent/register` with `registerSecret` and `hostname`, then writes
the issued `serverId` / `token` back to `config.json`. After that it uses
`Authorization: Bearer <token>` on `/api/v1/health`. The inventory row must
already exist (`ipOrHostname` = `hostname`). Re-registering rotates the token.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `backendUrl` | string | — (required) | Base URL; `/api/v1/health` and `/api/v1/agent/register` are appended. |
| `hostname` | string | OS hostname | Must match the portal inventory `ipOrHostname` at register time. |
| `registerSecret` | string | — (required if no token) | Shared `AGENT_REGISTER_SECRET`; same on every agent. |
| `serverId` | string | — (issued at register) | Portal server UUID. Required once `token` is set. |
| `token` | string | — (issued at register) | Per-server bearer token. Empty/placeholder triggers registration. |
| `intervalSeconds` | int | `60` | Seconds between collection cycles. |
| `timeoutSeconds` | number | `10` | HTTP request timeout. |
| `diskPaths` | string[] | `["/"]` / `["C:\\"]` | Mount points to report. |
| `retryRetentionHours` | number | `24` | Drop buffered payloads older than this. |
| `queuePath` | string | `retry_queue.db` | SQLite offline-queue path. |
| `logFile` | string\|null | `null` | Log file path; `null` → stderr (journald/EventLog). |
| `logLevel` | string | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `backup` | object\|null | `null` | Backup checker config (see below); `null` = none. |

Validate a config without starting the agent:

```bash
monitoring-agent --config config.json --check-config
```

### Backup checkers

Backup tooling varies per server, so the checker is configurable, not
hardcoded. Set `backup.checker` and its `options`:

| Checker | What it inspects | Key options |
|---------|------------------|-------------|
| `generic_path` | Newest file under a folder (or a single file); fresh ⇒ success | `path`, `freshnessHours` (26) |
| `rsync_log` | An rsync `--stats` log's last run | `logPath`, `freshnessHours` |
| `veeam_log` | A Veeam job log / session summary | `logPath`, `freshnessHours` |
| `windows_server_backup` | `Get-WBSummary` (Windows only) | — |

A configured checker that finds no usable evidence reports `status: "unknown"`
rather than failing the agent. Adding support for a new backup tool is one
function plus one registry entry in `agent/collectors/backup.py`.

---

## Data contract

One payload is sent per cycle. All timestamps are ISO-8601 UTC; `disk` is an
array; `backup` may be `null`.

```json
{
  "serverId": "srv-1029",
  "hostname": "db-prod-02",
  "os": "Linux",
  "timestamp": "2026-08-20T10:15:00Z",
  "health": {
    "cpuUsagePercent": 42.3,
    "memory": { "totalBytes": 16777216000, "usedBytes": 8123456000, "usagePercent": 48.4 },
    "disk": [ { "path": "/", "totalBytes": 512000000000, "usedBytes": 215000000000, "usagePercent": 42.0 } ],
    "network": { "bytesSent": 998877665, "bytesRecv": 5544332211, "isReachable": true },
    "uptimeSeconds": 1032945,
    "lastBootTime": "2026-08-08T02:11:03Z"
  },
  "backup": {
    "status": "success",
    "lastBackupTime": "2026-08-20T02:00:11Z",
    "backupType": "incremental",
    "location": "/backups/db-prod-02/2026-08-20.tar.gz",
    "sizeBytes": 4831928321
  }
}
```

Sent with headers `Authorization: Bearer <token>` and
`Content-Type: application/json`.

---

## Running

From source (development):

```bash
python -m agent --config config.json          # run the loop
python -m agent --config config.json --once   # a single cycle, then exit
python -m agent --version
```

---

## Building standalone binaries

Produces a single executable per OS (no Python required on the target). Build on
each target OS — PyInstaller does not cross-compile.

```bash
pip install pyinstaller
pyinstaller build_pyinstaller.spec
# -> dist/monitoring-agent            (all platforms)
# -> dist/monitoring-agent-service    (Windows only)
```

---

## Installing as a service

### Linux (systemd)

```bash
sudo ./install_linux.sh ./dist/monitoring-agent
sudo nano /etc/monitoring-agent/config.json   # set hostname, registerSecret, backendUrl
sudo systemctl restart monitoring-agent
journalctl -u monitoring-agent -f
```

Runs as a dedicated unprivileged `monitoring-agent` user with `Restart=always`
and systemd hardening. The retry queue lives under `/var/lib/monitoring-agent`.

### Windows

```powershell
# From an elevated PowerShell:
.\install_windows.ps1 -DistDir .\dist
notepad $env:ProgramData\MonitoringAgent\config.json   # set hostname, registerSecret, backendUrl
Restart-Service MonitoringAgent
Get-Service MonitoringAgent
```

Registers an auto-starting Windows Service that restarts on failure.

---

## Security notes

- The per-server token is only ever placed in the request `Authorization`
  header — it is never logged. The registration secret is only sent in the
  register body.
- `config.json` holds secrets and is gitignored; only `config.example.json`
  (with placeholders) is committed. The installers restrict its permissions;
  the agent warns at startup if the file is group/world-readable.
- The agent makes only outbound HTTPS requests and reads local metrics/logs.

---

## Testing

```bash
pytest            # ~70 unit tests, no network or root required
```

Collectors, transport, queue, and service tooling are all unit-tested with
psutil/HTTP/systemd/pywin32 mocked, so the suite runs anywhere.

---

## Project layout

```
agent/
  main.py                  # CLI + collection loop + delivery policy
  config.py                # typed config loader with validation
  logging_setup.py         # rotating file / stderr logging
  timeutils.py             # ISO-8601 UTC helpers
  payload.py               # payload assembly (data contract)
  collectors/
    health.py              # CPU/mem/disk/net/uptime via psutil
    backup.py              # pluggable backup-evidence checkers
  transport/
    sender.py              # HTTPS POST + outcome classification
    register.py            # first-run POST /api/v1/agent/register
    retry_queue.py         # SQLite offline queue
  service/
    linux_systemd.py       # systemd unit generation/install
    windows_service.py     # Windows Service wrapper (pywin32)
build_pyinstaller.spec     # single-file binary build
install_linux.sh           # systemd installer
install_windows.ps1        # Windows Service installer
tests/                     # unit tests
```

---

## Extensibility

- **New backup tool** → add a checker function + registry entry in
  `agent/collectors/backup.py`.
- **New metric** → extend `agent/collectors/health.py` and the payload; the loop
  is unchanged.
