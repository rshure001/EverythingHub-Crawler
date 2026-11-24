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

function Invoke-Docker([string[]]$Arguments, [string]$FailureMessage) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw $FailureMessage }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop with Linux containers enabled."
}
Invoke-Docker @("info") "Docker Engine is not ready. Start Docker Desktop and retry."
Invoke-Docker @("compose", "version") "Docker Compose v2 is required. Update Docker Desktop and retry."

$EnvPath = Join-Path $DbDir ".env"
$envExists = Test-Path -LiteralPath $EnvPath
$lines = if ($envExists) { @(Get-Content -LiteralPath $EnvPath) } else { @() }

# Never invent replacement credentials for a database volume that already exists.
$volumeNames = @(& docker volume ls --format "{{.Name}}" | Where-Object { $_ -match "(^|_)everythinghub_pgdata$" })
if ($LASTEXITCODE -ne 0) { throw "Could not inspect Docker volumes safely." }
$hasExistingVolume = $volumeNames.Count -gt 0

$dbPassword = Get-EnvValue $lines "EH_DB_PASSWORD"
$searchDbPassword = Get-EnvValue $lines "EH_SEARCH_DB_PASSWORD"
$apiToken = Get-EnvValue $lines "EH_SEARCH_API_TOKEN"
$requiredSecrets = @($dbPassword, $searchDbPassword, $apiToken)
$missingSecret = @($requiredSecrets | Where-Object { [string]::IsNullOrWhiteSpace($_) -or $_ -like "replace-with-*" }).Count -gt 0

if ($hasExistingVolume -and (-not $envExists -or $missingSecret)) {
    throw "An existing PostgreSQL data volume was found but its original .env secrets are missing or incomplete. Restore the original database\.env from a secure backup; do not generate new passwords or delete the volume."
}
if ([string]::IsNullOrWhiteSpace($dbPassword) -or $dbPassword -like "replace-with-*") { $dbPassword = New-Secret }
if ([string]::IsNullOrWhiteSpace($searchDbPassword) -or $searchDbPassword -like "replace-with-*") { $searchDbPassword = New-Secret }
if ([string]::IsNullOrWhiteSpace($apiToken) -or $apiToken -like "replace-with-*") { $apiToken = New-Secret }

# Hex-only secrets avoid URI and Docker Compose interpolation problems.
# Preserve optional settings such as a previously configured tunnel token.
$otherLines = @($lines | Where-Object { $_ -notmatch "^(EH_DB_PASSWORD|EH_SEARCH_DB_PASSWORD|EH_SEARCH_API_TOKEN|DATABASE_URL)=" })
$envContent = @(
    "# Generated locally. Keep private; never commit or share this file."
    "EH_DB_PASSWORD=$dbPassword"
    "EH_SEARCH_DB_PASSWORD=$searchDbPassword"
    "EH_SEARCH_API_TOKEN=$apiToken"
    "DATABASE_URL=postgresql://everythinghub:$dbPassword@127.0.0.1:55432/everythinghub"
) + $otherLines
[System.IO.File]::WriteAllLines($EnvPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Checking the current Everything Hub PostgreSQL Compose configuration..."
Invoke-Docker @("compose", "--env-file", ".env", "-f", "docker-compose.yml", "config", "--quiet") "Docker Compose configuration validation failed."

Write-Host "Starting Everything Hub PostgreSQL 17 and its authenticated search API..."
Invoke-Docker @("compose", "--env-file", ".env", "-f", "docker-compose.yml", "up", "-d", "--build") "Docker Compose could not start the stack."

Write-Host "Waiting for PostgreSQL and the search API..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    & docker exec everythinghub-db pg_isready -U everythinghub -d everythinghub *> $null
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
    & docker compose --env-file .env -f docker-compose.yml ps
    & docker compose --env-file .env -f docker-compose.yml logs --tail=60 everythinghub-db everythinghub-search
    throw "The local stack did not become healthy. No importer or crawler was started."
}

# Verify the live schema and least-privilege API role against the current main-branch migrations.
$sql = "SELECT (to_regclass('eh_quad.eh_documents') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_crawl_queue') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_catalogs') IS NOT NULL)::text || '|' || (to_regclass('eh_quad.eh_catalog_records') IS NOT NULL)::text || '|' || (SELECT count(*) = 1 FROM pg_roles WHERE rolname='eh_search_api' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)::text || '|' || has_table_privilege('eh_search_api', 'eh_quad.eh_documents', 'SELECT')::text || '|' || has_table_privilege('eh_search_api', 'eh_quad.eh_crawl_queue', 'SELECT')::text;"
$check = & docker exec everythinghub-db psql -U everythinghub -d everythinghub -Atc $sql
if ($LASTEXITCODE -ne 0 -or $check.Trim() -ne "true|true|true|true|true|true|false") {
    throw "PostgreSQL is reachable, but the eh_quad schema or restricted search-role checks failed. Do not import or start workers."
}

Write-Host ""
Write-Host "READY: Everything Hub PostgreSQL 17 is running locally."
Write-Host "Verified: eh_documents, eh_crawl_queue, eh_catalogs, eh_catalog_records."
Write-Host "Verified: eh_search_api is non-superuser, does not bypass RLS, can read search documents, and cannot read the crawl queue."
Write-Host "Database: 127.0.0.1:55432 (loopback only)"
Write-Host "Authenticated search API: http://127.0.0.1:58080"
Write-Host "Private credentials: database\.env"
Write-Host "No repository data was imported; no crawler, sitemap worker, or production cutover was started."
