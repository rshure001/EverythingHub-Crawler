# Everything Hub dedicated database schema

Everything Hub's database work is confined to the `eh_quad` schema in the
separate Supabase project `everything-hub-control`. The Dog Tag Day Story Wall
project is separate and must never be used for these migrations or imports.

## Schema

Use **only** `002_eh_quad_schema.sql` for the current schema. It creates the
`eh_quad` schema, document/search tables, crawl queue, host leases, run
metrics, media table, lane state, indexes, and the `eh_quad.eh_search_pages`
search RPC. It is idempotent and additive. All tables have RLS enabled; only the server-side service role receives the minimum search privileges.

`001_everythinghub_core.sql` is a legacy, unqualified first draft. Do not apply
it to the live Supabase project; it does not enforce the required schema boundary.

Apply the 002 migration only after the Everything Hub PostgreSQL service is
accepting connections and the target project has been positively identified. Before deploying the Edge search function, add `eh_quad` to the project's exposed Data API schemas; keep `anon` and `authenticated` ungranted. The function uses the service role and the migration grants it only schema usage, document reads, and search-RPC execution.
Do not attempt recovery by dropping, resetting, recreating, or restoring the
project.

## Import the repository index

The importer is locked to `eh_quad`; it rejects any other schema value.
It is additive/upsert-only and never deletes destination rows. It reads
`data/index.json`, validates HTTP(S) URLs, hashes content, and upserts in
batches. Prototype crawler pages currently enter the `general` lane.

```bash
python -m pip install 'psycopg[binary]>=3.2,<4'
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require'
export EH_DB_SCHEMA='eh_quad'
python database/import_repository_index.py
```

Keep `DATABASE_URL` and `EH_SEARCH_API_TOKEN` in a private secret store or
local environment only. Never commit credentials or service-role keys.

## Authenticated search API

`database/search_api.py` is also locked to `eh_quad`. It requires
`DATABASE_URL` and `EH_SEARCH_API_TOKEN`; `/healthz` checks database
readiness and `/search` requires a bearer token. It does not expose an
unauthenticated search endpoint.

## Safe cutover sequence

1. Wait for PostgreSQL recovery; confirm read/write access in
   `everything-hub-control`.
2. Apply `002_eh_quad_schema.sql` and verify the objects exist in `eh_quad`.
3. Run the isolated importer smoke test and import a snapshot additively.
4. Validate document counts, URL uniqueness, sample content, and ranked search.
5. Connect one test crawler worker to the dedicated queue and verify robots and
   host limits.
6. Run a controlled shadow period; only then consider switching live traffic.

The staged Edge search source is `supabase/functions/everything-hub-web-search/index.ts`; it explicitly selects `eh_quad` for both the RPC and fallback query. It is not deployed by this pull request. Deployment requires the database migration, exposed-schema configuration, and live search tests to pass.

No migration or import is run against Dog Tag Day. The files alone do not
publish or switch production traffic.
