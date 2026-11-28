# Structured source connectors

These connectors import external structured metadata into a reviewable staging file.
They do not write to PostgreSQL, alter the canonical crawler index, or publish a
catalog. A downstream integration must explicitly validate rights and merge records.

## Wikidata entity connector

Wikidata structured data is available under CC0-1.0. The connector stores the
entity QID, source URL, retrieval timestamp, license, and stable
`wikidata:QID` record ID with each staged record.

Run from the repository root:

```sh
python crawler/connectors/wikidata_entity_connector.py \
  --catalog-id cameras \
  --output data/source-staging/wikidata-cameras.json \
  $(awk '/^Q[1-9][0-9]*$/ { printf "--qid %s ", $0 }' crawler/connectors/camera_wikidata_qids.txt)
```

The QID list is an initial seed list, not a claim of complete camera coverage.
The connector makes one request per unique QID with a one-second default delay.
Review the resulting JSON and compare stable IDs against existing catalog
records before merging. Errors are kept separate; partial results are never
silently marked complete.
