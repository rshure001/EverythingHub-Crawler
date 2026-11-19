# Everything Hub Vercel homepage

## Source of truth
- Homepage: `vercel-homepage/index.html`
- Approved artwork: `vercel-homepage/assets/everythinghub-approved.png`
- Media preview policy: `vercel-homepage/media-preview-policy.js`
- Category landing pages: `vercel-homepage/discover/**`
- Search catalog: `public-search/manifest.json` and the shards listed there.

The homepage uses same-origin paths for the approved artwork (`/assets/everythinghub-approved.png`) and media policy (`/media-preview-policy.js`). Do not hard-code a deployment-specific preview hostname into the homepage.

## Search catalog
The manifest is authoritative for page count, shard count, timestamps, and SHA-256. Counts change with each successful crawl; always read the current manifest instead of copying a count into application code or deployment configuration.

Last verified repository snapshot: **2026-11-19 20:06:48 UTC**
- Indexed pages: **16,665**
- Search shards: **96**
- Unique indexed hosts: **35**
- Discovered URLs: **47,331**
- Frontier waiting: **17,864**

These are snapshot measurements, not permanent constants.

## Vercel deployment
- Project: `everythinghub-homepage-fixed`
- Project ID: `prj_cbClZyDtkrdJrcTZrqkPvbfLPazg`
- Project source files are packaged under `src/` for the Vercel deployment.
- `src/vercel.json` forwards topic, sitemap, and robots routes to the Everything Hub site Edge Function.
- The GitHub-to-Vercel auto-deploy connection is not currently configured; a READY deployment must be checked against its exact source files and runtime before it is called live.

## Production verification
The browser workflow checks:
- One search form and one search input.
- All eight category controls.
- The approved artwork and media-preview policy.
- Media links, attribution and preview licensing.
- Rolex and NASA search.
- Image and video modes.
- Mobile layout and browser errors.

The canonical Vercel hostname is the deployment verification target. Custom domains must be checked separately because DNS configuration can differ from Vercel's project alias state.
