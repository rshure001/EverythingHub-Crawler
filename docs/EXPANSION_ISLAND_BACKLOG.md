# Expansion islands — candidate source backlog

These are discovery candidates, not activated sources. Before ingestion, verify current API terms,
robots policy, license, attribution rules, and rate limits. Prefer feeds, open APIs, and bulk
downloads. Do not copy full content unless reuse terms allow it.

| Lane | Candidate island | Preferred entry point | Initial integration |
|---|---|---|---|
| general | Library of Congress | https://www.loc.gov/apis/ | Open API; metadata and permitted records |
| general | Internet Archive | https://archive.org/developers/ | Metadata/API; respect item-level rights |
| general | Wikidata | https://www.wikidata.org/wiki/Wikidata:Data_access | Structured open data; preserve attribution/license |
| science | NASA | https://api.nasa.gov/ | API datasets and metadata |
| science | USGS | https://www.usgs.gov/products/data-and-tools | Public datasets and services |
| science | NOAA | https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation | Data APIs and metadata |
| news | NPR | https://feeds.npr.org/ | RSS feeds; index links and permitted metadata |
| news | BBC | https://www.bbc.co.uk/syndication | Syndication feeds under their terms |
| news | Associated Press | https://developer.ap.org/ | Licensed API only; do not scrape protected content |
| technology | GitHub | https://docs.github.com/en/rest | Public API; respect API limits and licenses |
| technology | MDN | https://developer.mozilla.org/ | Public documentation pages and metadata |
| technology | IETF | https://www.ietf.org/standards/ | RFC metadata and public standards |
| business | U.S. Census Bureau | https://www.census.gov/data/developers.html | Public statistics API |
| business | SEC EDGAR | https://www.sec.gov/edgar/sec-api-documentation | Public filings API; obey fair-access policy |
| business | Small Business Administration | https://www.sba.gov/about-sba/open-government | Public resources and datasets |
| government | USA.gov | https://www.usa.gov/ | Public government directory and metadata |
| government | Congress.gov | https://api.congress.gov/ | Official legislative API |
| government | Federal Register | https://www.federalregister.gov/developers/documentation/api/v1 | Official public API |
| culture | Smithsonian | https://www.si.edu/openaccess | Open Access API and CC0 assets |
| culture | Europeana | https://pro.europeana.eu/page/search | API; apply rights statements and attribution |
| culture | The Metropolitan Museum of Art | https://metmuseum.github.io/ | Open Access collection API |
| nature | GBIF | https://www.gbif.org/developer/summary | Biodiversity API; retain dataset attribution |
| nature | iNaturalist | https://api.inaturalist.org/v1/docs/ | API; honor per-observation licenses |
| nature | OpenStreetMap | https://wiki.openstreetmap.org/wiki/API | Use approved extracts/services; ODbL attribution |

## Activation gates

1. Record the exact terms, license, robots policy, and request limits with review evidence.
2. Confirm the source is reusable for the intended fields and display behavior.
3. Import a small sample into staging and retain source IDs and attribution.
4. Compare duplicate rate, freshness, broken links, search quality, and error rate.
5. Activate incremental sync only after review; pause if terms or behavior change.

## Growth accounting

Count a source as an "island absorbed" only after it is reviewed, imported, searchable,
attributed, and refreshable. Keep proposed, reviewed, active, paused, and retired totals separate.
