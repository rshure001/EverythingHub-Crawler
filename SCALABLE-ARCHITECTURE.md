# Everything Hub — Scalable Crawl Architecture

## Goal
Build toward a **23,000,000-page searchable index**. This is a total-index target, not a promise of 23 million fetches per hour. Throughput targets must be set from measured benchmarks and available compute.

## Current implementation
- 49 curated public starting sites.
- 10 enabled sitemap-discovery connectors for Wikipedia, Library of Congress, Smithsonian, NASA, NOAA, USGS, NIST, NIH, CDC, and the U.S. Census Bureau.
- Each connector reads the publisher's `robots.txt` sitemap declarations, accepts same-host sitemap URLs only, and is capped at 4 sitemap documents and 250 discovered URLs per connector per run (2,500 new URLs maximum).
- The page crawler has 8 concurrent workers, an 80-fetch run cap, a persistent JSON frontier capped at 50,000 discovered URLs, and maximum link depth 3.
- The crawler writes its own repository JSON index. Production Everything Hub search ingestion is **not connected**.
- GitHub Actions is a prototype runner, not a long-term distributed crawl fleet.

## Target architecture

1. **URL frontier (durable queue)**
   - Persist discovered URLs, canonical URL, source, depth, priority, next-fetch time, retry count, and status.
   - Enforce unique canonical URLs and deduplicate before enqueueing.
   - Partition work by hostname so one site's crawl rate is controlled independently.
   - Move queue state to a durable database before scaling beyond the prototype.

2. **Polite fetch workers**
   - Run multiple independently scalable worker processes.
   - Enforce robots.txt, per-host concurrency and delay, timeouts, response-size limits, content-type checks, and retry/backoff.
   - Identify the crawler clearly and provide a contact/policy URL.
   - Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions.

3. **Discovery**
   - Ingest publisher-declared sitemap.xml and sitemap indexes, robots.txt sitemap declarations, and permitted same-site links.
   - Normalize URLs, remove fragments/tracking parameters where safe, detect redirect/canonical duplicates, and cap crawl traps such as calendars and infinite query variants.
   - Use freshness, page importance, host fairness, and discovery source as crawl-priority signals.

4. **Content pipeline**
   - Extract title, canonical URL, description, language, timestamp, main text, and useful metadata.
   - Strip scripts, styles, navigation, and repeated boilerplate.
   - Hash normalized content to avoid storing unchanged copies; record fetch status and provenance.
   - Keep raw responses or compressed archival copies only where permitted and operationally justified.

5. **Storage and search**
   - Store crawl state and metadata in a dedicated durable database.
   - Store larger page bodies in object storage or another suitable blob store.
   - Build a dedicated inverted search index with incremental upserts and deletes.
   - Keep crawler output separate from the live search API until an authenticated, tested ingestion contract exists.

6. **Operations and measurement**
   - Track fetched pages/hour, successfully indexed pages/hour, unique URLs discovered, queue size, bytes downloaded, errors, duplicate rate, per-host request rate, and index lag.
   - Run load tests at increasing worker counts; raise concurrency only when host policies and error rates remain healthy.
   - Alert on stalled queues, repeated failures, storage growth, and index ingestion lag.

## Phased implementation

### Phase 1 — Trustworthy discovery and crawl
- Enable the ten sitemap connectors and test XML parsing and same-host restrictions.
- Demonstrate depth-1+ records with real pages.
- Persist frontier state between runs and avoid re-fetching the seed set unnecessarily.
- Improve extraction and URL canonicalization.
- Record run metrics and prove a sustained baseline over multiple runs.

### Phase 2 — Connect the owned production index
- Verify the production search API contract and authentication.
- Apply the EverythingHub-only database schema to a new, dedicated database.
- Import a snapshot of repository index data and validate counts, uniqueness, content, and search results.
- Connect one test worker to the durable queue; run a controlled shadow period before cutover.
- Do not touch Dog Tag Day databases or deployments.

### Phase 3 — Scale toward 23 million indexed pages
- Expand public and licensed source coverage in controlled batches.
- Benchmark storage, indexing, network bandwidth, and worker throughput.
- Scale workers and index shards based on measured bottlenecks.
- Maintain host-level politeness, deduplication, and removal/recrawl handling.
- Report actual indexed-page counts; do not count attempted fetches as indexed pages.

## Capacity math (planning only)
At a sustained **1,000 successfully indexed pages/second**, 23 million pages would take about 6.4 hours in idealized arithmetic. At **100 pages/second**, about 2.7 days. Real elapsed time will be longer due to discovery, duplicate filtering, failures, host rate limits, extraction, indexing, and recrawls. These are sizing examples, not current performance claims.

## Cost and deployment guardrails
- Prefer open-source components and free/local development for the prototype.
- Do not create paid cloud resources, upgrade plans, purchase credits, or deploy billable infrastructure without the owner's explicit approval of the quoted cost.
- Free tiers are not assumed to support 23 million pages. Estimate storage, compute, bandwidth, and index costs before production scale-up.
