# Ten bulk connectors — first acquisition wave

These are ten separate connector definitions for EverythingHub's acquisition system. They are
**configured candidates, not activated imports**. Each connector must pass source-specific terms,
license, attribution, privacy, and rate-limit review before it can download or publish data.

| Connector ID | Lane | Source | Acquisition mode | Official entry point | Initial payload |
|---|---|---|---|---|---|
| wikidata-dump | general | Wikidata | Bulk dump | https://www.wikidata.org/wiki/Wikidata:Database_download | Structured entity records; retain CC0/source metadata |
| loc-api | general | Library of Congress | API / bulk collections | https://www.loc.gov/apis/ | Catalog metadata and permitted digital-object metadata |
| nasa-open | science | NASA | Open API / dataset catalog | https://api.nasa.gov/ | Dataset and media metadata; check each endpoint's terms |
| usgs-data | science | USGS | Data services / bulk downloads | https://www.usgs.gov/products/data-and-tools | Public geospatial and scientific metadata |
| noaa-data | science | NOAA NCEI | API / bulk datasets | https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation | Climate and environmental observations with source metadata |
| census-api | business | U.S. Census Bureau | API / bulk files | https://www.census.gov/data/developers.html | Public statistics, tables, geographies |
| sec-edgar | business | SEC EDGAR | Bulk archives / API | https://www.sec.gov/edgar/sec-api-documentation | Company submissions and filing metadata; comply with fair-access policy |
| congress-api | government | Congress.gov | API | https://api.congress.gov/ | Bills, members, actions, committee metadata |
| smithsonian-open | culture | Smithsonian Open Access | API / bulk media metadata | https://www.si.edu/openaccess | CC0 collection records and permitted media |
| gbif-occurrences | nature | GBIF | API / download service | https://www.gbif.org/developer/summary | Biodiversity occurrence records with dataset attribution |

## Current adapter implementation

- `loc-api`: configured for the official `/search/` JSON endpoint, `results` records, and `pagination.next` URLs. It remains disabled pending item-level rights review.
- `gbif-occurrences`: configured for the documented `/v1/occurrence/search` endpoint with bounded 300-record offset pages. It preserves each record's license and dataset identifiers; it remains disabled pending dataset/license review. GBIF search has a 100,000-result ceiling; larger collections require its asynchronous download workflow and separate adapter.
- `json_api_runner.py`: supports cursor, offset, and allowlisted next-URL pagination; checkpoints staged byte offsets and cursor state; refuses changed connector configurations on resume; stages only and never publishes to production.
- `test_json_api_runner.py`: all network calls mocked; covers approval gates, provenance, checkpoint resume, allowlisted next URLs, rejected hosts, and offset pagination.

These adapters are implementation work, not permission to activate sources. The registry keeps them disabled until review is recorded.

## Acquisition scorecard

Run `python connectors/acquisition_report.py --registry connectors/registry.json --staging-dir staging/connectors` to produce a source-by-source JSON report. It reads local staging JSONL and checkpoint files only; it does not contact sources. It reports pages, accepted staged records, rejected records, license breakdowns, completion state, and separately registered indexed/searchable counts. Staged records are never counted as indexed or searchable.

## Common connector contract

Every connector must implement the same lifecycle:

1. `review()`: require recorded terms URL, license/rights statement, attribution, allowed fields,
   request limits, privacy review, and a dated approval.
2. `discover(cursor)`: return a bounded page or bulk-file manifest plus a durable next cursor.
3. `fetch(item)`: retrieve only through the documented endpoint or permitted download method.
4. `normalize(item)`: produce the common EverythingHub staging envelope with source provenance.
5. `checkpoint(cursor, counts)`: persist progress so interruption does not restart completed work.
6. `validate(record)`: enforce source-specific field and rights rules.
7. `stage(batch)`: use the restartable bulk importer; never write directly to the live search index.
8. `publish(batch)`: separate approval-gated operation, disabled until production index path is tested.

## Safety and accounting

- Default status for all ten: `proposed`; review status: `not_reviewed`; production writes: disabled.
- A source only becomes active after its exact dataset/API endpoint and reuse terms are reviewed.
- Keep source-native IDs, canonical links where provided, license/rights statement, attribution,
  retrieval timestamp, snapshot/version, and source-specific update cursor.
- Do not assume an entire organization's catalog shares one license. Review at dataset/item level
  where necessary (notably USGS, NOAA, NASA endpoints, SEC submissions, and GBIF datasets).
- Respect API quotas, robots directives where applicable, and documented fair-access requirements.
- No scraping behind logins, paywalls, or access controls; no bypassing rate limits.
- Track discovered, fetched, parsed, accepted, duplicate, rejected, indexed, and searchable counts
  separately. Downloads are not searchable records.
- Keep the eight lanes isolated with independent queues, checkpoints, retries, and pause switches.

## Activation order

Start with a small sample from each of the ten sources after review, not ten full-scale downloads
at once. Validate schema, rights, duplicate rate, record quality, storage footprint, and search
retrieval. Then expand each connector independently using bulk snapshots and incremental updates.

**This file defines ten connector targets; it does not claim that any connector has been authorized,
run, or populated the live EverythingHub index.**
