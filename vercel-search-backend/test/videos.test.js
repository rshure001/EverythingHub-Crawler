"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/videos");
const { parseRobots, normalizeNASAItem, resetForTests } = handler._test;

function response(status, body, url) {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    headers: { get() { return null; } },
    async text() { return body; }
  };
}

function nasaItem(index, overrides = {}) {
  const id = "NASA_VIDEO_" + index;
  return {
    data: [{
      nasa_id: id,
      title: "NASA mission video " + index,
      media_type: "video",
      date_created: "2026-11-20T00:00:00Z",
      center: "NASA",
      photographer: "NASA",
      copyright: "NASA",
      description: "A NASA mission video.",
      ...overrides
    }],
    links: [{
      rel: "preview",
      href: "https://images-assets.nasa.gov/video/" + id + "/" + id + "~thumb.jpg"
    }]
  };
}

function mockResponse() {
  return {
    statusCode: 0,
    headers: {},
    body: null,
    setHeader(name, value) { this.headers[name] = value; return this; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    end() { return this; }
  };
}

test("robots parser applies longest matching rule and crawl delay", () => {
  resetForTests();
  const policy = parseRobots([
    "User-agent: *",
    "Disallow: /private",
    "Allow: /private/public",
    "Crawl-delay: 3"
  ].join("\n"), "EverythingHubVideoSearch/1.0");
  assert.equal(policy.allows("https://images-api.nasa.gov/private/hidden"), false);
  assert.equal(policy.allows("https://images-api.nasa.gov/private/public/item"), true);
  assert.equal(policy.allows("https://images-api.nasa.gov/search?q=NASA"), true);
  assert.equal(policy.delayMs, 3000);
});

test("robots parser prefers a specific user-agent group over wildcard", () => {
  resetForTests();
  const policy = parseRobots([
    "User-agent: EverythingHubVideoSearch",
    "Disallow: /search",
    "User-agent: *",
    "Allow: /"
  ].join("\n"), "EverythingHubVideoSearch/1.0");
  assert.equal(policy.allows("https://images-api.nasa.gov/search?q=NASA"), false);
});

test("NASA normalizer requires video metadata and an HTTPS NASA preview", () => {
  const valid = normalizeNASAItem(nasaItem(1));
  assert.ok(valid);
  assert.equal(valid.source, "NASA Image and Video Library");
  assert.equal(valid.url, "https://images.nasa.gov/details/NASA_VIDEO_1");
  assert.match(valid.thumbnail, /^https:\/\/images-assets\.nasa\.gov\//);
  assert.equal(valid.credit, "NASA");

  const thirdParty = nasaItem(2, { copyright: "Third Party Studio" });
  assert.equal(normalizeNASAItem(thirdParty), null);

  const nonVideo = nasaItem(3, { media_type: "image" });
  assert.equal(normalizeNASAItem(nonVideo), null);

  const insecurePreview = nasaItem(4);
  insecurePreview.links[0].href = "http://images-assets.nasa.gov/video/unsafe.jpg";
  assert.equal(normalizeNASAItem(insecurePreview), null);
});

test("video handler returns deduplicated NASA metadata after API and thumbnail robots checks", async () => {
  resetForTests();
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url) => {
    const parsed = new URL(url);
    calls.push(parsed.href);
    if (parsed.hostname === "images-api.nasa.gov" && parsed.pathname === "/robots.txt") {
      return response(200, "User-agent: *\nAllow: /search\n", parsed.href);
    }
    if (parsed.hostname === "images-assets.nasa.gov" && parsed.pathname === "/robots.txt") {
      return response(200, "User-agent: *\nAllow: /video/\n", parsed.href);
    }
    if (parsed.hostname === "images-api.nasa.gov" && parsed.pathname === "/search") {
      const items = Array.from({ length: 20 }, (_, index) => nasaItem(index));
      items.push(nasaItem(0));
      return response(200, JSON.stringify({ collection: { items } }), parsed.href);
    }
    throw new Error("Unexpected external request: " + parsed.href);
  };

  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.provider, "NASA Image and Video Library");
    assert.equal(res.body.count, 20);
    assert.equal(res.body.items.length, 20);
    assert.ok(res.body.items.every(item => item.title && item.credit && item.thumbnail));
    assert.ok(res.body.items.every(item => item.url.startsWith("https://images.nasa.gov/details/")));
    assert.ok(res.body.items.every(item => item.attributionUrl.includes("nasa.gov")));
    assert.ok(calls.every(url => ["images-api.nasa.gov", "images-assets.nasa.gov"].includes(new URL(url).hostname)));
  } finally {
    global.fetch = originalFetch;
    resetForTests();
  }
});

test("robots denial blocks the NASA API request and fails closed", async () => {
  resetForTests();
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url) => {
    calls.push(String(url));
    return response(200, "User-agent: *\nDisallow: /search\n", String(url));
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA" } }, res);
    assert.equal(res.statusCode, 503);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls.length, 1);
  } finally {
    global.fetch = originalFetch;
    resetForTests();
  }
});

test("robots denial of preview assets prevents their display", async () => {
  resetForTests();
  const originalFetch = global.fetch;
  global.fetch = async (url) => {
    const parsed = new URL(url);
    if (parsed.hostname === "images-api.nasa.gov" && parsed.pathname === "/robots.txt") {
      return response(200, "User-agent: *\nAllow: /search\n", parsed.href);
    }
    if (parsed.hostname === "images-assets.nasa.gov" && parsed.pathname === "/robots.txt") {
      return response(200, "User-agent: *\nDisallow: /video/\n", parsed.href);
    }
    if (parsed.hostname === "images-api.nasa.gov" && parsed.pathname === "/search") {
      return response(200, JSON.stringify({ collection: { items: [nasaItem(1)] } }), parsed.href);
    }
    throw new Error("Unexpected external request: " + parsed.href);
  };
  try {
    const res = mockResponse();
    await handler({ method: "GET", query: { q: "NASA" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.count, 0);
  } finally {
    global.fetch = originalFetch;
    resetForTests();
  }
});
