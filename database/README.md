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

## Observed production deployment state (2026-11-18 15:03 UTC)

The dedicated Supabase project is `everything-hub-control` (PostgreSQL 17.11). The Supabase control plane reports `ACTIVE_HEALTHY`, but a read-only inventory query restricted to `information_schema.tables` with `table_schema = 'eh_quad'` still fails with PostgreSQL SQLSTATE `57P03`: “the database system is not accepting connections” / “Hot standby mode is disabled.” In the 13:58–14:58 UTC log window, 793 PostgreSQL log events were returned and 606 carried SQLSTATE `57P03`; the latest observed refusal was at 14:56:36 UTC. This is a database recovery/availability blocker, not proof that the schema is empty. Do not apply migrations, import production rows, reset, restore, or restart the project while direct SQL is unavailable.

### Deployed Edge Function registry

- `everything-hub-web-search`: version **26**, active. Its source explicitly targets `eh_quad` and retains the database-independent public-catalog and Bing RSS fallbacks. Current live smoke tests prove the static catalog and independent fallback paths; they do **not** prove the PostgreSQL search path is healthy.
- `everything-hub-media-search`: version **22**, active.
- `everything-hub-independent-search`: version **3**, active.
- `everything-hub-crawler`: version **49**, registered active but its deployed handler intentionally returns HTTP 503 with status `paused` while database storage recovery is in progress.
- `everything-hub-sitemap-discovery`: version **7**, registered active but its deployed handler intentionally returns HTTP 503 with status `paused`.
- `everything-hub-site`: version **54**, active.
- `everything-hub-browser`: version **11**, active.

The paused crawler and sitemap handlers explicitly state that existing queue/index data are preserved. The production database-native worker in GitHub is not enabled against production.

### Latest public crawler snapshot (2026-11-18 15:02 UTC)

Crawler run **#142** completed successfully at 15:01 UTC after the watchdog detected a stale run and dispatched one recovery crawl. The new catalog was committed to `main` as `b12ea28`; the manifest timestamp is 15:01:01 UTC.

- Public catalog: **8,761 pages across 49 SHA-256-verified shards**.
- Compressed catalog: **3,868,575 bytes**.
- Discovered URLs: **38,324**; frontier waiting: **20,030**.
- Run #142: 41 fetch attempts; 33 successful HTTP responses; 30 HTML pages parsed; 3 non-HTML responses; 8 fetch/parse errors.
- Indexed this run: **26**; not indexed: 15; robots-blocked: 1; noindex excluded: 4; nofollow not expanded: 0.
- Robots-policy retrieval failures: 2, handled fail-closed.
- Rate-limited responses: 1; the crawler honored the returned policy/backoff.
- Sitemap discovery: 3 hosts and 3 files checked; 0 additional URLs.
- Duration: 13.97 seconds; measured indexing rate: 111.7 pages/minute.

The worker rejected cross-origin redirects, skipped oversized/non-HTML resources within its configured limits, and preserved the frontier for later work. These are public repository catalog metrics, not production `eh_quad` row counts.

### Live search, media, and homepage verification

- Search smoke test **#49** passed against the new catalog: **1,969 NASA matches**, **1,946 science-lane matches**, and **10 Rolex results** from the independent Bing RSS fallback.
- Media smoke test **#15, attempt 2** passed on 2026-11-18 15:02 UTC: **21 image results** and **2 video results**. Wikimedia Commons results included source pages, creators, license URLs, credit/attribution fields and usage notes. The sampled image license was CC BY-SA 3.0 with attribution required; the sampled Apollo 11 video was marked public domain. Bing media results remain discovery links only and are not treated as reuse permission.
- Live diagnostics **#68, attempt 2** passed on 2026-11-18 15:02 UTC: canonical Vercel homepage HTTP 200, eight category tiles, exactly one search form inside the approved artwork, artwork served as PNG, and the custom browser endpoint HTTP 200 with navigation controls. The web-search endpoint returned the public crawler fallback and independent Bing fallback; database-backed search remained unavailable. Image/video checks again returned 21/2.
- `https://everythinghub.ink` remained unreachable during the 12-second diagnostic. The verified homepage target remains `https://everythinghub-homepage-fixed.vercel.app/`.

### Isolated database validation

The isolated importer runs **#155 and #156** both passed against the exact **8,761-record** snapshot in a temporary PostgreSQL database using `eh_quad`: 8,761 expected and imported, 0 skipped, 0 unexpected robots-denied stored rows, and 0 invalid lane rows. The same run's database-native crawler integration processed 5 queue items, indexed 2 fixture pages, and reported 0 failures; it verified robots denial, noindex handling, same-host queueing, cross-origin redirect rejection, media references, stale-claim lease recovery, run accounting, schema locking, authenticated search, lane filtering and pagination. The RPC returned 0 robots-denied rows, pagination returned three unique allowed fixtures, and the science filter returned only the science fixture.

These are temporary PostgreSQL tests, not production imports. A main-branch repeat (run **#157**) was queued after the status-documentation merge; no production database writes have been made.

### Publishing and scheduling status

Publisher run **#66** successfully validated and uploaded a **7,770,151-byte** GitHub Pages artifact (artifact ID `14439272914`, SHA-256 `ed0b48c6e62b2699dfafa6b8b884bf4cf39fe95d858796e34f1f4782bdca559a`). The Pages site lookup returned HTTP 404; a previous site-creation attempt was rejected with `Resource not accessible by integration`. The workflow already declares `pages: write` and `id-token: write`; the remaining blocker is repository Pages site creation/administration access for the GitHub Actions integration. The validated artifact is retained and the deploy job is skipped. No repository settings were changed.

Scheduled GitHub Actions delivery is inconsistent. In the latest 100 workflow runs inspected, only three `schedule` events appeared; the most recent was at 11:30 UTC, with no later scheduled event visible through 14:58 UTC. Re-running watchdog job #169 at 15:00 UTC caused it to detect a 3,373-second-old crawler attempt and dispatch run #142, which then completed successfully. The recovery logic works; normal scheduled-event delivery remains an operational risk.

No production migration, import, reset, restore, or write was attempted. The separate Dog Tag Day Story Wall Supabase project was not queried for this work and remains outside the Everything Hub target. The files and isolated tests do not publish or switch production traffic.

## Hurricane Core intelligence storage

`003_eh_quad_intelligence.sql` is the additive storage layer for the Hurricane
Core. Apply it only after PostgreSQL accepts connections and only after
`002_eh_quad_schema.sql` has been verified in the dedicated
`everything-hub-control` project.

It adds:
- An explicit source registry with neutral defaults; publisher domains are not
  assigned credibility automatically.
- Topic assignments with method, confidence, and verification status.
- Evidence links that preserve corroboration, contradiction, updates, and
  references as separate relationship types.
- Append-only feedback events with no account identifiers or email addresses.
- A versioned rank profile for the bounded learning weights.
- A source-quality score column on documents, defaulting to neutral 0.500.

All new tables have RLS enabled. Only the server-side service role receives
access. Feedback events are insert/read only; the migration grants no update or
delete permission on that table. The migration does not create policies or
grants for anon/authenticated/public.

The migration is prepared and statically tested but **not applied**. Do not
apply it, import feedback, or write production rows while the PostgreSQL
recovery error SQLSTATE `57P03` persists. Never use Dog Tag Day Story Wall.
