const assert = require("node:assert/strict");
const test = require("node:test");

const {
  MAX_MANIFEST_BYTES,
  MAX_CATALOG_GZIP_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_SHARD_BYTES,
  MAX_SHARD_UNCOMPRESSED_BYTES,
  MAX_TOTAL_SHARD_BYTES,
  MAX_TOTAL_COMPRESSED_SHARD_BYTES,
  MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES,
  MAX_SHARD_CONCURRENCY,
  validateShardManifest
} = require("../src/lib/catalog");

test("Vercel catalog loader accepts the current expanded search manifest", () => {
  assert.equal(MAX_MANIFEST_BYTES, 256_000);
  assert.ok(MAX_MANIFEST_BYTES >= 129_293);
});

test("Vercel catalog loader accepts the current 14,112,415-byte compressed catalog with headroom", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 16_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 14_112_415);
});

test("Vercel catalog loader exposes the 180,000-page bounded shard envelope", () => {
  assert.equal(MAX_INDEXED_PAGES, 180_000);
  assert.equal(MAX_SHARD_COUNT, 768);
  assert.equal(MAX_SHARD_BYTES, 350_000);
  assert.equal(MAX_SHARD_UNCOMPRESSED_BYTES, 500_000);
  assert.equal(MAX_TOTAL_SHARD_BYTES, 100_000_000);
  assert.equal(MAX_TOTAL_COMPRESSED_SHARD_BYTES, 100_000_000);
  assert.equal(MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, 400_000_000);
  assert.equal(MAX_SHARD_CONCURRENCY, 32);
});

test("Vercel shard validator accepts the full 180,000-page capacity envelope", () => {
  const shards = Array.from({ length: 768 }, (_, index) => ({
    file: "shard-" + String(index + 1).padStart(4, "0") + ".json.gz",
    pages: index < 288 ? 235 : 234,
    bytes: 130_208,
    uncompressed_bytes: 500_000,
    sha256: "a".repeat(64)
  }));
  const manifest = {
    version: 1,
    integrity: "sha256",
    updated_at: Math.floor(Date.now() / 1000),
    total_pages: 180_000,
    shard_count: 768,
    shards
  };
  const checked = validateShardManifest(manifest);
  assert.equal(checked.totalPages, 180_000);
  assert.equal(checked.totalBytes, 99_999_744);
  assert.equal(checked.totalUncompressedBytes, 384_000_000);
});
