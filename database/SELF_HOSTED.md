# Run EverythingHub's own PostgreSQL database

This is a self-hostable PostgreSQL 17 deployment for EverythingHub only.
It binds the database port to localhost; do not expose PostgreSQL directly
to the public internet. Put application access behind a private network or
authenticated API. Dog Tag Day is not part of this stack.

## Start

1. Install Docker Engine and the Docker Compose plugin on the private host.
2. Copy `.env.example` to `.env`; replace the password with a long random value.
3. From this directory run:

```sh
set -a
. ./.env
set +a
EH_DB_PASSWORD="$EH_DB_PASSWORD" docker compose up -d
docker compose ps
docker compose logs --tail=50 everythinghub-db
```

The first initialization applies `001_everythinghub_core.sql` automatically.
The named Docker volume persists database data across container restarts.

## Backups

Create a compressed logical backup:

```sh
docker compose exec -T everythinghub-db pg_dump -U everythinghub -d everythinghub -Fc > everythinghub-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Keep backups encrypted and off-host. Test restores before relying on them.
Do not delete the named volume to reset the database.

## Importing existing crawler data

Set `DATABASE_URL` from the local .env file and run
`python database/import_repository_index.py` from the repository root after
the index snapshot is present. The import is additive/upsert-only.

## Operational notes

- The PostgreSQL port is loopback-only (127.0.0.1:55432).
- Use private networking or a tightly authenticated application service for
  remote access; never open port 5432 to the world.
- Store credentials in a host secret manager or protected environment file.
- This compose file provisions only the database. It does not switch the
  production crawler or search API to this database.
