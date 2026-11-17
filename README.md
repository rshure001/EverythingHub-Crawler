# Everything Hub Crawler

Independent, polite crawler for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt` (a newly assembled set, not a recovered historical list).
- Up to 16 concurrent workers, with at most one request per hostname in each batch; a batch never contains two URLs for the same host.
- A 1-second minimum host interval, increased when robots.txt declares a longer Crawl-delay or Request-rate.
- A six-minute run budget; next-allowed host timestamps are persisted with the frontier across scheduled runs.
- Persistent frontier with a 50,000 discovered-URL ceiling; host pacing is keyed by hostname across HTTP and HTTPS.
- Same-host redirects are followed only after the target URL passes robots.txt; cross-host redirects are blocked. A strict 1,600-page fetch cap is enforced per run; sitemap requests are separately bounded.
- Public search shards are validated against the canonical index before each crawler commit. New manifests include a SHA-256 digest for every shard; the validator checks the digest as well as byte size, page count, URL uniqueness, and exact canonical-index coverage. Legacy manifests without digest metadata remain readable during rollout.
- Maximum link depth: 3.
- Same-host HTML link discovery plus bounded sitemap discovery from robots.txt declarations: up to 3 seed hosts per run, one declared sitemap and up to 2 same-host child sitemaps per host, at most 100 page URLs per host, and a 1 MB sitemap-response cap. Hosts are rechecked after 7 days; robots failures are retried after 1 hour. The crawler honors `Retry-After` on 429/503 responses, applies conservative cooldowns to transient failures, and requeues retryable URLs in the durable frontier.
- Checks each site's `robots.txt`; if robots rules cannot be read, the crawler fails closed for that host. Also honors applicable HTML `meta name="robots"` and HTTP `X-Robots-Tag` `noindex`/`nofollow`/`nosnippet` directives, including crawler-specific directives; `max-snippet:N` limits stored text and descriptions; `rel="nofollow"` links are not added to the frontier.
- Stores normalized page records in `data/index.json` and resumable frontier state in `data/frontier.json`.

## Run locally
```bash
python -m py_compile crawler/crawl.py crawler/report.py crawler/build_search_shards.py crawler/validate_public_index.py
python -m unittest discover -s crawler -p 'test_*.py' -v
python crawler/crawl.py
python crawler/build_search_shards.py
python crawler/validate_public_index.py
python crawler/report.py
```

## Automation and observability
GitHub Actions schedules the crawler every five minutes (`*/5 * * * *`) and supports manual dispatch. A repository concurrency group serializes crawler runs so a new run cannot overlap an active crawl; at most one pending run is retained by GitHub Actions. Each run validates configuration and tests, allows up to 16 workers across distinct hosts, enforces the six-minute crawl budget and 1,600-page hard cap, rebuilds and verifies the public search shards, and publishes a compact health report. Per-host pacing remains at least one second and increases for robots.txt `Crawl-delay` or `Request-rate`; host timestamps persist in the frontier, and `Retry-After`/transient cooldowns are requeued rather than bypassed. A separate watchdog checks every 15 minutes and also runs after crawler/diagnostic workflow completion; it validates manifest freshness before deciding whether to dispatch a recovery crawl. The isolated importer smoke test runs every 15 minutes against a temporary PostgreSQL service and never connects to production. Ordinary crawler-source pushes trigger validation only; editing the crawler workflow itself triggers one bounded crawl. The live-diagnostics workflow checks the homepage, browser endpoint, database-backed search, independent search fallback, and media APIs hourly at minute 17 UTC and after completed crawler runs. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, depth distribution, robots-policy exclusions, and noindex/nofollow exclusions. Attempted fetches are not counted as indexed pages.

## Search integration status

Historical operational snapshot: **2026-11-17 01:09 UTC**. Later dated checks are recorded below.

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


## Recovery crawl and post-crawl verification — 2026-11-17 06:15 UTC

- The schedule watchdog detected that the last successful crawl was **7,670 seconds** old and dispatched exactly one recovery run. Watchdog decision: `dispatch`; the recovered crawler run completed successfully. The updated watchdog workflow test now covers both crawler and live-diagnostics completion events; all **11 watchdog tests** passed. A subsequent watchdog check reported `healthy` at **212 seconds** after the successful crawl and did not dispatch a duplicate.
- **Crawler run #101 / 44029467038 passed:** **80 fetched, 52 indexed, 28 not indexed**. The public index grew to **3,469 pages**, **31 hosts**, **31,271 discovered URLs**, and **21,261 queued URLs**. Policy accounting: **2 robots-blocked**, **14 noindex**, **11 nofollow**, **2 robots-policy fetch failures (fail-closed)**, **0 rate-limited responses**, and **0 transient retries queued**. The run completed in **18.9 seconds** and committed the updated index/frontier as commit `1ab6221`.
- The public index validator passed with **19 shards** and a **1,052,220-byte** gzip catalog. All shard sizes, SHA-256 digests, canonical URLs, record coverage, and compressed-catalog integrity matched the committed index.
- **Post-crawl diagnostics #44029743684 and #44029818545 passed:** the current canonical Vercel alias returned HTTP 200; all 8 categories were present; the approved artwork returned HTTP 200; and the homepage contained exactly **one** functional search form inside the artwork. Search returned **1,808** public crawler-index NASA matches and **10** independent Bing RSS Rolex results. Media discovery returned **21 images** and **2 videos** from Wikimedia Commons with license/attribution metadata. The custom hostname `everythinghub.ink` still timed out; the canonical Vercel alias remains the verified target.
- **Publisher #44029818589 passed validation:** the 3,469-page / 19-shard catalog was rebuilt and verified, and a **2,116,549-byte** GitHub Pages artifact was uploaded (artifact ID `14304945836`). The deploy job was skipped because the Pages site lookup returned 404 and the previous create attempt returned **“Resource not accessible by integration.”** No repository settings were changed.
- **Supabase production PostgreSQL remains unavailable.** At **06:15:37 UTC**, WAL redo was still in progress after **5,710 seconds**; the database was still not accepting connections at **06:15:36 UTC**. PostgREST continued reporting **PGRST000** / SQLSTATE **57P03** (“Hot standby mode is disabled”). The live diagnostics correctly used the static crawler catalog and independent Bing fallback. The production crawler Edge Function remains deliberately paused. No production schema listing, migration, import, restore, or write was attempted.
- No Dog Tag Day or Story Wall data was changed.


## Automation check — 2026-11-17 06:57 UTC

- **Production project control plane:** `everything-hub-control` reports `ACTIVE_HEALTHY` (project ref `vdceayvfgkwpgwixrhwi`). This does not mean PostgreSQL is accepting connections.
- **Read-only schema check:** `list_tables` scoped only to `eh_quad` failed with `FATAL: 57P03` / `Hot standby mode is disabled`. No query against the separate Dog Tag Day Story Wall project was run.
- **Fresh database evidence:** PostgreSQL logs at **2026-11-17 06:57:55 UTC** still report WAL `redo in progress` after **2,130 seconds** in the current startup process (started 06:22:24 UTC). The latest observed client failure was **06:57:52.894 UTC**, SQLSTATE **57P03**, with the message “the database system is not accepting connections.” PostgREST and PgBouncer log streams remain active, but that does not establish SQL readiness.
- **Deployed Edge Function inventory:** crawler **v49** is ACTIVE but deliberately returns HTTP 503 with a paused status; web search **v19**, media search **v21**, and static publisher **v10** are ACTIVE. The web-search implementation is explicitly scoped to `eh_quad`, applies a 2.5-second database fetch deadline, and has SHA-256-validated static-catalog fallback. No function was redeployed in this check.
- **Latest completed GitHub checks:** crawler run **#101** (`44029467038`) succeeded; watchdog `44029875227` succeeded; post-crawl diagnostics `44029818545` succeeded; publisher validation/artifact upload `44029818589` succeeded with its Pages deploy job skipped; validation `44029955645` succeeded. The crawler snapshot remains **3,469 pages**, **19 shards**, **31 hosts**, **31,271 discovered URLs**, and **21,261 queued URLs** (latest completed crawl evidence, not a new crawl in this check).
- **Safety:** no production SQL writes, migrations, imports, restores, or crawler restarts were attempted. The production crawler remains paused until PostgreSQL accepts connections and the dedicated `eh_quad` schema can be inspected read-only. No Dog Tag Day or Story Wall data was touched.
- **Next gate:** wait for the current WAL recovery to finish; then repeat the read-only `eh_quad` table inventory and run the isolated importer test against the resulting schema shape before enabling any production write path. If the 57P03/WAL-redo condition persists, escalate the project recovery issue to Supabase support rather than attempting a restore or database reset.


## Automation check — 2026-11-17 07:01 UTC

- **Search fallback hardening:** `supabase/functions/everything-hub-web-search/db-fetch.mjs` now classifies PostgREST **PGRST002** (schema-cache query failure during database startup/recovery) as transient. This routes that failure through the existing SHA-256-validated public-catalog fallback. Added a regression test for PGRST002 while preserving the negative test that missing RPC code PGRST202 is not treated as a transport outage.
- **CI passed:** validation run **44035306909** completed successfully, including crawler/schema/Edge Function tests and static-manifest validation. Search API smoke **44035306890** passed with **1,808 NASA crawler-index matches** and **10 independent Bing RSS Rolex results**.
- **Production Edge Function deployed:** `everything-hub-web-search` is now **version 20**, ACTIVE, with the PGRST002 classifier included. Its entrypoint and all three helper files match the current GitHub source. JWT setting remains unchanged (`verify_jwt=false`). No schema or data write occurred.
- **Post-deploy live diagnostics passed:** run **44035306955**. Canonical Vercel homepage returned HTTP **200**; the approved artwork returned HTTP **200 image/webp**; exactly **one** search form was found inside the artwork; all **8** categories were present. Search returned the verified public crawler catalog for NASA and independent Bing RSS for Rolex; image discovery returned **21** Wikimedia Commons results and video discovery returned **2**. The database-backed Rolex path remains blocked, but its static/Bing fallback responded HTTP **200** in **3.525 seconds**. The custom hostname `everythinghub.ink` remained unreachable.
- **Production browser verification passed:** run **44035306928**. HTTP **200**, one visible search input and one form, form inside artwork, all **8** category modes, **2 image cards**, **2 video cards**, **24 visible Rolex results**, **24 visible NASA results**, and **0 page errors**. At the **390 px** mobile viewport, document width was **390 px** with no horizontal overflow; search remained visible, inside the artwork, and absolutely positioned. Browser report and screenshots were uploaded as artifact **14306309382**.
- **Artwork quality note:** the approved artwork request succeeds, but the current source image reports only **220 px** natural width. It was not replaced; a higher-resolution copy of the same approved artwork is still needed before improving display sharpness.
- **Database remains blocked:** a fresh read-only `eh_quad` table listing still fails with SQLSTATE **57P03**. PostgreSQL logs at **07:01:15 UTC** show WAL redo at **2,330.07 seconds**; the latest “not accepting connections” event was **07:01:23.687 UTC**, detail “Hot standby mode is disabled.” The Supabase control plane still reports `ACTIVE_HEALTHY`, which is not evidence of SQL readiness.
- **Safety:** no production migration, import, restore, crawler restart, or SQL write was attempted. The crawler Edge Function remains paused at version **49**. No Dog Tag Day or Story Wall data was touched.
- **Next gate:** wait for PostgreSQL recovery, then inventory only `eh_quad` read-only. Once that succeeds, validate table shape and run the temporary PostgreSQL importer tests before considering a production additive import. Keep the crawler paused until the schema check and import plan are verified.

## Automation check — live verification snapshot (2026-11-17 08:01 UTC)

- **Public catalog revalidated read-only:** `public-search/manifest.json` declares **3,469 pages across 19 shards**; the sum of all shard page counts is exactly 3,469. All 19 shard SHA-256 strings are valid, every shard is within the **350,000-byte** limit (largest **349,842 bytes**), and the compressed catalog is **1,052,220 bytes** with a valid SHA-256 digest. Manifest update time: **2026-11-17 06:12:36 UTC**.
- **Live homepage HTTP check:** canonical Vercel alias returned **HTTP 200**, **76,054 bytes** of HTML, the approved artwork reference, exactly **one** search form, and all **8** category modes in the delivered markup. This was an HTTP/HTML check, not a new JavaScript browser-render test; the most recent full browser-render evidence remains run **44035306928**.
- **Live public search endpoint:** NASA returned **1,808** catalog matches and 5 requested sample records. Response explicitly reports `fallback=true`, `fallbackReason=database_unavailable`, `indexIntegrity=sha256`, and `indexTransport=gzip`; this confirms the static catalog fallback is serving while database search is unavailable.
- **Live media endpoint:** NASA image discovery returned **17** Wikimedia Commons results in this request, with source pages, creator/credit fields, and available license metadata. Public-domain entries were identified; reuse still requires checking the linked source/license details. This is a fresh endpoint result count, not a claim that 17 cards were rendered on the homepage.
- **Deployed search fallback source verified:** Supabase `everything-hub-web-search` is **ACTIVE v20**. Its deployed `db-fetch.mjs` content exactly matches the current GitHub file, including transient classification for SQLSTATE **57P03** and PostgREST **PGRST002**. Media search remains **ACTIVE v21**. The crawler remains **ACTIVE v49 but deliberately paused**, returning HTTP 503 until database write readiness is restored.
- **Database recovery remains blocked:** Supabase control plane reports `ACTIVE_HEALTHY`, but the read-only `eh_quad` table inventory fails with **SQLSTATE 57P03** (“the database system is not accepting connections”; detail: “Hot standby mode is disabled”). In the latest 24-hour PostgreSQL log query, **15,909 of 21,928** events were 57P03 connection failures. The latest 57P03 was **08:01:07.846 UTC**; a new automatic-recovery startup began at **08:00:08 UTC**, and WAL redo was still in progress at **08:01:09.874 UTC**. This indicates the SQL endpoint is not ready despite the control-plane health label.
- **Safety:** no SQL writes, migrations, imports, restores, or crawler restarts were attempted. Only the `everything-hub-control` project was inspected; the separate Dog Tag Day Story Wall Supabase project was not queried or modified. No Dog Tag Day or Story Wall data was changed.
- **Next gate:** wait for PostgreSQL recovery to complete, then perform a read-only inventory of `eh_quad` only. Keep the crawler paused until that succeeds; run the isolated importer test against the actual schema shape before considering an additive production import. The GitHub Pages permission and `everythinghub.ink` routing blockers remain as previously documented.


## Homepage media-search fix and latest production gate — 2026-11-17 09:03 UTC

- Read-only public catalog check: **3,530 pages across 19 shards**. The manifest's compressed catalog is **1,081,352 bytes** with SHA-256 `1590f7006637b8c5dd1f2d21ca68a66eadc47e01a8f28b73f4d2e92c87f606bf`. This is the current committed manifest, not a new crawl performed by this verification.
- Fixed the Everything Hub homepage's Images/Videos search path in `rshure001/rondo-commando` commit `246a3a216d1d091544ff4ac4e6a5806f17ad288d`. `searchPublicSources()` called the removed `fetchCommonsMedia()` helper; it now uses the existing `fetchHomepageMedia()` wrapper to call the deployed media-search Edge Function and carries through provider, thumbnail/video URLs, and rights notes. Static assertions confirm zero remaining `fetchCommonsMedia()` references and one shared-helper call.
- Vercel production deployment `dpl_Bo4dEgGCPvsKe4XdKdHgb9qxAcGn` reached **READY**. The canonical alias `https://everythinghub-homepage-fixed.vercel.app/` returned **HTTP 200**; response source contains exactly one search form, the approved artwork reference, and the corrected media-helper call. This is an HTTP/source check only; no interactive browser test of Images/Videos mode was run in this verification.
- Supabase Edge Function inventory: `everything-hub-web-search` **v20 ACTIVE**, `everything-hub-media-search` **v21 ACTIVE**, and `everything-hub-crawler` **v49 ACTIVE but deliberately paused** (explicit HTTP 503 response). No Edge Function was redeployed in this run.
- PostgreSQL remains unavailable despite the project control plane reporting `ACTIVE_HEALTHY`. The read-only `eh_quad` table inventory still fails with SQLSTATE **57P03** / `Hot standby mode is disabled`. PostgreSQL logs at **09:02:33 UTC** still say the database is not accepting connections; at **09:02:30 UTC**, WAL redo was still in progress after **3,740 seconds**. No production SQL writes, imports, migrations, restores, or crawler restart were attempted.
- The GitHub Pages publisher permission issue and `everythinghub.ink` custom-host fetch failure remain unresolved. The canonical Vercel alias is the only hostname verified in this run.
- Safety: only Everything Hub homepage code and this crawler-repository status note were changed. The production `eh_quad` schema was not modified. No Dog Tag Day or Story Wall data was touched.
- Next gate: continue waiting for PostgreSQL recovery; once SQL accepts connections, inspect only `eh_quad` read-only and rerun the isolated importer validation before enabling any production write path. Separately, run an interactive browser test for Images and Videos mode after the new homepage build is available to the browser-test workflow.


## Client catalog delivery correction — 2026-11-17 09:07 UTC

- A direct production GET exposed that the previous local `search-index.json.gz` deployment was a **zero-byte file** (HTTP 200, `content-length: 0`). It was not counted as a successful index delivery.
- Corrected the homepage loader to retrieve the current public crawler manifest and compressed catalog from the repository's raw public index. It validates manifest structure, compressed byte length and SHA-256 before decompressing. If browser gzip decompression is unavailable or the compressed catalog fails validation, it falls back to the 19 manifest-listed shards, with concurrency capped at **4** and per-shard byte length, SHA-256, schema, and page-count checks.
- Removed the empty local gzip artifact from the new deployment. Vercel production deployment `dpl_4TXd2zQfcxSD2fSVVAwZ5Jo2Lbbg` is **READY**. The canonical alias returned **HTTP 200**; the served homepage source contains the GitHub-backed loader, one search form, the approved artwork, and the shared media-search helper. The old local `/search-index.json.gz` path now returns **404 by design** because the client reads the public repository catalog instead.
- The browser-side raw-GitHub fetch and interactive Images/Videos results still need an actual browser-runtime test; the HTTP/source check alone does not prove those client-side requests complete.
- Current public manifest remains **3,530 pages / 19 shards**; compressed catalog metadata declares **1,081,352 bytes** and SHA-256 `1590f7006637b8c5dd1f2d21ca68a66eadc47e01a8f28b73f4d2e92c87f606bf`.
- The deployed media-search Edge Function remains **v21 ACTIVE**; web-search remains **v20 ACTIVE**; crawler remains **v49 ACTIVE but paused**. No Supabase Edge Function or production schema was changed in this run.
- PostgreSQL is still unavailable; the read-only `eh_quad` inventory fails with SQLSTATE **57P03**. No production write, migration, import, restore, or crawler restart was attempted. Dog Tag Day and Story Wall data remain untouched.
- Next gate: browser-runtime validation of the GitHub-backed catalog loader and Images/Videos mode; independently, wait for PostgreSQL to accept connections before any `eh_quad` inventory or production crawler resume.


## Automation run — 2026-11-17 10:14 UTC

### Code and pipeline changes
- The crawler watchdog now also wakes after the crawler/schema validation workflow completes. Its push-triggered recovery run detected a stale last crawl (7,234 seconds) and dispatched exactly one recovery crawl. Subsequent watchdog runs observed the active/recent crawl and did not duplicate it.
- The public shard builder now targets **340,000 bytes**, leaving 10 KB below the 350,000-byte serving/validation ceiling. A regression test locks in that headroom.
- Production browser verification now checks that GitHub `manifest.json` and `catalog-v1.json.gz` are actually fetched successfully, then exercises Images and Videos category searches end-to-end.
- The search API smoke workflow now runs after successful crawler completions as well as on code pushes/manual dispatch.

### Latest successful crawler — run #104
- **3,608 indexed pages**, **31 unique hosts**
- **31,373 discovered URLs**, **21,169 frontier URLs waiting**
- 50 fetched; 36 parsed/indexed; 14 not indexed
- Robots: 2 disallowed; 5 noindex excluded; 3 nofollow not expanded; 2 robots-policy fetch failures handled fail-closed
- 0 rate-limited responses; 0 transient retries
- 3 sitemap hosts/files checked; 0 new sitemap URLs
- Duration 8.3 seconds
- **20 shards**, page-count sum 3,608, largest shard **339,971 bytes**
- Compressed catalog: **1,122,047 bytes**, SHA-256 verified

Run: https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44062190723

### Production verification
- Browser run #34 passed: HTTP 200; one search form inside approved artwork; eight categories; 390px mobile viewport with no horizontal overflow; zero page errors.
- Browser fetched the manifest and compressed catalog successfully (HTTP 200).
- Images mode: **21** results and 21 rendered image previews. Videos mode: **2** results and four video indicators.
- NASA: 1,825 public-index matches; Rolex independent fallback: 10 Bing RSS results.
- Vercel search backend refreshed after its 5-minute cache and returned **indexedPages=3,608** (HTTP 200).
- Search API smoke run #7 passed: 1,825 NASA results and 10 independent Rolex results.
- Media results carry Commons source links and available license/attribution metadata; reuse still requires checking each item's license.

Browser: https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44062366775  
Search smoke: https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44062886710  
Live diagnostics: https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44062366820

### Isolated importer test
The temporary PostgreSQL 17 test environment created only the `eh_quad` test schema and imported **3,608 records** (0 skipped, 0 invalid lane rows). The 12 schema-isolation unit tests passed; authenticated search, 401 for unauthenticated requests, three-page pagination, and science-lane filtering passed. This was a disposable test database, **not production Supabase**.

Run: https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44062366732

### Remaining blockers
- Supabase project control plane reports `ACTIVE_HEALTHY`, but PostgreSQL is still replaying WAL. In the 15-minute log window ending 10:14 UTC: **173 SQLSTATE 57P03 connection refusals**, 55 redo-progress messages, and zero “ready to accept connections” messages. Latest log: 10:14:19 UTC; redo elapsed 2,030 seconds. Production `eh_quad` read-only inventory remains unavailable. The production crawler Edge Function v49 stays paused; web-search v20 and media-search v21 remain active. No production writes/imports/migrations/restores were made.
- Publisher run #35 validated and uploaded its artifact, but GitHub Pages deployment was skipped: Pages site lookup returned 404 and creation returned `Resource not accessible by integration`. No repository settings were changed.
- The custom-domain fetch probe for `everythinghub.ink` returned `deployment_fetch_failed`; the canonical Vercel homepage passed browser verification.
- Dog Tag Day and Story Wall data were not accessed or modified. No AppDeploy was used.


## Automation run — 2026-11-17 11:16 UTC

### Robots-policy search safety fix merged
- Merged PR [#23](https://github.com/rshure001/EverythingHub-Crawler/pull/23), commit `acb66d0dd97e68155bbc1e0b907cd1d9636dc49e`.
- The `eh_quad.eh_search_pages` SQL RPC and both authenticated Python search/count queries now require `robots_allowed IS TRUE`; denied rows cannot appear in results or inflate counts.
- The importer now soft-disables only exact existing `eh_quad.eh_documents` rows explicitly marked robots-denied by the crawler. It never deletes rows; a later allowed snapshot can re-enable the row.
- The isolated importer workflow now runs on pull requests and includes a robots-denied fixture plus direct RPC assertions.

### Validation
- PR validation run #110: passed, including Python compilation, crawler/schema unit tests, Edge search tests, public index validation, and release-manifest validation.
- Isolated PostgreSQL 17 importer smoke run #87: passed. Imported **3,608** records into the disposable test database; **0** invalid lane rows. The search API returned exactly **3** allowed fixture rows; the robots-denied fixture returned **0** rows through both the Python API and `eh_quad.eh_search_pages`.
- The database test suite ran **22 tests** successfully.
- No production migration or import was run.

### Production recovery snapshot
- Target Supabase project: `everything-hub-control` (`vdceayvfgkwpgwixrhwi`); only this project was queried for Everything Hub. The separate Dog Tag Day Story Wall project was not accessed.
- Read-only `eh_quad` table inventory still fails with PostgreSQL `57P03`: the database is not accepting connections.
- In the 15-minute log window ending **2026-11-17 11:16 UTC**, there were **152** messages reporting that connections were not accepted and **45** WAL redo progress messages. Latest log: **11:15:56 UTC**, redo elapsed **5,741.52 seconds**.
- Public catalog remains **3,608 pages / 20 shards**; shard page totals reconcile, largest shard **339,971 bytes**, compressed catalog **1,122,047 bytes**. Latest successful crawler run remains #104; no new crawl snapshot was published in this interval.
- Crawler Edge Function v49 remains an explicit 503 pause. Web search v20 and media search v21 remain active. No production schema, Dog Tag Day, or Story Wall data was changed. No AppDeploy was used.

### Next gate
Wait for PostgreSQL to accept connections, inspect only `eh_quad` read-only, then apply the additive migration and importer under the documented validation sequence. Do not resume database-backed crawling before that gate passes.


## Automation run — 2026-11-17 13:03 UTC

### Public crawler and index
- Latest successful public crawler run: [#44081625920](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44081625920), completed **12:17:24 UTC**.
- Run totals: **80 fetched**, **59 indexed**, **21 not indexed**; **2 robots-blocked URLs**, **4 noindex exclusions**, **0 nofollow expansions**, **2 robots-policy fetch failures (fail-closed)**, **0 rate-limited responses**, and **0 transient retries queued**.
- Published snapshot: **3,667 pages**, **31 hosts**, **31,423 discovered URLs**, **21,137 queued URLs**, and **20 shards**. The shard page totals reconcile exactly; all 20 SHA-256 values are well-formed; largest shard is **339,928 bytes**; compressed catalog is **1,153,757 bytes**. Manifest timestamp: **2026-11-17 12:17:12 UTC**.
- The crawler continues to enforce the 80-page per-run cap, robots.txt fail-closed behavior, host pacing, Retry-After handling, and bounded sitemap discovery.

### Watchdog freshness improvement
- The schedule watchdog now fetches the committed `public-search/manifest.json`, validates its basic structure and timestamp, and checks catalog freshness as well as the latest crawler workflow attempt.
- The watchdog uses the existing **75-minute** recovery window. A fresh successful run with a stale manifest waits for the recovery window rather than creating an immediate duplicate; a stale run can request one recovery dispatch.
- Watchdog test run [#44088719368](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44088719368) passed **18/18 tests**. Its live decision was `healthy`: last crawler attempt age **2,745 seconds**, public manifest age **2,714 seconds**, **3,667** indexed pages.

### Search, media, and browser verification
- Browser verification [#44084462550](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44084462550) passed against the canonical Vercel alias `everythinghub-homepage-fixed-rshure001-8053.vercel.app`: HTTP **200**, approved artwork loaded, exactly one search form inside the artwork, all **8** category tiles, **24** visible Rolex results, **24** visible NASA results, **21** image results, **2** video results, no mobile horizontal overflow, and **0** page errors.
- Media-search Edge Function v21 returned HTTP **200** for live image/video discovery probes. Commons results include available license and attribution metadata; Bing results remain explicitly unverified for reuse.
- The custom domain `everythinghub.ink` was not browser-verified in that run. The canonical Vercel alias passing does not establish that the custom domain is currently serving the same deployment.

### Production database and publishing gates
- Supabase project `everything-hub-control` reports `ACTIVE_HEALTHY` in the control plane, but PostgreSQL is still replaying WAL. At **2026-11-17 12:57:50 UTC**, the startup log reported redo elapsed **5,460.41 seconds**; at **12:57:51.786 UTC**, PostgreSQL returned SQLSTATE **57P03** (“the database system is not accepting connections”; detail: “Hot standby mode is disabled”). Read-only `eh_quad` table and migration inventory calls still fail.
- The production crawler Edge Function remains intentionally paused at v49 with HTTP 503. Search v20, independent-search v2, and media-search v21 remain active. Do not resume the database-backed crawler or importer until PostgreSQL accepts connections and the `eh_quad` inventory/migration checks pass.
- The latest publisher run [#44081745316](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44081745316) validated and uploaded the release artifact; the GitHub Pages deploy job was skipped because the Pages site is not available to the connected Actions integration. No Pages settings or permissions were changed. The canonical Vercel alias has browser-pass evidence; the custom domain still needs its own browser check.
- No production database write, migration, import, restore, or destructive operation was attempted. The separate Dog Tag Day Story Wall Supabase project was not accessed. No AppDeploy was used.


### Follow-up validation — 2026-11-17 13:05 UTC
- Full validation run [#44089258962](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44089258962) passed: **78 crawler/watchdog tests**, **22 database-schema-isolation and media-compliance tests**, the Edge Search Node test suite, and the public release-manifest validator. The validator confirmed **3,667 pages across 20 shards** and verified the compressed catalog.
- A fresh read-only production check at **13:04:56 UTC** still failed. PostgreSQL logs showed WAL redo elapsed **5,870.10 seconds** at 13:04:39 UTC and SQLSTATE **57P03** through 13:04:56 UTC. The direct read-only eh_quad inventory and migration calls returned **ECONNREFUSED** on PostgreSQL port 5432. Control-plane ACTIVE_HEALTHY is not SQL readiness.
- The exact remaining production gate is PostgreSQL recovery and restored read-only connectivity. Until that succeeds, the production crawler stays paused and no production import or migration is safe.


## Current configuration and verified checkpoint — 2026-11-17 18:00 UTC

- Current crawler source is configured for **16 workers**, a **1,600-page hard cap**, a **six-minute crawl budget**, a **50,000-URL frontier ceiling**, and at least **one second between requests to the same hostname**. Each selected batch contains at most one URL per hostname. The effective delay is increased for matching robots.txt `Crawl-delay` and `Request-rate` directives; saved host cooldowns persist between runs. Same-host redirect targets are rechecked against robots.txt, cross-host redirects are blocked, and a robots-policy read failure denies that host. The crawler honors `Retry-After` and requeues transient failures.
- The workflow schedule is every five minutes, with a concurrency group that prevents overlapping crawler jobs. The latest completed crawl run at this checkpoint was [#112](https://github.com/rshure001/EverythingHub-Crawler/actions/runs/44139336563). It fetched **409**, indexed **322**, and did not index **87**. Policy accounting: **16 robots-blocked**, **25 noindex**, **17 nofollow**, **2 robots-policy fetch failures (fail-closed)**, **1 rate-limited response**, and **1 transient retry queued**. The retry was retained with cooldown rather than immediately repeated.
- That committed public index contains **4,560 pages across 25 shards**, **31 indexed hosts**, **32,264 discovered URLs**, and **20,751 queued URLs**. The compressed catalog is **1,595,814 bytes**. The validator passed all shard byte-size, SHA-256, URL uniqueness, canonical-index coverage, and compressed-catalog checks. The next crawl run was still active/queued at the time of this checkpoint, so the 16-worker setting has not yet been represented by a completed crawl result.
- The dedicated Supabase project `everything-hub-control` reports `ACTIVE_HEALTHY` in the control plane, but read-only `eh_quad` table inventory and migration inspection still fail with **SQLSTATE 57P03**: PostgreSQL is not accepting connections (`Hot standby mode is disabled`). The latest inspected log at **18:00:40 UTC** still contained connection rejections; WAL redo was reported at **4,990 seconds**. The production crawler Edge Function remains intentionally paused at **v49**. Search v20 is active and explicitly scopes its database client to `eh_quad`, with a SHA-256-validated public-catalog fallback; independent search v2 and media search v21 are active. No production migration, import, restore, or database write was attempted.
- This checkpoint did not access the separate Dog Tag Day Story Wall Supabase project or its data. The temporary importer tests remain isolated from production. No AppDeploy was used.
