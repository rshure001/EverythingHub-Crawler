# Everything Hub Crawler

Independent, polite crawler for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt` (a newly assembled set, not a recovered historical list).
- 8 concurrent workers and a maximum of 80 fetch attempts per run.
- Persistent frontier with a 50,000 discovered-URL ceiling.
- Maximum link depth: 3.
- Same-host HTML link discovery; sitemap XML is not preloaded because it previously consumed the run budget without producing searchable pages.
- Checks each site's `robots.txt`; if robots rules cannot be read, the crawler fails closed for that host.
- Stores normalized page records in `data/index.json` and resumable frontier state in `data/frontier.json`.

## Run locally
```bash
python -m py_compile crawler/crawl.py crawler/report.py
python -m unittest discover -s crawler -p 'test_*.py' -v
python crawler/crawl.py
python crawler/report.py
```

## Automation and observability
GitHub Actions runs on a 15-minute schedule and supports manual dispatch. Each run validates configuration, executes unit tests, crawls up to 80 pages, and publishes a compact health report in the workflow summary. The workflow does not trigger on pushes, preventing crawler data commits from recursively launching more crawls. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetched pages, parsed pages, and depth distribution. Attempted fetches are not counted as indexed pages.

## Search integration status
The crawler currently writes its own repository index. It is **not yet connected** to Everything Hub's production search index. Do not publish or expose crawler records as production search results until the production ingestion endpoint, authentication, and indexing format are verified end-to-end.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.
