import test from "node:test";
import assert from "node:assert/strict";
import { searchStaticPages } from "./static-search.mjs";

const pages = [
  {
    url: "https://www.rolex.com/",
    title: "Rolex Official Watches",
    description: "Swiss luxury watchmaker",
    text: "Explore Rolex watches and the history of the brand.",
    lane: "business",
  },
  {
    url: "https://example.org/watches",
    title: "Luxury watches explained",
    description: "Rolex and other Swiss watches",
    text: "A guide to choosing a watch.",
    lane: "culture",
  },
  {
    url: "https://science.nasa.gov/apollo-11/",
    title: "NASA Apollo 11 Mission",
    description: "Apollo 11 was the first crewed Moon landing.",
    text: "NASA mission history and lunar exploration.",
    lane: "science",
  },
  {
    url: "https://blocked.example/rolex",
    title: "Rolex blocked page",
    description: "Must not appear in public search.",
    text: "Robots disallowed.",
    lane: "business",
    robots_allowed: false,
  },
  {
    url: "javascript:alert(1)",
    title: "Rolex unsafe URL",
    description: "Invalid public result.",
    text: "Must be ignored.",
    lane: "business",
  },
];

test("ranks a title and exact-phrase match above a body-only match", () => {
  const result = searchStaticPages(pages, "Rolex", null, 10, 0);
  assert.equal(result.count, 2);
  assert.equal(result.results[0].url, "https://www.rolex.com/");
  assert.equal(result.results[0].origin, "Everything Hub public crawler index");
});

test("requires all query terms to occur in indexed content", () => {
  const result = searchStaticPages(pages, "NASA Apollo 11", null, 10, 0);
  assert.equal(result.count, 1);
  assert.equal(result.results[0].url, "https://science.nasa.gov/apollo-11/");
});

test("filters by the requested search lane", () => {
  const result = searchStaticPages(pages, "Rolex", "culture", 10, 0);
  assert.equal(result.count, 1);
  assert.equal(result.results[0].url, "https://example.org/watches");
  assert.equal(result.results[0].tentacle, "culture");
});

test("supports stable pagination and reports whether more matches exist", () => {
  const many = Array.from({ length: 5 }, (_, index) => ({
    url: "https://example.org/item-" + index,
    title: "Rolex guide " + index,
    description: "Rolex watches",
    text: "Swiss watches.",
    lane: "business",
  }));
  const first = searchStaticPages(many, "Rolex", null, 2, 0);
  const second = searchStaticPages(many, "Rolex", null, 2, 2);
  assert.equal(first.count, 5);
  assert.equal(first.results.length, 2);
  assert.equal(first.hasMore, true);
  assert.equal(second.results.length, 2);
  assert.equal(second.hasMore, true);
  assert.notEqual(first.results[0].url, second.results[0].url);
});

test("excludes robots-denied records and non-HTTP URLs", () => {
  const result = searchStaticPages(pages, "Rolex", null, 10, 0);
  assert.equal(result.results.some((item) => item.url.includes("blocked.example")), false);
  assert.equal(result.results.some((item) => item.url.startsWith("javascript:")), false);
});

test("normalizes punctuation and case in multi-term queries", () => {
  const result = searchStaticPages(pages, "  nasa-apollo 11  ", null, 10, 0);
  assert.equal(result.count, 1);
  assert.equal(result.results[0].source, "science.nasa.gov");
});
