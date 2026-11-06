# Everything Hub Crawler

Independent, free-tier crawler for the Everything Hub search project.

The scheduled GitHub Actions workflow discovers pages from configured XML sitemaps, fetches a bounded batch politely, and stores normalized records in `data/index.json` as a versioned repository artifact.

## Status

Initial crawler scaffold. Search-index API publishing is deliberately not enabled until its supported ingestion endpoint and authentication requirements are verified.

## Configuration

Edit `crawler/seeds.txt` to add sitemap URLs. The crawler uses a maximum of 80 pages per run (8 workers × 10 pages), applies request timeouts, and identifies itself with a descriptive user agent.

## Run locally

`python crawler/crawl.py`

## Schedule

GitHub Actions runs every five minutes when GitHub honors the schedule. GitHub may delay scheduled runs, so this does not guarantee 1,000 pages per hour.