# Everything Hub Crawler

Independent, polite crawler for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt` (a newly assembled set, not a recovered historical list).
- Up to 8 concurrent workers, with at most one request per host in each batch.
- A 1-second minimum host interval, increased when robots.txt declares a longer Crawl-delay or Request-rate.
- A six-minute run budget; next-allowed host timestamps are persisted with the frontier across scheduled runs.
- Persistent frontier with a 50,000 discovered-URL ceiling; host pacing is keyed by hostname across HTTP and HTTPS.
- Same-host redirects are followed only after the target URL passes robots.txt; cross-host redirects are blocked. A strict 80-page fetch cap is enforced per run; sitemap requests are counted separately.
- Public search shards are validated against the canonical index before each crawler commit. New manifests include a SHA-256 digest for every shard; the validator checks the digest as well as byte size, page count, URL uniqueness, and exact canonical-index coverage. Legacy manifests without digest metadata remain readable during rollout.
- Maximum link depth: 3.
- Same-host HTML link discovery plus bounded sitemap discovery from robots.txt declarations: up to 3 seed hosts per run, one declared sitemap and up to 2 same-host child sitemaps per host, at most 100 page URLs per host, and a 1 MB sitemap-response cap. Hosts are rechecked after 7 days; robots failures are retried after 1 hour. The crawler honors `Retry-After` on 429/503 responses, applies conservative cooldowns to transient failures, and requeues retryable URLs in the durable frontier.
- Checks each site's `robots.txt`; if robots rules cannot be read, the crawler fails closed for that host.
- Stores normalized page records in `data/index.json` and resumable frontier state in `data/frontier.json`.

## Run locally
```bash
python -m py_compile crawler/crawl.py crawler/report.py
python -m unittest discover -s crawler -p 'test_*.py' -v
python crawler/crawl.py
python crawler/report.py
```

## Automation and observability
GitHub Actions runs the crawler hourly at minute 13 UTC and supports manual dispatch. A separate isolated importer smoke test runs every 15 minutes at minutes 7, 22, 37, and 52 UTC against a temporary PostgreSQL service; it never connects to production. Ordinary crawler-source pushes trigger validation only; editing the crawler workflow itself triggers one bounded crawl. Each crawl validates configuration, fetches at most 80 pages, rebuilds and verifies public search shards, and publishes a compact health report. Crawler data commits cannot recursively launch another crawl. A separate live-diagnostics workflow checks the homepage, browser endpoint, database-backed search, independent search fallback, and media APIs hourly at minute 17 UTC. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, and depth distribution. Attempted fetches are not counted as indexed pages.

## Search integration status
The crawler writes the canonical repository index (`data/index.json`) and a compact static catalog (`public-search/`). As of 2026-11-16 11:08 UTC, the latest completed crawler run contains **2,784 pages in 15 shards across 31 hosts**. The latest completed production browser check at 10:08 UTC confirmed that the canonical Vercel homepage returned HTTP 200, displayed all eight categories and the approved artwork, and served **2,654 pages in 14 shards**. That verified production catalog is 130 pages behind the current repository snapshot. A new production catalog check was triggered after the 11:08 crawler update and is still running; do not claim production is synchronized until it completes. The Vercel project has no verified GitHub auto-deploy connection, so crawler commits do not publish there automatically. The current GitHub Pages workflow publishes only `site/` and does not include `public-search/`; it is not yet a crawler-catalog publishing route. The homepage source in `vercel-homepage/index.html` is designed to load `search-index/manifest.json` and its shards, but no production deployment was performed in this run. No custom-domain or DNS changes were made.

The production database path is separate. The `eh_quad` migration, schema-locked importer, and authenticated search API pass isolated PostgreSQL tests. The deployed `everything-hub-web-search` Edge Function is **version 11** and was read back using `supabase.schema("eh_quad")`, `eh_search_pages`, and `eh_documents`; it contains no legacy `eh_pages` query. The Supabase control plane reports the project as `ACTIVE_HEALTHY`, but PostgreSQL is still unavailable: the latest log at 2026-11-16 11:07:30 UTC reports WAL redo in progress (`2260.00 s`) and repeated `the database system is not accepting connections` messages. SQL, table listing, and migration listing fail with `57P03`. The deployed `everything-hub-crawler` Edge Function is version 49 and deliberately returns HTTP 503 with a paused/recovery response. No production migration or import has been attempted. After PostgreSQL accepts connections, verify the `eh_quad` migration and tables, run the additive importer against `eh_quad` only, then test database-backed search before declaring it live.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.
