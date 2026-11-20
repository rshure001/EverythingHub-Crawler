import test from "node:test";
import assert from "node:assert/strict";
import { searchStaticPages } from "./static-search.mjs";

const pages = [
  { url: "https://www.rolex.com/watches", title: "Rolex Watches", description: "Swiss precision watchmaking", text: "Luxury watch brand", lane: "business", robots_allowed: true },
  { url: "https://example.org/watch", title: "Watch", description: "A wrist timepiece", text: "Rolex is mentioned in this long article", lane: "culture", robots_allowed: true },
  { url: "https://www.nasa.gov/missions/mars", title: "NASA Mars Missions", description: "Explore Mars with NASA", text: "Science and space exploration", lane: "science", robots_allowed: true },
  { url: "https://blocked.example/rolex", title: "Rolex blocked", description: "Rolex", text: "", lane: "business", robots_allowed: false },
];

test("title and phrase matches rank above body-only matches", () => {
  const result = searchStaticPages(pages, "Rolex");
  assert.equal(result.results[0].url, "https://www.rolex.com/watches");
  assert.equal(result.count, 2);
});

test("structured product name, brand, SKU, and price are searchable and returned", () => {
  const productPage = {
    url: "https://jewel.example/ring-18k",
    title: "Jewelry collection",
    description: "Fine jewelry",
    text: "Shop our collection",
    lane: "shopping",
    product: {
      type: "product", name: "18K Diamond Pinky Ring",
      brand: "Rolex Fine Jewelry", sku: "RING-18K-442",
      price: "1299.00", currency: "USD", availability: "InStock",
      image: "https://jewel.example/images/ring.jpg",
      url: "https://jewel.example/ring-18k",
    },
  };
  for (const query of ["Rolex", "RING-18K-442", "1299.00", "InStock"]) {
    const result = searchStaticPages([productPage], query);
    assert.equal(result.count, 1, query);
    assert.equal(result.results[0].product.name, "18K Diamond Pinky Ring");
    assert.equal(result.results[0].product.image, "https://jewel.example/images/ring.jpg");
  }
});

test("multi-term queries require all terms", () => {
  const result = searchStaticPages(pages, "NASA Mars");
  assert.equal(result.count, 1);
  assert.equal(result.results[0].url, "https://www.nasa.gov/missions/mars");
});

test("lane filter is applied", () => {
  const result = searchStaticPages(pages, "Rolex", "culture");
  assert.equal(result.count, 1);
  assert.equal(result.results[0].tentacle, "culture");
});

test("robots-denied records are excluded", () => {
  const result = searchStaticPages(pages, "Rolex");
  assert.equal(result.results.some((item) => item.url.includes("blocked.example")), false);
});

test("invalid schemes, malformed URLs, and credential-bearing URLs are excluded", () => {
  const result = searchStaticPages([
    { url: "javascript:alert(1)", title: "Rolex" },
    { url: "not a URL", title: "Rolex" },
    { url: "https://user:secret@example.com/rolex", title: "Rolex" },
  ], "Rolex");
  assert.equal(result.count, 0);
});

test("duplicate canonical URLs are removed", () => {
  const result = searchStaticPages([pages[0], { ...pages[0], title: "Rolex duplicate" }], "Rolex");
  assert.equal(result.count, 1);
});

test("tracking parameters, fragments, host case, and default ports canonicalize", () => {
  const result = searchStaticPages([
    {
      url: "HTTPS://EXAMPLE.COM:443/rolex?utm_source=newsletter&ref=watch#details",
      title: "Rolex Watch",
      description: "Swiss watchmaking",
      lane: "business",
    },
    {
      url: "https://example.com/rolex?ref=watch",
      title: "Rolex Rolex Watchmaking",
      description: "Swiss watchmaking",
      lane: "business",
    },
  ], "Rolex");
  assert.equal(result.count, 1);
  assert.equal(result.results[0].url, "https://example.com/rolex?ref=watch");
  assert.equal(result.results[0].title, "Rolex Rolex Watchmaking");
});

test("a non-matching duplicate cannot hide a later matching record", () => {
  const result = searchStaticPages([
    {
      url: "https://example.com/shared?utm_campaign=one",
      title: "General information",
      description: "A page about something else",
      lane: "general",
    },
    {
      url: "https://example.com/shared#section",
      title: "Rolex service centers",
      description: "Find a Rolex service center",
      lane: "business",
    },
  ], "Rolex");
  assert.equal(result.count, 1);
  assert.equal(result.results[0].title, "Rolex service centers");
});

test("Unicode compatibility normalization works", () => {
  const result = searchStaticPages([{ url: "https://example.com", title: "ＲＯＬＥＸ" }], "Rolex");
  assert.equal(result.count, 1);
});

test("pagination and result limits are bounded", () => {
  const expanded = Array.from({ length: 30 }, (_, i) => ({
    url: "https://example.com/" + i,
    title: "NASA Mission " + i,
    description: "NASA mission",
    text: "space",
    lane: "science",
  }));
  const first = searchStaticPages(expanded, "NASA", null, 999, 0);
  const second = searchStaticPages(expanded, "NASA", null, 10, 10);
  assert.equal(first.results.length, 20);
  assert.equal(first.hasMore, true);
  assert.equal(second.results.length, 10);
  assert.equal(second.results[0].url, first.results[10].url);
});

test("snippets and response fields remain compatible", () => {
  const result = searchStaticPages(pages, "NASA");
  assert.equal(result.results[0].source, "www.nasa.gov");
  assert.equal(result.results[0].origin, "Everything Hub public crawler index");
  assert.equal(result.results[0].tentacle, "science");
  assert.ok(result.results[0].snippet.length > 0);
  assert.ok(Number.isFinite(result.results[0].relevance));
});

test("Hurricane evidence score refines lexical relevance without replacing it", () => {
  const result = searchStaticPages(pages, "Rolex", null, 10, 0, Date.parse("2026-11-18T00:00:00Z"));
  const maxLexical = Math.max(...result.results.map((item) => item.relevance));
  assert.ok(result.results.every((item) => Number.isFinite(item.intelligenceScore)));
  assert.ok(result.results.every((item) => Number.isFinite(item.rankScore)));
  assert.ok(result.results.every((item) => ["high", "medium", "low"].includes(item.evidenceBand)));
  assert.ok(result.results.every((item) => item.evidenceComponents?.relevance !== undefined));
  const expected = result.results.map((item) => Number((
    0.85 * (item.relevance / maxLexical) + 0.15 * item.intelligenceScore
  ).toFixed(6)));
  assert.deepEqual(result.results.map((item) => item.rankScore), expected);
  assert.deepEqual(result.results.map((item) => item.rankScore), [...expected].sort((a, b) => b - a));
});

test("publication time takes precedence over crawl time for freshness", () => {
  const now = Date.parse("2026-11-18T00:00:00Z");
  const result = searchStaticPages([
    {
      url: "https://a.example/nasa",
      title: "NASA Mars mission",
      description: "NASA mission",
      fetched_at: now / 1000,
      published_at: (now - 365 * 86400000) / 1000,
    },
    {
      url: "https://b.example/nasa",
      title: "NASA Mars mission",
      description: "NASA mission",
      fetched_at: now / 1000,
      published_at: now / 1000,
    },
  ], "NASA", null, 10, 0, now);
  const old = result.results.find((item) => item.url.includes("a.example"));
  const recent = result.results.find((item) => item.url.includes("b.example"));
  assert.ok(old.evidenceComponents.freshness < recent.evidenceComponents.freshness);
  assert.equal(recent.evidenceComponents.freshness, 1);
  assert.ok(recent.rankScore > old.rankScore);
});

test("corroboration counts distinct hosts rather than repeated pages", () => {
  const result = searchStaticPages([
    { url: "https://a.example/one", title: "Apollo mission", description: "Apollo space" },
    { url: "https://a.example/two", title: "Apollo mission", description: "Apollo space" },
    { url: "https://b.example/three", title: "Apollo mission", description: "Apollo space" },
  ], "Apollo", null, 10, 0, Date.parse("2026-11-18T00:00:00Z"));
  assert.equal(result.count, 3);
  assert.ok(result.results.every((item) => item.corroboratingHosts === 1));
});

test("unknown page age receives a conservative freshness value", () => {
  const result = searchStaticPages([
    { url: "https://example.org/nasa", title: "NASA mission" },
  ], "NASA", null, 10, 0, Date.parse("2026-11-18T00:00:00Z"));
  assert.equal(result.results[0].evidenceComponents.freshness, 0.35);
});
