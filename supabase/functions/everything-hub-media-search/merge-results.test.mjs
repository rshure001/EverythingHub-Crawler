import test from "node:test";
import assert from "node:assert/strict";
import { mergeMediaResults, mediaProviderLabel } from "./merge-results.mjs";

test("combines Commons reuse metadata with broad Bing discovery", () => {
  const commons = [{ pageUrl: "https://commons.wikimedia.org/wiki/File:Octopus", source: "Wikimedia Commons", license: "CC BY-SA 4.0" }];
  const bing = [{ pageUrl: "https://example.org/octopus", source: "Bing Images", usageRights: "Unverified" }];
  const results = mergeMediaResults(bing, commons);
  assert.deepEqual(results.map((item) => item.source), ["Wikimedia Commons", "Bing Images"]);
  assert.equal(mediaProviderLabel(results, "image"), "Wikimedia Commons + Bing Images");
});

test("deduplicates source pages after removing fragments", () => {
  const results = mergeMediaResults(
    [{ pageUrl: "https://example.org/item#bing", source: "Bing Images" }],
    [{ pageUrl: "https://example.org/item#commons", source: "Wikimedia Commons" }],
  );
  assert.equal(results.length, 1);
  assert.equal(results[0].source, "Wikimedia Commons");
  assert.equal(results[0].pageUrl, "https://example.org/item");
});

test("rejects invalid source-page schemes and malformed URLs", () => {
  const results = mergeMediaResults([
    { pageUrl: "javascript:alert(1)", source: "Bing Images" },
    { pageUrl: "not a URL", source: "Bing Images" },
  ], []);
  assert.equal(results.length, 0);
});

test("caps total output at 24 and fills the Commons and Bing allocations", () => {
  const commons = Array.from({ length: 20 }, (_, i) => ({ pageUrl: `https://commons.example/${i}`, source: "Wikimedia Commons" }));
  const bing = Array.from({ length: 20 }, (_, i) => ({ pageUrl: `https://bing.example/${i}`, source: "Bing Images" }));
  const results = mergeMediaResults(bing, commons, 999);
  assert.equal(results.length, 24);
  assert.equal(results.filter((item) => item.source === "Wikimedia Commons").length, 8);
  assert.equal(results.filter((item) => item.source === "Bing Images").length, 16);
});

test("reports the correct video provider label", () => {
  assert.equal(mediaProviderLabel([{ source: "Bing Videos" }], "video"), "Bing Videos");
  assert.equal(mediaProviderLabel([{ source: "Wikimedia Commons" }], "video"), "Wikimedia Commons");
  assert.equal(mediaProviderLabel([], "image"), "No media provider");
});
