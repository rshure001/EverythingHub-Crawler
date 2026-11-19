# Everything Hub independent Vercel search backend

The source for the existing everythinghub-search-backend Vercel project is
kept here so production behavior is reviewable and testable in GitHub.

## Runtime

- Entry points: src/api/search.js and src/api/health.js
- Runtime: Node.js 26, built-in modules only
- Catalog source: the versioned public-search manifest and gzip catalog on
  the repository's main branch
- Database dependency: none
- External discovery provider: Bing RSS, cached per query for two minutes

## Integrity and safety

- Verifies manifest version, timestamps, page count, shard count, compressed
  size, and SHA-256 before accepting the catalog.
- Rejects invalid URLs and any record explicitly marked robots-denied.
- Limits compressed and decompressed catalog sizes and request timeouts.
- Keeps a last-known-good in-memory catalog for up to one hour if GitHub has a
  transient outage; responses identify stale catalog use.
- Does not fetch or download indexed source pages.

## Pagination

The first response reserves up to ten slots for Bing and returns the first
forty crawler results when the page size is fifty. Subsequent offset=50
requests start at crawler result forty, not crawler result fifty. This keeps
the Bing supplement from creating a ten-result gap and prevents page-two
duplicates. Regression tests cover pages one through three.

## Deployment packaging

- The direct Vercel upload mirrors `vercel-search-backend/src/` under `src/vercel-search-backend/src/` in the deployment payload.
- That folder's `package.json` explicitly pins CommonJS and Node.js 26.x; the backend uses built-in Node modules only.
- Keep the package manifest in Git so direct Vercel API deployments remain reproducible and reviewable.

## Tests

Run from the repository root:

    node --test vercel-search-backend/test/*.test.js

The GitHub workflow runs these tests on pull requests and main-branch changes.
