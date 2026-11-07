# Search-path incident — production query bottleneck

**Checked:** 2026-11-07  
**Function:** `everything-hub-web-search` v4  
**Scope:** read-only production inspection; no production change.

## Confirmed live failure

The deployed web-search Edge Function v4 is a thin wrapper around one database call:

```ts
supabase.rpc("eh_search_pages", {
  search_query: q,
  result_limit: limit
})
```

The function itself does not crawl or build an index. Its response depends on the database function `public.eh_search_pages`.

Production logs in the last 24 hours show:
- 59 HTTP 500 responses from `everything-hub-web-search`.
- 26 HTTP 546 worker-resource-limit responses from `everything-hub-web-search`.
- Latest observed web-search 546: 2026-11-07 20:25:09 UTC.
- Requests ran for up to approximately 150 seconds before the platform terminated them.

PostgreSQL logs independently show repeated SQLSTATE 57014, `canceling statement due to statement timeout`, inside `eh_search_pages` at approximately 19:42–19:57 UTC. Several simultaneous requests were canceled at the same timestamp. This directly links the user-facing search failures to the database search function timing out; it is not merely a frontend rendering issue.

## Other production paths also affected

The same 24-hour function-edge log aggregation showed:
- Crawler: 292 HTTP 200, 142 HTTP 500, 10 HTTP 546.
- Sitemap discovery: 178 HTTP 200, 87 HTTP 546, 5 HTTP 500.
- Web search: 3 HTTP 200, 59 HTTP 500, 26 HTTP 546.
- Media search: 20 HTTP 200, 10 HTTP 503, 3 HTTP 546, 1 HTTP 504.

These are request counts by endpoint/status, not page counts or crawl throughput.

## Immediate engineering order

1. Recover database responsiveness before adding crawler load.
2. Inspect `eh_search_pages` definition, execution plan, indexes, table statistics, and representative query latency in a safe/read-only way.
3. Determine whether the search function performs broad `ILIKE`/full-text scans, sorts large candidate sets, casts/normalizes fields per row, or fails to use the intended search indexes. Do not assume the cause until the definition and plan are inspected.
4. Design an indexed search path that returns a bounded candidate set, with safe limits and statement-time budgets. Preserve ranking and relevance; validate with representative queries (Rolex, NASA, common terms, rare terms).
5. Verify results and latency under concurrent test load before production canary.
6. Only after search is stable, repair queue writes and scale crawl workers in measured stages.

## Production rollout boundary

Do not raise crawler concurrency while search RPCs are timing out. More ingestion can worsen database contention and make the product less usable. No production function, schema, index, schedule, queue row, or deployment was changed by this report.
