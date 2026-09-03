# Monitoring Agent — Architecture & Operations Guide

A single, self-contained walkthrough of the monitoring agent: **how it's built**,
**what it emits**, **how it talks to the backend**, **how to install it on the
servers you want to monitor**, and **what every folder and file is for**.

> **Where this fits with the other docs**
> - **`AGENT_SPEC.md`** — the original build *specification* (the "what & why" requirements the agent was built to satisfy).
> - **`README.md`** — the day-to-day *usage* quickstart (config reference, run/build/test commands).
> - **`ARCHITECTURE.md`** (this file) — the *understanding* doc: the mental model, the data flow, and a file-by-file map of the codebase.

---

## 1. What the agent is, in one paragraph

The monitoring agent is a small, cross-platform (Linux + Windows) background
program that runs on each server you want to watch. Once every interval (default
**60 s**) it measures the host's health (CPU, memory, disk, network, uptime),
checks for evidence that the server's *existing* backup tooling ran, packages
both into one JSON document, and **POSTs it over HTTPS** to a central portal's
ingestion endpoint. It runs as a proper OS service (systemd on Linux, a Windows
Service on Windows), starts on boot, restarts on crash, and buffers data locally
when the backend is unreachable so nothing is lost. It **never performs backups
and never opens an inbound port** — it only reads local metrics and makes
outbound calls.

---

## 2. How the agent is built

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.10+** | One codebase for both operating systems. |
| Health metrics | [`psutil`](https://pypi.org/project/psutil/) | Same API on Windows & Linux for CPU/RAM/disk/net/boot time. |
| HTTP client | [`requests`](https://pypi.org/project/requests/) | POST to the ingestion API, with timeout + outcome classification. |
| Offline buffer | `sqlite3` (Python stdlib) | On-disk retry queue; survives restarts/reboots. |
| Windows service | `pywin32` | Runs the agent under the Windows Service Control Manager. Installed only on Windows. |
| Packaging | **PyInstaller** | Compiles to a single native binary per OS — **no Python runtime needed on the target server**. |
| Version | `1.0.0` | Defined in `agent/__init__.py` and `pyproject.toml`. |

**Build model.** The source is a normal Python package (`agent/`). For deployment
it's frozen with PyInstaller into standalone executables (see
`build_pyinstaller.spec`). PyInstaller does **not** cross-compile, so you build
on each target OS:

```bash
pip install pyinstaller
pyinstaller build_pyinstaller.spec
# -> dist/monitoring-agent            (the agent — all platforms)
# -> dist/monitoring-agent-service    (the Windows Service host — Windows builds only)
```

The result is a native binary you drop onto a server and register as a service —
the installers in §6 do the registering for you.

---

## 3. The runtime model (what happens each cycle)

Everything is driven by one resilient loop in `agent/main.py` (`AgentRunner`):

```
        ┌──────────────────────────────  every intervalSeconds  ──────────────────────────────┐
        │                                                                                      │
        ▼                                                                                      │
  collect_health(psutil) ─┐                                                                    │
                          ├─► build_payload() ─► Sender.send() ──► POST /api/v1/health         │
  run_backup_check()  ────┘                          │                                         │
                                                     │  classify HTTP outcome:                 │
                                                     ├─ 2xx ......... DELIVERED → drain retry queue (oldest first)
                                                     ├─ 401 / 403 ... DROP_AUTH  → discard payload, log clearly, keep collecting
                                                     ├─ other 4xx ... DROP_CLIENT→ discard payload (retry won't help)
                                                     └─ 429/5xx/net/timeout ... RETRY → enqueue in SQLite, resend next cycle
        │                                                                                      │
        └──────────────────────────────  sleep, then repeat  ──────────────────────────────────┘
```

Key resilience properties (all implemented, all unit-tested):

- **One bad cycle never kills the service** — exceptions in a cycle are logged and the loop continues (`AgentRunner.run_forever`).
- **Offline-tolerant** — failed sends are buffered to an on-disk SQLite queue and resent oldest-first once the backend recovers. Buffered items survive restarts/reboots and are dropped after a retention window (default **24 h**).
- **Auth-aware** — `401/403` means the token is wrong; retrying can't fix that, so the payload is dropped (with a clear log line) instead of hammering the backend, while collection continues so the queue is ready the moment the token is fixed.
- **Clean shutdown** — `SIGINT`/`SIGTERM` (what systemd and the Windows SCM send) set a stop event so the loop exits between cycles and closes the queue.

---

## 4. What the agent emits (the data contract)

The agent is a **client**: it doesn't answer requests, it *produces and sends* one
JSON payload per cycle. This shape is the authoritative contract — the backend's
`/api/v1/health` route and validator are expected to match it. It's assembled in
`agent/payload.py` from the health collector (`agent/collectors/health.py`) and
the backup checker (`agent/collectors/backup.py`).

```json
{
  "serverId": "srv-1029",
  "hostname": "db-prod-02",
  "os": "Linux",
  "timestamp": "2026-08-20T10:15:00Z",
  "health": {
    "cpuUsagePercent": 42.3,
    "memory": { "totalBytes": 16777216000, "usedBytes": 8123456000, "usagePercent": 48.4 },
    "disk": [
      { "path": "/", "totalBytes": 512000000000, "usedBytes": 215000000000, "usagePercent": 42.0 }
    ],
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

Field notes:
- All timestamps are **ISO-8601 UTC** (`YYYY-MM-DDTHH:MM:SSZ`), produced by `agent/timeutils.py`.
- `disk` is an **array** — one entry per configured mount/drive (`diskPaths`).
- `backup` is **`null`** when no backup checker is configured for that server.

### 4.1 Health block — `collectors/health.py`

| Field | Source (psutil) |
|---|---|
| `cpuUsagePercent` | `cpu_percent(interval=1)` (1 s sample; negligible vs. a 60 s cycle) |
| `memory.*` | `virtual_memory()` — total/used bytes + percent |
| `disk[]` | `disk_usage(path)` per configured path; an unavailable path is logged and skipped, not fatal |
| `network` | `net_io_counters()` for bytes sent/recv, plus an `isReachable` link-up flag from `net_if_stats()` |
| `uptimeSeconds` / `lastBootTime` | derived from `boot_time()` |

### 4.2 Backup block — `collectors/backup.py` (pluggable)

The agent inspects *evidence* left by whatever backup tool the server already
runs. The checker is selected in config (`backup.checker`), never hardcoded. Each
checker maps its findings to `{status, lastBackupTime, backupType, location, sizeBytes}`.

| Checker | What it inspects | `status` logic |
|---|---|---|
| `generic_path` | Newest file under a folder (or a single file) | Fresh file ⇒ `success`; stale ⇒ `failed`; missing/empty ⇒ `unknown` |
| `rsync_log` | An rsync `--stats` log | rsync completion markers ⇒ `success` (`incremental`); trailing `rsync error` ⇒ `failed`; stale log downgrades success ⇒ `failed` |
| `veeam_log` | A Veeam job log / session summary | `failed`/`error` ⇒ `failed`; in-flight ⇒ `in_progress`; `warning`/`success` ⇒ `success` |
| `windows_server_backup` | `Get-WBSummary` via PowerShell (Windows only) | `LastBackupResultHR == 0` ⇒ `success`, else `failed` |

Two safety rules make backup checking non-fatal:
- A configured checker that finds no usable evidence reports **`status: "unknown"`** rather than failing.
- `run_backup_check` traps *everything* (misconfig or unexpected error) and degrades to `unknown` — a flaky backup log can never stop health reporting.

Statuses are drawn from a fixed set: `success`, `failed`, `in_progress`, `unknown`.

---

## 5. How it's connected to the backend endpoint

**Transport:** `agent/transport/sender.py` (delivery) + `agent/transport/retry_queue.py` (offline buffer).

- **Endpoint.** The config holds a base `backendUrl`; the agent appends the fixed path, so it POSTs to **`{backendUrl}/api/v1/health`** (`AgentConfig.health_endpoint`).
- **Direction.** Strictly **outbound HTTPS, agent-initiated**. The backend never connects into the monitored server, so the only firewall rule needed is outbound 443. No inbound port is ever opened.
- **Headers.** Every request carries:
  ```
  Authorization: Bearer <per-server-token>
  Content-Type: application/json
  ```
- **Authentication.** The token is issued per-server by the portal at registration and stored in `config.json`. The agent only ever *holds and sends* it — it never generates or derives one. The token is placed **only** in the request header and is **never logged**.
- **Response handling.** The HTTP status is classified into an action for the loop (`SendOutcome`):

  | Backend response | Outcome | Agent behaviour |
  |---|---|---|
  | `2xx` | `DELIVERED` | Success; then drain any buffered payloads oldest-first. |
  | `401` / `403` | `DROP_AUTH` | Bad/revoked token — drop the payload, log a clear error, **stop** retrying it (keep collecting). |
  | `429`, `5xx`, network error, timeout | `RETRY` | Transient — buffer to SQLite and resend on later cycles. |
  | other `4xx` | `DROP_CLIENT` | Malformed request — drop (retrying won't help). |

- **Offline buffer.** The retry queue is a small SQLite database (WAL mode) storing `{id, payload, created_at}`. `peek` returns the oldest non-expired item; `purge_expired` drops anything past `retryRetentionHours` (default 24 h) so it never grows unbounded. Because it's on disk, a backend outage that spans a reboot still loses no data (within the retention window).

---

## 6. How to install the agent on a monitored server

Two steps: **(1)** get a binary onto the box, **(2)** run the OS installer, which
registers the service and seeds a config you then fill in with the server's
`serverId`, `token`, and `backendUrl`.

### 6.0 Prerequisite — build the binary (once per OS)

```bash
pip install -r requirements-dev.txt
pyinstaller build_pyinstaller.spec        # produces ./dist/monitoring-agent[.exe]
```

Ship the resulting binary (and, on Windows, `monitoring-agent-service.exe`) to the target server alongside `config.example.json` and the installer script.

### 6.1 Linux (systemd) — `install_linux.sh`

```bash
sudo ./install_linux.sh ./dist/monitoring-agent
sudo nano /etc/monitoring-agent/config.json     # set hostname, registerSecret, backendUrl
sudo systemctl restart monitoring-agent
journalctl -u monitoring-agent -f               # watch it run
```

What the installer does:
- Creates a dedicated, unprivileged **`monitoring-agent`** system user (no home, no shell).
- Installs the binary to **`/usr/local/bin/monitoring-agent`**.
- Seeds **`/etc/monitoring-agent/config.json`** from `config.example.json` with **`chmod 600`** (it holds the token).
- Creates state dir **`/var/lib/monitoring-agent`** (owned by the service user) for the retry queue.
- Writes a systemd unit with `Restart=always`, `RestartSec=5`, `WantedBy=multi-user.target`, and hardening (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, `ReadWritePaths=/var/lib/monitoring-agent`, …), then `daemon-reload` + `enable --now`.

> **Logging on Linux:** because the unit is hardened with `ProtectSystem=strict` and only `/var/lib/monitoring-agent` is writable, prefer **stderr → journald** for logs (`journalctl -u monitoring-agent`). If you set `logFile` in config, point it inside a writable path (e.g. under `/var/lib/monitoring-agent/`); otherwise logging safely falls back to stderr rather than failing.

### 6.2 Windows (Windows Service) — `install_windows.ps1`

```powershell
# From an elevated PowerShell:
.\install_windows.ps1 -DistDir .\dist
notepad $env:ProgramData\MonitoringAgent\config.json   # set hostname, registerSecret, backendUrl
Restart-Service MonitoringAgent
Get-Service MonitoringAgent
```

What the installer does:
- Copies `monitoring-agent-service.exe` (and `monitoring-agent.exe` if present) to **`C:\Program Files\MonitoringAgent\`**.
- Seeds **`C:\ProgramData\MonitoringAgent\config.json`** from `config.example.json` and restricts its ACL to **Administrators + SYSTEM**.
- Registers the **`MonitoringAgent`** Windows Service, sets it to **auto-start**, and configures **failure recovery** (restart on crash).
- Starts the service via `Start-Service` (SCM), then prints the resulting status.

The service reports `SERVICE_RUNNING` to the SCM before registration, retries
config/registration errors instead of exiting, and runs with working directory
`C:\ProgramData\MonitoringAgent` so a relative `queuePath` does not land in
`C:\Windows\System32`. Logs go to **`C:\ProgramData\MonitoringAgent\agent.log`**
(even when `logFile` is `null`) and to the Application Event Log (source
`MonitoringAgent`).

### 6.3 Config you must fill in (minimum)

`backendUrl` is required. If `token` is empty, `registerSecret` and a
`hostname` that matches inventory `ipOrHostname` are required — the agent
registers on first start and writes `serverId` / `token` into the file.
Validate a config without starting the service:

```bash
monitoring-agent --config /etc/monitoring-agent/config.json --check-config
```

The full config key reference lives in **`README.md` → Configuration**. A ready
template is `config.example.json`.

### 6.4 Where things live after install

| | Linux | Windows |
|---|---|---|
| Binary | `/usr/local/bin/monitoring-agent` | `C:\Program Files\MonitoringAgent\monitoring-agent-service.exe` |
| Config | `/etc/monitoring-agent/config.json` (0600) | `C:\ProgramData\MonitoringAgent\config.json` (ACL: Admin+SYSTEM) |
| Retry queue (state) | `/var/lib/monitoring-agent/retry_queue.db` | path from `queuePath` in config |
| Service name | `monitoring-agent` (systemd) | `MonitoringAgent` (SCM) |
| Logs | `journalctl -u monitoring-agent` | `C:\ProgramData\MonitoringAgent\agent.log` (+ Application Event Log, source `MonitoringAgent`) |

---

## 7. Architecture — layered design

The code is organized in clean layers, each depending only on the ones below it.
This is what keeps it easy to extend (add a metric or a backup checker) without
touching transport, auth, or the service wrappers.

```
                       ┌───────────────────────────────────────────────┐
   Service layer       │  service/linux_systemd.py   service/windows_   │   ← how the OS starts/stops/keeps
   (OS integration)    │                             service.py         │     the agent alive
                       └───────────────────────┬───────────────────────┘
                                                │ runs
                       ┌────────────────────────▼──────────────────────┐
   Orchestration       │  main.py  — AgentRunner: the loop, the         │   ← ties everything together
                       │  collect → build → deliver → flush policy      │
                       └───┬───────────────┬───────────────────┬────────┘
                           │               │                   │
             ┌─────────────▼──┐   ┌─────────▼────────┐   ┌──────▼──────────────┐
   Domain    │  collectors/   │   │   payload.py     │   │    transport/       │
   layers    │  health.py     │   │ (data contract)  │   │  sender.py (HTTPS)  │
             │  backup.py     │   │                  │   │  retry_queue.py     │
             └─────────────┬──┘   └──────────────────┘   └──────┬──────────────┘
                           │                                    │
             ┌─────────────▼────────────────────────────────────▼──────────────┐
   Foundation│  config.py   logging_setup.py   timeutils.py   __init__.py        │
             └──────────────────────────────────────────────────────────────────┘
```

- **Foundation** — config loading/validation, logging setup, time formatting, version. No knowledge of metrics or HTTP.
- **Domain** — *collectors* gather data; *payload* shapes it to the contract; *transport* delivers it and buffers on failure. None of them know about the loop or the OS service.
- **Orchestration** — `AgentRunner` sequences a cycle and owns the deliver/buffer/flush policy.
- **Service** — thin OS-specific wrappers that launch `AgentRunner` and keep it running as a managed service.

---

## 8. Folder structure — file by file

```
monitoring-agent/
├── agent/                        # the Python package (all agent source)
│   ├── __init__.py               # package doc + __version__ = "1.0.0"
│   ├── __main__.py               # enables `python -m agent` → calls main()
│   ├── main.py                   # ★ entrypoint: CLI args, AgentRunner, the collection loop & delivery policy
│   ├── config.py                 # load/validate config.json → typed AgentConfig; derives health_endpoint; perms check
│   ├── logging_setup.py          # rotating-file + stderr logging; never lets logging crash the agent
│   ├── timeutils.py              # ISO-8601 UTC timestamp helpers (the contract's time format)
│   ├── payload.py                # ★ assembles the JSON data contract from health + backup
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── health.py             # ★ CPU/RAM/disk/network/uptime via psutil → the "health" block
│   │   └── backup.py             # ★ pluggable backup-evidence checkers → the "backup" block
│   ├── transport/
│   │   ├── __init__.py
│   │   ├── sender.py             # ★ HTTPS POST + Bearer auth + response→outcome classification
│   │   ├── register.py           # ★ first-run POST /api/v1/agent/register → persist token
│   │   └── retry_queue.py        # ★ SQLite offline buffer (FIFO, 24h retention, survives reboots)
│   └── service/
│       ├── __init__.py
│       ├── linux_systemd.py      # systemd unit generation + install/uninstall/render CLI
│       └── windows_service.py    # Windows Service wrapper (pywin32); bridges SCM stop → loop stop
│
├── tests/                        # ~70 unit tests; psutil/HTTP/systemd/pywin32 all mocked (no network/root)
│   ├── test_config.py            #   validates config parsing & error reporting
│   ├── test_health.py            #   health collector
│   ├── test_backup.py            #   each backup checker
│   ├── test_payload.py           #   contract shape
│   ├── test_sender.py            #   HTTP outcome classification
│   ├── test_register.py          #   bootstrap register + persist
│   ├── test_retry_queue.py       #   enqueue/peek/purge/retention
│   ├── test_main.py              #   loop deliver/buffer/flush policy
│   ├── test_service_linux.py     #   systemd unit rendering
│   └── test_service_windows.py   #   Windows service wrapper (mocked)
│
├── config.example.json           # template config (placeholder token) — copied in by the installers
├── AGENT_SPEC.md                 # the original build specification (requirements & design decisions)
├── README.md                     # usage quickstart: config reference, run/build/test commands
├── ARCHITECTURE.md               # this document
├── build_pyinstaller.spec        # PyInstaller build → single-file binaries (agent + Windows service host)
├── install_linux.sh              # systemd installer (user, binary, config, state dir, unit, enable)
├── install_windows.ps1           # Windows Service installer (copy, config ACL, register, recovery, start)
├── pyproject.toml                # package metadata, deps, entry point (monitoring-agent = agent.main:main), tooling
├── requirements.txt              # runtime deps (psutil, requests; pywin32 on Windows only)
├── requirements-dev.txt          # dev deps (pytest, pyinstaller, …)
└── .gitignore                    # ignores config.json (real token), build artifacts, venv, caches
```

★ = the files to read first to understand the core behaviour.

**A note on the two service paths.** `agent/service/linux_systemd.py` is the
in-package way to render/install a systemd unit *when running from source*; the
production `install_linux.sh` writes an equivalent unit for the compiled binary
(where Python isn't installed). They intentionally mirror each other. On Windows,
`agent/service/windows_service.py` *is* the service host — it's compiled into
`monitoring-agent-service.exe` and registered by `install_windows.ps1`.

---

## 9. Configuration at a glance

Full reference is in `README.md`; the essentials:

| Key | Required | Default | Purpose |
|---|---|---|---|
| `backendUrl` | ✅ | — | Base URL; `/api/v1/health` (and register) are appended. |
| `hostname` | if no token | OS hostname | Must match inventory `ipOrHostname`. |
| `registerSecret` | if no token | — | Shared secret for `POST /api/v1/agent/register`. |
| `serverId` | with token | issued | Stable server UUID from the portal. |
| `token` | or register | issued | Per-server bearer token. Empty → register on start. |
| `intervalSeconds` | | `60` | Seconds between collection cycles. |
| `timeoutSeconds` | | `10` | HTTP request timeout. |
| `diskPaths` | | `["/"]` / `["C:\\"]` | Mounts/drives to report (one `disk[]` entry each). |
| `retryRetentionHours` | | `24` | Drop buffered payloads older than this. |
| `queuePath` | | `retry_queue.db` | SQLite offline-queue path. |
| `logFile` | | `null` | Log file path; `null` → stderr (journald / Event Log). |
| `logLevel` | | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `backup` | | `null` | `{ "checker": <name>, "options": {…} }`; `null` = no backup check. |

Config validation reports **all** problems at once (so you fix the file in one
pass) and refuses to start without either a token or a registration secret.

---

## 10. Build, run, and test quick reference

```bash
# Dev setup
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# Run from source
python -m agent --config config.json              # run the loop
python -m agent --config config.json --once       # one cycle then exit
python -m agent --config config.json --check-config
python -m agent --version

# Test (no network or root needed — everything external is mocked)
pytest

# Build standalone binaries (run on each target OS)
pyinstaller build_pyinstaller.spec
```

---

## 11. Extending the agent

The layered design means the two most common changes are local and low-risk:

- **Support a new backup tool** → add one checker function in
  `agent/collectors/backup.py` and register it in the `_CHECKERS` dict. Nothing
  else changes; select it via `backup.checker` in config.
- **Add a new health metric** → extend `collect_health` in
  `agent/collectors/health.py` (and the contract in `payload.py`). The loop,
  transport, auth, and service layers are untouched.

Everything the agent does **not** do — running backups, storing history,
rendering dashboards, sending alerts/emails, agentless SSH/WinRM monitoring — is
deliberately out of scope and handled by the backend/portal (see
`AGENT_SPEC.md` §11).
```
