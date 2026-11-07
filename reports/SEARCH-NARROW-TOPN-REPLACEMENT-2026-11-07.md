# Search replacement candidate — narrow top-N retrieval

**Date:** 2026-11-07  
**Status:** isolated proposal; not applied to Supabase  
**Live caller:** `everything-hub-web-search` v4  
**Live RPC:** `public.eh_search_pages(search_query text, result_limit integer)`

## Newly recovered production function definition

A targeted read-only database query successfully retrieved the exact live function definition after earlier management-connection timeouts.

The deployed RPC:
- Builds an English `websearch_to_tsquery` from up to 160 characters.
- Filters `eh_pages` through `search_vector @@ tsq`.
- Calculates full-text relevance plus exact-title, title-substring, and host-substring bonuses.
- Orders by relevance and fetch timestamp.
- Returns up to 20 rows, including the potentially large `content_text` field.

The full-text GIN index was previously verified to exist and was used by the planner for the representative Rolex query shape. That means a missing basic search index is not the established root cause.

## Specific optimization hypothesis

The existing SQL selects `content_text` into the same candidate relation that is ranked and sorted. Since page text can be large, carrying it through a broad relevance sort can increase tuple width, memory pressure, and possible temporary I/O.

The isolated replacement changes the execution shape:
1. Build the tsquery once.
2. Find matches using the existing indexed `search_vector @@ tsq` predicate.
3. Rank and sort only the narrow `id`, relevance, and timestamp fields.
4. Apply the existing maximum 20-row limit.
5. Fetch `content_text` and the remaining display columns only for those final rows.

It also explicitly rejects an empty/stop-word tsquery before scanning. The RPC argument names, result columns, ranking weights, ordering, limit, SECURITY DEFINER behavior, and existing function ACL are intended to remain compatible. The replacement uses an explicit `pg_catalog, public` search path and schema-qualified table references.

## What is not yet proven

- This is a plausible reduction in sort width, not a measured fix.
- The SQL-management connection timed out again during a subsequent EXPLAIN attempt, so no before/after execution plan or latency measurement is available.
- The exact live function privileges and owner have not been independently audited in this pass.
- The current query may also be suffering from concurrent load, broad common-term candidate sets, or database-wide contention.

## Validation gate

1. Run the SQL in a non-production database with the exact production schema and data shape.
2. Compare `EXPLAIN (ANALYZE, BUFFERS)` and p50/p95 latency for Rolex, common broad terms, multiword phrases, stop words, and empty input.
3. Confirm output equivalence: same result fields, ranking weights, result limit, and ordering.
4. Test concurrent searches and confirm no statement-timeout/resource-limit responses.
5. Review SECURITY DEFINER ownership and grants before any production replacement.
6. Only after successful validation and explicit approval should a production cutover be considered.

No production function, database object, grant, queue, crawler schedule, or concurrency setting was changed.
