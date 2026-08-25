# Installation guide

Build on the **same OS** you will install on. Python is only needed to build.
After install the service runs on its own and only needs outbound HTTPS to
`backendUrl`.

Create the server in the portal first. `hostname` in `config.json` must match
inventory `ipOrHostname`.

## Config

```bash
cp config.example.json config.json
```

Edit `config.json`. Set `hostname`, `registerSecret`, and `backendUrl`. Leave
`serverId` and `token` empty. Set `logFile` to `null`. Set `"backup": null`
unless you want backup evidence (then point `backup.options.path` at a real
folder on that machine).

Linux:

```json
"diskPaths": ["/"],
"queuePath": "/var/lib/monitoring-agent/retry_queue.db",
"logFile": null
```

Windows:

```json
"diskPaths": ["C:\\"],
"queuePath": "C:\\ProgramData\\MonitoringAgent\\retry_queue.db",
"logFile": null
```

Keep `config.json` next to the installer.

## Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pyinstaller build_pyinstaller.spec
sudo ./install_linux.sh ./dist/monitoring-agent
```

```bash
sudo systemctl status monitoring-agent
journalctl -u monitoring-agent -f
```

Success looks like `agent registered` then `payload delivered`. Restart with
`sudo systemctl restart monitoring-agent`.

```bash
sudo ./uninstall_linux.sh
```

Removes the service, binary, config, retry queue, and service user.

## Windows

Copy the repo onto the Windows PC. Python 3.10+. **Administrator** PowerShell,
from the repo folder:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller build_pyinstaller.spec
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1 -DistDir .\dist
```

```powershell
Get-Service MonitoringAgent
Get-WinEvent -LogName Application -ProviderName MonitoringAgent -MaxEvents 20
```

Older Windows: `Get-EventLog -LogName Application -Source MonitoringAgent -Newest 20`

```powershell
Stop-Service MonitoringAgent
& "$env:ProgramFiles\MonitoringAgent\monitoring-agent-service.exe" remove
Remove-Item -Recurse -Force "$env:ProgramFiles\MonitoringAgent"
Remove-Item -Recurse -Force "$env:ProgramData\MonitoringAgent"
```

If the exe is already gone:

```powershell
sc.exe stop MonitoringAgent
sc.exe delete MonitoringAgent
```
