# 64-worker fleet foundation

Status: design and acceptance contract only. This document does not authorize production deployment or imply that 64 workers are running.

## Target topology

- Eight independent crawl lanes: general, science, news, technology, business, government, culture, nature.
- Eight worker slots per lane at the first distributed-fleet milestone (64 total).
- Each lane has a durable queue, retry/dead-letter handling, and per-host scheduling.
- Workers publish normalized page records to one deduplicating index-ingestion boundary.

## Required controls

1. Enforce robots.txt and site terms; do not bypass blocks, CAPTCHAs, authentication, or rate limits.
2. Apply per-origin concurrency and minimum-delay limits. The per-origin limiter is shared across all lanes, not one limiter per worker.
3. Use leases with expiry so crashed workers do not strand queue items.
4. Retry transient network/server failures with bounded exponential backoff and jitter; do not retry permanent 4xx responses indefinitely.
5. Canonicalize URLs and deduplicate before counting a page as indexed.
6. Cap response bytes, content types, redirects, and crawl depth.
7. Persist queue transitions and ingestion results; surface failed writes rather than reporting success.
8. Keep public-provider search aggregation independent from crawler ingestion so a crawler outage does not break web search.

## Throughput definition

The 800-pages/minute objective means 800 distinct, validated page records accepted by the central index in a rolling 60-second window. It does not mean discovered URLs, HTTP requests, retries, or duplicate records.

Measure at least:
- fetched pages/minute
- successful HTML fetches/minute
- unique records accepted/minute
- duplicates, robots-denied, throttled, and failed fetches
- queue depth and oldest-item age per lane
- per-origin request rate and error rate
- ingestion latency and rejected writes

## Capacity planning

At 64 workers, an idealized 5-second end-to-end worker cycle yields 768 completions/minute before failures, host throttling, queue overhead, and indexing delays. Therefore 64 is a coordination milestone, not a guarantee of 800 indexed pages/minute. Benchmark representative sites and increase worker slots only where queue demand, host policies, and measured resource headroom justify it.

## Release gates

- Unit tests for queue leases, retries, URL canonicalization, robots decisions, and deduplication.
- Integration test with a local fixture site and a deliberately slow/failing host.
- Load test proving the rolling-minute metric and backpressure behavior.
- Security review of ingestion authentication, database row-level security, and write limits.
- Verify indexed records appear in the live search endpoint.
- Production canary and deployment require explicit approval; do not merge or deploy this design solely because it exists.

## Initial lane ownership

| Lane | Scope |
|---|---|
| general | broad public web and reference material |
| science | research, science institutions, datasets |
| news | publisher feeds and public news pages |
| technology | software, engineering, standards |
| business | companies, markets, commerce |
| government | public agencies, laws, public records |
| culture | arts, history, education |
| nature | environment, geography, wildlife |

