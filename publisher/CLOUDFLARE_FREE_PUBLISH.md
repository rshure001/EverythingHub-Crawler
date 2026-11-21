# Free publishing route — Cloudflare Pages

This is the zero-hosting-cost static publishing fallback for EverythingHub. It does not replace the existing search backend, crawler, or approved homepage artwork.

## One-time account setup

1. Create or select a Cloudflare Pages account/project using the Cloudflare dashboard.
2. Create an API token with **Account / Cloudflare Pages / Edit** permission for the correct account.
3. In this GitHub repository, add these Actions secrets:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
4. Run **Actions → EverythingHub Free Publisher (Cloudflare Pages) → Run workflow** and type `PUBLISH`.
5. Verify the resulting `https://everythinghub.pages.dev/` homepage, `robots.txt`, and `sitemap.xml`.
6. Only after the pages.dev release is verified, attach `EverythingHub.org` as a custom domain in Cloudflare Pages and complete the DNS instructions shown by Cloudflare. This workflow does not change DNS.

## What the workflow does

- Rebuilds and validates the public crawler catalog and media catalog.
- Checks the immutable homepage release manifest and SHA-256 digests.
- Stages the search catalog and machine-readable release health into `site/`.
- Uploads the validated `site/` directory to Cloudflare Pages.
- Verifies the deployed homepage, robots file, and sitemap over HTTPS.

The workflow is manual-only and requires the explicit `PUBLISH` input. It never changes DNS, the search database, or Dog Tag Day resources.

## Free-tier boundary

Cloudflare documents 500 Pages builds per month, one concurrent build, up to 20,000 files per site, and a 25 MiB maximum individual file on the Free plan. Static asset requests are free and unlimited; Pages Functions have separate Workers quotas. This publisher deploys static files only and does not move the EverythingHub search API onto Pages Functions.

A Cloudflare account, account ID, and scoped API token are required. The token must be stored as a GitHub Actions secret and must never be committed to the repository.
