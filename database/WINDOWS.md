# Windows setup — Everything Hub's free self-hosted PostgreSQL

This runs the existing PostgreSQL 17 replacement on your Windows computer using Docker Desktop. It is separate from Supabase and never touches Dog Tag Day's Story Wall database.

## Start

1. Install and start Docker Desktop (Linux containers).
2. Open PowerShell in the repository root.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\database\start-windows.ps1
```

The launcher creates three strong random secrets in `database/.env` if needed, preserves existing credentials and Docker volumes, starts the current `database/docker-compose.yml` stack, waits for both health checks, and tests authenticated search.

## Local-only endpoints

- PostgreSQL: `127.0.0.1:55432`
- Authenticated search API: `http://127.0.0.1:58080`
- Health check: `http://127.0.0.1:58080/healthz`

Both services bind to loopback only. Do not expose PostgreSQL port 5432 to the internet. Keep `database/.env` private and never commit it.

## Inspect

```powershell
docker compose --env-file database/.env -f database/docker-compose.yml ps
docker compose --env-file database/.env -f database/docker-compose.yml logs --tail=100
```

Do not run `docker compose down -v` or delete the named volume. Existing data remains intact. The launcher does not import the public index or switch production traffic; those are separate steps after schema and search verification.
