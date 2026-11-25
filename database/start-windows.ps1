# Everything Hub isolated PostgreSQL launcher for Windows PowerShell 5.1+
# Safe by default: never deletes volumes, never changes Supabase, never exposes PostgreSQL publicly.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Compose = Join-Path $Here "docker-compose.yml"
$EnvFile = Join-Path $Here ".env"

function New-Secret([int]$Bytes = 48) {
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
  return ([System.BitConverter]::ToString($buffer) -replace "-", "").ToLowerInvariant()
}

Write-Host "Everything Hub independent database — Windows setup" -ForegroundColor Cyan
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker CLI is not installed. Install/start Docker Desktop, then run this script again."
}
$dockerInfo = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Docker Desktop engine is not running. Start Docker Desktop; this script will not install software or change your machine."
}
if (-not (Test-Path $Compose)) { throw "Missing compose file: $Compose" }

# Refuse to overwrite existing credentials or silently attach to an unknown deployment.
if (-not (Test-Path $EnvFile)) {
  $dbPassword = New-Secret
  $apiToken = New-Secret
  $envText = @"
EH_DB_PASSWORD=$dbPassword
EH_SEARCH_API_TOKEN=$apiToken
DATABASE_URL=postgresql://everythinghub:$dbPassword@127.0.0.1:55432/everythinghub
"@
  [System.IO.File]::WriteAllText($EnvFile, $envText, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "Created a private .env with new random credentials (not committed to Git)." -ForegroundColor Green
} else {
  Write-Host "Existing database/.env found; preserving its credentials." -ForegroundColor Yellow
  $rawEnv = Get-Content $EnvFile -Raw
  if ($rawEnv -notmatch '(?m)^EH_DB_PASSWORD=.+$' -or $rawEnv -notmatch '(?m)^EH_SEARCH_API_TOKEN=.+$') {
    throw "Existing .env is incomplete. It was not changed. Add EH_DB_PASSWORD and EH_SEARCH_API_TOKEN locally."
  }
}

# Guard against taking over ports owned by another application.
foreach ($port in @(55432,58080)) {
  $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) {
    $owned = (& docker ps --filter "publish=$port" --format "{{.Names}}" 2>$null)
    if (-not $owned) { throw "Port $port is already used by another process (PID $($listener.OwningProcess)). Nothing was stopped or changed." }
  }
}

Push-Location $Here
try {
  & docker compose -f $Compose up -d --build
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed. Existing volumes were not deleted." }

  $ready = $false
  for ($i = 0; $i -lt 36; $i++) {
    $dbHealth = (& docker inspect --format "{{.State.Health.Status}}" everythinghub-db 2>$null)
    $apiHealth = (& docker inspect --format "{{.State.Health.Status}}" everythinghub-search 2>$null)
    if ($dbHealth -eq "healthy" -and $apiHealth -eq "healthy") { $ready = $true; break }
    Start-Sleep -Seconds 5
  }
  if (-not $ready) {
    & docker compose -f $Compose ps
    throw "Services did not become healthy within 3 minutes. Data volume was preserved; inspect logs with: docker compose -f database/docker-compose.yml logs --tail=100"
  }

  $health = Invoke-RestMethod -Uri "http://127.0.0.1:58080/healthz" -TimeoutSec 8
  if ($health.status -ne "ok" -or $health.database -ne "connected") {
    throw "Search API health check failed. Database volume was preserved."
  }
  Write-Host ""
  Write-Host "INDEPENDENT DATABASE READY" -ForegroundColor Green
  Write-Host "PostgreSQL: 127.0.0.1:55432 (loopback only)"
  Write-Host "Search API: http://127.0.0.1:58080"
  Write-Host "Health:     http://127.0.0.1:58080/healthz"
  Write-Host "Credentials are stored locally in database/.env. Do not send or commit that file."
  Write-Host "Next: run the importer only after the public index snapshot has been verified."
} finally {
  Pop-Location
}
