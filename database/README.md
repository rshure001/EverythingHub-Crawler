# EverythingHub independent database

This directory contains the versioned PostgreSQL schema and the database-native
crawler/search services for EverythingHub. It is separate from Dog Tag Day.

## Components

- `eh_documents`: durable searchable page records; generated weighted English
  full-text vector with a GIN index.
- `eh_crawl_queue`: durable, retryable URL work queue partitioned by eight lanes.
- `eh_host_leases`: schema support for per-host coordination as the worker fleet scales.
- `eh_crawl_runs`: schema support for auditable worker-run counters.
- `eh_media`: media discovered on indexed pages.
- `eh_lane_state`: independent enablement and worker caps for all eight lanes.
- `crawl_worker.py`: first database-native worker; claims URLs from PostgreSQL,
  checks robots.txt, fetches HTML, writes directly to `eh_documents`, and
  queues same-host links. It starts as one worker with conservative host pacing.
- `search_api.py`: authenticated PostgreSQL full-text search endpoint.

## Apply schema

Create a **dedicated PostgreSQL database** for EverythingHub. Do not run this
migration against Dog Tag Day or another production database. Apply
`001_everythinghub_core.sql` using the database owner's normal migration
mechanism. The SQL is transactional and uses idempotent table/index creation.

## Run the database-native crawler

Install the PostgreSQL driver on the private host:

```bash
python -m pip install 'psycopg[binary]>=3.2,<4'
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require'
export EH_DB_SCHEMA=public
python database/crawl_worker.py
```

The worker inserts the 49 configured starting URLs into the database queue,
then claims pending work using PostgreSQL row locks (`SKIP LOCKED`). It stores
page content directly in `eh_documents` and discovers additional same-host
HTML links up to depth 3. It defaults to 80 indexed HTML pages per run and
waits 1.5 seconds between requests to the same host. Tune with
`EH_CRAWL_MAX_PAGES`, `EH_CRAWL_MAX_DEPTH`, and `EH_CRAWL_HOST_DELAY`.

This first cut deliberately uses one worker process. Do not start a fleet until
host-level coordination and run accounting are enabled and load-tested. Lane
assignment is currently a conservative hostname heuristic, not a semantic
classifier; review lane distribution before increasing parallelism.

Keep `DATABASE_URL` in a private secret store or local environment only.
Never commit credentials or service-role keys. The database connection must be
reachable from the private host; the compose example binds ports to localhost.

## Import an existing repository snapshot

The importer remains available as a one-time/additive migration path. It reads
`data/index.json`, validates HTTP(S) URLs, hashes content, and upserts in
batches. It never deletes destination rows. The JSON crawler currently does not
assign specialist lanes, so snapshot records enter `general`.

```bash
python -m pip install 'psycopg[binary]>=3.2,<4'
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require'
python database/import_repository_index.py
```

## Verification and cutover

The isolated GitHub Actions smoke test now exercises the direct worker against
a temporary PostgreSQL schema and a local HTTP fixture. It verifies that the
worker fetches a page, writes its searchable document, completes the queue row,
and enqueues a discovered link. This validates code behavior in CI; it does not
mean a persistent production database or worker host is already running.

1. Provision or identify the owner's existing private host and database.
2. Apply the schema and import the existing JSON snapshot if desired.
3. Run the worker in a controlled shadow period and verify queue/document counts.
4. Validate search results and host behavior.
5. Only then move production search traffic deliberately.

No production crawler, live search endpoint, existing database, or Dog Tag Day
system is changed merely by adding these files.
