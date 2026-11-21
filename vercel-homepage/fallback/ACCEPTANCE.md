# Everything Hub fallback acceptance contract

This file records the live acceptance requirements for the free Vercel fallback. A deployment is not considered ready until the browser workflow has inspected the public site at desktop and mobile widths and retained screenshots plus a JSON report.

## Live routes

- Homepage: https://everythinghub-homepage-fixed.vercel.app/
- Search backend: https://everythinghub-search-backend.vercel.app/
- Approved artwork resource (must remain unchanged): https://everythinghub-search-engine-4x25f8.v2.appdeploy.ai/resources/everythinghub-approved-octopus.png

## Required checks

1. Approved octopus artwork and branding load from the exact approved resource.
2. Search input and all eight categories work.
3. Rolex, Las Vegas and NASA searches show relevant visible catalog/resource cards and ordinary web results.
4. Images show real thumbnails with source, creator where supplied, and explicit license attribution; CC BY/CC BY-SA items require creator attribution and a license link.
5. Videos show real video cards, titles, thumbnails and source links.
6. Top Stories displays current public-source headlines; each news tab and Refresh works.
7. Mobile has no horizontal overflow, clipping, broken images or overlapping controls.
8. No browser errors break core functions.
9. Core links and navigation work.

## Catalog accounting

External link-only directories are reported separately from page-derived catalogs and records. They are not represented as fetched source pages, rights-cleared content or commercially eligible records. The current curated directories are Rolex official watch collections, Las Vegas official business/place/visitor resources, and NASA official science/data/media resources. No source-page content is copied or fetched for these directories.

## Deployment constraints

- Use the existing free Vercel fallback while AppDeploy publishing is blocked.
- Do not purchase AppDeploy credits.
- Do not change DNS.
- Do not replace or modify the approved artwork resource.
- Do not claim the original AppDeploy URL is fixed unless it is separately deployed and browser-verified.
- Do not notify the user that Everything Hub is done until every required live browser check passes.
