# 100-million-record expansion plan

## Goal and definition

The first strategic objective is to build a searchable collection containing **100,000,000
distinct, useful records**. This is a collection-size target, not a claim about current
production capacity or a daily ingestion promise.

A record counts only after it is:
- acquired through a documented, permitted source or dataset;
- normalized with a stable source ID and canonical identifier;
- deduplicated without losing provenance;
- validated for required fields and attribution;
- committed to the index and retrievable through search.

Do not count raw URLs, duplicate rows, fetch attempts, rejected records, or unindexed files.

## Recommended strategy: bulk first, crawl second

Individual page crawling is the wrong primary mechanism for a 100-million-record collection.
Prioritize acquisition methods in this order:

1. **Open bulk datasets and data dumps** with explicit reuse terms and manageable snapshots.
2. **Public APIs with bulk/export endpoints** and documented pagination or change feeds.
3. **RSS/Atom feeds and public sitemaps** for incremental freshness.
4. **Polite page crawling** for the long tail and sources without better interfaces.

Candidate source families to evaluate (not pre-approved or activated):
- general: Wikidata dumps, Library of Congress digital collections, Internet Archive metadata
- science: NASA open data, USGS data, NOAA datasets
- news: publisher RSS feeds and explicitly licensed archives
- technology: GitHub public API/catalogs within API terms, MDN and IETF reference material
- business: SEC EDGAR public filings, US Census datasets, SBA public resources
- government: Federal Register, Congress.gov, USA.gov and agency datasets
- culture: Smithsonian Open Access, Europeana, The Metropolitan Museum of Art open-access collection
- nature: GBIF occurrence datasets, iNaturalist API under its terms, OpenStreetMap extracts

Before importing any source, verify its current license, terms, attribution rules, privacy restrictions,
API limits, redistribution rights, and update mechanism. A public URL does not automatically mean
the content can be republished or indexed without restrictions. Store the evidence and review date.

## Pipeline architecture

Use a staged, restartable pipeline:

1. **Source registry:** one record per source, including terms evidence, access method, format,
   expected size, cadence, owner lane, attribution, and pause switch.
2. **Acquisition adapters:** isolated adapter per dataset/API/feed; checkpoint every page/file chunk.
3. **Immutable raw landing:** retain original source identifiers and snapshot metadata; do not
   silently overwrite source data.
4. **Normalization:** map each source into a versioned common record envelope.
5. **Deduplication:** exact canonical IDs first, then conservative fingerprints; preserve all
   source-to-record relationships.
6. **Validation and policy filter:** reject malformed, restricted, sensitive, or unlicensed records.
7. **Bulk index writer:** batch writes, partition by stable hash or lane, retry idempotently.
8. **Search publication:** publish only validated index segments; keep ingestion separate from
   query serving.
9. **Incremental refresh:** consume source deltas/change feeds instead of repeatedly importing
   full snapshots.
10. **Observability:** per-source counts for discovered, downloaded, normalized, deduplicated,
    accepted, rejected, indexed, and searchable records.

## Scale design

- Keep the eight lanes as independent scheduling and operational boundaries.
- Partition large imports by source and stable record key; make every chunk resumable and idempotent.
- Use bounded queues and backpressure. One slow source must not stall other sources.
- Separate bulk ingestion from search serving so imports do not take down queries.
- Use object/file storage for raw snapshots and staged chunks; use the search database only for
  normalized searchable records and operational metadata.
- Build and validate index segments in staging, then publish atomically.
- Add worker capacity only after load tests prove storage, database, network, and search headroom.
- Avoid a single shared database queue-claim function as the global coordinator at high scale.

## Phased execution

### Phase A — inventory and measurement
- Inventory existing indexed records and sources.
- Establish a trustworthy baseline for distinct, searchable records.
- Audit current source rights and provenance.
- Add import/run counters and per-source checkpoints.

### Phase B — first bulk islands
- Select a small number of high-volume, clearly reusable datasets.
- Implement adapters and test with bounded samples.
- Verify counts, deduplication, attribution, update behavior, and search retrieval.
- Estimate storage and indexing cost from measured sample sizes before full import.

### Phase C — collection expansion
- Add more approved datasets across all eight lanes.
- Run parallel imports with independent checkpoints and backpressure.
- Reconcile source counts against indexed and searchable counts.

### Phase D — scale out
- Benchmark ingestion and query serving independently.
- Partition index and storage where measured limits require it.
- Add workers and partitions based on load-test evidence, not arbitrary concurrency.
- Move to incremental refresh after initial snapshots are complete.

## Scorecard

Report these separately:
- distinct searchable records total;
- new records indexed per hour and rolling 24 hours;
- materially updated records per hour and rolling 24 hours;
- source datasets reviewed / approved / active / paused;
- raw bytes acquired and staged;
- normalized, duplicate, rejected, and indexed counts;
- import lag, queue age, write latency, and failure/retry rate;
- search latency and relevance test results.

## Important current constraints

The existing repository crawler is a small prototype with 49 seed hosts, eight workers, and an
80-fetch-per-run ceiling. It is not a bulk dataset ingestion system and is not connected to the
production search index.

The production Supabase environment has experienced queue-claim statement timeouts and worker
resource timeouts. Do not point large imports at it or increase production concurrency until the
database path is measured and repaired. The current production collection size and rate must be
measured directly; they are not established by this plan.

This plan is a staged engineering blueprint. It does not authorize paid infrastructure, new
subscriptions, third-party credit purchases, or production database changes.
