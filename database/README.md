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
It never deletes destination rows. It upserts allowed pages and soft-disables
only exact existing rows whose current crawler record explicitly says
`robots_allowed=false`; those rows remain stored for audit but are excluded
from both database search paths. If a later allowed snapshot includes the URL,
the upsert restores `robots_allowed=true`. It reads `data/index.json`,
validates HTTP(S) URLs, hashes content, and upserts in batches. Prototype
crawler pages currently enter the `general` lane.

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

The deployed `everything-hub-web-search` version 20 and legacy `everything-hub-search` version 4 both explicitly select `eh_quad`. The web-search function calls `eh_search_pages` and uses the schema-qualified `eh_documents` fallback path; it also has database-independent fallbacks to the public crawler catalog and Bing RSS. The database RPC and authenticated Python search API both require `robots_allowed IS TRUE` in result and count queries, so soft-excluded documents cannot leak into result counts. The deployed function source is schema-scoped, but its database path and the production schema objects cannot currently be validated because PostgreSQL is rejecting connections with SQLSTATE `57P03`. Do not apply migrations or import data until database connections and schema inspection succeed.

## Observed production deployment state (2026-11-17 12:27 UTC)

- Supabase Edge Function inspection shows `everything-hub-web-search` version **20** and legacy `everything-hub-search` version **4**. Both deployed sources explicitly use `.schema("eh_quad")`; the current web-search source also contains static public-crawler and independent Bing RSS fallbacks.
- The separate Everything Hub crawler function is version **49** and explicitly returns HTTP 503 with status `paused` while database storage recovery is in progress. Sitemap discovery version **7** is paused for the same reason. These functions report that existing queue and indexed data were preserved.
- The database control plane reports `ACTIVE_HEALTHY`, but direct SQL and PostgREST connections are currently failing with PostgreSQL SQLSTATE `57P03`: “the database system is not accepting connections” / “Hot standby mode is disabled.” Table and migration listings therefore cannot be verified.
- Recent Edge request logs show media discovery and independent search returning HTTP 200. The database-backed search RPC is timing out during the PostgreSQL outage; a successful web-search fallback response does not prove that the database path is healthy.
- GitHub Pages publishing is permission-gated: `publish-site.yml` validates and uploads the release artifact, but skips deployment when the repository Pages endpoint returns 404. The workflow records that a previous `configure-pages` site-creation attempt was rejected with `Resource not accessible by integration`. The required capability is repository Pages site creation/administration permission for the GitHub Actions integration; no repository setting was changed.
- The alternate Supabase static-publisher workflow requires the repository secret `SUPABASE_PUBLISHER_USER_JWT` containing a signed-in Supabase user token. The connected GitHub tools do not expose secret-presence metadata, so availability is unverified; no token value was requested or used.
- The canonical homepage's separate public search path remains independent of the database-backed `eh_quad` search. No database migration, import, reset, or restore was attempted during this inspection.

No migration or import has been run against Dog Tag Day; that separate project is not an Everything Hub target. The files alone do not publish or switch production traffic.

## Latest repository snapshot and recovery status (2026-11-17 12:27 UTC)

The public crawler repository's latest `main` commit is `c18fecb` (2026-11-17 12:17:18 UTC). Its published public search catalog contains **3,667 pages across 20 SHA-256-listed shards**; the compressed catalog is **1,153,757 bytes**. These are public repository catalog counts, not production database row counts.

Production `eh_quad` remains unverified because the dedicated `everything-hub-control` PostgreSQL service is rejecting connections with SQLSTATE `57P03` and detail “Hot standby mode is disabled.” `list_tables` and `list_migrations` cannot complete while the database is unavailable. Do not apply `002_eh_quad_schema.sql`, run the importer, or attempt project reset/restore until direct database reads succeed.

The public GitHub crawler remains separately scheduled hourly and has published the latest catalog snapshot. The Supabase database-backed crawler and sitemap-discovery Edge Functions are paused; their deployed responses explicitly state that existing queue and index data were preserved. Dog Tag Day Story Wall remains in its separate Supabase project and is not a migration or import target.
