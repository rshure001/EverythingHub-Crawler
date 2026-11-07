# One-million readiness checkpoint

**Checkpoint:** 2026-11-07 20:46:51 UTC  
**Branch:** `scale/one-million-readiness`

## Verified development-crawler result

- Unique page records in repository index: **465**
- Fetch attempts in latest run: **80**
- New page records in latest run: **51**
- Latest-run depth distribution: depth 1 = 20, depth 2 = 30, depth 3 = 3
- Total depth distribution: depth 0 = 27, depth 1 = 180, depth 2 = 198, depth 3 = 60
- Remaining frontier entries: **8,766**
- Discovered URLs tracked: **9,424**
- GitHub Actions validation, unit tests, crawl, and index/frontier commit: **success**

## Interpretation

The crawler is demonstrating multi-level link discovery and persistent frontier operation. The 80 fetch attempts are not 80 guaranteed new pages; 51 records were added in this run. The 465 records are the development repository's JSON index only.

## Production status

The production Supabase database query timed out during this checkpoint. Production page count and queue state are therefore **not freshly verified**. The development repository crawler is not yet connected to the live search ingestion path. No production database changes, deployment, concurrency increase, or paid infrastructure changes were made.

## Next acceptance gates

1. Recheck production database connectivity and inspect crawler queue constraints.
2. Repair the deployed crawler retry/status update so failed rows always satisfy the queue's `next_attempt_at` constraint.
3. Define and test an authenticated ingestion contract that deduplicates canonical URLs and safely upserts page metadata.
4. Verify end-to-end that a development-crawled test page appears in live search before enabling bulk ingestion.
5. Replace the small scheduled GitHub Actions crawl with a durable, host-fair worker/frontier architecture before raising throughput materially.
6. Measure sustained successful unique-page indexing rate and resource usage; scale gradually toward one million.

**Counting rule:** only distinct, successfully stored, searchable page records count toward the one-million target. Fetch attempts, discovered URLs, queued URLs, and retries do not count.
