# Public search catalog

The crawler publishes a versioned, SHA-256-pinned catalog in `manifest.json`.

## Transports

- `catalog-v1.json.gz` is an optional compact transport for the independent Vercel search backend and the browser client. The builder, validator, Vercel reader, and browser client allow up to 16,000,000 compressed bytes and 64,000,000 decoded bytes; the manifest records the actual size and digest for each release.
- The browser client verifies the gzip digest and page count, then enforces a 64,000,000-byte decompressed-size limit. If gzip is absent, too large, or invalid, it falls back to the verified shard list with bounded concurrency. The browser client accepts compressed shards up to 64,000,000 aggregate bytes, legacy raw shards up to 100,000,000 bytes, and decoded shard data up to 256,000,000 bytes. The server-side Vercel reader supports 80,000,000 compressed bytes and 320,000,000 decoded bytes.
- Individual `shard-NNNN.json.gz` files are SHA-256 pinned and capped at 350,000 compressed bytes and 500,000 decoded bytes. Legacy raw `shard-NNNN.json` files remain readable during migration. The browser client intentionally remains bounded to 512 shards and 130,000 pages, with a 256,000,000-byte decoded-data ceiling to limit browser memory; the Vercel server-side reader is bounded to 640 shards and 150,000 pages.
- The Supabase Edge Function intentionally retains a separate 6,000,000-byte runtime cap and 30,000,000-byte decompression cap to protect its CPU budget. On database recovery it delegates the larger verified catalog to the Vercel backend; do not raise the Edge limits just because the browser/Vercel transport is 10 MB.

## Crawl and reuse policy

The crawler excludes robots-denied and noindex pages, fails closed when robots policy cannot be established, and respects source-specific crawl delays and rate limits. Media discovery retains metadata only; a discovered URL is not permission to download, extract, or reuse media. Source-term approval is required before extraction.
