# Everything Hub Crawler

Independent, polite crawler and public-source discovery pipeline for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt`.
- **10 enabled bulk discovery connectors** in `crawler/connectors.json`; each reads publisher-declared sitemap locations from that site's `robots.txt`.
- Connector caps: up to 4 sitemap documents and 250 discovered URLs per connector per run; 2,500 new URLs maximum per run.
- 8 concurrent page-fetch workers and a maximum of 80 page fetch attempts per run.
- Persistent frontier with a 50,000 discovered-URL ceiling.
- Maximum link depth: 3.
- Same-host HTML link discovery and same-host sitemap URL acceptance.
- Checks each site's `robots.txt`; if robots rules cannot be read, the page crawler fails closed for that host.
- Stores normalized page records in `data/index.json` and resumable frontier state in `data/frontier.json`.

## Run locally
```bash
python -m py_compile crawler/crawl.py crawler/report.py crawler/connectors.py
python -m unittest discover -s crawler -p 'test_*.py' -v
python crawler/connectors.py
python crawler/crawl.py
python crawler/report.py
```

## Automation and observability
GitHub Actions runs on a 15-minute schedule and supports manual dispatch. Each run validates the connector registry, executes unit tests, discovers URLs from the ten sitemap connectors, crawls up to 80 pages, and publishes a compact health report in the workflow summary. The workflow does not trigger on pushes, preventing crawler data commits from recursively launching more crawls. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, and depth distribution. Attempted fetches are not counted as indexed pages.

## Search integration status
The crawler currently writes its own repository index. It is **not yet connected** to Everything Hub's production search index. Do not publish or expose crawler records as production search results until the production ingestion endpoint, authentication, and indexing format are verified end-to-end.

## Scale and safety
These ten connectors discover URLs from public publisher sitemap declarations; they do not copy Bing's private index and do not guarantee that every discovered URL is accessible or indexable. The crawler is still a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions.

The 10-million-record synthetic benchmark remains a benchmark only, not 10 million live pages. Report actual fetched, parsed, deduplicated, and indexed records separately. Do not create paid infrastructure or buy credits without the owner's explicit approval.
