# Everything Hub — Scalable Crawl Architecture

## Goal
Build toward a **23,000,000-page searchable index**. This is a total-index target, not a promise of 23 million fetches per hour. Throughput targets must be set from measured benchmarks and available compute.

## Current verified baseline
- Latest committed GitHub Actions crawl: run 77, completed successfully on 2026-11-16 at 03:06 UTC.
- That run fetched 84 pages and indexed 69; the committed repository index contains 2,533 page records across 30 hosts.
- The frontier held 29,812 discovered URLs, with 24,516 waiting. The run recorded 9 robots-blocked checks, checked 3 sitemap hosts, requested 4 sitemap files, added 0 URLs from sitemaps, and completed in 60.8 seconds.
- The committed public search index contains 13 validated shards. A subsequent test run verified the strict 80-page cap and built a 2,592-page/14-shard working index, but its data push was rejected after another commit advanced main; that larger snapshot is not committed.
- The crawler now has bounded robots-declared sitemap discovery, same-host sitemap parsing, a 1 MB sitemap-response cap, persisted sitemap recheck times, Retry-After handling for 429/503, transient URL requeueing, and a strict 80-page fetch cap. The push-triggered validation workflow passed 23 crawler tests, 6 schema-isolation tests, public-index validation, and static release-manifest validation. Crawls now run on schedule/manual dispatch only; code pushes use validation-only CI to avoid concurrent data commits.
- Live Supabase PostgreSQL remains unavailable: at 03:05 UTC its logs reported WAL redo in progress for about 49.7 minutes and FATAL 57P03 (“database system is not accepting connections”). The dedicated `eh_quad` schema migration/import has not been applied to production. The Supabase crawler and sitemap Edge Functions are deployed but intentionally return HTTP 503 while database write access is unavailable.
- Independent Bing RSS search returned 10 Rolex results; Wikimedia Commons media discovery returned 21 images for “octopus” and 2 videos for “Apollo 11 NASA”. The database-backed search probe timed out, so crawler records are not yet connected to production search.
- The canonical Vercel homepage is READY; browser checks verified the approved octopus artwork (709 px natural width), all 8 categories, 24 Rolex results, and a 390 px mobile layout without horizontal overflow. The custom domain `everythinghub.ink` timed out from external probes despite being marked verified in the Vercel project. Account-level DNS inspection returned 403, so no DNS changes were attempted. The legacy GitHub Pages publisher is blocked by “Resource not accessible by integration”; Vercel remains the working deployment route.
- All work in this run was limited to Everything Hub code and the dedicated `eh_quad` design; no Dog Tag Day or Story Wall data was modified.


## Target architecture

1. **URL frontier (durable queue)**
   - Persist discovered URLs, canonical URL, source, depth, priority, next-fetch time, retry count, and status.
   - Enforce unique canonical URLs and deduplicate before enqueueing.
   - Partition work by hostname so one site's crawl rate is controlled independently.
   - Keep queue state outside Git commits; never use a single in-memory queue for large-scale work.

2. **Polite fetch workers**
   - Run multiple independently scalable worker processes.
   - Enforce robots.txt, one request per hostname per batch across schemes, persisted host pacing, crawl-delay/request-rate directives, robots-checked same-host redirects, timeouts, response-size limits, content-type checks, and retry/backoff.
   - Identify the crawler clearly and provide a contact/policy URL.
   - Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions.

3. **Discovery**
   - Ingest sitemap.xml and sitemap indexes, robots.txt sitemap declarations, and permitted same-site links.
   - Normalize URLs, remove fragments/tracking parameters where safe, detect redirect/canonical duplicates, and cap crawl traps such as calendars and infinite query variants.
   - Use depth as one signal, not the only priority: sitemap freshness, page importance, host fairness, and discovery source also matter.

4. **Content pipeline**
   - Extract title, canonical URL, description, language, timestamp, main text, and useful metadata.
   - Strip scripts, styles, navigation, and repeated boilerplate.
   - Hash normalized content to avoid storing unchanged copies; record fetch status and provenance.
   - Keep raw responses or compressed archival copies only where permitted and operationally justified.

5. **Storage and search**
   - Store crawl state and metadata in a durable database.
   - Store larger page bodies in object storage or another suitable blob store.
   - Build a dedicated inverted search index (for example, OpenSearch) with incremental upserts and deletes.
   - Keep crawler output separate from the live search API until an authenticated, tested ingestion contract exists.

6. **Operations and measurement**
   - Track fetched pages/hour, successfully indexed pages/hour, unique URLs discovered, queue size, bytes downloaded, errors, duplicate rate, per-host request rate, and index lag.
   - Run load tests at increasing worker counts; raise concurrency only when host policies and error rates remain healthy.
   - Alert on stalled queues, repeated failures, storage growth, and index ingestion lag.

## Phased implementation

### Phase 1 — Make the prototype trustworthy
- Depth 1–3 discovery is now verified in the repository index; continue checking depth distribution and host coverage.
- Persist frontier state between runs rather than rebuilding it each time.
- Improve extraction and URL canonicalization.
- Record run metrics and prove a sustained baseline over multiple runs.
- Keep GitHub Actions for tests and small demonstration crawls only.

### Phase 2 — Separate the services
- Add durable frontier and metadata database.
- Add a worker service that can run more than one replica.
- Add object storage for page bodies and a separate search-index service.
- Create a documented, authenticated ingestion API for Everything Hub.

### Phase 3 — Scale toward 23 million indexed pages
- Expand domain discovery in controlled batches.
- Benchmark storage, indexing, network bandwidth, and worker throughput.
- Scale workers and index shards based on measured bottlenecks.
- Maintain host-level politeness, deduplication, and removal/recrawl handling.
- Report actual indexed-page counts; do not count attempted fetches as indexed pages.

## Capacity math (planning only)
At a sustained **1,000 successfully indexed pages/second**, 23 million pages would take about 6.4 hours in idealized arithmetic. At **100 pages/second**, about 2.7 days. Real elapsed time will be longer due to discovery, duplicate filtering, failures, host rate limits, extraction, indexing, and recrawls. These are sizing examples, not current performance claims.

## Cost and deployment guardrails
- Prefer open-source components and free/local development for the prototype.
- Do not create paid cloud resources, upgrade plans, purchase credits, or deploy billable infrastructure without the owner's explicit approval.
- Free tiers are not assumed to support 23 million pages. Estimate storage, compute, bandwidth, and index costs before production scale-up.

## Acceptance gates
- Depth 1+ is demonstrated with real page records.
- A repeat run resumes from durable frontier state and does not re-fetch the entire seed set unnecessarily.
- Metrics distinguish attempted, fetched, parsed, deduplicated, and indexed pages.
- Search integration is validated end-to-end against the production search API before claiming crawler pages appear in live results.
- Scale claims are based on measured sustained throughput, not multiplying one run by a schedule interval.
