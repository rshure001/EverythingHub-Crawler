# Search-path diagnosis: `eh_search_pages` (2026-11-07)

## New evidence recovered from PostgreSQL logs

The Supabase SQL-management connection timed out during a read-only attempt to inspect the live function and indexes. The PostgreSQL log stream nevertheless contains the exact DDL used to install the current search function and one logged execution plan.

The current logged function signature is:

```sql
public.eh_search_pages(
  query_text text,
  lane_filter text default null,
  result_limit integer default 10
)
```

Its current implementation:
- Parses up to 160 characters with `websearch_to_tsquery('english', ...)`.
- Filters `public.eh_pages` with `p.search_vector @@ parsed.tsq`.
- Optionally filters `p.tentacle`.
- Computes relevance using `ts_rank_cd(search_vector, tsq)`, another title-vector rank, and substring checks over lowercased title/description.
- Returns `count(*) over()` for every matching row.
- Sorts the full matching set by relevance and fetched_at, then applies a limit capped at 20.

## Performance implications (hypotheses, not yet proven)

1. `count(*) over()` requires accounting for the full matching set even though the API returns at most 20 rows. This can prevent early termination after the top results.
2. Relevance expressions and ordering are evaluated across matching candidates. Broad/common terms can create a large candidate set and expensive sort/rank work.
3. The five-query diagnostic statement logged at 2026-11-07 02:23:27 UTC took 19,872.901 ms. Its outer plan shows five lateral function scans, one per query (Rolex, climate change, Las Vegas, heart disease, World Cup). This is a multi-query aggregate, not an isolated latency measurement for any one term.
4. The plan shown in the log is only the outer wrapper plan; it does not expose the inner SQL plan of the SQL-language function.

## Confirmed constraints

- PostgreSQL logs contain repeated statement-timeout cancellations inside `eh_search_pages`.
- A fresh read-only `execute_sql` request for function/index metadata timed out at the management connection layer.
- The performance-advisor request also timed out before returning results.
- No production database, function, schedule, queue, or concurrency settings were changed in this investigation.

## Next safe diagnostic sequence

1. Re-establish a working read-only SQL connection; retrieve `pg_get_functiondef`, `pg_indexes` for `eh_pages`, column metadata, and relevant table statistics using small targeted queries.
2. Verify the actual GIN index on `search_vector` and any useful supporting indexes before proposing index changes.
3. Capture isolated `EXPLAIN (FORMAT JSON)` plans for representative narrow and broad terms, preferably in a non-production environment. Do not enable production explain settings just to obtain a plan.
4. Compare an exact-count implementation with a fast top-N path that avoids counting the entire candidate set on every request; keep exact totals optional/separate if product requirements allow.
5. Benchmark common terms, rare terms, empty/stop-word queries, lane filters, and concurrent requests.
6. Only then prepare a reviewed migration and tests. Production application/deployment remains gated on explicit approval.

## Priority

Search responsiveness is a prerequisite for meaningful crawl scale. Do not increase production crawl concurrency while search is timing out and the crawler queue terminal-state update bug remains unresolved.
