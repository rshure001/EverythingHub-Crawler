"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const handler = require("../src/api/search-fast");
const { queryTerms, selectFeeds, searchNewsItems } = handler._test;
const { NEWS_FEEDS } = require("../src/api/news")._test;
const { resetRobotsForTests } = require("../src/lib/robots");

function mockResponse(status, body, url) {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    async text() { return body; }
  };
}

function mockResponseObject() {
  return {
    statusCode: 0,
    headers: {},
    body: null,
    setHeader(name, value) { this.headers[name] = value; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    end() { return this; }
  };
}

function feedXml(host) {
  const title = host.includes("jpl.nasa.gov") ? "NASA Artemis mission update" : "Latest public update from " + host;
  const summary = host.includes("jpl.nasa.gov") ? "NASA space science and exploration news." : "Verified public information from " + host;
  return "<rss><channel><item><title>" + title + "</title><link>https://" + host + "/news/latest</link><pubDate>Sun, 22 Nov 2026 12:00:00 GMT</pubDate><description><![CDATA[" + summary + "]]></description></item></channel></rss>";
}

test.beforeEach(() => resetRobotsForTests());
test.afterEach(() => resetRobotsForTests());

test("web fast-fallback does not call robots-disallowed external search providers", async () => {
  const originalFetch = global.fetch;
  let calls = 0;
  global.fetch = async () => { calls += 1; throw new Error("unexpected external request"); };
  try {
    const res = mockResponseObject();
    await handler({ method: "GET", query: { q: "NASA", lane: "web" } }, res);
    assert.equal(res.statusCode, 503);
    assert.equal(res.body.status, "unavailable");
    assert.equal(calls, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test("news search uses permitted official RSS feeds and returns query-matched stories", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    if (value.endsWith("/robots.txt")) return mockResponse(200, "User-agent: *\nAllow: /\n", value);
    return mockResponse(200, feedXml(new URL(value).hostname), value);
  };
  try {
    const res = mockResponseObject();
    await handler({ method: "GET", query: { q: "NASA", lane: "news" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.status, "ok");
    assert.equal(res.body.query, "NASA");
    assert.ok(res.body.results.some(item => /NASA Artemis/.test(item.title)));
    assert.ok(res.body.providers.includes("NASA Jet Propulsion Laboratory"));
    assert.equal(calls.some(url => url.startsWith("https://news.google.com/")), false);
    assert.ok(calls.some(url => url.includes("jpl.nasa.gov/feeds/news/")));
  } finally {
    global.fetch = originalFetch;
  }
});

test("generic news queries return recent permitted stories without requiring a literal keyword match", async () => {
  const originalFetch = global.fetch;
  global.fetch = async url => {
    const value = String(url);
    if (value.endsWith("/robots.txt")) return mockResponse(200, "User-agent: *\nAllow: /\n", value);
    return mockResponse(200, feedXml(new URL(value).hostname), value);
  };
  try {
    const res = mockResponseObject();
    await handler({ method: "GET", query: { q: "latest world news", lane: "news" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.status, "ok");
    assert.ok(res.body.results.length > 0);
    assert.equal(res.body.queryMatchFallback, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test("news search shows recent verified stories when no feed item matches the exact query", async () => {
  const originalFetch = global.fetch;
  global.fetch = async url => {
    const value = String(url);
    if (value.endsWith("/robots.txt")) return mockResponse(200, "User-agent: *\nAllow: /\n", value);
    return mockResponse(200, feedXml(new URL(value).hostname), value);
  };
  try {
    const res = mockResponseObject();
    await handler({ method: "GET", query: { q: "Las Vegas", lane: "news" } }, res);
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.status, "ok");
    assert.equal(res.body.queryMatchFallback, true);
    assert.ok(res.body.results.length > 0);
    assert.match(res.body.note, /showing the latest verified stories/i);
  } finally {
    global.fetch = originalFetch;
  }
});

test("news search fails closed when all official feed robots policies deny access", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async url => {
    const value = String(url);
    calls.push(value);
    return mockResponse(200, "User-agent: *\nDisallow: /\n", value);
  };
  try {
    const res = mockResponseObject();
    await handler({ method: "GET", query: { q: "NASA", lane: "news" } }, res);
    assert.equal(res.statusCode, 502);
    assert.equal(res.body.status, "unavailable");
    assert.ok(calls.length > 0);
    assert.ok(calls.every(url => url.endsWith("/robots.txt")));
  } finally {
    global.fetch = originalFetch;
  }
});

test("news query normalization and feed selection remain bounded", () => {
  assert.deepEqual(queryTerms("Latest world news"), []);
  assert.deepEqual(queryTerms("NASA space mission"), ["nasa", "space", "mission"]);
  const selected = selectFeeds("NASA Artemis");
  assert.ok(selected.length > 0 && selected.length <= 5);
  assert.equal(new Set(selected.map(feed => feed.url)).size, selected.length);
  assert.ok(selected.some(feed => feed.source === "NASA Jet Propulsion Laboratory"));
  const matched = searchNewsItems([{ title: "NASA Artemis", summary: "Space flight", source: "NASA", url: "https://nasa.gov/story" }], "NASA");
  assert.equal(matched.results.length, 1);
  assert.equal(NEWS_FEEDS.science.length, 3);
});
