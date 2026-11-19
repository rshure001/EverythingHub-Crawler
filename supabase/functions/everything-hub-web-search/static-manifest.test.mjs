import test from "node:test";
import assert from "node:assert/strict";
import { isValidStaticManifest, MAX_STATIC_INDEX_PAGES, MAX_STATIC_INDEX_SHARDS } from "./static-manifest.mjs";

function makeManifest(shardCount = 35, totalPages = 6350) {
  return {
    version: 1,
    integrity: "sha256",
    total_pages: totalPages,
    shard_count: shardCount,
    shards: Array.from({ length: shardCount }, (_, index) => ({
      file: `shard-${String(index + 1).padStart(4, "0")}.json`,
      bytes: 100,
      pages: 1,
      sha256: "a".repeat(64),
    })),
  };
}

test("accepts the current 12k-plus, 68-shard public catalog", () => {
  assert.equal(isValidStaticManifest(makeManifest(68, 12025)), true);
});

test("accepts the configured shard ceiling", () => {
  assert.equal(isValidStaticManifest(makeManifest(MAX_STATIC_INDEX_SHARDS)), true);
});

test("rejects catalogs beyond the bounded shard ceiling", () => {
  assert.equal(isValidStaticManifest(makeManifest(MAX_STATIC_INDEX_SHARDS + 1)), false);
});

test("rejects shard-count mismatches", () => {
  assert.equal(isValidStaticManifest({ ...makeManifest(35), shard_count: 34 }), false);
});

test("accepts the configured page ceiling and rejects counts beyond it", () => {
  assert.equal(isValidStaticManifest(makeManifest(MAX_STATIC_INDEX_SHARDS, MAX_STATIC_INDEX_PAGES)), true);
  assert.equal(isValidStaticManifest(makeManifest(MAX_STATIC_INDEX_SHARDS, MAX_STATIC_INDEX_PAGES + 1)), false);
  assert.equal(isValidStaticManifest(makeManifest(35, 0)), false);
  assert.equal(isValidStaticManifest({ ...makeManifest(35), total_pages: "6350" }), false);
});

test("rejects unsupported versions and integrity modes", () => {
  assert.equal(isValidStaticManifest({ ...makeManifest(35), version: 2 }), false);
  assert.equal(isValidStaticManifest({ ...makeManifest(35), integrity: "md5" }), false);
});
