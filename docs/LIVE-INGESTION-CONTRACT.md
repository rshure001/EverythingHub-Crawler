# Live ingestion contract — design gate

**Status:** design only; not connected or deployed.

## Purpose

Move successfully crawled, canonical page records from the development crawler into Everything Hub's live search index without treating fetch attempts, discovered links, or queued URLs as indexed pages.

## Required contract

The production schema and deployed function signatures must be inspected before implementation. Do not assume table names, column names, RPC arguments, or authentication settings from the prototype.

### Request

- Authenticated crawler identity; reject anonymous writes.
- Bounded batch size and request-body size.
- Each item includes requested URL, final URL, fetch timestamp, HTTP status, content type, title, description, extracted text, content hash, and crawl depth.
- Include crawler/version provenance and a stable idempotency key.
- Do not send binary bodies or unbounded page text.

### Canonicalization and deduplication

- Normalize scheme/host casing, default ports, fragments, and known tracking parameters.
- Preserve meaningful query parameters.
- Validate HTTP(S), reject credentials in URLs, and enforce private-network/localhost protections after DNS resolution and on redirects.
- Use the verified canonical URL as the unique identity; retain requested and final URLs for audit.
- Upsert idempotently. Repeated submissions must not create duplicate searchable pages.

### Storage and search visibility

- Separate fetch outcome from indexing outcome.
- Store only successful, policy-compliant HTML pages as searchable records.
- Preserve source URL, fetched-at time, content hash, title, description, extracted text, and crawl provenance.
- Define an explicit searchable/eligible state. Failed, blocked, oversized, duplicate, and non-HTML responses must not inflate the indexed-page count.
- Confirm that the live search endpoint can retrieve the canary page before enabling bulk ingestion.

### Reliability and rate controls

- Use bounded batches, retryable error classification, exponential backoff with jitter, and a dead-letter state after a finite retry budget.
- Enforce per-host concurrency and request pacing; honor robots.txt and redirects.
- Make queue claims lease-based and reclaim expired leases safely.
- Keep queue status transitions valid under database constraints. A failed state must never write NULL into a required retry timestamp.
- Emit counters for fetched, parsed, accepted, deduplicated, rejected, failed, and searchable records.

## Acceptance gates

1. Read-only inspection confirms the actual production schema, constraints, indexes, RLS policies, and deployed function/RPC signatures.
2. A non-production test validates auth, URL safety, idempotent upsert, retry behavior, and bounded payloads.
3. One canary page is ingested and then found through the actual live search endpoint.
4. Reconciliation proves one unique stored searchable page was added and repeated submission adds zero.
5. Only after explicit production-change approval may the canary be run in production.
6. Bulk ingestion and higher concurrency remain disabled until health, host fairness, resource use, and sustained unique-indexing rate are measured.

## Counting rule

The million-page target counts only distinct, successfully stored, searchable page records. Discovered URLs, fetch attempts, queue entries, retries, duplicates, and failed records do not count.
