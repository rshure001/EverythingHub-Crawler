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

test("Vercel catalog loader accepts the current 14,112,415-byte compressed catalog with headroom", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 16_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 14_112_415);
});

test("Vercel catalog loader accepts the current 29,045-page, 164-shard index with headroom", () => {
  assert.equal(MAX_INDEXED_PAGES, 40_000);
  assert.ok(MAX_INDEXED_PAGES >= 29_045);
  assert.equal(MAX_SHARD_COUNT, 256);
  assert.ok(MAX_SHARD_COUNT >= 164);
  assert.equal(MAX_SHARD_BYTES, 1_000_000);
  assert.equal(MAX_TOTAL_SHARD_BYTES, 80_000_000);
  assert.ok(MAX_TOTAL_SHARD_BYTES >= 55_460_106);
  assert.equal(MAX_SHARD_CONCURRENCY, 32);
});
