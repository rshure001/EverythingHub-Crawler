# Media extraction preflight

The crawler discovers media metadata only. This preflight does not download media, invoke OCR/transcription/video extraction, or change license and reuse-rights fields.

## Required review before extraction

Add a review to `crawler/media-source-terms.json` only after a human has reviewed the source's current terms and confirmed that the proposed extraction is permitted. Each review must include:

- `origin`: exact media origin (scheme, host, and effective port).
- `extraction_permission`: boolean `true`.
- `source_terms_status`: `allow_extraction`.
- `source_terms_url`: public canonical URL for the terms reviewed.
- `source_terms_sha256`: SHA-256 digest of the exact terms snapshot reviewed.
- `source_terms_checked_at`: timezone-aware ISO-8601 timestamp.

The review expires after 90 days. Duplicate origin reviews are ambiguous and fail closed. Denied, malformed, stale, or missing reviews never authorize extraction.

## Fresh robots decision

A candidate must have `robots_allowed=true`, remain `robots_allowed_not_fetched`, and have a `robots_checked_at` timestamp no older than 24 hours. Each newly admitted or rediscovered candidate receives a fresh timestamp from the crawler's robots check. Legacy candidates without that timestamp remain discoverable but are deferred until rediscovered and checked again.

## Important separation of permissions

- Robots permission is not source-terms permission.
- Permission to extract is not permission to redistribute.
- The catalog continues to retain `rights_status=unknown` and `usage_rights=Unknown`.
- This preflight reports aggregate eligibility only. It does not create a download queue or fetch media bytes.
- Any future extraction worker must re-check robots and source terms immediately before access, obey per-host rate limits and size limits, and keep extracted text out of public search until a separate publication/rights review authorizes it.

The default registry is intentionally empty. No source is pre-approved.
