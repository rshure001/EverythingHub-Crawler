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
- Checks each site's `robots.txt`; if robots rules cannot be read, the crawler fails closed for that host. Also honors applicable HTML `meta name="robots"` and HTTP `X-Robots-Tag` `noindex`/`nofollow` directives, including crawler-specific directives; `rel="nofollow"` links are not added to the frontier.
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

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, depth distribution, robots-policy exclusions, and noindex/nofollow exclusions. Attempted fetches are not counted as indexed pages.

## Search integration status

Operational snapshot: **2026-11-17 01:09 UTC**.

- The latest completed GitHub Actions crawler run (#43991961223) fetched **80** pages and indexed **62**. The catalog now contains **3,363 pages across 18 shards and 31 hosts**. The frontier contains **31,153 discovered URLs** and **21,407 queued URLs**.
- That run excluded **7 noindex pages**, did not expand **4 nofollow pages**, blocked **1 robots-disallowed URL**, and recorded **0 rate-limited responses** and **0 transient retries**. The compressed public catalog is **999,423 bytes**; its SHA-256 and all shard records passed validation.
- PR #22 is merged. The crawler now honors applicable HTML `meta name="robots"`, HTTP `X-Robots-Tag`, page-level `nofollow`, and link-level `rel="nofollow"`. A fetched page explicitly marked `noindex` is removed from the next index snapshot; unrelated records are preserved.
- The latest crawler run and the post-merge validation workflow both passed. The isolated importer smoke test then imported **3,363/3,363** records into a temporary PostgreSQL database, skipped **0**, found **0 invalid lane rows**, and passed pagination and lane-filter checks. This is test evidence only, not a production import.
- The deployed `everything-hub-web-search` Edge Function is version **19** and explicitly scopes database access to `eh_quad`. When PostgreSQL is unavailable it falls back to the committed static crawler catalog and then the independent Bing RSS fallback. The last completed live diagnostics returned crawler-index results for NASA and independent Bing RSS results for Rolex.
- The deployed media-search Edge Function is version **21**. The last live diagnostics returned **21 Wikimedia Commons image results and 2 video results**, with source and reuse-rights metadata. The media-discovery smoke workflow passed. Bing results remain discovery references, not automatically reuse-licensed media.
- Vercel production deployment `dpl_6iQjvwS9DDEER8aaRZMyzbrktFGM` is **READY** on the existing `everything-hub-ten.vercel.app` alias. It includes the real binary artwork at `/assets/everythinghub-approved.png`. The homepage now calls the `everything-hub-web-search` Edge Function for crawler results, so it uses `eh_quad` when available and the published static catalog/Bing fallback during database recovery instead of depending on absent local shard files.
- Production browser verification **passed** in run #43993170079: HTTP 200, approved artwork loaded at **709 px** natural width, all **8** category tiles present, search input visible, **390 px** mobile viewport with no horizontal overflow, **24** visible Rolex results and **24** visible NASA results, and **0** page errors. Direct endpoint probes returned NASA from the static crawler catalog (**1,792** matches, gzip transport) and Rolex from the independent Bing RSS fallback (**10** results).
- The GitHub Pages publisher rebuilt and validated the catalog and uploaded its release artifact, but deployment remains blocked. `actions/configure-pages` received **404** when reading the Pages site, then **“Resource not accessible by integration”** when attempting to create it, despite the workflow token reporting `Pages: write`. A repository owner/administrator must enable Pages or grant the GitHub Actions integration permission to create the Pages site. No GitHub Pages release was published.

### Production database blocker

The Supabase control plane reports the project as active, but PostgreSQL is repeatedly restarting. At **2026-11-17 01:09:01 UTC**, the latest observed logs still show **“database system was not properly shut down; automatic recovery in progress”** and WAL redo at 40 seconds. SQLSTATE **57P03** (“the database system is not accepting connections”; detail: “Hot standby mode is disabled”) continues through 01:08:59. A read-only `eh_quad` table listing now fails with **ECONNREFUSED** to PostgreSQL port 5432. The production crawler Edge Function remains intentionally paused with HTTP 503 while the database is unavailable. No production migration, import, restore, or destructive operation has been attempted.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.

## Latest automation check — 2026-11-17 02:59 UTC

- **Database remains unavailable.** A read-only listing of the dedicated `eh_quad` schema still fails with a PostgreSQL connection timeout. PostgreSQL logs at **02:59:56 UTC** show another automatic-recovery restart and SQLSTATE **57P03** (the database is not accepting connections; hot standby is disabled). No migration, import, restore, or data write was attempted.
- **The broken hero image is explained by the same outage.** At **02:32:04 UTC**, Supabase Storage logged HTTP **429** for `/object/info/public/everything-hub-publishing/assets/approved-master.png`; its internal database lookup failed with `08P01` / `server_login_retry`. The object URL is being requested, but Storage cannot complete the database-backed object lookup during recovery.
- The production `everything-hub-crawler` Edge Function remains intentionally paused (version **49**, HTTP 503). This protects the existing index/frontier while the database is unstable.
- The media-search Edge Function (version **21**) returned HTTP **200** for image and video discovery requests at **02:24:56 UTC**. The web-search Edge Function (version **19**) remains scoped to `eh_quad` and contains verified static-catalog plus independent Bing RSS fallbacks.
- The latest committed crawler snapshot remains **3,363 pages / 18 shards / 31 hosts**, with **31,153 discovered URLs** and **21,407 queued URLs**. The last recorded completed crawl run (#43991961223) fetched **80** pages and indexed **62**; no newer crawler-output commit is present after the **01:09:27 UTC** status commit.
- A separate Vercel **preview** deployment was created to remove the Storage dependency from the hero image: **https://everythinghub-homepage-fixed-824h3trnm-rshure001-8053.vercel.app/**. It is **READY**, contains `src/index.html` and a local `src/assets/everythinghub-approved.png`, and uses the existing web/media Edge Functions for search. The image asset is the same repository-approved binary (SHA-1 `9770d91de70b91d27d83a7c0e00bbc6a9d2e3143`). The preview has no custom-domain alias and has **not** replaced `everythinghub.ink`. Browser rendering/search verification of this new preview is still pending; Vercel's deployment/file-tree checks alone do not prove a browser pass.
- No `eh_quad` data, Dog Tag Day data, or Story Wall data was changed.
