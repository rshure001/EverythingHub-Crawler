# Free Vercel fallback homepage

This directory contains the exact static homepage source used by the existing free Vercel fallback project `everythinghub-homepage-fixed`.

## Deployment mapping

- Repository source: `vercel-homepage/fallback/index.html`
- Vercel deployment file: `src/index.html`
- Preview/staging URL: `https://everythinghub-homepage-fixed-rshure001-8053.vercel.app/src/index.html`
- Production project URL: `https://everythinghub-homepage-fixed.vercel.app/`
- Search backend: `https://everythinghub-search-backend.vercel.app`

The page intentionally keeps the approved artwork resource unchanged:

`https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/resources/everythinghub-approved-octopus.png`

No DNS records are changed by this source or deployment workflow.

## Media rights

Image previews are restricted to Wikimedia Commons metadata that passes the same conservative allowlist as `../media-preview-policy.js`: public domain, CC0, or canonical CC BY / CC BY-SA licenses. CC BY and CC BY-SA require a creator credit. The visible card includes the Commons source, creator where required, and a link to the license.

Unverified and non-commercial / no-derivatives licenses are not embedded. Source-page links remain available separately.

## Deployment rule

AppDeploy acceptance is opt-in through the GitHub repository variable `APPDEPLOY_ACCEPTANCE_ENABLED=true`. When that variable is absent, the browser acceptance workflow tests the free Vercel fallback only. Do not purchase AppDeploy credits or change DNS to deploy this fallback.
