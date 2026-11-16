# Everything Hub Vercel homepage source

This is the source HTML for the `everythinghub-homepage-fixed` Vercel project. The production project root is `src/`, so this file is deployed as `src/index.html` and the crawler catalog is deployed under `src/search-index/`.

## Crawler catalog
- Source of truth: `public-search/manifest.json` and its 14 `shard-*.json` files.
- Repository catalog as of 2026-11-16 06:08 UTC: 2,654 pages in 14 shards across 31 hosts.
- Production deployment `dpl_2LExaSgucu7XGWARGsU69KJTiWFY` now serves the same 2,654-page catalog. The canonical Vercel hostname and all 14 shard URLs returned HTTP 200.
- The homepage loads `search-index/manifest.json`, fetches all listed shards, and maps crawler fields into the local ranking index. The 2,654-page catalog is now published on the canonical Vercel production hostname.
- Search result labels identify crawler hits as `EverythingHub index`; independent web providers remain supplemental.

## Production deployment
- Vercel project: `everythinghub-homepage-fixed`
- Project ID: `prj_cbClZyDtkrdJrcTZrqkPvbfLPazg`
- Verified deployment: `dpl_AywBVASPaQHe5XjFQUG3djN6FBRS`
- Production aliases assigned by Vercel: `everythinghub.ink`, `everything.dogtagday.org`, and `everythinghub-homepage-fixed.vercel.app`

The approved artwork and visual layout were not changed in this update. Only the crawler-catalog loader was connected to the published search shards.

## Custom-domain status
Vercel lists `everythinghub.ink` and `everything.dogtagday.org` as aliases on the production deployment, and the project-domain lookup reports `verified: true`. However, the current DNS check still reports `misconfigured: true`: authoritative nameservers are Namecheap (`dns1.registrar-servers.com`, `dns2.registrar-servers.com`), the observed apex A record is `192.64.119.157`, and Vercel recommends `76.76.21.21`. Fetch attempts through both custom hostnames failed, while the canonical Vercel production hostname and all 14 catalog shards returned HTTP 200. No DNS changes were made.

## GitHub publishing status
The Vercel project currently has no verified GitHub auto-deploy connection. The 2,654-page catalog was published through a controlled file-upload deployment from the GitHub snapshot; the canonical production hostname, homepage structure, and all 14 shards were verified. Future crawler commits will not automatically publish until a GitHub-to-Vercel connection or authenticated publishing workflow is configured. The production database importer remains separate and is blocked by Supabase PostgreSQL WAL recovery (`57P03`).
