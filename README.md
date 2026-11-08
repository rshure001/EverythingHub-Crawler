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
python -m unittest discover -s imports -p 'test_*.py' -v
python crawler/crawl.py
python crawler/report.py
```

## Automation and observability
GitHub Actions runs on a 15-minute schedule and supports manual dispatch. Each run validates configuration, executes unit tests, crawls up to 80 pages, and publishes a compact health report in the workflow summary. The workflow does not trigger on pushes, preventing crawler data commits from recursively launching more crawls. GitHub may delay scheduled runs.

The health report distinguishes total indexed records, unique hosts, discovered URLs, queued URLs, fetch attempts, parsed HTML pages, newly indexed records, meaningfully updated records, unchanged records, fetch errors, and depth distribution. The primary throughput counter is new plus meaningfully updated records; fetch attempts, unchanged pages, and non-HTML responses do not count.

## Search integration status
The crawler currently writes its own repository index. It is **not yet connected** to Everything Hub's production search index. Do not publish or expose crawler records as production search results until the production ingestion endpoint, authentication, and indexing format are verified end-to-end.

## Scale and safety
This GitHub Actions worker is a prototype runner, not a distributed crawl fleet. Respect robots rules and host-level limits. Do not bypass logins, paywalls, CAPTCHAs, access controls, or site restrictions. Do not create paid infrastructure or buy credits without the owner's explicit approval.

## Bulk dataset staging

A safe, offline-first bulk importer is available at `imports/bulk_ingest.py`. It accepts JSONL
or CSV files, requires a source registry record marked `reviewed_allowed`, requires a documented
license or terms URL, normalizes records, deduplicates source identifiers, and writes JSONL
staging output. It does not publish to production.

Example:

```bash
python imports/bulk_ingest.py --source source.json --input dataset.jsonl --output staging/dataset.jsonl --format jsonl
```

The source registry record must include `source_id`, `name`, `canonical_host`, `lane`,
`review_status`, `source_status`, and a `license` or `terms_url`. Review and verify reuse
rights before marking any source approved. Bulk importer tests run separately from crawler tests.
