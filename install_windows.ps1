#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the monitoring agent as a Windows Service.
.DESCRIPTION
    Copies the built binaries to Program Files, seeds a config under
    ProgramData, registers the Windows Service via the service host binary, and
    configures it to auto-start and restart on failure.
.EXAMPLE
    .\install_windows.ps1
    .\install_windows.ps1 -DistDir .\dist
#>
param(
    [string]$DistDir = ".\dist",
    [string]$InstallDir = "$env:ProgramFiles\MonitoringAgent",
    [string]$ConfigDir = "$env:ProgramData\MonitoringAgent"
)

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot

$agentExe   = Join-Path $DistDir "monitoring-agent.exe"
$serviceExe = Join-Path $DistDir "monitoring-agent-service.exe"

$configSrc = @(
    (Join-Path $scriptDir "config.json"),
    (Join-Path (Get-Location) "config.json")
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $configSrc) {
    Write-Error "config.json not found next to the installer or in the current directory. Copy config.example.json to config.json, fill it in, then re-run the installer."
}

if (-not (Test-Path $serviceExe)) {
    Write-Error "Service binary not found at '$serviceExe'. Build it first: pyinstaller build_pyinstaller.spec"
}

Write-Host ">> Creating $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item $serviceExe $InstallDir -Force
if (Test-Path $agentExe) { Copy-Item $agentExe $InstallDir -Force }

Write-Host ">> Preparing $ConfigDir"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$configFile = Join-Path $ConfigDir "config.json"
if (-not (Test-Path $configFile)) {
    Copy-Item $configSrc $configFile -Force
    # Restrict the config (it holds a token) to Administrators + SYSTEM.
    icacls $configFile /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" | Out-Null
    Write-Host "   Installed config.json to $configFile"
} else {
    Write-Host "   Existing $configFile left in place."
}

$installedService = Join-Path $InstallDir "monitoring-agent-service.exe"

Write-Host ">> Registering Windows Service"
& $installedService install

# Auto-start, and restart automatically on failure.
sc.exe config MonitoringAgent start= auto | Out-Null
sc.exe failure MonitoringAgent reset= 60 actions= restart/5000/restart/5000/restart/5000 | Out-Null

Write-Host ">> Starting service"
try {
    Start-Service MonitoringAgent
    Start-Sleep -Seconds 2
    $svc = Get-Service MonitoringAgent
    Write-Host "   Service Status: $($svc.Status)"
} catch {
    Write-Warning "Service registered but failed to start."
    Write-Warning "Check $ConfigDir\agent.log for details."
}

Write-Host ""
Write-Host "Done. Useful commands:"
Write-Host "  Get-Service MonitoringAgent"
Write-Host "  Get-Content `"$ConfigDir\agent.log`" -Tail 30"
Write-Host "  Get-EventLog -LogName Application -Source MonitoringAgent -Newest 20"
