# Production crawler incident — read-only verification

**Checked:** 2026-11-07  
**Project:** `vdceayvfgkwpgwixrhwi` (`everything-hub-control`)

## Confirmed deployment inventory

The Supabase project reports `ACTIVE_HEALTHY`. The deployed Edge Function inventory reports:

| Function | Version | Status | JWT |
|---|---:|---|---|
| everything-hub-crawler | 30 | ACTIVE | required |
| everything-hub-search | 3 | ACTIVE | required |
| everything-hub-media-search | 8 | ACTIVE | not required |
| everything-hub-web-search | 4 | ACTIVE | not required |
| everything-hub-sitemap-discovery | 5 | ACTIVE | required |

The active crawler source was retrieved read-only from the deployed function.

## Confirmed code defect

In `retryOrFail()`, the deployed crawler computes:

- `status = "pending"` for retryable errors, otherwise `status = "failed"`
- `next_attempt_at = timestamp` only when pending
- `next_attempt_at = null` when failed

The same update writes that value to `eh_crawl_queue`. This is a concrete candidate for the previously observed NOT NULL constraint violations. The actual table constraint could not be rechecked because the read-only SQL query timed out.

## Safe repair requirements — not applied

Before deployment, inspect the actual `eh_crawl_queue` column nullability, defaults, checks, indexes, and the claim RPC definition. Then test the repair outside production.

A safe candidate is to keep `next_attempt_at` non-null for every transition (for terminal failures, set it to the transition timestamp or a schema-approved sentinel) while ensuring the claim RPC selects only `pending` rows whose retry timestamp is due. Do not choose a sentinel until the claim function and constraints are verified.

Also check every queue update result. If a status update fails, log the queue ID and database error and expose the failure in run metrics; do not report the item as successfully completed.

## Current verification limitation

A read-only query against `information_schema.columns` for `eh_crawl_queue`, `eh_pages`, `eh_host_leases`, and `eh_crawl_runs` timed out with a database connection timeout. No SQL writes were issued. No production function was changed or redeployed.

## Release gates

1. Read actual table constraints and indexes plus `claim_eh_crawl_batch` definition.
2. Verify queue transitions for pending, blocked, done, and failed states in a non-production environment.
3. Confirm lease cleanup and stale-lease recovery.
4. Deploy only after review and explicit production-change approval.
5. Verify successful queue updates and a searchable canary before any throughput increase.
