# Everything Hub independent Vercel search backend

The source for the existing everythinghub-search-backend Vercel project is
kept here so production behavior is reviewable and testable in GitHub.

## Runtime

- Entry points: src/api/search.js and src/api/health.js
- Runtime: Node.js 26, built-in modules only
- Catalog source: the versioned public-search manifest and either its optional
  compressed catalog or bounded SHA-pinned shards from the same immutable Git commit
- Database dependency: none
- External discovery provider: Bing RSS, cached per query for two minutes

## Integrity and safety

- Fetches the Git ref first, then retrieves the manifest and all catalog
  objects from that same immutable commit SHA; shard requests never follow a
  moving main-branch URL.
- Verifies manifest version, timestamp, page count, shard count, compressed
  byte count, and SHA-256 before accepting a gzip catalog.
- When the optional gzip file is absent, too large, or fails integrity checks,
  the backend loads the bounded shard set and verifies each shard's byte count,
  SHA-256, page count, schema, URL safety, and robots-denied exclusions.
- Bounds the optional gzip catalog to 16,000,000 bytes and decoded JSON to
  64,000,000 bytes. The sharded path supports up to 180,000 pages, 640 shards, 350,000 compressed bytes and 500,000 decoded bytes per shard, 80,000,000 aggregate compressed bytes, and 320,000,000 aggregate decoded bytes. Shard
  downloads use a 32-worker pool and per-request timeouts.
- Keeps a last-known-good in-memory catalog for up to one hour if GitHub has a
  transient outage; responses identify stale catalog use.
- Does not fetch or download indexed source pages; it reads only the
  repository's generated index shards and optional compressed catalog.

## Pagination

The first response reserves up to ten slots for Bing and returns the first
forty crawler results when the page size is fifty. Subsequent offset=50
requests start at crawler result forty, not crawler result fifty. This keeps
the Bing supplement from creating a ten-result gap and prevents page-two
duplicates. Regression tests cover pages one through three.

## Deployment packaging

- The direct Vercel upload maps vercel-search-backend/src/api/ to src/api/,
  vercel-search-backend/src/lib/ to src/lib/, and the package manifest to
  src/package.json in the deployment payload.
- That folder's package.json explicitly pins CommonJS and Node.js 26.x; the
  backend uses built-in Node modules only.
- Keep the package manifest in Git so direct Vercel API deployments remain
  reproducible and reviewable.

## Tests

Run from the repository root:

    node --test vercel-search-backend/test/*.test.js

The GitHub workflow runs these tests on pull requests and main-branch changes.


## Page-derived catalog search preview

The independent search API reads the commit-pinned data/page-derived-catalogs manifest, registry.json.gz, and records.json.gz artifacts. It verifies each compressed artifact's byte count and SHA-256, checks unique catalog and record IDs, and rejects commercially eligible records in candidate staging.

Matching catalog candidates are returned separately as catalogResults; the homepage renders them ahead of ordinary page results. Cards include catalog type, matched record titles, source-page and record counts, and a source link. Record descriptions and full attributes are not copied into these public preview cards.

These are candidate previews only. Source terms and reuse rights remain unreviewed, commercial_eligible remains false, and recordsPublished remains zero. The preview endpoint does not mark records as production-published or rights-cleared. The Vercel project still requires its existing controlled deployment path; GitHub auto-deployment is not configured.


Freshness guard: catalog preview metadata includes the source-index page count and SHA-256. The search API compares both with the active page-search snapshot and labels the preview stale when they differ or when freshness cannot be checked. The homepage's indexed counter uses indexedPages (source pages), not the matching-result count.


Freshness guard: candidate previews are marked current only when the source-index page count and update timestamp match the active page-search snapshot within 60 seconds. The source-index SHA and page-search manifest SHA are preserved as separate provenance values; they are not compared as if they represented the same file.


Relevance ranking: commerce catalogs with matching product/brand/model records and geographic catalogs with matching place records receive type-aware boosts. Article/resource/media catalogs receive a smaller title-only catalog bonus unless the query explicitly asks for content, reducing incidental mentions in Rolex and Las Vegas searches.




## Video search and media rights

- Video search uses the official Wikimedia Commons API at commons.wikimedia.org/w/api.php; it does not scrape YouTube HTML, use undocumented YouTube search feeds, or use Bing's undocumented video RSS endpoint.
- The backend fetches metadata only and never downloads or re-hosts video/audio files.
- It checks robots.txt for the Commons API host before querying, serializes requests per host, applies a minimum one-second interval, uses maxlag=5, and caches results for five minutes. Thumbnail files are loaded directly by the user's browser from the API-provided upload.wikimedia.org preview URL; the backend does not fetch those assets.
- Only video files with CC0, public-domain, CC BY or CC BY-SA terms are displayed. Noncommercial, no-derivatives, unknown-license and uncredited CC BY/CC BY-SA items are excluded.
- Video cards link to the original Commons file page and display the license plus creator/credit attribution. The license/reuse link is visible on every card.
- NASA Image and Video Library was evaluated but disabled fail-closed because images-assets.nasa.gov returned HTTP 403 for robots.txt, so preview-asset access could not be verified.
- The live preflight is node vercel-search-backend/test/videos-live-smoke.cjs; it checks the Commons robots policy and requires at least 15 usable, openly licensed NASA-matching video records before production browser acceptance.
