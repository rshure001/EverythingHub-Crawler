# Fresh production verification — queue schema and search failures

**Checked:** 2026-11-07  
**Scope:** read-only production inspection; development-branch documentation only. No production writes, deployments, schedule changes, or concurrency changes.

## Queue schema confirmation

The live `public.eh_crawl_queue` column metadata confirms:

- `next_attempt_at` is `timestamp with time zone`.
- `is_nullable = NO`.
- Default is `now()`.

This independently confirms that the deployed crawler must not write NULL to this column. For a terminal failure, the minimal candidate is to omit `next_attempt_at` from the update patch (preserving its existing non-null value), while retryable failures set a future timestamp. This remains a candidate repair, not a production change.

The live `claim_eh_crawl_batch` function-definition query failed at the database connection layer with a connection timeout. Its locking, lease recovery, ordering, and retry filtering therefore remain unverified.

## Fresh search-path failure sample

In the latest two-hour log window queried:

- PostgreSQL: 34 events with SQLSTATE `57014` (`canceling statement due to statement timeout`).
- Web-search Edge Function: 21 HTTP 500 responses, average reported execution time about 103,158 ms, maximum 149,464 ms.
- Web-search Edge Function: 5 HTTP 546 resource-limit responses, average reported execution time about 140,055 ms, maximum 150,196 ms.
- No crawler endpoint rows were returned by the same two-hour endpoint/status aggregation; this is not proof that the crawler is healthy.

These are request/error counts and durations, not page throughput.

## Engineering decision

1. Do not raise crawler load while search requests are timing out.
2. Keep the queue patch isolated until the claim RPC and queue policy are verified.
3. Continue read-only search diagnosis and bounded plan inspection.
4. Add regression tests for retryable and terminal queue failures, including a simulated NOT NULL constraint.
5. Only after tests pass, seek explicit approval before any production canary.

## Production boundary

No production database rows, schema, functions, Edge Function deployments, schedules, or concurrency settings were changed.
