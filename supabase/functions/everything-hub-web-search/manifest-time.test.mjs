import test from "node:test";
import assert from "node:assert/strict";
import { normalizeManifestTimestamp } from "./manifest-time.mjs";

test("converts the crawler manifest's Unix seconds to ISO 8601", () => {
  assert.equal(
    normalizeManifestTimestamp(1794963997),
    "2026-11-18T01:06:37.000Z",
  );
});

test("accepts an existing ISO timestamp", () => {
  assert.equal(
    normalizeManifestTimestamp("2026-11-18T01:06:37Z"),
    "2026-11-18T01:06:37.000Z",
  );
});

test("rejects missing, invalid, zero, and negative timestamps", () => {
  for (const value of [null, undefined, "", "not-a-date", 0, -1, NaN, Infinity]) {
    assert.equal(normalizeManifestTimestamp(value), null);
  }
});

test("rejects timestamps outside JavaScript's supported date range", () => {
  assert.equal(normalizeManifestTimestamp(Number.MAX_VALUE), null);
});
