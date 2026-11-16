# Everything Hub Vercel homepage source

This is the source HTML for the `everythinghub-homepage-fixed` Vercel project. The production project root is `src/`, so this file is deployed as `src/index.html` and the crawler catalog is deployed under `src/search-index/`.

## Crawler catalog
- Source of truth: `public-search/manifest.json` and its 14 `shard-*.json` files.
- Current catalog: 2,592 pages in 14 shards.
- The homepage loads `search-index/manifest.json`, fetches all listed shards, and maps crawler fields into the local ranking index.
- Search result labels identify crawler hits as `EverythingHub index`; independent web providers remain supplemental.

## Production deployment
- Vercel project: `everythinghub-homepage-fixed`
- Project ID: `prj_cbClZyDtkrdJrcTZrqkPvbfLPazg`
- Verified deployment: `dpl_AywBVASPaQHe5XjFQUG3djN6FBRS`
- Production aliases assigned by Vercel: `everythinghub.ink`, `everything.dogtagday.org`, and `everythinghub-homepage-fixed.vercel.app`

The approved artwork and visual layout were not changed in this update. Only the crawler-catalog loader was connected to the published search shards.
