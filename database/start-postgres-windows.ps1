$ErrorActionPreference = "Stop"

$DbDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $DbDir

function Get-EnvValue([string[]]$Lines, [string]$Key) {
    $match = $Lines | Where-Object { $_ -match ("^" + [regex]::Escape($Key) + "=") } | Select-Object -First 1
    if ($null -eq $match) { return $null }
    return $match.Substring($Key.Length + 1)
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install/start Docker Desktop, then run this script again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is not ready. Start Docker Desktop with Linux containers enabled, then rerun."
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required. Update Docker Desktop, then rerun."
}

$EnvPath = Join-Path $DbDir ".env"
if (Test-Path $EnvPath) {
    $lines = @(Get-Content -LiteralPath $EnvPath)
} else {
    $lines = @()
}

$dbPassword = Get-EnvValue $lines "EH_DB_PASSWORD"
$apiToken = Get-EnvValue $lines "EH_SEARCH_API_TOKEN"
if ([string]::IsNullOrWhiteSpace($dbPassword) -or $dbPassword -like "replace-with-*") {
    $dbPassword = New-Secret
}
if ([string]::IsNullOrWhiteSpace($apiToken) -or $apiToken -like "replace-with-*") {
    $apiToken = New-Secret
}

# Hex-only generated credentials are safe in the PostgreSQL URL and Compose env file.
$envContent = @(
    "# Generated locally. Keep this file private; never commit it."
    "EH_DB_PASSWORD=$dbPassword"
    "EH_SEARCH_API_TOKEN=$apiToken"
    "DATABASE_URL=postgresql://everythinghub:$dbPassword@127.0.0.1:55432/everythinghub"
)
[System.IO.File]::WriteAllLines($EnvPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Starting Everything Hub PostgreSQL 17 and its authenticated local search API..."
docker compose --env-file .env -f docker-compose.yml up -d --build
if ($LASTEXITCODE -ne 0) { throw "Docker Compose could not start the stack." }

Write-Host "Waiting for PostgreSQL and the search API..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    docker exec everythinghub-db pg_isready -U everythinghub -d everythinghub *> $null
    if ($LASTEXITCODE -eq 0) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:58080/healthz" -TimeoutSec 4
            if ($health.status -eq "ok" -and $health.database -eq "connected") {
                $ready = $true
                break
            }
        } catch { }
    }
    Start-Sleep -Seconds 3
}
if (-not $ready) {
    docker compose --env-file .env -f docker-compose.yml ps
    docker compose --env-file .env -f docker-compose.yml logs --tail=60 everythinghub-db everythinghub-search
    throw "The local stack did not become healthy. No crawler or importer was started."
}

$schemaCheck = docker exec everythinghub-db psql -U everythinghub -d everythinghub -Atc "SELECT (to_regclass('eh_quad.eh_documents') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_crawl_queue') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_catalogs') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_catalog_records') IS NOT NULL)::text;"
if ($LASTEXITCODE -ne 0 -or $schemaCheck.Trim() -ne "true|true|true|true") {
    throw "PostgreSQL is reachable, but the expected eh_quad page/queue/catalog tables are not all present. Do not import or start workers."
}

Write-Host ""
Write-Host "READY: PostgreSQL 17 is running locally."
Write-Host "Verified eh_quad tables: eh_documents, eh_crawl_queue, eh_catalogs, eh_catalog_records."
Write-Host "Database endpoint: 127.0.0.1:55432 (loopback only)"
Write-Host "Authenticated search API: http://127.0.0.1:58080"
Write-Host "Credentials saved in database\.env. Do not share or commit that file."
Write-Host "No crawler, sitemap worker, or production cutover was started."
