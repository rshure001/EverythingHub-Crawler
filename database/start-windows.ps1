# Everything Hub free self-hosted PostgreSQL launcher for Windows PowerShell 5.1+
# Starts only the dedicated Everything Hub Docker Compose stack.
# Never contacts Supabase, deletes volumes, or exposes PostgreSQL publicly.
$ErrorActionPreference = "Stop"
$DatabaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DatabaseDir
$Compose = Join-Path $DatabaseDir "docker-compose.yml"
$EnvFile = Join-Path $DatabaseDir ".env"

function New-Secret([int]$Bytes = 48) {
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
  return ([System.BitConverter]::ToString($buffer) -replace "-", "").ToLowerInvariant()
}

Write-Host "Everything Hub — free local PostgreSQL replacement" -ForegroundColor Cyan
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker Desktop is not installed. Install and start Docker Desktop, then rerun this script."
}
$dockerInfo = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Docker Desktop is not running. Start it; no files, containers, or databases were changed."
}
$composeVersion = & docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Docker Compose v2 is required. Update Docker Desktop and rerun."
}
if (-not (Test-Path $Compose)) { throw "Missing compose file: $Compose" }

if (-not (Test-Path $EnvFile)) {
  $dbPassword = New-Secret
  $searchPassword = New-Secret
  $apiToken = New-Secret
  $envText = @"
EH_DB_PASSWORD=$dbPassword
EH_SEARCH_DB_PASSWORD=$searchPassword
EH_SEARCH_API_TOKEN=$apiToken
DATABASE_URL=postgresql://everythinghub:$dbPassword@127.0.0.1:55432/everythinghub
"@
  [System.IO.File]::WriteAllText(
    $EnvFile,
    $envText,
    (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false)
  )
  Write-Host "Created three unique local secrets in database/.env." -ForegroundColor Green
} else {
  $rawEnv = Get-Content $EnvFile -Raw
  foreach ($key in @("EH_DB_PASSWORD","EH_SEARCH_DB_PASSWORD","EH_SEARCH_API_TOKEN")) {
    if ($rawEnv -notmatch "(?m)^$key=.+$") {
      throw "Existing database/.env is missing $key. It was not changed."
    }
  }
  Write-Host "Existing database/.env found; preserving all credentials." -ForegroundColor Yellow
}

# Refuse to take over occupied ports unless the existing listener belongs to this stack.
foreach ($port in @(55432,58080)) {
  $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) {
    $container = (& docker ps --filter "publish=$port" --format "{{.Names}}" 2>$null)
    $expected = if ($port -eq 55432) { "everythinghub-db" } else { "everythinghub-search" }
    if (-not ($container -contains $expected)) {
      throw "Port $port is already used by another process (PID $($listener.OwningProcess)). Nothing was stopped."
    }
  }
}

Push-Location $RepoRoot
try {
  & docker compose --env-file $EnvFile -f $Compose up -d --build
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed. Existing named volumes were preserved." }

  $ready = $false
  for ($i = 0; $i -lt 36; $i++) {
    $dbHealth = (& docker inspect --format "{{.State.Health.Status}}" everythinghub-db 2>$null)
    $apiHealth = (& docker inspect --format "{{.State.Health.Status}}" everythinghub-search 2>$null)
    if ($dbHealth -eq "healthy" -and $apiHealth -eq "healthy") { $ready = $true; break }
    Start-Sleep -Seconds 5
  }
  if (-not $ready) {
    & docker compose --env-file $EnvFile -f $Compose ps
    throw "Database/API did not become healthy within 3 minutes. Data was preserved; inspect logs with docker compose -f database/docker-compose.yml logs --tail=100."
  }

  $health = Invoke-RestMethod -Uri "http://127.0.0.1:58080/healthz" -TimeoutSec 10
  if ($health.status -ne "ok" -or $health.database -ne "connected") {
    throw "Search API database health check failed. Data volume was preserved."
  }

  $tokenLine = Get-Content $EnvFile | Where-Object { $_ -match "^EH_SEARCH_API_TOKEN=" } | Select-Object -First 1
  $token = $tokenLine.Substring("EH_SEARCH_API_TOKEN=".Length)
  $headers = @{ Authorization = "Bearer $token" }
  $search = Invoke-RestMethod -Uri "http://127.0.0.1:58080/search?q=EverythingHub&limit=1" -Headers $headers -TimeoutSec 10
  if ($null -eq $search.total -or $null -eq $search.results) {
    throw "Authenticated search smoke test returned an unexpected response. Data was preserved."
  }

  Write-Host ""
  Write-Host "INDEPENDENT EVERYTHING HUB DATABASE READY" -ForegroundColor Green
  Write-Host "PostgreSQL: 127.0.0.1:55432 (loopback only)"
  Write-Host "Search API: http://127.0.0.1:58080"
  Write-Host "Health:     http://127.0.0.1:58080/healthz"
  Write-Host "Authenticated search smoke test: passed"
  Write-Host "Credentials remain private in database/.env. Do not send or commit that file."
  Write-Host "The old Supabase database was not changed. No production cutover was performed."
} finally {
  Pop-Location
}
