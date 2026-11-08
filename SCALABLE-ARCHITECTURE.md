# Everything Hub — Scalable Crawl Architecture

## Goal
Build toward a **23,000,000-page searchable index**. This is a total-index target, not a promise of 23 million fetches per hour. Throughput targets must be set from measured benchmarks and available compute.

## Current verified baseline
- The first GitHub Actions crawl completed successfully.
- It attempted 80 fetches and persisted 27 page records.
- The current crawler has 8 workers, an 80-fetch run cap, a 500-URL in-memory queue, and maximum link depth 3.
- The first index contained 27 records, all at depth 0. Deeper discovery is not yet proven.
- The crawler writes its own repository JSON index. Production Everything Hub search ingestion is not connected.
- GitHub Actions is a prototype runner, not a suitable long-term distributed crawl fleet.

## Target architecture

1. **URL frontier (durable queue)**
   - Persist discovered URLs, canonical URL, source, depth, priority, next-fetch time, retry count, and status.
   - Enforce unique canonical URLs and deduplicate before enqueueing.
   - Partition work by hostname so one site's crawl rate is controlled independently.
   - Keep queue state outside Git commits; never use a single in-memory queue for large-scale work.

2. **Polite fetch workers**
   - Run multiple independently scalable worker processes.
   - Enforce robots.txt, per-host concurrency and delay, timeouts, response-size limits, content-type checks, and retry/backoff.
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
- Fix and test depth-1 link discovery; verify real depth distribution in output.
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
