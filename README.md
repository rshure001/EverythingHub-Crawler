# Everything Hub Crawler

Independent crawler for the Everything Hub search project.

## Current configuration
- 49 curated public starting sites in `crawler/seeds.txt` (these are a newly assembled set, not a recovered historical list).
- 8 concurrent workers.
- Maximum 80 fetched pages per run.
- Maximum discovered URL queue: 50,000 (persistent between runs).
- Maximum link depth: 3.
- Prioritizes same-host HTML links discovered from fetched pages; sitemap XML is not preloaded because it previously consumed the run budget without producing searchable pages.
- Checks each site's `robots.txt` before fetching.
- Stores normalized page records in `data/index.json`.

## Run locally
`python crawler/crawl.py`

## Automation
GitHub Actions is configured for a 15-minute schedule and manual dispatch. The workflow does not trigger on pushes, preventing crawler data commits from recursively launching more crawls. GitHub may delay scheduled runs. Each run attempts at most 80 fetches; actual indexed pages can be lower due to robots rules, errors, and non-HTML responses.

## Search integration
The crawler currently writes its own repository index. It is not yet connected to Everything Hub's production search index. Do not publish or expose crawler records as production search results until the production ingestion endpoint, authentication, and indexing format are verified.