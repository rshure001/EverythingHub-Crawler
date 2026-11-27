# Everything Hub infrastructure operations runbook

Last reviewed: 2026-11-26

This runbook separates the crawler, catalog-entry pipeline, search release, hosting, and database recovery so that a healthy component is never mistaken for a fully published product.

## Service boundaries

| Component | Repository location / workflow | Responsibility | Safe success signal |
|---|---|---|---|
| URL crawler | `.github/workflows/crawl.yml` | Discover and fetch permitted pages; update the canonical crawl index | Completed crawl job plus validated index/shards |
| Catalog inventory | `.github/workflows/retroactive-catalog-index.yml` | Analyze indexed pages and build catalog candidates | Analysis, deduplication, registry and source-policy validation all pass |
| Catalog seed expansion | `.github/workflows/catalog-seed-expansion.yml`, `crawler/catalog_seed_expansion.py` | Bounded, policy-approved catalog landing-page metadata discovery | Expansion ledger updated; source and robots checks pass |
| Public search data | `public-search/manifest.json` and shards | Static, SHA-256-verified search corpus | Manifest and every shard validate; search smoke test returns results |
| Site release | `site/`, `publisher/publish.py`, `site/release-manifest.json` | Validate and publish the homepage and browser client | Release manifest validates, deployment completes, live files match |
| Production database | Supabase project `everything-hub-control` | Durable application/search state | Authenticated read/write health checks succeed; migrations are verified |
| Hosting fallback | `.github/workflows/everythinghub-self-host.yml` | Publish approved static site files to Cloudflare Pages | Required credentials exist, deploy succeeds, public URL is fetched and verified |
| Operations checks | `crawl-watchdog.yml`, `everything-hub-live-diagnostics.yml` | Detect stale schedules, failed runs, and unavailable endpoints | Reports distinguish healthy, failed, stale, blocked, and unobserved states |

## Release gate

A release is not complete until all of these checks pass:

1. `python publisher/publish.py --check` validates exact file membership, byte sizes, and SHA-256 hashes.
2. The hosting workflow uploads the approved artifact and reports a successful deployment.
3. The deployed homepage and search client are fetched from the published URL.
4. Their contents match the approved repository files.
5. The public search smoke test returns usable results from the intended index or explicitly identified fallback.

A green crawler run does not satisfy the release gate. A successful workflow job does not, by itself, prove that the custom domain is live.

## Catalog-entry publication gate

Keep these quantities separate in reports:

- catalog candidates discovered;
- catalog landing pages reviewed and policy-approved;
- entry metadata extracted;
- entries deduplicated and validated;
- entries staged;
- entries published to the public search index;
- entries confirmed searchable.

Do not count a staged or validated record as published. Preserve source URLs and provenance for every published entry. Keep robots, source terms, and permitted-field checks fail-closed. The bounded seed expander is intentionally limited to ten landing pages per run; do not raise its limit until request pacing, queue freshness, retry behavior, and source-policy coverage are benchmarked.

## Recovery order

1. **Protect data.** Do not reset, restore, migrate, or import into a database that is still in recovery or refusing connections.
2. **Keep independent crawling observable.** Check the latest scheduled crawl step, not just the overall workflow conclusion. Verify index page count, shard count, hashes, and update time.
3. **Repair the release contract separately.** Regenerate the release manifest from the exact approved `site/` files, then run the publisher check before triggering any deployment.
4. **Verify hosting independently.** Check credentials and provider configuration without printing secrets. If the Cloudflare Pages workflow cannot authenticate, report that blocker rather than claiming deployment.
5. **Restore database service through provider recovery.** Once connections succeed, inspect migration state read-only first, then run only reviewed migrations and isolated import tests before production writes.
6. **Re-enable database-dependent functions only after verification.** Preserve static search fallback while the database is unavailable.

## Cost and safety guardrails

- Use existing free workflows and open-source tooling where practical.
- Do not create paid resources, upgrade plans, or incur usage charges without explicit approval.
- Never bypass robots.txt, source terms, authentication, paywalls, rate limits, or provider protections.
- Never expose API keys, tokens, or credentials in logs or reports.
- Do not publish unvalidated catalog records or overwrite production data to make a status check pass.

## Status vocabulary

- **PASS:** the exact check ran and succeeded.
- **FAILED:** the check ran and returned a failure.
- **BLOCKED:** a prerequisite prevented the check from running.
- **STALE:** the last successful result is older than its documented freshness threshold.
- **UNOBSERVED:** there is no reliable result. This is not equivalent to healthy.
