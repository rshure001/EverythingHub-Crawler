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
- The Playwright browser-check workflow was updated in commit `e20d4bcc0671883e250e2be7d7681e230346cf73`: push-triggered checks target the new local-artwork preview, while scheduled and crawler-completion checks continue targeting the existing production alias. No status result for the new push-triggered check was returned during this run, so the preview remains browser-unverified.

## Latest automation check — 2026-11-17 05:00 UTC

- **Crawler run #44014574628 completed successfully** (job crawl). The first two publication attempts encountered concurrent advances to main; the workflow's bounded replay logic restarted from the latest main twice, reran the capped crawl, rebuilt and validated the catalog, and the final push succeeded. This confirms the concurrent-push recovery path is working.
- Final published crawl: **80 fetched, 60 indexed, 20 not indexed**; **3,421 indexed records**, **31 unique hosts**, **31,221 discovered URLs**, and **21,293 queued URLs**. Policy accounting: **102 robots-blocked**, **6 noindex**, **4 nofollow**, **3 robots-policy fetch failures (fail-closed)**, **0 rate-limited responses**, and **0 transient retries queued**.
- The public catalog has **18 shards** and a **1,027,750-byte compressed catalog**. The workflow validator confirmed all shard byte sizes, SHA-256 digests, URLs, exact canonical-index coverage, and compressed-catalog integrity. Manifest updated_at: **2026-11-17 04:06:13 UTC**.
- **Search smoke #44015255023 passed:** the database-independent crawler/static search returned **1,800 NASA matches**; the independent Bing RSS fallback returned **10 Rolex results**.
- **Media smoke #44015399429 passed:** **21 image results** and **2 video results**, both from Wikimedia Commons, with source pages and reuse metadata. The first image is CC BY-SA 3.0 with attribution required; the first Apollo 11 video is Public Domain with no attribution requirement. The smoke test also verifies that Bing results are explicitly marked unverified for reuse.
- **Production browser check #44015535266 passed** against https://everythinghub-homepage-fixed-rshure001-8053.vercel.app/: HTTP 200, exactly one search form inside the approved artwork, all 8 categories, 2 image cards, 2 video cards, 24 visible Rolex results, 24 visible NASA results, no mobile horizontal overflow, and zero page errors. Vercel deployment dpl_82EpL9Ky8x4ezwMtbW7aKercwjUC is READY. The stable Vercel alias was fetched successfully. Vercel's domain association API reports everythinghub.ink and everything.dogtagday.org verified and attached, but direct fetch probes for those two custom hostnames returned deployment_fetch_failed in this run; custom-host browser rendering remains unverified.
- **Supabase production blocker persists.** The control plane reports ACTIVE_HEALTHY, but read-only listing of the dedicated eh_quad schema fails with PostgreSQL SQLSTATE 57P03: the database system is not accepting connections / Hot standby mode is disabled. Postgres logs still show the error at 04:59:52 UTC. The health advisor returned no lints; the security advisor could not complete because the database connection failed. The deployed crawler Edge Function remains version 49 with an explicit paused/503 response. The public web-search function (v19) and media-search function (v21) remain active and their fallback/smoke checks passed.
- The GitHub Pages publisher's previously documented blocker remains unresolved: actions/configure-pages received a 404 for the Pages site and Resource not accessible by integration when attempting to create it. No permissions or repository settings were changed. Vercel production publishing is the currently verified route.
- No production database writes, migrations, imports, or restores were attempted. No Dog Tag Day or Story Wall data was changed.


## Latest automation check — 2026-11-17 06:10 UTC

- Added crawler regression coverage for robots.txt HTTP 429, 500, 503, and network timeouts. All such policy-fetch failures must remain fail-closed. Added schema-selector tests rejecting qualified names and SQL-injection-shaped values; the importer remains locked to `eh_quad`.
- **Validation #44029121082 passed:** 66 crawler tests and 18 database/schema-isolation tests; crawler compilation, search-function tests, static manifest validation, and shard/catalog integrity checks passed.
- **Isolated importer smoke #44029120988 passed:** imported **3,421/3,421** current repository records into a temporary PostgreSQL 17 service under `eh_quad`; **0 invalid lane rows**. Lane counts: business 10, culture 55, government 562, nature 387, news 152, science 2,201, technology 54. Authenticated search, lane filtering, and pagination fixtures passed. This is isolated test evidence only; it is not a production import.
- Updated live diagnostics to run after each completed crawler workflow and to test the current canonical Vercel alias, `https://everythinghub-homepage-fixed.vercel.app/`. **Diagnostics #44029045670 passed:** HTTP 200, 8 categories, approved artwork HTTP 200, and exactly **one** functional search form inside the artwork. The public static catalog returned 1,800 NASA matches; independent Bing RSS returned 10 Rolex results; Wikimedia Commons returned 21 images and 2 videos with usage-rights metadata.
- The live diagnostics continue to identify PostgreSQL as unavailable. The static crawler catalog and independent fallback work, but the database-backed `eh_quad` search path is not restored. Postgres logs at **06:08:27 UTC** show WAL redo still in progress after **5,280 seconds**; the latest “not accepting connections” event was **06:08:36 UTC**. No “ready to accept connections” event was present in the queried log window. PostgREST reported schema-cache error **PGRST002** and `/rpc/eh_search_pages` returned HTTP 503 at **06:09:26 UTC**. The production crawler Edge Function remains intentionally paused at version 49.
- Updated the GitHub Pages publisher so a 404 site lookup no longer retries the known-unauthorized site-creation action. **Publisher #44028605945 passed validation and uploaded a 2,065,449-byte release artifact; deployment was correctly skipped.** Exact blocker remains: the Pages site lookup returns 404 and the previous create attempt returned **“Resource not accessible by integration.”** The Vercel canonical alias is the verified publishing route; `everythinghub.ink` still timed out in the diagnostics check.
- No production database writes, migrations, imports, or restores were attempted. No Dog Tag Day or Story Wall data was changed.
