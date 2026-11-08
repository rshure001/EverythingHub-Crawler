# EverythingHub independent database

This directory contains the first versioned schema for a standalone PostgreSQL
database dedicated to EverythingHub. It is separate from Dog Tag Day.

## Components

- `eh_documents`: durable searchable page records; generated weighted English
  full-text vector with a GIN index.
- `eh_crawl_queue`: durable, retryable URL work queue partitioned by eight lanes.
- `eh_host_leases`: per-host leases to prevent concurrent requests to one host.
- `eh_crawl_runs`: auditable worker-run counters.
- `eh_media`: media discovered on indexed pages.
- `eh_lane_state`: independent enablement and worker caps for all eight lanes.

## Apply schema

Create a **new, dedicated PostgreSQL database** for EverythingHub. Do not run
this migration against Dog Tag Day or against the existing production database.
Apply `001_everythinghub_core.sql` using the database owner's normal migration
mechanism. The SQL is transactional and uses idempotent table/index creation.

## Import the repository index

The importer is additive and idempotent; it does not delete destination rows.
It reads `data/index.json`, validates HTTP(S) URLs, hashes content, and upserts
in batches. The current repository crawler does not yet assign specialist lanes,
so imported prototype pages enter `general` and can be reclassified later.

```bash
python -m pip install 'psycopg[binary]>=3.2'
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require'
python database/import_repository_index.py
```

Keep `DATABASE_URL` in a private secret store or local environment only.
Never commit credentials or service-role keys.

## Safe cutover sequence

1. Provision a separate database and apply the schema.
2. Import a snapshot of repository index data.
3. Validate document counts, URL uniqueness, sample content, and search results.
4. Connect one test crawler worker to the new queue and verify robots/host limits.
5. Run a controlled shadow period and compare indexed-page rates and error rates.
6. Only after validation, move production search/crawl traffic deliberately.

No production crawler, live search endpoint, existing database, schedule, or
Dog Tag Day system is changed by these files alone.
