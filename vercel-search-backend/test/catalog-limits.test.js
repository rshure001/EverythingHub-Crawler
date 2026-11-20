const assert = require("node:assert/strict");
const test = require("node:test");

const {
  MAX_CATALOG_GZIP_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_SHARD_BYTES,
  MAX_SHARD_UNCOMPRESSED_BYTES,
  MAX_TOTAL_SHARD_BYTES,
  MAX_TOTAL_COMPRESSED_SHARD_BYTES,
  MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES,
  MAX_SHARD_CONCURRENCY
} = require("../src/lib/catalog");

test("Vercel catalog loader accepts the current 14,112,415-byte compressed catalog with headroom", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 16_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 14_112_415);
});

test("Vercel catalog loader accepts the 125,000-page bounded shard envelope", () => {
  assert.equal(MAX_INDEXED_PAGES, 125_000);
  assert.equal(MAX_SHARD_COUNT, 512);
  assert.equal(MAX_SHARD_BYTES, 350_000);
  assert.equal(MAX_SHARD_UNCOMPRESSED_BYTES, 500_000);
  assert.equal(MAX_TOTAL_SHARD_BYTES, 100_000_000);
  assert.equal(MAX_TOTAL_COMPRESSED_SHARD_BYTES, 64_000_000);
  assert.equal(MAX_TOTAL_SHARD_UNCOMPRESSED_BYTES, 256_000_000);
  assert.equal(MAX_SHARD_CONCURRENCY, 32);
});
