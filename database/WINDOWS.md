# Windows setup — Everything Hub independent PostgreSQL

This launches a completely separate PostgreSQL 17 database and authenticated search API on a Windows machine with Docker Desktop. It does not connect to or modify the existing Supabase project.

## One-time prerequisites

- Windows 10/11 (64-bit)
- Docker Desktop installed and running, using its Linux-container engine
- This repository checked out locally on branch `feature/independent-postgres-core`

## Start

Open PowerShell in the repository folder and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\database\start-windows.ps1
```

The launcher:
- Creates strong random database and API credentials in `database/.env` only if that file does not already exist.
- Preserves existing credentials and existing Docker volumes.
- Refuses to take over occupied ports 55432 or 58080 when they are not owned by this stack.
- Starts PostgreSQL and the authenticated search API.
- Waits for both container health checks and then verifies `/healthz`.

## Local endpoints

- PostgreSQL: `127.0.0.1:55432` (loopback only; not exposed to the internet)
- Search API: `http://127.0.0.1:58080`
- Health check: `http://127.0.0.1:58080/healthz`

The API token stays in `database/.env`; never put it in website JavaScript or commit the file.

## Verify and inspect

```powershell
docker compose -f database/docker-compose.yml ps
docker compose -f database/docker-compose.yml logs --tail=100
```

Do not run `docker compose down -v`, delete the named volume, or import production data until the public crawler index snapshot and source counts have been verified. The importer is additive/upsert-only and is locked to the Everything Hub schema.

## Next migration stages

1. Confirm the new database health check succeeds.
2. Verify schema objects and run the isolated search smoke test.
3. Verify the current public index manifest and shard checksums.
4. Import the snapshot additively and compare source/imported counts.
5. Test search lanes and one crawler worker locally.
6. Only after those checks, plan a private authenticated connection from the hosted search service. No production traffic or DNS cutover occurs during this setup.
