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
The crawler writes the canonical repository index (`data/index.json`) and a compact static catalog (`public-search/`). At 2026-11-16 06:08 UTC the repository catalog contains **2,654 pages in 14 shards across 31 hosts**. The canonical Vercel production hostname now serves the same **2,654-page / 14-shard** snapshot from deployment `dpl_2LExaSgucu7XGWARGsU69KJTiWFY`; the homepage and all 14 shard URLs returned HTTP 200, and the homepage title, search box, eight categories, and approved artwork were verified. This was a controlled Vercel file-upload deployment from the GitHub snapshot. The Vercel project is not linked to the GitHub repository, so automatic GitHub-to-Vercel publishing is still not configured. Do not claim that future crawler commits publish automatically.

The production database path is separate. The `eh_quad` migration, schema-locked importer, and authenticated search API pass isolated PostgreSQL tests. Production Supabase is still in WAL redo recovery and rejects connections with PostgreSQL `57P03`; no production migration or import has been attempted. The deployed database-backed Edge Function also timed out during live diagnostics. Do not route production search to `eh_quad` until PostgreSQL is healthy, the dedicated migration is applied, and the deployed function is verified against `eh_quad` only.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.
