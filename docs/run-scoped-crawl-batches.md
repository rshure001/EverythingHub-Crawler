# Run-scoped crawl batches: initial contract

Status: isolated prototype on `feat/run-scoped-crawl-batches`; not connected to production crawl writes.

## Purpose

Allow fetch workers to produce independent result files while preserving one authoritative writer for the canonical index and frontier.

## Batch format

Each worker writes one JSON object:

- `schema_version`: integer, currently 1
- `run_id`: non-empty workflow/run identifier
- `worker_id`: non-empty stable worker identifier
- `pages`: list of page records

Batches are written atomically using a temporary file and rename. Each write returns a byte count, SHA-256 digest, and page count. Inputs must contain safe HTTPS page URLs and finite, non-negative `fetched_at` values.

## Reducer rules

1. Read and validate every batch before accepting its records.
2. Merge by exact canonical URL; this module does not rewrite URLs.
3. The newest `fetched_at` record wins.
4. Equal timestamps use canonical JSON bytes as a deterministic tie-break, independent of worker completion order.
5. Emit pages sorted by URL, so the same inputs produce the same output.
6. Replaying the same batch is idempotent.
7. The reducer returns metrics for batch count, worker count, accepted batch pages, duplicate occurrences, replaced existing records, and final unique pages.

## Deliberate safety boundary

This prototype does not write `data/index.json`, `data/frontier.json`, public shards, or the public manifest. It does not schedule workers, partition hosts, or change the current 250-worker limit. Those integrations require separate review and tests for frontier ownership, retries, robots pacing, stale-run rejection, and atomic single-writer publication.

## Test coverage

- Batch write/read round trip and digest metadata
- Whole-batch rejection for invalid URLs and non-finite timestamps
- Newer-record conflict resolution
- Deterministic tie-breaking across batch order
- Idempotent replay
- Stable URL ordering and duplicate accounting
- Loading and reducing multiple on-disk batches
- Rejection of credentials and local/internal hosts
