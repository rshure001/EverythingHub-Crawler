# Deployed crawler v30 source audit and isolated v31 patch plan

**Checked:** 2026-11-07  
**Live function:** `everything-hub-crawler`, active version 30  
**Scope:** source retrieval and static audit only; no production write or deployment.

## Confirmed source-level defects

The currently deployed v30 source was retrieved directly from the active Supabase Edge Function.

### 1. Terminal queue failure violates live schema

`retryOrFail()` always includes `next_attempt_at`, setting it to `null` for terminal failures. Live schema inspection confirms this column is NOT NULL with default `now()`. This reproduces the SQLSTATE 23502 defect already documented.

### 2. Queue state updates are not checked

Several queue updates ignore their returned `error`:
- Marking an item `blocked` after initial URL validation.
- Marking an item `blocked` after robots denial.
- Marking an item `blocked` after a rejected redirect.
- Marking a successfully fetched page `done`.
- The `retryOrFail()` update itself.

A fetch can therefore be counted as successful even if the queue transition failed. The exception path can also silently fail to persist retry/failure state.

### 3. Lease cleanup is logged but not reflected in run health

The `finally` block logs a failed host-lease deletion but continues. This can leave stale leases without a run-level failure signal. The lease policy and reclaim behavior still need the exact claim-RPC definition.

### 4. Crawl-run insert errors are ignored

The final `eh_crawl_runs` insert result is not checked. A request may return a normal JSON response even when the run record failed to persist.

### 5. Body-size cap is applied after full download

The worker calls `response.text()` and only then slices to 500,000 characters. This does not cap transferred bytes or peak memory. A bounded streaming reader is needed, with cancellation once the byte limit is reached.

### 6. Seed synchronization is still on the hot path

Each crawler invocation loads up to 250 enabled seeds and upserts them before claiming work. This creates avoidable database traffic on every worker invocation. Seed sync should be moved to its own slower schedule after confirming the existing seed refresh policy.

## Fresh production observations

The most recent two-hour log aggregation available at this checkpoint showed:
- 34 PostgreSQL statement-timeout events (SQLSTATE 57014).
- 21 web-search Edge Function HTTP 500 responses (average reported duration ~103 seconds; max ~149 seconds).
- 5 web-search Edge Function HTTP 546 responses (average ~140 seconds; max ~150 seconds).

The live search function still depends on `eh_search_pages(search_query, result_limit)`. No evidence yet supports raising crawler concurrency.

## v31 isolated repair acceptance criteria

1. Build a patch against the exact retrieved v30 source, but keep it outside production.
2. Make every queue transition check its PostgREST error; throw or record a structured failure rather than reporting success.
3. For terminal states, omit `next_attempt_at`; for retryable states, set a non-null future timestamp.
4. Ensure a failed page/media/queue write cannot increment the successful/new-searchable page counters.
5. Make lease cleanup and crawl-run persistence failures visible in structured run diagnostics.
6. Implement a real streaming byte cap before decoding the HTML body.
7. Move seed loading/upsert off the frequent worker path, with an explicit independent schedule and safe idempotency.
8. Verify the exact live claim-RPC definition and queue constraints before considering a canary.
9. Test retryable failures, permanent failures, attempt-limit failures, DB update errors, duplicate URLs, lease cleanup failures, oversized bodies, and run-record write failures.
10. Only after a green test suite and a reviewed patch, request explicit approval for a production canary.

## Non-goals and rollout boundary

- No production schema, queue row, function, deployment, schedule, or concurrency setting was changed.
- Do not treat `target_hourly: 1000` or a projected rate from one run as measured throughput.
- Report separately: fetch attempts, successful fetches, unique inserts, refreshed existing rows, rows actually returned by live search, and all write failures.
