# Mass-production scale plan — based on deployed production crawler

**Checked:** 2026-11-07  
**Production function:** `everything-hub-crawler` v30  
**Project:** `vdceayvfgkwpgwixrhwi`

## Current deployed throughput controls (verified from source)

- Queue claim batch: **4 items per invocation** (`claim_eh_crawl_batch({batch_size:4})`).
- Concurrent page workers: **2**.
- Link discovery: at most **6 validated links per fetched page**.
- Seed loading: up to **250 enabled seeds per invocation**.
- Cron invocation evidence: approximately every **10 minutes**.
- The function records `target_hourly: 1000`, but this is only a response field; it does not control actual throughput.

With four queue claims every ten minutes, the scheduled claim ceiling is approximately **24 queue items/hour** if one invocation completes per schedule interval. This is not a successful unique-page rate and is nowhere near the stated 1,000/minute ambition.

## Current production bottleneck evidence

Last 24-hour function-edge log aggregation:
- HTTP 200: 294
- HTTP 500: 142
- HTTP 546 worker resource limits: 10

Latest logged success was 2026-11-07 03:38:40 UTC; latest 500 was 18:11:06 UTC. A scheduled invocation was logged at 20:00:08 UTC.

PostgREST logs show failed POST upserts to `eh_crawl_queue` (HTTP 500) at 18:11, 17:04, 15:15 and earlier. PostgreSQL logs independently confirm SQLSTATE 23502: `next_attempt_at` cannot be NULL. A previous queue-status scan estimated roughly 109k pending items and roughly 695 in-progress items; these are prior query-plan estimates, not current exact counts.

## Scale architecture required

1. **Fix queue correctness first:** all queue state updates must satisfy actual NOT NULL and check constraints; check every database response and record failed writes.
2. **Decouple scheduling from work:** short invocations claim bounded batches and dispatch work; never run one long Edge Function that attempts to process an enormous batch.
3. **Raise throughput in measured stages:** start with bounded batch and concurrency increases in a non-production environment, then canary production only after explicit approval.
4. **Keep host fairness:** per-host concurrency and pacing, robots.txt, DNS/private-IP protections, redirect validation, body-size/time limits.
5. **Make writes idempotent:** unique canonical URL; distinguish fetched, updated, duplicate, rejected, failed, and newly searchable page counts.
6. **Observe every stage:** claim rate, queue wait, fetch rate, successful unique insert rate, PostgREST write failures, worker-limit responses, DB latency, stale leases, and per-host distribution.
7. **Only scale while healthy:** automatic backoff on 500/546, DB latency, or resource pressure; no concurrency ramp during error spikes.

## Throughput targets

- Immediate engineering objective: restore reliable production runs and measure the baseline unique searchable pages/minute.
- Next: test increasing worker count and batch size in isolation; determine safe sustained capacity.
- Target: 1,000 verified unique searchable pages/minute. This requires **60,000/minute?** No: 1,000/minute equals 60,000/hour. Do not confuse these units.
- One million unique pages is a separate cumulative milestone.

No production setting, schedule, function, or database record was changed by this report.
