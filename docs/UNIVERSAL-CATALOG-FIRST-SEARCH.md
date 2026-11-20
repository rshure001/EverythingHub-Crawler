# Everything Hub — Universal Catalog-First Search

## Product direction

Everything Hub is a **catalog discovery and search engine across every subject**, not a page-only crawler and not a retail-only product index.

The primary unit of discovery is a **catalog**: an organized collection of related entities or records exposed by a website, API, feed, registry, directory, dataset portal, knowledge base, marketplace, archive, or other source.

A web page is still useful and countable, but it is often only:
- the catalog's landing page,
- a category or collection page,
- a record detail page,
- a source/evidence page for a catalog record.

Do not mistake discovery of one page for discovery of the collection represented by that page.

## Query examples

### Query: Rolex
Resolve the query as a brand/entity and discover relevant catalogs and collections, such as:
- Rolex watch models and model families
- current and historical product references where public data is available
- authorized retailer/dealer directories
- service-centre/location directories
- official brand pages, announcements, manuals and support resources
- independent watch databases, reference archives, articles and specialist marketplaces

Show each catalog as a collection with its source, coverage, record count (when measurable), freshness and access method. Do not imply that one source is the complete universe of Rolex records.

### Query: Las Vegas
Resolve the query as a geographic entity and discover catalogs such as:
- businesses and organizations
- hotels, restaurants, attractions and entertainment venues
- events and public calendars
- government services, agencies and public records
- neighborhoods, places, transport and geographic features
- schools, libraries, museums and cultural collections
- public datasets, planning, permits, maps and statistics
- news, research, archives and community resources

A location query should open a browsable collection of relevant catalogs and their records, not merely return a list of pages containing the words “Las Vegas.”

### Query: men's diamond pinky rings
Resolve the intent to a product/topic concept and search across jewelry/product catalogs, retailer catalogs, brand catalogs, marketplace collections, product records and supporting pages. A result page can contain many structured product records from multiple sources.

## Universal data model

Keep the existing database architecture and extend its logical model; do not create a parallel database.

1. **Catalog** — a collection with a stable internal ID, title, description, domain/subject, catalog type, owner/publisher, source URL/API, geography, language, access method, license/terms, last-discovered time, last-synced time and status.
2. **Catalog record** — an individual item in a catalog: product, business, place, event, person (where appropriate and public), dataset, document, service, model, article, listing, collection item or other typed entity.
3. **Record attributes** — normalized searchable fields plus source-specific attributes; retain original values and provenance rather than flattening away important domain detail.
4. **Relationships** — catalog contains record; entity belongs to brand/category/location; record is offered by seller; place is in region; dataset describes subject; record is supported by source page.
5. **Source page** — URL, canonical URL, page type, fetch status, timestamp, content hash and provenance. It supports discovery and verification but is not a substitute for a catalog record.
6. **Catalog snapshot** — timestamped counts, source version/etag where available, coverage, sync status, failures and change summary.

Every record must retain its source and attribution. Deduplicate equivalent entities across catalogs while preserving each source's offer/listing and provenance. Do not merge distinct variants, locations, editions or offers merely because their names are similar.

## Discovery and ingestion priority

For each discovered site or organization, look for the richest permitted collection interface first:

1. Public APIs, bulk exports, data feeds and downloadable catalog files.
2. Sitemap indexes, category trees, collection indexes and public registries.
3. Structured metadata such as JSON-LD, schema.org, RDF/JSON-LD and other machine-readable records.
4. Public directory/search endpoints and documented pagination.
5. Individual pages as a fallback for missing fields, verification and record enrichment.

Respect robots policies, terms, rate limits, licensing, privacy, authentication and access controls. Do not bypass logins, paywalls, CAPTCHAs or restrictions. If full-catalog access is unavailable, label coverage honestly as partial and show the source's accessible scope.

Use source-specific adapters behind one common catalog ingestion contract. Examples of adapter families include commerce feeds, business directories, geographic/places catalogs, government registries, CKAN/open-data portals, library/archive catalogs, scholarly indexes, event calendars, media collections, software/package registries and general web collections.

## Search behavior

For a user query:
1. Resolve likely entities, concepts, geography and intent.
2. Retrieve matching catalogs and matching records in parallel.
3. Rank catalogs by relevance, authority, coverage, freshness and accessibility.
4. Search inside the relevant catalogs for matching records.
5. Return catalog cards with record counts and coverage labels, followed by browsable records.
6. Use source pages as evidence and click-through destinations.

Support both catalog-level search (“Rolex”, “Las Vegas”, “NASA”) and item-level search (“men's diamond pinky ring”, a specific watch reference, a venue, a dataset).

## Honest counting and dashboard metrics

Report separate counters; never add unlike units into one misleading total:
- catalogs discovered
- catalogs successfully connected
- catalog records discovered
- unique normalized entities
- source pages discovered
- source pages fetched
- records successfully indexed
- records updated / unchanged / removed
- catalog sync failures and coverage limitations

A catalog record and its source page can both count in their own metric, but one must not be reported as the other. A catalog with 20,000 products is one catalog, 20,000 catalog records, and potentially many source pages.

Show per-catalog counts and last successful sync. Do not call a catalog complete unless the source provides a verifiable completeness signal or the coverage method establishes it.

## Build sequence

1. Define and validate the common catalog/record/provenance contract against existing `eh_quad` and `eh_documents` design. No production schema changes while database recovery is unresolved.
2. Build a catalog registry and adapter interface with fixture-based tests.
3. Extend the current CKAN discovery to ingest dataset records and resource metadata as catalog records, not just save a list of resource URLs.
4. Add broad non-retail adapters, starting with geographic/place catalogs and public registries, alongside commerce catalogs.
5. Build query-to-catalog resolution and catalog-level result cards.
6. Add incremental sync, deduplication, change detection and honest per-catalog coverage metrics.
7. Connect the public search UI only after end-to-end tests prove catalog records are returned from the live index.

## Acceptance tests

- “Rolex” returns multiple relevant catalog collections, not just a homepage URL.
- “Las Vegas” returns geographic, business, events, public-service, cultural and open-data collections where available.
- “Men's diamond pinky rings” searches item records across relevant jewelry catalogs.
- Catalog counts, record counts and source-page counts are distinct and traceable.
- A partial source is labeled partial; no fabricated completeness or record counts.
- Existing Dog Tag Day / Story Wall data and unrelated projects remain untouched.
- The same shared ingestion/index pipeline handles every subject family; no retail-only special case becomes the core architecture.
