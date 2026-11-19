const assert = require("node:assert/strict");
const test = require("node:test");

const {
  MAX_CATALOG_GZIP_BYTES,
  MAX_INDEXED_PAGES,
  MAX_SHARD_COUNT
} = require("../src/lib/catalog");

test("Vercel catalog reader accepts the current 10,113,629-byte catalog", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 12_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 10_113_629);
});

test("Vercel catalog reader accepts current page and shard growth with headroom", () => {
  assert.equal(MAX_INDEXED_PAGES, 30_000);
  assert.ok(MAX_INDEXED_PAGES >= 21_024);
  assert.equal(MAX_SHARD_COUNT, 256);
  assert.ok(MAX_SHARD_COUNT >= 121);
});
