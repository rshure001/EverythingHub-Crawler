# Everything Hub Vercel homepage source

This is the source HTML for the `everythinghub-homepage-fixed` Vercel project. The production project root is `src/`, so this file is deployed as `src/index.html` and the crawler catalog is deployed under `src/search-index/`.

## Crawler catalog
- Source of truth: `public-search/manifest.json` and its 14 `shard-*.json` files.
- Repository catalog as of 2026-11-16 05:57 UTC: 2,654 pages in 14 shards.
- Last verified production deployment still serves 2,592 pages (manifest last modified 2026-11-16 04:11 UTC); 62 newer repository records are not yet live.
- The homepage loads `search-index/manifest.json`, fetches all listed shards, and maps crawler fields into the local ranking index. The current production catalog is an older snapshot; the repository's newer 2,654-page catalog has not yet been published.
- Search result labels identify crawler hits as `EverythingHub index`; independent web providers remain supplemental.

## Production deployment
- Vercel project: `everythinghub-homepage-fixed`
- Project ID: `prj_cbClZyDtkrdJrcTZrqkPvbfLPazg`
- Verified deployment: `dpl_AywBVASPaQHe5XjFQUG3djN6FBRS`
- Production aliases assigned by Vercel: `everythinghub.ink`, `everything.dogtagday.org`, and `everythinghub-homepage-fixed.vercel.app`

The approved artwork and visual layout were not changed in this update. Only the crawler-catalog loader was connected to the published search shards.

## Custom-domain status
Vercel has the `everythinghub.ink` alias assigned and marks domain ownership as verified, but its DNS configuration is currently `misconfigured: true`. The authoritative nameservers are Namecheap (`dns1.registrar-servers.com`, `dns2.registrar-servers.com`); the observed apex A record is `192.64.119.157`. Vercel's current recommended IPv4 values are `216.198.79.1` and `64.29.17.1` (with `76.76.21.21` as an alternate recommendation). The custom hostname timed out in the browser check, while the canonical Vercel hostname and all 14 catalog shards returned HTTP 200. No DNS changes were made from this session.

## GitHub publishing status
The Vercel project currently has no verified GitHub auto-deploy connection. The latest Vercel production deployment predates the 2026-11-16 05:56 UTC crawler commit, and the production manifest still reports 2,592 pages. Do not claim the repository's 2,654-page catalog is live until a new deployment is created and its manifest and browser search are verified. The production database importer remains separate and is blocked by Supabase PostgreSQL WAL recovery (`57P03`).
