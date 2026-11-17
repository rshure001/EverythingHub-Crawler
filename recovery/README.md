# Everything Hub — Independent Search Recovery Service

This is a small, read-only search API that runs directly from the crawler's
committed static index. It does **not** use Supabase, PostgreSQL, the crawler
database, paid APIs, or external search providers at request time.

## Run locally

From the repository root:

```sh
docker build -f recovery/Dockerfile -t everythinghub-static-search .
docker run --rm -p 8080:8080 everythinghub-static-search
```

Or with Python 3.12+:

```sh
EH_INDEX_DIR=./public-search PORT=8080 python recovery/static_search_api.py
```

## Test

```sh
curl http://localhost:8080/healthz
curl 'http://localhost:8080/search?q=NASA&limit=10'
curl 'http://localhost:8080/search?q=Rolex&limit=10'
```

Health returns the number of pages loaded. Search supports `q`, `limit`,
`offset`, and an optional `tentacle` lane. Results rank title matches above
description, URL, and body matches. Requests are read-only.

## Recovery behavior

- The service starts from the versioned `public-search/shard-*.json` snapshot.
- Supabase can be down and this API still starts and searches.
- A new crawler commit updates the index snapshot; rebuild/restart the container
  to load the new snapshot.
- Keep the last known-good image/snapshot so rollback is simply redeploying it.
- `/healthz` reports readiness and indexed-page count.

## Production guardrails

This API is a recovery/search service for the existing public metadata index,
not a complete Google replacement. It does not fetch pages, crawl, or provide
live news/shopping data. Put it behind HTTPS and a rate-limiting reverse proxy
before exposing it publicly. Set `EH_CORS_ORIGIN` to the exact homepage origin
instead of the default wildcard. No paid infrastructure is provisioned by this
repository change.

This commit adds the service source and container recipe only. It does not
deploy a server, change production traffic, alter the approved homepage artwork,
or modify Supabase, Dog Tag Day, or Story Wall data.
