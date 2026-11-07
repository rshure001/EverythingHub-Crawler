# Live search-path inspection update (2026-11-07)

## Read-only production facts recovered

The targeted metadata queries succeeded after a prior broad SQL request timed out.

### Live `eh_pages` indexes

- `eh_pages_pkey`: btree(id)
- `eh_pages_url_key`: unique btree(url)
- `eh_pages_search_vector_gin`: GIN(search_vector)
- `eh_pages_host_idx`: btree(host)
- `eh_pages_fetched_at_idx`: btree(fetched_at DESC)
- `eh_pages_tentacle_idx`: btree(tentacle)

The full-text GIN index is present, so the search timeout is not explained by a missing basic full-text index.

### Table size and statistics

- Estimated rows: 33,955
- Live tuples estimate: 35,986
- Dead tuples estimate: 662
- Total table size including indexes/TOAST: 416 MB
- Last auto-analyze: 2026-11-07 03:09:41 UTC

### Important: there are two overloaded search functions

1. `eh_search_pages(text, integer)`
   - Parameters: `search_query`, `result_limit`
   - Returns title, URL, description, content_text, host, fetched_at, relevance
   - This is the overload the deployed `everything-hub-web-search` v4 Edge Function calls, because it sends named RPC arguments `search_query` and `result_limit`.
   - It is `SECURITY DEFINER`, which deserves a separate privilege review; no security changes were made.

2. `eh_search_pages(text, text, integer)`
   - Parameters: `query_text`, `lane_filter`, `result_limit`
   - Returns URL, title, description, tentacle, fetched_at, relevance, total_count
   - This newer overload computes extra title/description ranking and `count(*) over()`.

This overload split is a source of operational ambiguity. The recent 19.87-second five-query diagnostic used the second overload, whereas the live web-search Edge Function v4 currently calls the first overload. Treat their performance separately.

### EXPLAIN plan for the live Edge Function's Rolex query shape

A read-only `EXPLAIN (COSTS, VERBOSE)` of the first overload's underlying query produced:

- Bitmap Index Scan on `eh_pages_search_vector_gin`
- Bitmap Heap Scan on `eh_pages`
- Estimated 184 matching rows for `Rolex`
- Sort by calculated relevance descending, then `fetched_at` descending
- Limit 10

This proves the GIN index is used for that representative term. It is a planner estimate only, not actual elapsed time or proof that every query shape is fast.

## Revised diagnosis

- The deployed web-search endpoint uses the older two-argument overload, not the newer count-window overload.
- The five-query 19.87-second diagnostic exercised the newer overload and must not be reported as the live endpoint's single-query latency.
- The newer overload's full-match count and added ranking expressions are still optimization candidates, but the immediate live endpoint issue needs isolated measurements of the older overload across common/broad terms and concurrent calls.
- The live table is approximately 36k rows, and a GIN full-text index exists. Need inspect actual plans/latencies for high-frequency queries and the database timeout context before deciding whether ranking/sorting, table fetch/TOAST, or concurrency is the dominant cause.

## Next safe steps

1. Run non-mutating `EXPLAIN` plans for representative common and rare queries against the exact old overload; use `EXPLAIN ANALYZE` only if the query is bounded and safe.
2. Check per-index usage and table statistics, including whether the GIN index is heavily rechecked and whether the table has healthy vacuum/analyze state.
3. Review the two overloads together: decide whether to consolidate on one named signature and one response contract in a reviewed development migration. Do not drop or replace either production function until callers and grants are inventoried.
4. Review `SECURITY DEFINER` grants and function ownership for the older overload. Do not alter privileges until actual required callers and RLS model are known.
5. Keep production load unchanged until search and crawler queue failures are repaired and validated.

No production changes, deployments, schema modifications, or load increases were made.
