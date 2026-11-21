# Run Everything Hub's own PostgreSQL database

This is a self-hosted PostgreSQL 17 database for Everything Hub only. It uses
the dedicated eh_quad schema and the same page, crawler, intelligence, and
catalog migrations as the managed Everything Hub database. Dog Tag Day is
not part of this stack.

## First initialization

1. Install Docker Engine and the Docker Compose plugin on a private host.
2. Copy .env.example to .env and set three different long random secrets:
   EH_DB_PASSWORD, EH_SEARCH_DB_PASSWORD, and EH_SEARCH_API_TOKEN.
   Keep the database passwords URL-safe (letters and numbers) for the
   connection URI. Never commit .env.
3. From this directory run:

```sh
set -a
. ./.env
set +a
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 everythinghub-db
docker compose logs --tail=50 everythinghub-search
```

On a brand-new named volume, the official PostgreSQL image runs the
initialization files in order:

1. init/000_roles.sql creates a non-login trusted service_role and a
   restricted eh_search_api role.
2. 002_eh_quad_schema.sql creates the dedicated page index, search RPC,
   crawler queue, host leases, run metrics, media, and lane state.
3. 003_eh_quad_intelligence.sql creates the source registry, topics,
   evidence links, append-only feedback, and ranking profiles.
4. 004_eh_quad_feedback_cursor.sql adds the monotonic feedback cursor.
5. 005_eh_quad_catalogs.sql creates catalog metadata, records, indexes,
   and catalog search.
6. init/050_search_role.sh configures the separate read-only API login and
   row-level policies. It permits only robots-allowed pages and catalog
   records explicitly marked public_search_allowed=true.

The legacy 001_everythinghub_core.sql is intentionally not mounted. It is
an unqualified first draft and must not be used for the dedicated schema.

## Services and security

- PostgreSQL is bound only to 127.0.0.1:55432; it is not exposed publicly.
- The authenticated search API is bound only to 127.0.0.1:58080.
- The search API connects as eh_search_api, not the database administrator.
  That role is non-superuser, read-only, has no RLS bypass, and can see only
  robots-allowed pages and explicitly public catalog records.
- EH_SEARCH_API_TOKEN protects /search; /healthz reports readiness without
  exposing data.
- Use DATABASE_URL with the administrator account only for controlled
  additive imports. Never put admin credentials or service-role keys in
  browser code.
- Keep both ports private. Remote access must go through a private network or
  a separately authenticated application gateway; never expose port 5432
  directly to the internet.

Example local search request:

```sh
curl -H "Authorization: Bearer $EH_SEARCH_API_TOKEN" \
  "http://127.0.0.1:58080/search?q=NASA&limit=10"
```

## Backups

Create a compressed logical backup:

```sh
docker compose exec -T everythinghub-db pg_dump -U everythinghub -d everythinghub -Fc > everythinghub-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Keep backups encrypted and off-host. Test restores before relying on them.
Do not delete the named volume to reset the database.

## Importing existing crawler data

After the new database passes /healthz and the read-only checks, set
DATABASE_URL from the private .env and run the additive importers from the
repository root:

```sh
python database/import_repository_index.py
python database/import_universal_catalogs.py
```

Both importers are schema-locked to eh_quad, validate source URLs and
robots/source-policy flags, and use upserts rather than deleting destination
rows. Catalog records without explicit public-search approval are skipped.

## Existing volumes and safe upgrades

PostgreSQL initialization scripts run only when the data directory is empty.
If a named volume already exists, Docker will not replay these scripts.
Do not run docker compose down -v, delete the volume, or reset an existing
database. Apply any needed additive migrations manually after taking a
verified backup and inspecting the current schema. The managed Supabase
database and this self-hosted database remain separate until a controlled
shadow test and explicit cutover approval.
