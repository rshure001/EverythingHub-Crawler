# Everything Hub Crawler

Independent, polite crawler and database-native indexing worker for Everything Hub.

## Existing JSON crawler

- 49 curated public starting sites in `crawler/seeds.txt` (a newly assembled set, not a recovered historical list).
- 8 concurrent workers and a maximum of 80 fetch attempts per run.
- Persistent frontier with a 50,000 discovered-URL ceiling.
- Maximum link depth: 3.
- Same-host HTML link discovery; sitemap XML is not preloaded.
- Checks each site's `robots.txt`; if robots rules cannot be read, the crawler fails closed for that host.
- Stores normalized page records in `data/index.json` and resumable frontier state in `data/frontier.json`.

## Database-native worker

`database/crawl_worker.py` is the direct database path. It seeds the PostgreSQL
queue, claims pending URLs with row locking, checks robots.txt, fetches HTML,
writes page records directly to `eh_documents`, and enqueues newly discovered
same-host links. It does not use the JSON file as an intermediate handoff.

The first cut intentionally runs as one worker with 1.5-second per-host pacing,
80 indexed HTML pages per run, and depth 3. Lane assignment is currently a
hostname heuristic; do not scale to multiple workers until host coordination
and lane distribution have been reviewed.

See [database/README.md](database/README.md) for setup and run instructions.

## Existing JSON crawler: run locally

```bash
python -m py_compile crawler/crawl.py crawler/report.py
python -m unittest discover -s crawler -p 'test_*.py' -v
python crawler/crawl.py
python crawler/report.py
```

## Automation and observability

The JSON crawler's GitHub Actions workflow runs on a 15-minute schedule and
supports manual dispatch. It validates configuration, executes unit tests,
crawls up to 80 pages, and publishes a compact health report. It does not
trigger on pushes, preventing crawler data commits from recursively launching
more crawls. GitHub may delay scheduled runs.

The database smoke-test workflow now also runs the database-native worker
against an isolated temporary PostgreSQL schema and a local HTTP fixture. It
checks that a fetched page is inserted into `eh_documents`, its queue item is
completed, and a discovered same-host link is added to the queue.

## Production status

The database-native worker and search API are implemented and covered by an
isolated CI smoke test. That test does **not** prove that a persistent production
database or private worker host is currently running, nor does it switch live
Everything Hub search traffic. Verify the owner's existing private host and
database before production cutover.

## Scale and safety

Respect robots rules and host-level limits. Do not bypass logins, paywalls,
CAPTCHAs, access controls, or site restrictions. The crawler and database are
separate from Dog Tag Day.
