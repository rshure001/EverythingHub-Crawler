# Everything Hub — Independent PostgreSQL Core

This is a separate PostgreSQL 17 search/catalog database for Everything Hub. It is designed to run alongside the current Supabase project during migration. It does not connect to, modify, or delete Supabase data.

## Included
- Full-text search catalog with GIN indexing and title trigram matching
- Crawl sources and policy fields
- Durable crawl queue with atomic multi-worker leases using `FOR UPDATE SKIP LOCKED`
- Worker checkpoints, health status, and audit log
- Docker Compose deployment with persistent volume and health check

## Start on a dedicated Linux host
1. Install Docker Engine and the Docker Compose plugin.
2. Copy `.env.example` to `.env` and set a unique long random password.
3. Run `docker compose up -d`.
4. Verify with `docker compose ps` and `docker compose exec everythinghub-postgres pg_isready -U eh_admin -d everythinghub`.
5. Connect locally: `docker compose exec everythinghub-postgres psql -U eh_admin -d everythinghub`.
6. Verify schema: `\dt eh_search.*`.

Postgres listens only on 127.0.0.1:5432. Keep it private. The website should reach it through a private backend/API, not a browser or public database port.

## Important operational notes
- Init SQL runs automatically only when the data volume is first initialized. For an existing database, apply it deliberately with psql after a backup.
- Back up with `pg_dump -Fc -U eh_admin everythinghub > everythinghub-$(date +%F).dump`; test restores regularly.
- Store secrets outside GitHub. Never commit `.env`, passwords, connection strings, or backups.
- Do not import the old index until its schema, counts, checkpoints, and protected datasets have been inventoried and verified.
- No DNS cutover, production search routing change, or Supabase write is part of this package.
- This package does not provision cloud infrastructure; hosting may incur charges. Confirm host and cost before provisioning.
