const assert = require("node:assert/strict");
const test = require("node:test");

const {
  MAX_CATALOG_GZIP_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT,
  MAX_SHARD_BYTES,
  MAX_TOTAL_SHARD_BYTES,
  MAX_SHARD_CONCURRENCY
} = require("../src/lib/catalog");

test("Vercel shard reader accepts the current 10,841,934-byte gzip manifest export", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 12_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 10_841_934);
});

test("Vercel shard reader accepts the current 22,607-page, 129-shard index with headroom", () => {
  assert.equal(MAX_INDEXED_PAGES, 30_000);
  assert.ok(MAX_INDEXED_PAGES >= 22_607);
  assert.equal(MAX_SHARD_COUNT, 256);
  assert.ok(MAX_SHARD_COUNT >= 129);
  assert.equal(MAX_SHARD_BYTES, 1_000_000);
  assert.equal(MAX_TOTAL_SHARD_BYTES, 48_000_000);
  assert.equal(MAX_SHARD_CONCURRENCY, 32);
});
