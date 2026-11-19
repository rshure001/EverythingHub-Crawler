# Read-only Windows preflight for the Everything Hub self-owned host.
# This script does not install software, change DNS, open firewall ports, or start services.
$ErrorActionPreference = "Stop"
$Failed = $false

function Pass($Message) { Write-Host "OK: $Message" -ForegroundColor Green }
function Warn($Message) { Write-Host "CHECK: $Message" -ForegroundColor Yellow }
function Fail($Message) { Write-Host "MISSING: $Message" -ForegroundColor Red; $script:Failed = $true }

Write-Host "Everything Hub Windows host preflight" -ForegroundColor Cyan
Write-Host "Read-only checks only; no system or network settings will be changed."
Write-Host ""

$os = Get-CimInstance Win32_OperatingSystem
Pass ("Windows detected: " + $os.Caption + " (" + $os.Version + ")")

$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
if ($ramGB -ge 4) { Pass ("Installed RAM: $ramGB GB") }
elseif ($ramGB -ge 2) { Warn ("Installed RAM: $ramGB GB; workable for a light test, but 4 GB+ is preferred") }
else { Fail ("Installed RAM: $ramGB GB; at least 2 GB is required") }

$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"
$freeGB = [math]::Round($disk.FreeSpace / 1GB, 1)
if ($freeGB -ge 20) { Pass ("Free system-drive space: $freeGB GB") }
elseif ($freeGB -ge 10) { Warn ("Free system-drive space: $freeGB GB; 20 GB+ is preferred") }
else { Fail ("Free system-drive space: $freeGB GB; at least 10 GB is required") }

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wsl) {
    $wslStatus = & wsl.exe --status 2>&1
    if ($LASTEXITCODE -eq 0) { Pass "WSL is available"; $wslStatus | ForEach-Object { Write-Host "  $_" } }
    else { Warn "WSL command exists, but WSL status needs attention" }
    $distros = & wsl.exe --list --quiet 2>&1
    if ($LASTEXITCODE -eq 0 -and ($distros -join "").Trim()) {
        Pass "A WSL Linux distribution is installed"
        $distros | ForEach-Object { Write-Host "  $_" }
    } else { Warn "No WSL Linux distribution was detected" }
} else { Fail "WSL is not available; the host stack requires a Linux container environment (WSL2 with Docker Desktop)" }

$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($docker) {
    Pass "Docker CLI found"
    & docker.exe version
    if ($LASTEXITCODE -eq 0) {
        Pass "Docker engine is responding"
        & docker.exe compose version
        if ($LASTEXITCODE -eq 0) { Pass "Docker Compose is available" }
        else { Fail "Docker Compose plugin is unavailable" }
    } else { Fail "Docker is installed but its engine is not running; Docker Desktop must be started" }
} else { Fail "Docker CLI not found; Docker Desktop is not installed or not on PATH" }

Write-Host ""
if ($Failed) {
    Write-Host "Preflight found prerequisites to resolve. Nothing was installed or changed." -ForegroundColor Yellow
    exit 1
}
Write-Host "Preflight passed. This checks local readiness only; public hosting is not active." -ForegroundColor Green
