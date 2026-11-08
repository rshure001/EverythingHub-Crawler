# EverythingHub expansion engine

## Operating model

Grow by onboarding independent public information collections ("islands") into one normalized,
searchable index. Absorb permitted data and interfaces, not private infrastructure, credentials,
or restricted content. Preserve attribution and source provenance on every record.

## Eight-lane federation

Each lane owns its source registry, discovery cursor, crawl frontier, retry budget, freshness
policy, worker pool, and health counters:

- general: reference collections and broad public directories
- science: research institutions, scientific datasets, observatories
- news: publisher feeds and public news sitemaps
- technology: standards bodies, documentation, open-source project catalogs
- business: public company registries, economic statistics, business reference collections
- government: public agency publications, legislation, public records portals
- culture: museums, libraries, archives, arts collections
- nature: environment, biodiversity, weather, geography collections

Lane separation is a scheduling and product boundary. It does not override source terms,
robots directives, API licenses, or access restrictions.

## Island onboarding contract

Every candidate source gets a registry record with:
- stable source ID and canonical host
- owning lane and source class (RSS, sitemap, open API, public crawl)
- terms/robots review status and evidence URL
- allowed content fields and attribution requirement
- refresh cadence and per-host request ceiling
- discovery cursor and last successful sync
- estimated record count, actual imported count, duplicate count
- source status: proposed, reviewed, active, paused, retired

A source is not activated until its access method and reuse terms are reviewed. Prefer open
APIs, RSS/Atom feeds, public datasets, and sitemaps over broad HTML crawling. Never bypass
logins, paywalls, CAPTCHAs, rate limits, robots exclusions, or other access controls.

## Expansion loop

1. Discover a source candidate.
2. Review permission, licensing, attribution, and technical access.
3. Run a bounded sample import in a staging namespace.
4. Normalize canonical URLs, source IDs, timestamps, language, lane, and content hashes.
5. Deduplicate without erasing provenance; keep source-to-record relationships.
6. Validate counts, freshness, broken-link rate, search relevance, and attribution.
7. Activate the source's independent incremental sync job.
8. Monitor it; pause automatically on repeated errors or policy changes.

## Isolation and scale

- No global queue lock across lanes.
- Each lane has its own queue, concurrency cap, retry schedule, and run metrics.
- Per-host leases remain shared safety controls so two lanes do not overload the same host.
- Source adapters are isolated by source ID; one failing island cannot block other sources.
- Search reads a normalized index and must not wait for live crawling.
- Use bounded batches and keyset cursors; never rank the entire pending queue to claim a small batch.
- Apply backpressure when database latency, queue age, worker errors, or host limits rise.
- Scale worker count only after database and connection-pool headroom is demonstrated.

## Scorecard

Track per lane and per source:
- eligible / reviewed / active / paused sources
- records discovered / fetched / newly indexed / updated / duplicate / rejected
- successful records per minute (not attempted requests)
- queue age p50/p95 and fetch latency p50/p95
- HTTP status and retry distributions
- database write latency, statement timeouts, and connection-pool saturation
- search latency p50/p95 and relevance test pass rate

## Current reality and next gates

The repository crawler currently has 49 curated seed hosts, a 50,000-URL frontier ceiling,
8 concurrent workers, and up to 80 fetch attempts per scheduled run. Its repository JSON index
is not yet connected to the production search index. Treat those values as prototype settings,
not production scale.

The production Supabase project has shown substantial statement timeouts and worker timeouts.
Do not increase production concurrency until queue claims, database writes, and search have
measured headroom. The 10,000 successfully indexed pages/minute goal is a target, not a current
measured rate.

A separate bounded-claim SQL proposal exists on branch
`repair/bounded-lane-queue-claims`. It targets only the independent self-hosted schema and is
not approved for the production Supabase schema.
