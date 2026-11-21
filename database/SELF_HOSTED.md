# Run Everything Hub's own PostgreSQL 17 database

This is the independent, self-hosted Everything Hub database. It is separate
from Supabase and from Dog Tag Day. It does not change production traffic or
start any crawler/sitemap workers.

## Windows (Docker Desktop)

1. Install Docker Desktop and enable Linux containers.
2. Open PowerShell in the repository's `database` folder.
3. Run:

```powershell
.\\start-postgres-windows.ps1
```

The launcher creates a private `.env` file with strong random credentials if
one does not already exist, starts the PostgreSQL 17 and authenticated search
API containers, and verifies the database plus the `eh_quad` page, queue, and
catalog tables. It does not start a crawler, import repository data, alter
Supabase, or open firewall/router ports. Keep the computer powered on for
continuous service.

## Linux

1. Install Docker Engine and the Docker Compose plugin.
2. Copy `.env.example` to `.env` and replace both placeholder secrets with
   long random hexadecimal values.
3. From this directory run:

```sh
docker compose --env-file .env -f docker-compose.yml up -d --build
docker compose --env-file .env -f docker-compose.yml ps
docker compose --env-file .env -f docker-compose.yml logs --tail=50 everythinghub-db
```

## Database initialization

On a brand-new Docker volume, the PostgreSQL entrypoint creates the local
`service_role` compatibility role and applies migrations 002 through 005 in
order. This initializes the isolated `eh_quad` schema used by the search API,
including page documents, crawl queue, intelligence, and catalog tables.
The legacy unqualified `001_everythinghub_core.sql` is deliberately not used:
the API is locked to `eh_quad`.

Docker only runs these initialization scripts when the data volume is empty.
Never delete the volume to force reinitialization. For an existing database,
back it up and apply reviewed migrations manually.

## Verify and search

- PostgreSQL: `127.0.0.1:55432` (loopback only)
- Authenticated search API: `http://127.0.0.1:58080`
- Health: `http://127.0.0.1:58080/healthz`

Example from PowerShell (the token is stored in the private `.env` file):

```powershell
$token = (Get-Content .env | Where-Object { $_ -like 'EH_SEARCH_API_TOKEN=*' })[0].Split('=',2)[1]
Invoke-RestMethod -Headers @{ Authorization = "Bearer $token" } -Uri "http://127.0.0.1:58080/search?q=NASA&limit=10"
```

## Backups

Create a compressed logical backup without passing the binary dump through
PowerShell's text pipeline:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$containerFile = "/tmp/everythinghub-$stamp.dump"
docker exec everythinghub-db pg_dump -U everythinghub -d everythinghub -Fc -f $containerFile
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
docker cp "everythinghub-db:$containerFile" ".\\everythinghub-$stamp.dump"
if ($LASTEXITCODE -ne 0) { throw "docker cp failed" }
docker exec everythinghub-db rm -f $containerFile
Write-Host "Backup created: everythinghub-$stamp.dump"
```

Keep backups encrypted and off-host. Test restores before relying on them.
Do not delete the named Docker volume.

## Importing existing crawler data

Only after the database is verified and a backup exists, set
`DATABASE_URL` from the private `.env` and run
`python database/import_repository_index.py` from the repository root.
The importer is restricted to `eh_quad` and additive/upsert-only. Catalog
records are imported separately by `database/import_universal_catalogs.py`.
Do not run either importer automatically during database startup.

## Security and operational boundaries

- PostgreSQL binds only to `127.0.0.1:55432`; never expose port 5432 publicly.
- The search API binds only to `127.0.0.1:58080` on the host and requires a
  bearer token for search.
- Keep `.env` private and out of Git. Never put the token in browser code.
- This local stack is independent and is not yet a production cutover.
- Do not start crawl/sitemap workers or switch production functions without
  explicit approval.
