# Public search catalog

The crawler publishes a versioned, SHA-256-pinned catalog in `manifest.json`.

## Transports

- `catalog-v1.json.gz` is an optional compact transport for the independent Vercel search backend and the browser client. The builder, validator, Vercel reader, and browser client allow up to 16,000,000 compressed bytes and 64,000,000 decoded bytes; the manifest records the actual size and digest for each release.
- The browser client verifies the gzip digest and page count, then enforces a 64,000,000-byte decompressed-size limit for the optional compact catalog. If gzip is absent, too large, or invalid, it falls back to the SHA-256-verified shard list with bounded concurrency. The browser client and Vercel server-side reader accept up to 150,000,000 aggregate compressed shard bytes and 540,000,000 aggregate decoded shard bytes.
- Individual `shard-NNNN.json.gz` files are SHA-256 pinned and capped at 350,000 compressed bytes and 600,000 decoded bytes. Legacy raw `shard-NNNN.json` files remain readable during migration. Both the browser client and Vercel server-side reader are bounded to 900 shards and 250,000 pages, with a 540,000,000-byte aggregate decoded-data ceiling. At the time of this update, the 212,941-page release uses 668 shards and 400,064,127 decoded bytes, within those validated limits.
- The Supabase Edge Function intentionally retains a separate 6,000,000-byte runtime cap and 30,000,000-byte decompression cap to protect its CPU budget. On database recovery it delegates the larger verified catalog to the Vercel backend; do not raise the Edge limits just because the browser/Vercel transport is 10 MB.

## Crawl and reuse policy

The crawler excludes robots-denied and noindex pages, fails closed when robots policy cannot be established, and respects source-specific crawl delays and rate limits. Media discovery retains metadata only; a discovered URL is not permission to download, extract, or reuse media. Source-term approval is required before extraction.
