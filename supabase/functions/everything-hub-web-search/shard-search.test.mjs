import test from "node:test";
import assert from "node:assert/strict";
import { intersectShardPostings, tokenizeIndexedQuery } from "./shard-search.mjs";

test("query tokenization matches crawler NFKC normalization and stop words", () => {
  assert.deepEqual(tokenizeIndexedQuery("The NASA — Café 42"), ["nasa", "café", "42"]);
  assert.deepEqual(tokenizeIndexedQuery("THE and with"), []);
});

test("query shard routing intersects every term and returns sorted ordinals", () => {
  const postings = new Map([
    ["las", [[8, 2], [2, 4], [5, 1]]],
    ["vegas", [[5, 3], [2, 7], [9, 1]]],
  ]);
  assert.deepEqual(intersectShardPostings(["las", "vegas"], postings), [2, 5]);
});

test("query shard routing returns no candidates when a term is absent", () => {
  const postings = new Map([["rolex", [[1, 2]]]]);
  assert.deepEqual(intersectShardPostings(["rolex", "missing"], postings), []);
});

test("query shard routing ignores malformed posting ordinals", () => {
  const postings = new Map([["nasa", [[3, 2], [-1, 1], ["4", 2], [true, 1]]]]);
  assert.deepEqual(intersectShardPostings(["nasa"], postings), [3]);
});
