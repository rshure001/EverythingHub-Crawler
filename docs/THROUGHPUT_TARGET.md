# EverythingHub throughput target

## North-star metric

**Successfully indexed new or meaningfully updated records per 24 hours.**

A record counts only after it is normalized, deduplicated, written to the searchable index,
and passes the indexing validation checks. Fetch attempts, discovered URLs, retries, and
duplicates do not count as successful throughput.

## Milestones

| Stage | Successful records/day | System average/minute |
|---|---:|---:|
| First operating milestone | 60,000 | 41.7 |
| Expansion milestone | 1,000,000 | 694.4 |
| Large-scale ambition | 10,000,000 | 6,944.4 |
| Long-term ambition | 100,000,000 | 69,444.4 |

These are targets, not claims about current production performance.

## Eight-lane planning split

At the first 60,000/day milestone, an even planning split is:

| Lane | Records/day | Average/hour | Average/minute |
|---|---:|---:|---:|
| general | 7,500 | 312.5 | 5.21 |
| science | 7,500 | 312.5 | 5.21 |
| news | 7,500 | 312.5 | 5.21 |
| technology | 7,500 | 312.5 | 5.21 |
| business | 7,500 | 312.5 | 5.21 |
| government | 7,500 | 312.5 | 5.21 |
| culture | 7,500 | 312.5 | 5.21 |
| nature | 7,500 | 312.5 | 5.21 |
| **Total** | **60,000** | **2,500** | **41.67** |

This is a planning baseline. Real lane output should follow source availability, licensing,
freshness requirements, and measured capacity rather than forcing equal quotas.

## Required daily scorecard

Capture these counters per lane, source, and 15-minute window:

- discovered URLs
- fetch attempts and successful fetches
- newly indexed records
- meaningfully updated records
- duplicates, rejected records, and policy-blocked records
- write failures and retry count
- queue depth and oldest eligible item age
- fetch latency and index-write latency (p50/p95)
- search latency and relevance-test pass rate

Publish both the 15-minute rate and rolling 24-hour total. The primary headline is
`newly_indexed + meaningfully_updated`, never fetch attempts.

## Scale gates

1. **Source gate:** documented access method, reuse rights, attribution, robots/terms review,
   request ceilings, and pause procedure.
2. **Correctness gate:** canonicalization, deduplication, provenance, and repeatable import tests.
3. **Queue gate:** bounded claims, keyset pagination, lane isolation, retry limits, and no
   full-frontier ranking for small batches.
4. **Database gate:** measured write capacity, connection headroom, low timeout rate, and
   tested recovery under load.
5. **Search gate:** indexed records are searchable; latency and relevance tests pass.
6. **Expansion gate:** raise concurrency in controlled steps only after all preceding gates pass.

## Current measurement status

The current production logs show queue-claim statement timeouts and worker timeouts. The
available telemetry does not establish a trustworthy successful-indexing rate. Therefore
the current rate is **unverified**, not zero and not 60,000/day. Instrument the successful
index-write path before publishing a production throughput number.
