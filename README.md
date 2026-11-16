# Everything Hub Crawler

Independent, polite crawler for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt` (a newly assembled set, not a recovered historical list).
- Up to 8 concurrent workers, with at most one request per host in each batch.
- A 1-second minimum host interval, increased when robots.txt declares a longer Crawl-delay or Request-rate.
- A six-minute run budget; next-allowed host timestamps are persisted with the frontier across scheduled runs.
- Persistent frontier with a 50,000 discovered-URL ceiling; host pacing is keyed by hostname across HTTP and HTTPS.
- Same-host redirects are followed only after the target URL passes robots.txt; cross-host redirects are blocked. A strict 80-page fetch cap is enforced per run; sitemap requests are counted separately.
- Public search shards are validated against the canonical index before each crawler commit.
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
GitHub Actions runs the crawler every 15 minutes at minutes 7, 22, 37, and 52 UTC, offset away from quarter-hour load peaks, and supports manual dispatch. Ordinary crawler-source pushes trigger validation only; editing the crawler workflow itself triggers one bounded crawl. Each crawl validates configuration, fetches at most 80 pages, rebuilds and verifies public search shards, and publishes a compact health report. Crawler data commits cannot recursively launch another crawl. A separate live-diagnostics workflow checks the homepage, browser endpoint, database-backed search, independent search fallback, and media APIs hourly at minute 17 UTC. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, and depth distribution. Attempted fetches are not counted as indexed pages.

## Search integration status
The crawler writes the canonical repository index (`data/index.json`) and a compact static catalog (`public-search/`). At 2026-11-16 08:34 UTC the repository catalog contains **2,654 pages in 14 shards across 31 hosts**. A fresh inspection of the current production deployment `dpl_C58draWWCZgFt2bvmmHiEBkCic3J` at 08:37 UTC found **13 site files and no search-index manifest or shards**; `/search-index/manifest.json`, `/search-index/shard-0001.json`, and the legacy `/search-index.json.gz` all returned HTTP 404. The current homepage script still references the missing gzip file. GitHub source commit `cfbc2b5` updates the homepage catalog loader to consume the validated manifest and 14 JSON shards, and commit `1d1a6b9` adds public CORS and short-cache headers to `public-search/vercel.json`; neither change has been deployed to the homepage yet. A separate Vercel static-index project would allow automatic GitHub publishing, but Vercel rejected project linking because the account has no GitHub Login Connection: `Failed to link rshure001/EverythingHub-Crawler. You need to add a Login Connection to your GitHub account first.` Do not claim the live homepage currently serves the crawler catalog or that future crawler commits publish automatically. The current production homepage itself still returns HTTP 200.

The production database path is separate. The `eh_quad` migration, schema-locked importer, and authenticated search API pass isolated PostgreSQL tests. On 2026-11-16 08:33 UTC, the deployed `everything-hub-web-search` Edge Function was updated to **version 11** from the reviewed GitHub source. A fresh read-back verified that it uses `supabase.schema("eh_quad")`, calls `eh_search_pages`, and reads `eh_documents`; it contains no legacy `eh_pages` query. This removes the prior public-schema fallback from that endpoint. Production search is still **not usable or verified** because PostgreSQL continues to reject connections with `57P03`. The last 24-hour log query showed 5,275 `57P03` events and 51 `53100` disk-full events (latest `53100` at 07:15:24 UTC). No production migration or import has been attempted. After PostgreSQL recovers, verify the migration, then test the deployed function against `eh_quad` before declaring database-backed search live.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.
