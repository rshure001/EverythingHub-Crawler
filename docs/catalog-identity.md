# Canonical record identity policy

## Catalog IDs and record IDs

Catalog definitions remain source-scoped. A catalog discovered on one publisher's site is not assumed to be the same catalog as a similarly named catalog on another host.

Record IDs may be shared across source hosts only when a stable external identifier is present and matches exactly. The page analyzer uses these rules:

1. Product GTIN remains the strongest product identifier.
2. An allowlisted schema.org `sameAs` identifier can provide a cross-host canonical identity. ORCID and ISNI identifiers must also pass their ISO 7064 MOD 11-2 check digit.
3. If no vetted external identifier exists, the record remains host-scoped and is keyed by its normalized type and title (or by the existing host-scoped SKU rule for products).

## Accepted external identifier namespaces

Only absolute HTTPS identifiers from these known namespaces are accepted:

- Wikidata entity IDs: `wikidata:Q…`
- ORCID researcher IDs: `orcid:…`
- VIAF authority IDs: `viaf:…`
- ISNI identifiers: `isni:…`
- GeoNames feature IDs: `geonames:…`
- OpenStreetMap object IDs: `osm:node|way|relation:…`

The parser rejects unknown hosts, non-HTTPS URLs, user information, query strings, fragments, malformed identifier paths, and invalid ORCID/ISNI check digits. A page-provided `sameAs` value remains a source claim; this pipeline does not dereference the identifier URL or independently verify the target entity.

## Provenance and deduplication

A cross-host merge changes only the canonical record grouping. It does not discard source evidence. The merged record retains all distinct source URLs, source-page IDs, catalog IDs, and timestamps. The record type remains part of the canonical key, so the same external identifier cannot merge a person with a product or place.

Unknown or absent identifiers never cause cross-host title-only merges. This deliberately favors duplicate candidates over false identity merges.

## Publishing and rights

These identifiers improve entity resolution; they do not grant content-reuse rights. Records remain candidate-stage until source terms and record-level reuse conditions are reviewed. This identity change does not fetch external identifier pages, download media, or publish records to production.

The analyzer version is incremented so existing host partitions are rebuilt with the updated identity rules.
