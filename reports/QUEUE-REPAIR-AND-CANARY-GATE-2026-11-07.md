# Queue repair and mass-production engineering gate

**Date:** 2026-11-07  
**Repository:** rshure001/EverythingHub-Crawler  
**Target:** 1,000 verified unique searchable pages/minute (60,000/hour)  
**Scope:** isolated development branch only; no production writes or deployment.

## Confirmed defect in deployed crawler v30

The deployed `retryOrFail` function builds this update:

```ts
{
  status,
  attempts,
  next_attempt_at: status === "pending" ? retryTime : null,
  locked_at: null,
  last_error,
  updated_at
}
```

For permanent failures and items reaching the attempt limit, `status` becomes `failed` and `next_attempt_at` becomes `null`. PostgreSQL has logged SQLSTATE 23502 for a NULL `eh_crawl_queue.next_attempt_at`, so this update violates the live NOT NULL constraint.

## Minimal repair candidate

Do not send a NULL for a terminal queue item. Omit the column from the update so its existing value remains unchanged. Also check and surface the database response:

```ts
async function retryOrFail(item: any, message: string) {
  const attempts = (item.attempts || 0) + 1;
  const permanent =
    /HTTP (?:400|401|403|404|406|410|415|422)|Non-HTML response|Insufficient readable page text|URL rejected|robots\\.txt/i.test(message);
  const status = permanent || attempts >= 3 ? "failed" : "pending";

  const patch: Record<string, unknown> = {
    status,
    attempts,
    locked_at: null,
    last_error: message.slice(0, 300),
    updated_at: now(),
  };

  // Only retryable rows receive a new retry timestamp.
  // Terminal rows retain their existing NOT NULL value.
  if (status === "pending") {
    patch.next_attempt_at =
      new Date(Date.now() + Math.min(attempts * 15, 60) * 60000).toISOString();
  }

  const { error } = await db
    .from("eh_crawl_queue")
    .update(patch)
    .eq("id", item.id);

  if (error) {
    console.error("Queue retry/fail update failed", item.id, error.message);
    throw error;
  }
}
```

This is a candidate patch, not a deployed fix. Before merging it, verify the live column type/default/constraints and the exact claim RPC contract. Confirm that leaving `next_attempt_at` unchanged is valid for terminal rows under the actual schema and queue policy.

## Required verification before canary

1. Read-only inspect `eh_crawl_queue` columns, NOT NULL/default/check constraints, indexes, and foreign keys.
2. Read the exact `claim_eh_crawl_batch` function definition and verify lock/lease recovery, ordering, retry-time filtering, and concurrency behavior.
3. Identify why queue upserts from seed loading and discovered links are returning PostgREST HTTP 500; do not assume every 500 has the same cause.
4. Add tests for: retryable failure, permanent failure, max-attempt failure, DB update failure, duplicate URL, lease cleanup, and claim/reclaim after worker interruption.
5. Exercise the candidate against a disposable test database with the production schema and RPC copied into it.
6. Run a small canary only after explicit production approval. Confirm pages are present in `eh_pages`, returned by the live search endpoint, and counted as newly searchable only once.
7. Ramp in small stages with automatic backoff. Stop on rising 500/546 rates, DB latency, queue age, stale leases, or host concentration.

## Resource-pressure concerns in deployed v30

- The crawler reads the full response body with `response.text()` and only then slices to 500,000 characters. This is not a hard memory/transfer cap. A streaming byte limit is needed.
- It attempts to load and upsert as many as 250 enabled seed rows on every invocation. Seed synchronization should be separated from the hot page-worker path.
- It validates up to six discovered links per page, but DNS resolution and host fairness need explicit bounded policies across the whole worker fleet, not just one invocation.
- It claims four items and runs at most two workers per scheduled invocation. The response field `target_hourly: 1000` is descriptive only and does not change throughput.
- Every database write must be checked. A fetched page should not be reported as successfully completed/searchable if the page, media, or queue-state write failed.

## Metrics that must remain separate

Report these independently: queue claims; fetch attempts; successful HTTP/HTML fetches; validated URLs discovered; unique page inserts; existing-page refreshes; rows searchable through the live search endpoint; rejected/blocked URLs; retryable failures; terminal failures; and DB write failures.

## Rollout boundary

No production function, schedule, concurrency, database schema, queue record, or deployment is changed by this document. The target remains 1,000 verified unique searchable pages/minute, but it must be demonstrated by sustained measurements—not inferred from attempted fetches or queue claims.
