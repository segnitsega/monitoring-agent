# Installation guide

Build the agent **on the same OS** you will install it on. PyInstaller does not
cross-compile (a Linux box cannot produce a Windows `.exe`).

Python is needed **only to build**. After install, the service runs from the
binary and does not need Python.

The agent only needs **outbound HTTPS** to `backendUrl`. It does not open an
inbound port.

## 1. Prepare `config.json`

`config.example.json` is the repo template only. The agent and the installers
use **`config.json`**.

```bash
cp config.example.json config.json
```

Copies the template to the real config file.

Edit `config.json` and set at least:

| Field | What to put |
|---|---|
| `hostname` | Must match the portal inventory `ipOrHostname` |
| `registerSecret` | Shared agent register secret from the portal |
| `backendUrl` | Portal base URL, e.g. `https://portal.example.com` |
| `backup` | A checker, or `"backup": null` if you are not reporting backups |

Leave `serverId` and `token` empty. The agent registers on first start and
writes them back.

Use OS-specific paths (see Linux / Windows sections below). Keep `config.json`
next to the installer when you run it.

---

## Linux

Paths for `config.json` on Linux:

```json
"diskPaths": ["/"],
"queuePath": "/var/lib/monitoring-agent/retry_queue.db",
"logFile": null
```

`logFile: null` sends logs to journald. Do not use `/var/log/...` unless you
also make that path writable for the service.

### Build

```bash
python3 -m venv .venv
```

Creates an isolated Python environment.

```bash
. .venv/bin/activate
```

Activates that environment for this shell.

```bash
pip install -r requirements-dev.txt
```

Installs runtime deps plus PyInstaller.

```bash
pyinstaller build_pyinstaller.spec
```

Builds `dist/monitoring-agent`.

### Install

```bash
sudo ./install_linux.sh ./dist/monitoring-agent
```

Must run as root. Creates the `monitoring-agent` system user, copies the binary
to `/usr/local/bin/monitoring-agent`, copies **your** `config.json` to
`/etc/monitoring-agent/config.json`, creates `/var/lib/monitoring-agent` for
the retry queue, registers a systemd service, enables it on boot, and starts it.

If `/etc/monitoring-agent/config.json` already exists, it is left in place.

### Check

```bash
sudo systemctl status monitoring-agent
```

Shows whether the service is running.

```bash
journalctl -u monitoring-agent -f
```

Follows live logs. You should see registration (first start) then health posts.

### Uninstall

```bash
sudo systemctl disable --now monitoring-agent
```

Stops the service and disables it on boot.

```bash
sudo rm -f /etc/systemd/system/monitoring-agent.service
sudo systemctl daemon-reload
```

Removes the systemd unit.

```bash
sudo rm -f /usr/local/bin/monitoring-agent
sudo rm -rf /etc/monitoring-agent
sudo rm -rf /var/lib/monitoring-agent
sudo userdel monitoring-agent
```

Removes the binary, config (including the token), retry queue, and service user.

---

## Windows

Build and install **on the Windows PC**. Copy or clone this repo there first.

Paths for `config.json` on Windows:

```json
"diskPaths": ["C:\\"],
"queuePath": "C:\\ProgramData\\MonitoringAgent\\retry_queue.db",
"logFile": null
```

`logFile: null` sends logs to the Application Event Log.

### Build

In PowerShell, from the repo folder:

```powershell
python -m venv .venv
```

Creates an isolated Python environment. Requires Python 3.10+.

```powershell
.\.venv\Scripts\activate
```

Activates that environment for this shell.

```powershell
pip install -r requirements-dev.txt
```

Installs runtime deps plus PyInstaller (`pywin32` is required on Windows).

```powershell
pyinstaller build_pyinstaller.spec
```

Builds `dist\monitoring-agent.exe` and `dist\monitoring-agent-service.exe`.
The service host is the one the installer registers.

### Install

Open **PowerShell as Administrator**, from the repo folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Allows this PowerShell session to run the installer script.

```powershell
.\install_windows.ps1 -DistDir .\dist
```

Copies the binaries to `C:\Program Files\MonitoringAgent\`, copies **your**
`config.json` to `C:\ProgramData\MonitoringAgent\config.json`, registers the
`MonitoringAgent` Windows Service, sets it to start on boot, restarts it on
crash, and starts it.

If `C:\ProgramData\MonitoringAgent\config.json` already exists, it is left in
place.

### Check

```powershell
Get-Service MonitoringAgent
```

Shows whether the service is running.

```powershell
Get-WinEvent -LogName Application -ProviderName MonitoringAgent -MaxEvents 20
```

Shows recent logs. On older Windows:

```powershell
Get-EventLog -LogName Application -Source MonitoringAgent -Newest 20
```

### Uninstall

Administrator PowerShell:

```powershell
Stop-Service MonitoringAgent
```

Stops the running service.

```powershell
& "$env:ProgramFiles\MonitoringAgent\monitoring-agent-service.exe" remove
```

Unregisters the service from Windows.

```powershell
Remove-Item -Recurse -Force "$env:ProgramFiles\MonitoringAgent"
Remove-Item -Recurse -Force "$env:ProgramData\MonitoringAgent"
```

Deletes the binaries, config (including the token), and retry queue.

If the exe is already gone:

```powershell
sc.exe stop MonitoringAgent
sc.exe delete MonitoringAgent
```

---

## After install

| | Linux | Windows |
|---|---|---|
| Binary | `/usr/local/bin/monitoring-agent` | `C:\Program Files\MonitoringAgent\` |
| Config the service reads | `/etc/monitoring-agent/config.json` | `C:\ProgramData\MonitoringAgent\config.json` |
| Service name | `monitoring-agent` | `MonitoringAgent` |
| Logs | `journalctl -u monitoring-agent` | Application Event Log, source `MonitoringAgent` |

The service does not read `config.json` from the repo after install. It reads
the copy in the OS path above.
