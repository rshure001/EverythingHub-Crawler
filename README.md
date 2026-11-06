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
GitHub Actions is configured for a five-minute schedule and manual dispatch. GitHub may delay scheduled runs, so the schedule is not a guarantee of 1,000 pages per hour.

## Search integration
The crawler currently writes its own repository index. Publishing to Everything Hub's production search index is not enabled until the production ingestion endpoint and authentication requirements are verified.