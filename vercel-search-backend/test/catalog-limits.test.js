const assert = require("node:assert/strict");
const test = require("node:test");

const { MAX_CATALOG_GZIP_BYTES } = require("../src/lib/catalog");

test("Vercel catalog reader accepts the observed 8,011,063-byte gzip catalog", () => {
  assert.equal(MAX_CATALOG_GZIP_BYTES, 10_000_000);
  assert.ok(MAX_CATALOG_GZIP_BYTES >= 8_011_063);
});
